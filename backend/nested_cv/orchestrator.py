# nested_cv/orchestrator.py
# Multi-artifact chained SQL/PySpark orchestrator.
#
# Wrapper-only rule (do not violate):
#   - The per-artifact render call is the existing
#     `mapping_sql_generator.generate_sql_from_mapping(...)`.
#   - This module composes its outputs into a single CTE chain. It does NOT
#     reimplement per-artifact SQL generation, dialect adaptation, UNION
#     splitting, mapping matching, or PySpark notebook assembly. Those live
#     in `backend/mapping_sql_generator.py` and `backend/pyspark.py`.

from __future__ import annotations

import ast
import asyncio
import json
import re
from dataclasses import dataclass
from enum import Enum
from typing import Awaitable, Callable, Optional

from .models import (
    NestedSession, OutputFormat, CvArtifact, DependencyLink,
)
from .dependency_graph import build_graph


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

_CTE_NAME_PREFIX = "cte"


class Phase(str, Enum):
    """Phases of multi-artifact generation. Surfaced to the UI so it can
    label the progress bar deterministically rather than parsing message text."""
    STARTING = "starting"
    VALIDATING = "validating"
    RENDERING = "rendering"      # one artifact is being rendered (per-artifact)
    RENDERED = "rendered"        # one artifact just finished (per-artifact)
    COMPOSING = "composing"      # stitching per-artifact bodies together
    FINALIZING = "finalizing"    # post-processing (de-qualify, normalize, etc.)
    COMPLETE = "complete"
    FAILED = "failed"


@dataclass
class GenerationProgress:
    """One progress event from the orchestrator.

    `phase` is the only field the UI should branch on; `message` is the
    human-readable text shown next to the bar. `current`/`total` are
    meaningful only during `RENDERING` / `RENDERED`; the worker in tasks.py
    turns them into a percentage.
    """
    phase: Phase
    message: str
    current: int = 0
    total: int = 0
    artifact_name: str = ""

    def __str__(self) -> str:
        return self.message


ProgressCallback = Callable[[GenerationProgress], None]


def _noop_progress(_: GenerationProgress) -> None:
    """Default callback when no UI is attached (tests, scripts)."""
    return None


def _safe_identifier(s: str, fallback: str = "X") -> str:
    """Sanitize a name for use as a SQL identifier. Strips non-alphanumerics."""
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", (s or "").strip())
    return cleaned or fallback


def artifact_prefix(artifact: CvArtifact) -> str:
    """The artifact's mapping-file stem, sanitized for use in an identifier.

    `cv_intermediate_sales.xlsx` → `cv_intermediate_sales`.
    """
    base = artifact.file_name or artifact.cv_display_name or ""
    base = re.sub(r"\.(xlsx|xlsm|xls|json)$", "", base.strip(), flags=re.IGNORECASE)
    return _safe_identifier(base, fallback="cv")


def chunk_cte_name(artifact: CvArtifact, chunk_name: str, is_root: bool) -> str:
    """CTE name for one chunk (HANA node) of an artifact.

    Root/parent CV  → the chunk name verbatim      (`projection_1`)
    Child CV        → `<mapping file>_<chunk>`     (`cv_intermediate_sales_projection_1`)

    The root is the one the user is actually building, so its nodes keep the
    names they had in HANA; children get namespaced because every CV in the
    chain tends to reuse the same node names (`projection_1`, `aggregation`)
    and they'd otherwise collide in one flat WITH list.
    """
    safe = _safe_identifier(chunk_name, fallback="chunk")
    return safe if is_root else f"{artifact_prefix(artifact)}_{safe}"


def last_chunk_name(artifact: CvArtifact) -> str:
    """Node name of the artifact's final chunk — the one whose SELECT becomes
    the artifact's own CTE (and, for the root, the query's output)."""
    best_name, best_num = None, None
    for row in (artifact.sql_info_raw or []):
        if not isinstance(row, dict):
            continue
        name = str(row.get("Node name") or row.get("node_name") or "").strip()
        if not name or name.lower() == "nan":
            continue
        sql = str(row.get("Chunk SQL Primary Optimized Base") or "")
        if "select" not in sql.lower():
            continue
        try:
            num = float(row.get("Chunk Number"))
        except (TypeError, ValueError):
            continue
        if best_num is None or num > best_num:
            best_name, best_num = name, num
    return best_name or "final"


def make_cte_name(artifact: CvArtifact) -> str:
    """Legacy uniquifier, kept only as a collision fallback.

    Superseded by `chunk_cte_name`; see that function for the naming contract.
    """
    short_id = (artifact.artifact_id or "")[:8]
    return f"{_CTE_NAME_PREFIX}_{short_id}_{_safe_identifier(artifact.cv_display_name)}"


