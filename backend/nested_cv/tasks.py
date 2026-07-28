# nested_cv/tasks.py
# Async generation task lifecycle
#
# Progress reporting: the orchestrator raises `GenerationProgress` events at
# well-defined phases. The worker here maps them to (progress %, message)
# pairs on the `NestedTask`, and surfaces the structured `phase` field too
# so the frontend can label the progress bar deterministically rather than
# parsing message text.

import asyncio
import threading

from .models import NestedSession, NestedTask, OutputFormat
from .session_store import get_session_store
from .orchestrator import (
    GenerationProgress,
    Phase,
    compose_for_session,
    source_table_casing_map,
    artifact_prefix,
    _topo_order_or_session,
    _noop_progress,
)


# Distinct, one-shot status messages per phase. The %-number itself is held
# in `task.progress` so the bar can drive off it; the message is what the
# user reads. Never put the % in the message — it gets shown alongside the
# bar and shows up twice.
_PHASE_LABELS = {
    Phase.STARTING: "Preparing…",
    Phase.VALIDATING: "Validating session…",
    Phase.RENDERING: "Rendering CV…",
    Phase.RENDERED: "Rendered CV…",
    Phase.COMPOSING: "Stitching output…",
    Phase.FINALIZING: "Finalizing…",
    Phase.COMPLETE: "Generation complete",
    Phase.FAILED: "Generation failed",
}

# The bar shows 0% at PENDING and 100% at COMPLETE. Phase transitions
# (RENDERING/RENDERED) move within that range. The numbers below are
# deliberate steps — a 2-CV chain walks 10 → 50 → 100, a 5-CV chain
# walks 10 → 25 → 50 → 75 → 95 → 100, etc. Final 100% is reserved for
# `_set_completed` so the user never sees the bar drop back.
_PHASE_PROGRESS = {
    Phase.STARTING: 10,
    Phase.VALIDATING: 15,
    Phase.COMPOSING: 92,
    Phase.FINALIZING: 97,
}


def _phase_to_progress(phase: Phase, current: int, total: int) -> int:
    """Map a structured phase to a 0–97 bar value.

    Per-artifact phases share the 20%–90% range, scaled by the artifact
    position. This keeps the bar moving smoothly for both 2-CV and 5-CV
    chains and is independent of wall-clock time — the old "every 2
    seconds bump 15%" produced the duplicate `Compositing… 40%` messages
    because the time loop outpaced the work.
    """
    if phase in _PHASE_PROGRESS:
        return _PHASE_PROGRESS[phase]
    if phase in (Phase.RENDERING, Phase.RENDERED) and total > 0:
        # First artifact starts at 20%, last reaches 90%.
        return 20 + int(70 * max(0, current - 1) / total)
    return task.progress if (task := _current_task.get()) else 0


# Used by `_phase_to_progress` to peek at the current task. Set on entry to
# `_run_generation` and cleared on exit. Lets the helper be a plain function
# without dragging the task through every callback.
_current_task: dict = {"task": None}


