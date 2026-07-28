# nested_cv/tasks.py
# Async generation task lifecycle

import asyncio
import threading
from .models import NestedTask, NestedSession, OutputFormat
from .session_store import get_session_store
from .sql_composer import compose_sql
from .pyspark_composer import compose_pyspark


async def _run_generation(task_id: str, session_id: str):
    """Background thread worker for SQL/PySpark generation."""
    store = get_session_store()
    task = store.get_task(task_id)
    session = store.get_session(session_id)

    if not task or not session:
        return

    # Mark IN_PROGRESS
    task.status = "IN_PROGRESS"
    task.progress = 5
    task.message = "Starting generation..."
    store.update_task(task)

    def _check_cancel() -> bool:
        """Return True if the user asked to cancel; if so, mark CANCELLED
        and bail out of the worker."""
        if store.is_cancel_requested(task_id):
            task.status = "CANCELLED"
            task.message = "Cancelled by user"
            task.progress = 0
            store.update_task(task)
            return True
        return False

    try:
        if _check_cancel():
            return
        artifacts = list(session.artifacts.values())
        links = session.dependency_links
        mappings = session.global_mappings

        if not artifacts:
            task.status = "FAILED"
            task.message = "No artifacts in session"
            task.progress = 0
            store.update_task(task)
            return

        # ── Single artifact: reuse Mapping Tool pipeline for exact parity ─────
        if len(artifacts) == 1 and artifacts[0].sql_info_raw:
            artifact = artifacts[0]
            task.progress = 10
            task.message = "Using Mapping Tool pipeline for single CV..."
            store.update_task(task)
            if _check_cancel():
                return

            # Convert MappingEntry[] → MappingTool format dicts
            mappings_list = [
                {
                    "sourceTable": m.source_ref_canonical,
                    "sourceField": m.source_column_raw,
                    "targetTable": m.target_table,
                    "targetField": m.target_column,
                }
                for m in mappings
                if m.artifact_id == artifact.artifact_id
            ]

            task.progress = 30
            task.message = "Generating SQL via Mapping Tool engine..."
            store.update_task(task)
            if _check_cancel():
                return

            # Call the same function as MappingTool
            from mapping_sql_generator import generate_sql_from_mapping
            result = await generate_sql_from_mapping(
                artifact.sql_info_raw,
                mappings_list,
                session.target_dialect,
                output_format=session.output_format,
            )

            if session.output_format == OutputFormat.PYSPARK.value:
                # result is (notebook_json, "")
                content = result[0] if result[0] else "# PySpark generation failed"
            else:
                # result is (cte_sql, temp_table_sql)
                content = result[0] if result[0] else "-- SQL generation failed"

            task.diagnostics = []
            task.progress = 80
            task.message = "Finalizing..."
            store.update_task(task)

            task.output_format = session.output_format
            task.result_content = content
            task.result_url = f"/api/nested/tasks/{task_id}/download"

            task.progress = 100
            task.status = "COMPLETED"
            task.message = "Generation complete"
            store.update_task(task)
            return

        # ── Multiple artifacts: use nested flattening pipeline ─────────────────
        task.progress = 20
        task.message = "Building dependency graph..."
        store.update_task(task)
        if _check_cancel():
            return

        task.progress = 40
        task.message = "Composing SQL..."
        store.update_task(task)
        if _check_cancel():
            return

        if session.output_format == OutputFormat.PYSPARK.value:
            lines, diags = compose_pyspark(artifacts, links, mappings)
            task.diagnostics = diags
            content = "\n".join(lines) if lines else "# No content generated"
        else:
            stmts, diags = compose_sql(artifacts, links, mappings, session.target_dialect)
            task.diagnostics = diags
            content = "\n\n".join(stmts) if stmts else "-- No SQL generated"

        task.progress = 80
        task.message = "Finalizing..."
        store.update_task(task)

        task.output_format = session.output_format
        task.result_content = content
        task.result_url = f"/api/nested/tasks/{task_id}/download"

        task.progress = 100
        task.status = "COMPLETED"
        task.message = "Generation complete"
        store.update_task(task)

    except Exception as e:
        task.status = "FAILED"
        task.message = f"Generation failed: {str(e)}"
        task.progress = 0
        store.update_task(task)


def start_generation_task(session: NestedSession) -> NestedTask:
    """Create and start an async generation task for a session."""
    store = get_session_store()

    task = store.create_task(session.session_id)
    task.status = "PENDING"
    task.message = "Task queued"
    store.update_task(task)

    # Start background thread (uses asyncio internally)
    thread = threading.Thread(
        target=lambda: asyncio.run(_run_generation(task.task_id, session.session_id)),
        daemon=True,
    )
    thread.start()

    return task