def _strip_sql_wrapper(sql_text: str) -> str:
    """Strip the author-header block + CREATE OR REPLACE/ALTER VIEW line +
    the trailing validation block from `generate_sql_from_mapping`'s
    `cte_sql_content` output. Returns the inner WITH ... SELECT body.

    Datasphere dialect skips CREATE VIEW entirely; for that case we just
    strip the author block + validation block.

    Why regex + not from `mapping_sql_generator.py`: that function is the
    canonical single-artifact renderer. Its inner SELECT/CTE chain is the
    only thing we want; the wrapper is boilerplate the chained output
    doesn't need. This parser only targets the deterministic boilerplate
    shape we observed — header comment, optional CREATE VIEW line, real
    SQL, optional validation comment block.
    """
    if not sql_text:
        return ""

    start_marker = re.search(
        r"CREATE\s+(?:OR\s+REPLACE|OR\s+ALTER)\s+VIEW\b[^\n]*\n",
        sql_text,
        re.IGNORECASE,
    )
    end_marker = re.search(
        r"\n--\s*Validation:\s*\n",
        sql_text,
    )
    if start_marker and end_marker:
        body = sql_text[start_marker.end():end_marker.start()]
    elif start_marker:
        body = sql_text[start_marker.end():]
    else:
        # No CREATE VIEW wrapper (Datasphere path). Cut from end of the
        # decorative comment block to the validation marker (or end).
        cut = sql_text.find("*/", sql_text.find("/***"))
        body = sql_text[cut + 2:] if cut != -1 else sql_text
        end = body.find("\n--\nValidation:")
        if end != -1:
            body = body[:end]
    body = body.strip()
    # `generate_sql_from_mapping` appends a trailing `;` to the per-artifact
    # body. When we nest that body inside another `WITH cte AS (...)`,
    # the bare `;` lands right before the outer `),` comma — most parsers
    # accept it as an empty statement, but stripping the trailing
    # semicolon makes the chained output more portable.
    body = body.rstrip()
    while body.endswith(";"):
        body = body[:-1].rstrip()
    return body


def source_table_casing_map(artifact: CvArtifact) -> dict[str, str]:
    """UPPER(table) → the exact spelling used in the workbook's
    `SourceTable_mapping_fields` keys.

    Why this exists: `mapping_sql_generator.match_mapping` builds
    `Flattened_Source` straight from those keys and then does an
    **exact, case-sensitive** `pd.merge` of `SourceTable` against the
    `sourceTable` we hand it. Meanwhile `flask_app.nested_add_cv_from_xlsx`
    stores `source_ref_canonical = src_table.upper()`. Passing the canonical
    form therefore matches zero rows and every mapping is silently dropped —
    no error, just SQL with the user's mappings ignored and (for nested
    sessions) the upstream CTE never wired in.

    So: resolve the canonical ref back to the workbook's own spelling before
    calling the renderer.
    """
    out: dict[str, str] = {}
    for row in (artifact.sql_info_raw or []):
        if not isinstance(row, dict):
            continue
        raw = row.get("SourceTable_mapping_fields")
        if isinstance(raw, str):
            raw = raw.strip()
            if not raw or raw == "nan":
                continue
            try:
                raw = ast.literal_eval(raw)
            except (ValueError, SyntaxError):
                continue
        if isinstance(raw, dict):
            for key in raw:
                name = str(key).strip()
                if name:
                    out.setdefault(name.upper(), name)
    return out


def _resolved_target_table_for(
    session: NestedSession,
    consumer_artifact_id: str,
    source_ref_canonical: str,
    producer_cte: dict[str, str],
) -> str | None:
    """If the given (consumer_artifact, source_ref) has been resolved to an
    uploaded CV producer that has already been emitted as a CTE, return the
    CTE name. Otherwise None — meaning the natural user-edited targetTable
    should be used."""
    key = (source_ref_canonical or "").upper()
    for link in session.dependency_links:
        if link.consumer_artifact_id != consumer_artifact_id:
            continue
        if (link.source_ref_canonical or "").upper() != key:
            continue
        if link.resolution != "uploaded_cv":
            continue
        if not link.producer_artifact_id:
            continue
        cte = producer_cte.get(link.producer_artifact_id)
        if cte:
            return cte
    return None


def _mappings_for_artifact(
    session: NestedSession,
    artifact: CvArtifact,
    producer_cte: dict[str, str],
) -> list[dict]:
    """Build the per-artifact mapping list that
    `generate_sql_from_mapping(...)` expects, with `targetTable` overridden
    by the producer's CTE name for any source that resolves to an
    already-emitted uploaded CV.

    The mapping-row shape mirrors what the frontend 'Column Mapping' editor
    stores on each artifact (sourceTable / sourceField / targetTable /
    targetField).
    """
    artifact_id = artifact.artifact_id
    casing = source_table_casing_map(artifact)
    out: list[dict] = []
    for m in session.global_mappings:
        if m.artifact_id != artifact_id:
            continue
        canonical = m.source_ref_canonical or ""
        # Hand the renderer the workbook's own spelling — see
        # `source_table_casing_map` for why the canonical form silently
        # matches nothing.
        source_table = casing.get(canonical.upper(), canonical)
        target_table = m.target_table or ""
        # If this source's been resolved to a producer that's already
        # emitted as a CTE, redirect the targetTable at that CTE so the
        # per-artifact renderer's column-mapping step rewrites FROM-clauses
        # to point at the upstream CTE instead of the raw source table.
        cte = _resolved_target_table_for(
            session,
            artifact_id,
            canonical,
            producer_cte,
        )
        if cte:
            target_table = cte
        out.append({
            "sourceTable": source_table,
            "sourceField": m.source_column_raw or "",
            "targetTable": target_table,
            "targetField": m.target_column or "",
        })
    return out