async def _run_generation(task_id: str, session_id: str):
    """Background thread worker for SQL/PySpark generation."""
    store = get_session_store()
    task = store.get_task(task_id)
    session = store.get_session(session_id)

    if not task or not session:
        return

    _current_task["task"] = task

    # Mark IN_PROGRESS with a known starting state.
    task.status = "IN_PROGRESS"
    task.progress = 5
    task.message = "Queued…"
    task.phase = Phase.STARTING.value
    store.update_task(task)

    def _check_cancel() -> bool:
        if store.is_cancel_requested(task_id):
            task.status = "CANCELLED"
            task.message = "Cancelled by user"
            task.progress = 0
            task.phase = Phase.FAILED.value
            store.update_task(task)
            return True
        return False

    def _set_failed(msg: str):
        task.status = "FAILED"
        task.message = msg
        task.progress = 0
        task.phase = Phase.FAILED.value
        task.diagnostics = []
        store.update_task(task)

    def _set_completed(content: str):
        task.output_format = session.output_format
        task.result_content = content
        task.result_url = f"/api/nested/tasks/{task_id}/download"
        task.diagnostics = []
        task.progress = 100
        task.phase = Phase.COMPLETE.value
        task.status = "COMPLETED"
        task.message = "Generation complete"
        store.update_task(task)

    def _emit(ev: GenerationProgress):
        """Orchestrator → task progress sink. Single place where the bar is
        updated so the bar and the message can never disagree."""
        if _check_cancel():
            raise asyncio.CancelledError
        task.phase = ev.phase.value
        # Only overwrite the message for non-per-artifact phases; for
        # RENDERING/RENDERED the orchestrator's message is already
        # meaningful (e.g. "Rendering cv_base_sales (1/3)") and carries
        # the artifact name we want shown.
        if ev.phase == Phase.RENDERING or ev.phase == Phase.RENDERED:
            task.message = ev.message
        else:
            label = _PHASE_LABELS.get(ev.phase, ev.phase.value.title())
            task.message = label
        task.progress = _phase_to_progress(ev.phase, ev.current, ev.total)
        store.update_task(task)

    try:
        if _check_cancel():
            return

        artifacts = list(session.artifacts.values())
        if not artifacts:
            _set_failed("No artifacts in session")
            return

        # ── Single artifact with sql_info_raw ──────────────────────────────
        # Fast path: reuse mapping_sql_generator.generate_sql_from_mapping
        # exactly as the standalone MappingTool does, for any session with
        # a single renderable artifact. Preserves single-CV parity.
        if len(artifacts) == 1 and artifacts[0].sql_info_raw:
            artifact = artifacts[0]
            task.result_filename = (
                f"{artifact_prefix(artifact)}.{'pyspark' if session.output_format == OutputFormat.PYSPARK.value else 'sql'}"
            )
            store.update_task(task)
            task.message = "Rendering single CV…"
            task.progress = 30
            task.phase = Phase.RENDERING.value
            store.update_task(task)
            if _check_cancel():
                return

            # Convert MappingEntry[] → MappingTool format dicts.
            # `sourceTable` must carry the workbook's own spelling, not the
            # upper-cased canonical form — `match_mapping` merges on it
            # case-sensitively. See `orchestrator.source_table_casing_map`.
            casing = source_table_casing_map(artifact)
            mappings_list = [
                {
                    "sourceTable": casing.get(
                        (m.source_ref_canonical or "").upper(),
                        m.source_ref_canonical or "",
                    ),
                    "sourceField": m.source_column_raw,
                    "targetTable": m.target_table,
                    "targetField": m.target_column,
                }
                for m in session.global_mappings
                if m.artifact_id == artifact.artifact_id
            ]

            task.message = "Generating SQL via Mapping Tool engine…"
            task.progress = 60
            store.update_task(task)
            if _check_cancel():
                return

            from mapping_sql_generator import generate_sql_from_mapping

            result = await generate_sql_from_mapping(
                artifact.sql_info_raw,
                mappings_list,
                session.target_dialect,
                output_format=session.output_format,
            )

            if session.output_format == OutputFormat.PYSPARK.value:
                content = result[0] if result[0] else "# PySpark generation failed"
            else:
                content = result[0] if result[0] else "-- SQL generation failed"

            task.message = "Finalizing…"
            task.progress = 90
            store.update_task(task)
            _set_completed(content)
            return

        # ── Multi-artifact (and any other case handled by the orchestrator)
        # Reuses `mapping_sql_generator.generate_sql_from_mapping` per
        # artifact and chains the per-artifact bodies through the wrapper
        # in `nested_cv/orchestrator.py`. No per-artifact SQL/PySpark logic
        # is reimplemented here — see the wrapper-only rule in
        # `backend/nested_cv/claude.md`.
        task.message = "Preparing composition…"
        task.progress = 10
        task.phase = Phase.STARTING.value
        store.update_task(task)
        if _check_cancel():
            return

        if not any(a.sql_info_raw for a in artifacts):
            _set_failed(
                "No artifact in this session has an XLSX payload. Upload a "
                "mapping workbook for at least one CV before generating."
            )
            return

        # Pick a download filename that mirrors the root (parent) CV's
        # mapping workbook, so a 5-CV chain downloads as `cv_sales_fact.sql`,
        # not `nested_cv_<taskid>.sql`.
        ext = "pyspark" if session.output_format == OutputFormat.PYSPARK.value else "sql"
        order = _topo_order_or_session(session)
        root_aid = order[-1] if order else None
        root_artifact = session.artifacts.get(root_aid) if root_aid else None
        if root_artifact is not None:
            stem = artifact_prefix(root_artifact)
            task.result_filename = f"{stem}.{ext}"
        else:
            task.result_filename = f"nested_cv_{task_id[:8]}.{ext}"
        store.update_task(task)

        # The orchestrator raises structured progress events; we translate
        # them into bar + message updates and finalise at 100% ourselves.
        rendered = await compose_for_session(session, _emit)
        if _check_cancel():
            return
        content, _fmt = rendered

        task.message = "Finalizing…"
        task.progress = 99
        task.phase = Phase.FINALIZING.value
        store.update_task(task)
        _set_completed(content)

    except asyncio.CancelledError:
        _set_failed("Generation cancelled")
    except Exception as e:
        _set_failed(f"Generation failed: {e}")
    finally:
        _current_task["task"] = None


def start_generation_task(session: NestedSession) -> NestedTask:
    """Create and start an async generation task for a session."""
    store = get_session_store()

    task = store.create_task(session.session_id)
    task.status = "PENDING"
    task.message = "Task queued"
    task.phase = Phase.STARTING.value
    store.update_task(task)

    # Start background thread (uses asyncio internally)
    thread = threading.Thread(
        target=lambda: asyncio.run(_run_generation(task.task_id, session.session_id)),
        daemon=True,
    )
    thread.start()

    return task