def _artifact_has_sql_payload(artifact: CvArtifact) -> bool:
    return bool(artifact.sql_info_raw)


def _producer_ref_rewrites(
    session: NestedSession,
    consumer_artifact_id: str,
    producer_cte: dict[str, str],
) -> dict[str, str]:
    """source_ref (as written in the workbook) → producer CTE name, for every
    link on this artifact that resolves to an already-emitted uploaded CV."""
    out: dict[str, str] = {}
    for link in session.dependency_links:
        if link.consumer_artifact_id != consumer_artifact_id:
            continue
        if link.resolution != "uploaded_cv" or not link.producer_artifact_id:
            continue
        cte = producer_cte.get(link.producer_artifact_id)
        ref = (link.source_ref_canonical or "").strip()
        if cte and ref:
            out[ref] = cte
    return out


def _rewrite_producer_refs(
    sql_text: str,
    rewrites: dict[str, str],
    hana: bool = False,
) -> str:
    """Point every remaining reference to an upstream CV at its CTE.

    `generate_sql_from_mapping` only rewrites tables it could match through
    `Matched_Mapping`, which is built from `SourceTable_mapping_fields`. Any
    chunk whose `SourceTable_mapping_fields` cell is blank/`nan` (common for
    the final aggregate chunk) comes back still referencing the raw CV name.
    In a chained document that name is not defined anywhere, so the query
    would fail at runtime. Fix it up here — wiring artifacts together is the
    orchestrator's job, not the per-artifact renderer's.

    `\\b` before the name means an already-rewritten `cte_<id>_<name>` is not
    matched again (the preceding `_` is a word character).

    `hana=True` targets the Datasphere/HANA table-function dialect, where an
    upstream result is a table *variable* referenced as `:name` — so any
    `"CV_X"` / `:cv_x` / `cv_x` spelling collapses to `:cte_…`.
    """
    for ref, cte in rewrites.items():
        if hana:
            sql_text = re.sub(
                rf':?"?\b{re.escape(ref)}\b"?',
                f":{cte}",
                sql_text,
                flags=re.IGNORECASE,
            )
        else:
            sql_text = re.sub(
                rf"\b{re.escape(ref)}\b",
                cte,
                sql_text,
                flags=re.IGNORECASE,
            )
    return sql_text


# ──────────────────────────────────────────────────────────────────────────────
# WITH-block hoisting
#
# Each artifact body comes back from `generate_sql_from_mapping` as a complete
# `WITH <nodes...> SELECT ...` statement. Nesting that verbatim inside an outer
# `WITH cte AS ( ... )` yields a WITH-inside-a-CTE, which T-SQL/Fabric,
# Redshift and Datasphere all reject. So we lift each artifact's inner CTEs
# into the outer WITH list under an artifact-scoped prefix (which also stops
# the identical `projection_1` node names from colliding across artifacts).
#
# Done textually rather than through sqlglot on purpose: the renderer emits
# `-- Step N: ...` comments the user relies on, and a sqlglot parse/regenerate
# round-trip drops them.
# ──────────────────────────────────────────────────────────────────────────────

_CTE_HEAD_RE = re.compile(
    r"([A-Za-z_][A-Za-z0-9_]*)\s*(?:\([^)]*\))?\s+AS\s*"
    r"(?:(?:NOT\s+)?MATERIALIZED\s*)?\(",
    re.IGNORECASE,
)


def _skip_ws_comments(s: str, i: int) -> int:
    n = len(s)
    while i < n:
        if s[i] in " \t\r\n":
            i += 1
        elif s.startswith("--", i):
            j = s.find("\n", i)
            i = n if j == -1 else j + 1
        elif s.startswith("/*", i):
            j = s.find("*/", i + 2)
            i = n if j == -1 else j + 2
        else:
            break
    return i


def _scan_balanced(s: str, i: int) -> int:
    """`i` points at `(`. Return the index just past the matching `)`, or -1.
    Skips over string literals, quoted identifiers and comments."""
    depth = 0
    n = len(s)
    while i < n:
        c = s[i]
        if s.startswith("--", i):
            j = s.find("\n", i)
            i = n if j == -1 else j + 1
            continue
        if s.startswith("/*", i):
            j = s.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        if c in "'\"`":
            quote = c
            i += 1
            while i < n:
                if s[i] == "\\":
                    i += 2
                    continue
                if s[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return -1


def _split_with_block(sql: str):
    """Split `WITH a AS (...), b AS (...) SELECT ...` into
    `([(lead_comments, name, body), ...], tail_select)`.

    Returns None when the text doesn't start with a plain `WITH`, or when the
    shape isn't the deterministic one the renderer emits — callers fall back
    to nesting the body unchanged.
    """
    if not sql:
        return None
    i = _skip_ws_comments(sql, 0)
    if sql[i:i + 4].upper() != "WITH":
        return None
    after = i + 4
    if after < len(sql) and (sql[after].isalnum() or sql[after] == "_"):
        return None
    i = after
    # RECURSIVE changes the scoping rules — don't try to be clever.
    probe = _skip_ws_comments(sql, i)
    if sql[probe:probe + 9].upper() == "RECURSIVE":
        return None

    ctes: list[tuple[str, str, str]] = []
    while True:
        j = _skip_ws_comments(sql, i)
        lead = sql[i:j]
        m = _CTE_HEAD_RE.match(sql, j)
        if not m:
            return None
        name = m.group(1)
        open_paren = m.end() - 1
        close = _scan_balanced(sql, open_paren)
        if close == -1:
            return None
        ctes.append((lead, name, sql[open_paren + 1:close - 1]))
        k = _skip_ws_comments(sql, close)
        if k < len(sql) and sql[k] == ",":
            i = k + 1
            continue
        tail = sql[close:].lstrip()
        if not tail:
            return None
        return ctes, tail


def _hoist_inner_ctes(body: str, rename: Callable[[str], str]) -> tuple[list[str], str]:
    """Lift `body`'s own WITH block into standalone CTE texts.

    `rename` maps each inner CTE's node name to its name in the chained
    document (identity-ish for the root, file-prefixed for children).

    Returns `(hoisted_cte_texts, remaining_select)`. When `body` has no
    liftable WITH block, returns `([], body)` and the caller nests it as-is.
    """
    split = _split_with_block(body)
    if not split:
        return [], body
    ctes, tail = split

    renames = {name: rename(name) for _lead, name, _b in ctes}

    def _apply(text: str) -> str:
        for old, new in renames.items():
            if old == new:
                continue
            text = re.sub(rf"\b{re.escape(old)}\b", new, text)
        return text

    hoisted: list[str] = []
    for lead, name, cte_body in ctes:
        comment = lead.strip()
        block = f"{renames[name]} AS (\n{_apply(cte_body).strip()}\n)"
        hoisted.append(f"{comment}\n{block}" if comment else block)
    return hoisted, _apply(tail).strip()


def _uniquify(name: str, used: set, artifact: CvArtifact) -> str:
    """Guard against two artifacts resolving to the same identifier (e.g. the
    same workbook uploaded twice)."""
    if name not in used:
        used.add(name)
        return name
    alt = f"{name}_{(artifact.artifact_id or '')[:8]}"
    i = 2
    while alt in used:
        alt = f"{name}_{(artifact.artifact_id or '')[:8]}_{i}"
        i += 1
    used.add(alt)
    return alt


def _topo_order_or_session(session: NestedSession) -> list[str]:
    """Leaves-first topo order; falls back to insertion order when no links."""
    artifacts = list(session.artifacts.values())
    if not artifacts:
        return []
    graph = build_graph(artifacts, session.dependency_links)
    order = graph.topological_sort()
    # Cycle handling: build_graph returns partial order in that case. Append
    # any artifacts missing from the partial order so the chained output
    # still includes them — best-effort rather than silent drop.
    seen = set(order)
    for a in artifacts:
        if a.artifact_id not in seen:
            order.append(a.artifact_id)
            seen.add(a.artifact_id)
    return order


# ──────────────────────────────────────────────────────────────────────────────
# SQL chain
# ──────────────────────────────────────────────────────────────────────────────

def _normalize_hana_var_refs(sql_text: str, cte_names) -> str:
    """Force every reference to an upstream CTE into HANA table-variable form.

    The Datasphere renderer double-quotes anything it considers a base table,
    so a `targetTable` we redirected at an upstream CTE comes back as
    `FROM "cte_…"` — which HANA reads as a physical table, not the table
    variable the chain actually defines. Collapse `"x"` / `x` / `:x` to `:x`.
    """
    for name in cte_names:
        if not name:
            continue
        sql_text = re.sub(
            rf':?"?\b{re.escape(name)}\b"?',
            f":{name}",
            sql_text,
        )
    return sql_text


def _dequalify_cte_refs(sql_text: str, cte_names) -> str:
    """Strip schema/project qualification and quoting from CTE references.

    Once `_mappings_for_artifact` redirects a `targetTable` at an upstream
    CTE, the per-artifact renderer treats that name like any other base table
    and applies the dialect's qualification rules — emitting
    `` `project.dataset.cte_…` `` for BigQuery or `"schema"."cte_…"`
    elsewhere. A CTE has no schema, so the query fails with "table not
    found". Collapse every qualified/quoted spelling back to the bare name.
    """
    for name in cte_names:
        if not name:
            continue
        n = re.escape(name)
        # `project.dataset.cte` / `cte`
        sql_text = re.sub(rf"`[^`]*?\b{n}`", name, sql_text)
        # "schema"."cte" / "cte"
        sql_text = re.sub(rf'(?:"[^"]*"\s*\.\s*)*"{n}"', name, sql_text)
        # schema.cte / project.dataset.cte (unquoted)
        sql_text = re.sub(rf'(?:\b[A-Za-z_]\w*\s*\.\s*)+{n}\b', name, sql_text)
    return sql_text


async def _render_artifact_sql(
    session: NestedSession,
    artifact: CvArtifact,
    producer_cte: dict[str, str],
    progress: ProgressCallback = _noop_progress,
) -> str:
    """Render a single artifact through the canonical mapping pipeline
    and return the stripped inner SELECT/CTE body."""
    # Local import — the orchestrator imports the canonical renderer at call
    # time so module-load order doesn't matter and tests can stub it.
    from mapping_sql_generator import generate_sql_from_mapping

    mappings = _mappings_for_artifact(session, artifact, producer_cte)
    full_sql, _temp_sql = await generate_sql_from_mapping(
        artifact.sql_info_raw,
        mappings,
        session.target_dialect,
        output_format="sql",
    )
    body = _strip_sql_wrapper(full_sql)
    if not body:
        # Renderer returned nothing — surface a placeholder so the chain
        # doesn't silently drop this artifact's contribution.
        return f"-- (empty render for {artifact.cv_display_name})"
    # Catch any upstream-CV reference the mapping step couldn't reach.
    is_hana = _is_datasphere(session.target_dialect)
    body = _rewrite_producer_refs(
        body,
        _producer_ref_rewrites(session, artifact.artifact_id, producer_cte),
        hana=is_hana,
    )
    if is_hana:
        body = _normalize_hana_var_refs(body, producer_cte.values())
    else:
        body = _dequalify_cte_refs(body, producer_cte.values())
    return body


async def compose_chained_sql(
    session: NestedSession,
    progress: ProgressCallback = _noop_progress,
) -> str:
    """Render the whole session as one chained SQL document.

    Output shape — root nodes keep their HANA names, child nodes are prefixed
    with their mapping file:

        WITH
          cv_base_sales_projection_1 AS ( ... ),
          cv_base_sales_aggregation  AS ( ... ),
          cv_intermediate_sales_projection_1 AS (
            ... FROM cv_base_sales_aggregation ...
          ),
          cv_intermediate_sales_aggregation AS ( ... ),
          projection_1 AS ( ... ),          -- root CV
          aggregation  AS ( ... )           -- root CV
        SELECT * FROM aggregation;
    """
    order = _topo_order_or_session(session)
    if not order:
        progress(GenerationProgress(Phase.STARTING, "No CVs in session"))
        return "-- (no CVs in session)"

    renderable = [aid for aid in order
                  if (a := session.artifacts.get(aid)) and _artifact_has_sql_payload(a)]
    total = len(renderable)
    progress(GenerationProgress(Phase.STARTING, f"Starting composition for {total} CV(s)..."))

    producer_cte: dict[str, str] = {}
    parts: list[str] = []
    used: set = set()
    final_name: str | None = None

    for idx, aid in enumerate(order):
        artifact = session.artifacts.get(aid)
        if artifact is None or not _artifact_has_sql_payload(artifact):
            continue
        is_root = idx == len(order) - 1
        position = sum(1 for i, x in enumerate(order[: idx + 1])
                       if (a := session.artifacts.get(x)) and _artifact_has_sql_payload(a))

        progress(GenerationProgress(
            Phase.RENDERING,
            f"Rendering {artifact.cv_display_name} ({position}/{total})",
            current=position,
            total=total,
            artifact_name=artifact.cv_display_name,
        ))

        def _rename(node_name: str, _a=artifact, _r=is_root) -> str:
            return chunk_cte_name(_a, node_name, _r)

        own_name = _uniquify(
            chunk_cte_name(artifact, last_chunk_name(artifact), is_root),
            used,
            artifact,
        )
        body = await _render_artifact_sql(session, artifact, producer_cte)
        hoisted, remaining = _hoist_inner_ctes(body, _rename)
        parts.extend(hoisted)
        parts.append(f"{own_name} AS (\n{remaining}\n)")

        if is_root:
            final_name = own_name
        else:
            producer_cte[aid] = own_name

        progress(GenerationProgress(
            Phase.RENDERED,
            f"Rendered {artifact.cv_display_name} ({position}/{total})",
            current=position,
            total=total,
            artifact_name=artifact.cv_display_name,
        ))

    progress(GenerationProgress(Phase.COMPOSING, "Stitching CVs into one document"))

    if not parts:
        return "-- (no renderable artifacts)"
    if final_name is None:
        # No root artifact — emit the CTEs alone rather than dropping them.
        return "WITH\n" + ",\n".join(parts) + ";\n"
    return "WITH\n" + ",\n".join(parts) + f"\nSELECT * FROM {final_name};\n"


# ──────────────────────────────────────────────────────────────────────────────
# Datasphere / HANA chain
#
# `consolidated_sql_from_df_dsp` does not emit a WITH block at all — it emits a
# HANA table-function body:
#
#     -- Step 1: ...
#     projection_1 = SELECT ...;
#     -- Step 2: ...
#     return SELECT ...;
#
# Wrapping that in `WITH cte AS ( ... )` is not valid anywhere, so Datasphere
# gets its own chaining shape: every artifact's intermediate table variables
# are emitted under an artifact-scoped prefix, each non-root artifact's
# `return` becomes a table variable named after its CTE, and only the root
# keeps a real `RETURN`.
# ──────────────────────────────────────────────────────────────────────────────

_DATASPHERE_DIALECTS = {"datasphere", "sap datasphere", "hana"}

_HANA_ASSIGN_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)\s*=\s*", re.IGNORECASE)
_HANA_RETURN_RE = re.compile(r"^RETURN\b\s*", re.IGNORECASE)


def _is_datasphere(dialect: str | None) -> bool:
    return (dialect or "").strip().lower() in _DATASPHERE_DIALECTS


def _split_statements(sql: str) -> list[str]:
    """Split on depth-0 semicolons, ignoring those inside strings/comments."""
    out: list[str] = []
    start = i = 0
    depth = 0
    n = len(sql)
    while i < n:
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j == -1 else j + 1
            continue
        if sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        c = sql[i]
        if c in "'\"`":
            quote = c
            i += 1
            while i < n:
                if sql[i] == "\\":
                    i += 2
                    continue
                if sql[i] == quote:
                    i += 1
                    break
                i += 1
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
        elif c == ";" and depth == 0:
            out.append(sql[start:i])
            start = i + 1
        i += 1
    if sql[start:].strip():
        out.append(sql[start:])
    return out


def _split_hana_body(body: str):
    """Split a HANA table-function body into
    `([(lead, var_name, select), ...], (lead, return_select))`.

    Returns None when the shape isn't recognised — the caller then treats the
    whole body as one opaque assignment.
    """
    assigns: list[tuple[str, str, str]] = []
    ret: tuple[str, str] | None = None
    for stmt in _split_statements(body):
        if not stmt.strip():
            continue
        j = _skip_ws_comments(stmt, 0)
        lead, rest = stmt[:j], stmt[j:]
        m = _HANA_RETURN_RE.match(rest)
        if m:
            ret = (lead, rest[m.end():])
            continue
        m = _HANA_ASSIGN_RE.match(rest)
        if m:
            assigns.append((lead, m.group(1), rest[m.end():]))
            continue
        return None
    if ret is None:
        return None
    return assigns, ret


async def compose_chained_datasphere(
    session: NestedSession,
    progress: ProgressCallback = _noop_progress,
) -> str:
    """Chain all artifacts into one HANA/Datasphere table-function body."""
    order = _topo_order_or_session(session)
    if not order:
        progress(GenerationProgress(Phase.STARTING, "No CVs in session"))
        return "-- (no CVs in session)"

    renderable = [aid for aid in order
                  if (a := session.artifacts.get(aid)) and _artifact_has_sql_payload(a)]
    total = len(renderable)
    progress(GenerationProgress(Phase.STARTING, f"Starting composition for {total} CV(s)..."))

    producer_cte: dict[str, str] = {}
    parts: list[str] = []
    used: set = set()
    root_return: str | None = None

    for idx, aid in enumerate(order):
        artifact = session.artifacts.get(aid)
        if artifact is None or not _artifact_has_sql_payload(artifact):
            continue
        is_root = idx == len(order) - 1
        position = sum(1 for i, x in enumerate(order[: idx + 1])
                       if (a := session.artifacts.get(x)) and _artifact_has_sql_payload(a))
        own_name = _uniquify(
            chunk_cte_name(artifact, last_chunk_name(artifact), is_root),
            used,
            artifact,
        )
        progress(GenerationProgress(
            Phase.RENDERING,
            f"Rendering {artifact.cv_display_name} ({position}/{total})",
            current=position,
            total=total,
            artifact_name=artifact.cv_display_name,
        ))
        body = await _render_artifact_sql(session, artifact, producer_cte)
        progress(GenerationProgress(
            Phase.RENDERED,
            f"Rendered {artifact.cv_display_name} ({position}/{total})",
            current=position,
            total=total,
            artifact_name=artifact.cv_display_name,
        ))

        split = _split_hana_body(body)
        if not split:
            # Unrecognised shape — keep the artifact's contribution rather
            # than dropping it, as one opaque table variable.
            if is_root:
                root_return = f"RETURN\n{body.strip()};"
            else:
                parts.append(f"{own_name} =\n{body.strip()};")
                producer_cte[aid] = own_name
            continue

        assigns, (ret_lead, ret_select) = split
        renames = {
            name: chunk_cte_name(artifact, name, is_root)
            for _lead, name, _sel in assigns
        }

        def _apply(text: str, _r=renames) -> str:
            for old, new in _r.items():
                if old == new:
                    continue
                # `:var` references keep their colon — `\b` sits after it.
                text = re.sub(rf"\b{re.escape(old)}\b", new, text)
            return text

        for lead, name, select in assigns:
            comment = lead.strip()
            stmt = f"{renames[name]} =\n{_apply(select).strip()};"
            parts.append(f"{comment}\n{stmt}" if comment else stmt)

        comment = ret_lead.strip()
        tail = _apply(ret_select).strip()
        if is_root:
            stmt = f"RETURN\n{tail};"
            root_return = f"{comment}\n{stmt}" if comment else stmt
        else:
            stmt = f"{own_name} =\n{tail};"
            parts.append(f"{comment}\n{stmt}" if comment else stmt)
            producer_cte[aid] = own_name

    if root_return is None:
        if not parts:
            return "-- (no renderable artifacts)"
        progress(GenerationProgress(Phase.COMPOSING, "Stitching CVs into one document"))
        return "\n\n".join(parts) + "\n"
    progress(GenerationProgress(Phase.COMPOSING, "Stitching CVs into one document"))
    return "\n\n".join(parts + [root_return]) + "\n"


# ──────────────────────────────────────────────────────────────────────────────
# PySpark chain
# ──────────────────────────────────────────────────────────────────────────────

_NOTEBOOK_HEADER_HINTS = (
    "from pyspark",
    "import pyspark",
    "spark = ",
    "sparkSession",
    "spark.read",
)


def _looks_like_notebook_header(source: str) -> bool:
    s = source.lower()
    return any(h.lower() in s for h in _NOTEBOOK_HEADER_HINTS)


def _strip_pyspark_notebook(json_text: str) -> tuple[list[dict], str | None]:
    """Parse the JSON text returned by `generate_sql_from_mapping(..., output_format='pyspark')`.
    Returns (cells, header_first_cell_source). The header cell is the first
    code cell that contains PySpark import/SparkSession-style lines; we keep
    it exactly once and reuse it as the only SparkSession in the chained
    notebook.
    """
    if not json_text:
        return [], None
    try:
        nb = json.loads(json_text)
    except (json.JSONDecodeError, TypeError):
        return [], None
    cells = nb.get("cells", [])
    header_source: str | None = None
    for c in cells[:2]:
        src = "".join(c.get("source", [])) if isinstance(c.get("source"), list) else (c.get("source") or "")
        if _looks_like_notebook_header(src):
            header_source = src
            break
    return cells, header_source


def _cell_source(cell: dict) -> str:
    src = cell.get("source", "")
    if isinstance(src, list):
        return "".join(src)
    return src or ""


# Python-source rewriting helpers. Every substitution below must skip string
# literals — `KNA1 = spark.table("KNA1")` would otherwise have its *table name*
# renamed along with the variable, silently pointing the job at a table that
# doesn't exist.
_PY_STRING_RE = re.compile(
    r"('''(?:.|\n)*?'''|\"\"\"(?:.|\n)*?\"\"\"|'[^'\n]*'|\"[^\"\n]*\")"
)
_PY_ASSIGN_RE = re.compile(r"^([A-Za-z_]\w*)\s*=(?!=)", re.MULTILINE)
_PY_DISPLAY_RE = re.compile(
    r"^[ \t]*display\(\s*([A-Za-z_]\w*)\s*\).*$", re.MULTILINE
)


def _sub_outside_strings(pattern: str, repl: str, text: str) -> str:
    """`re.sub` that leaves string literals untouched."""
    parts = _PY_STRING_RE.split(text)
    for i in range(0, len(parts), 2):  # even indices are non-string spans
        parts[i] = re.sub(pattern, repl, parts[i])
    return "".join(parts)


def _pyspark_code_section(cells: list[dict]) -> str:
    """Join an artifact's *code* cells into one Python block.

    Markdown cells (the author header table, `## Step N` headings) must be
    dropped — the chained result is a `.py`-style script shown in CodeMirror,
    not a notebook, so pasting `| Field | Value |` in would be a syntax error.
    """
    parts: list[str] = []
    for c in cells:
        if c.get("cell_type") != "code":
            continue
        src = _cell_source(c)
        if not src.strip():
            continue
        if _looks_like_notebook_header(src):
            # Shared once at the top of the chained script.
            continue
        parts.append(src.rstrip())
    return "\n\n".join(parts)


def _bind_producer_dataframes(section: str, producers) -> str:
    """Make the consumer use the producer's DataFrame instead of re-reading it.

    The per-artifact renderer only knows it was told to read a table called
    `cte_…`, so it emits `spark.table("cte_…")`. In the chained script that
    table doesn't exist — the producer's DataFrame does, under exactly that
    name. Drop the redundant reload lines and swap the reads for the variable.
    """
    for name in producers:
        section = re.sub(
            rf'^[ \t]*{re.escape(name)}\s*=\s*spark\.table\([^\n]*\)[ \t]*$\n?',
            "",
            section,
            flags=re.MULTILINE,
        )
        section = re.sub(
            rf'spark\.table\(\s*["\']{re.escape(name)}["\']\s*\)',
            name,
            section,
        )
    return section


def _namespace_pyspark_vars(section: str, artifact, is_root: bool, protected: set) -> str:
    """Rename every variable this artifact assigns to its chained-document name.

    Each artifact is rendered independently, so they all name their nodes
    `projection_1` / `aggregation`. Concatenated unprefixed, the later
    artifact's assignments silently clobber the earlier one's and the chain
    computes the wrong result. Root variables keep their node names (nothing
    downstream can collide with them); children get the file prefix.
    """
    if is_root:
        return section
    names = [n for n in dict.fromkeys(_PY_ASSIGN_RE.findall(section))
             if n not in protected]
    for name in names:
        new = chunk_cte_name(artifact, name, is_root)
        if new == name:
            continue
        section = _sub_outside_strings(
            rf"\b{re.escape(name)}\b", new, section,
        )
    return section


async def _render_artifact_pyspark(
    session: NestedSession,
    artifact: CvArtifact,
    producer_cte: dict[str, str],
) -> tuple[list[dict], str | None]:
    """Render one artifact's PySpark notebook. Returns (cells, header_source)."""
    from mapping_sql_generator import generate_sql_from_mapping

    mappings = _mappings_for_artifact(session, artifact, producer_cte)
    nb_json, _ = await generate_sql_from_mapping(
        artifact.sql_info_raw,
        mappings,
        session.target_dialect,
        output_format="pyspark",
    )
    # Same gap as the SQL path: chunks with no `SourceTable_mapping_fields`
    # come back still naming the upstream CV, which has no DataFrame in the
    # chained script. Point them at the producer's section instead.
    rewrites = _producer_ref_rewrites(session, artifact.artifact_id, producer_cte)
    if rewrites:
        nb_json = _rewrite_producer_refs(nb_json, rewrites)
    return _strip_pyspark_notebook(nb_json)


async def compose_chained_pyspark(
    session: NestedSession,
    progress: ProgressCallback = _noop_progress,
) -> str:
    """Compose all artifacts into a single runnable PySpark script.

    Output shape (text — CodeMirror-friendly):

        from pyspark.sql import SparkSession          # shared, emitted once
        spark = SparkSession.builder...

        # ===== cv_base_sales =====
        cv_base_sales_projection_1 = ...
        cv_base_sales_aggregation  = ...

        # ===== cv_sales_fact (root) =====
        projection_1 = (
            cv_base_sales_aggregation.alias("cs")...   # producer DataFrame reused
        )
        aggregation = ...
        display(aggregation)
    """
    order = _topo_order_or_session(session)
    if not order:
        progress(GenerationProgress(Phase.STARTING, "No CVs in session"))
        return "# (no CVs in session)\n"

    renderable = [aid for aid in order
                  if (a := session.artifacts.get(aid)) and _artifact_has_sql_payload(a)]
    total = len(renderable)
    progress(GenerationProgress(Phase.STARTING, f"Starting composition for {total} CV(s)..."))

    producer_cte: dict[str, str] = {}
    sections: list[str] = []
    header_source_seen: str | None = None
    final_var: str | None = None

    for idx, aid in enumerate(order):
        artifact = session.artifacts.get(aid)
        if artifact is None or not _artifact_has_sql_payload(artifact):
            continue
        is_root = idx == len(order) - 1
        position = sum(1 for i, x in enumerate(order[: idx + 1])
                       if (a := session.artifacts.get(x)) and _artifact_has_sql_payload(a))

        progress(GenerationProgress(
            Phase.RENDERING,
            f"Rendering {artifact.cv_display_name} ({position}/{total})",
            current=position,
            total=total,
            artifact_name=artifact.cv_display_name,
        ))

        cells, header_source = await _render_artifact_pyspark(
            session, artifact, producer_cte,
        )
        progress(GenerationProgress(
            Phase.RENDERED,
            f"Rendered {artifact.cv_display_name} ({position}/{total})",
            current=position,
            total=total,
            artifact_name=artifact.cv_display_name,
        ))

        if header_source_seen is None and header_source:
            header_source_seen = header_source

        section = _pyspark_code_section(cells)
        if not section.strip():
            continue

        # The variable this artifact ultimately produces is whatever it
        # displays; strip the per-artifact display() calls either way so the
        # chained script ends with exactly one.
        displayed = _PY_DISPLAY_RE.findall(section)
        section = _PY_DISPLAY_RE.sub("", section).strip()

        section = _bind_producer_dataframes(section, producer_cte.values())
        section = _namespace_pyspark_vars(
            section, artifact, is_root, set(producer_cte.values()),
        )

        if displayed:
            output_var = chunk_cte_name(artifact, displayed[-1], is_root)
        else:
            assigned = _PY_ASSIGN_RE.findall(section)
            output_var = assigned[-1] if assigned else None

        label = artifact_prefix(artifact) + (" (root)" if is_root else "")
        block = f"# ===== {label} =====\n{section}"

        if is_root:
            final_var = output_var
        elif output_var:
            # Downstream artifacts reference this artifact's result by name.
            producer_cte[aid] = output_var

        sections.append(block)

    if not sections:
        return "# (no renderable artifacts)\n"

    progress(GenerationProgress(Phase.COMPOSING, "Stitching CVs into one script"))
    header = header_source_seen or (
        "from pyspark.sql import SparkSession\n"
        "from pyspark.sql import functions as F\n"
        "spark = SparkSession.builder.appName('nested_cv_chain').getOrCreate()"
    )
    out = header.rstrip() + "\n\n" + "\n\n".join(sections)
    if final_var:
        out += f"\n\ndisplay({final_var})"
    return out + "\n"


# ──────────────────────────────────────────────────────────────────────────────
# Entry helper
# ──────────────────────────────────────────────────────────────────────────────

async def compose_for_session(
    session: NestedSession,
    progress: ProgressCallback = _noop_progress,
) -> tuple[str, str]:
    """Dispatch to the right composer based on `session.output_format`.

    Returns (result_content, output_format_value). The first string is the
    in-memory output document; caller assigns it to `NestedTask.result_content`.

    `progress` is invoked from the active composer with structured events; see
    `Phase` / `GenerationProgress`.
    """
    fmt = session.output_format
    if fmt == OutputFormat.PYSPARK.value:
        return await compose_chained_pyspark(session, progress), OutputFormat.PYSPARK.value
    if _is_datasphere(session.target_dialect):
        return await compose_chained_datasphere(session, progress), OutputFormat.SQL.value
    # default: SQL
    return await compose_chained_sql(session, progress), OutputFormat.SQL.value


__all__ = [
    "make_cte_name",
    "source_table_casing_map",
    "compose_chained_sql",
    "compose_chained_datasphere",
    "compose_chained_pyspark",
    "compose_for_session",
]
