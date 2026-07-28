# Nested CV Flattener — Backend

> **Living document.** Keep this file in sync whenever code in this folder
> changes. If you add/remove a module, change a generation flow, fix a bug, or
> alter a contract, update the relevant section here. The next session will
> rely on this to understand the folder without re-deriving everything.

---

## Purpose

The third tool in the HANACV2SQL app (`?tab=nested`). Merges multiple nested
HANA Calculation View mapping workbooks into one flat SQL/PySpark output.
Targets: BigQuery, Snowflake, Databricks, Microsoft Fabric, Redshift, SAP
Datasphere. Two output formats: `sql` and `pyspark`.

The frontend lives at `frontend/components/nested-cv/` (see its
`claude.md` for component/state/API details). This folder is the backend
half: typed models, session store, parser, dependency graph, mapping
service, and the async generation task lifecycle.

---

## Folder Layout

```
backend/nested_cv/
├── __init__.py              # Re-exports public API (see "Public API" below)
├── models.py                # Typed dataclasses + enums + ID generators
├── session_store.py         # Thread-safe in-memory session/task store
├── artifact_parser.py       # Mapping workbook → CvArtifact (v1 + v2 formats)
├── dependency_graph.py      # DAG, topo sort, cycle detection, validation
├── mapping_service.py       # Global mapping aggregation, dedup, conflict/identifier checks
├── tasks.py                 # Async generation lifecycle (dispatcher)
├── orchestrator.py          # Multi-artifact chaining (SQL CTE / HANA / PySpark)
├── input_files_test/        # Sample XLSXs (cv_sales_fact, cv_intermediate_sales, cv_base_sales, …)
└── claude.md                # THIS FILE
```

There is **no `sql_composer.py` and no `pyspark_composer.py`** here on purpose —
see "Wrapper-only rule" below. `orchestrator.py` *does* exist, but it only
**assembles** what `mapping_sql_generator.generate_sql_from_mapping` renders;
it never generates per-artifact SQL itself.

---

## Public API (`__init__.py`)

```python
from nested_cv import (
    # models
    NestedSession, NestedTask, CvArtifact, DependencyLink,
    MappingEntry, Diagnostic, GraphSummary,
    SqlChunk, SourceReference, OutputColumn,
    NestedPhase, OutputFormat, EmissionMode, ObjectKind, ResolutionType,
    new_session_id, new_task_id, new_artifact_id,
    # store
    NestedSessionStore, get_session_store,
    # parser
    parse_mapping_content,
    # graph
    build_graph, auto_resolve_links, DependencyGraph,
    # mapping
    MappingService,
    # tasks
    start_generation_task,
)
```

`orchestrator` is not re-exported from `__init__` — import it directly:

```python
from nested_cv.orchestrator import (
    compose_for_session,        # dispatcher used by tasks.py
    compose_chained_sql,
    compose_chained_datasphere,
    compose_chained_pyspark,
    source_table_casing_map,   # also used by tasks.py single-artifact path
    make_cte_name,
)
```

The composability entry point used by both `tasks.py` and `orchestrator.py` is
`mapping_sql_generator.generate_sql_from_mapping(...)` — see "Generation
pipeline" below.

---

## Key Concepts

### Two distinct notions of "mapping"

1. **Per-CV mapping_info** — rows in the user's Excel workbook per CV
   (`source_ref_canonical / source_column_raw / target_table / target_column`).
   Stored on `CvArtifact.mapping_rows` with `artifact_id` stamped at parse time.
2. **Global consolidated mapping** — `NestedSession.global_mappings` aggregates
   rows across all CVs for join-key lookup. `MappingService` owns dedup and
   conflict detection.

The frontend's "Column Mapping" modal reads from (1) per CV. The
`build_graph()` / validation flow reads from (2) for cross-CV join keys.

### Edge direction

`DependencyGraph` edges run **producer → consumer** (the nested CV feeds the
parent CV). Leaves in the topological order are the *base* CVs (the deepest
files the user uploaded). Roots are the user's top-level CV.

### Emission modes

- `inline_cte` — render as a `WITH cte_<name> AS (...)` block. Root artifacts
  cannot be inline (warning `ROOT_INLINE` in `validate()`).
- `emit_view` — render as `CREATE OR REPLACE VIEW <target_view_name> AS`.
  Duplicate target view names across artifacts → `DUPLICATE_VIEW_NAME` error.

### Single-artifact vs multi-artifact

`tasks.start_generation_task()` is the only entry point into generation. Its
two branches behave very differently today — see "Generation pipeline".

---

## File Inventory

### `models.py` — domain types
- Dataclasses: `NestedSession`, `NestedTask`, `CvArtifact`, `DependencyLink`,
  `MappingEntry`, `Diagnostic`, `GraphSummary`, `SqlChunk`,
  `SourceReference`, `OutputColumn`.
- Enums: `NestedPhase` (intro → uploading → resolving → mapping → validating
  → generating → done/error), `OutputFormat` (sql/pyspark), `EmissionMode`
  (inline_cte/emit_view), `ObjectKind` (physical_table/calculation_view/unknown),
  `ResolutionType` (uploaded_cv/physical_table/external_cv).
- ID generators: `new_session_id()`, `new_task_id()`, `new_artifact_id()`
  (all `uuid.uuid4()`).
- `CvArtifact.sql_info_raw: list` — internal-only payload kept verbatim so
  `mapping_sql_generator.generate_sql_from_mapping(...)` can be called
  from `tasks._run_generation` without re-parsing. **Excluded from
  `to_dict()`** because the frontend's `CvArtifact` TS type has no such
  field — shipping it over the wire would leak internal data. Round-trip
  via `from_dict()` still preserves it for any persisted-session path.

### `session_store.py` — thread-safe store
- `NestedSessionStore` (singleton returned by `get_session_store()`).
- Session TTL 24h, task TTL 1h. Background `cleanup_expired()` removes
  entries past TTL (not auto-scheduled — call from a periodic tick or rely on
  on-access expiry in `get_session`).
- All access through `threading.RLock`. Cancel support via
  `request_cancel(task_id)` / `is_cancel_requested(task_id)` —
  `tasks._run_generation` calls this between progress updates.

### `artifact_parser.py` — Excel workbook → `CvArtifact`
- `parse_mapping_content(content, file_name, password=None) -> CvArtifact`.
- Handles the v2 structured JSON shape
  (`schema_version`, `artifact_manifest`, `dependencies`, `output_schema`,
  `mapping_info`, `sql_chunks`) and a v1/legacy fallback (regex extraction,
  `LEGACY_FORMAT` warning).
- Helpers: `_canonicalize_ref()` (strip quotes, uppercase),
  `infer_object_kind()` (sniffs for `_CV`/`_T`/`TABLE` patterns; public,
  imported and used by `flask_app.py` to avoid duplication),
  `_safe_json_parse()`, `_sanitize_view_name()`.
- Stamps `MappingEntry.artifact_id` at parse time so each row knows which
  artifact it belongs to. The frontend relies on this in
  "Column Mapping" editing.

### `dependency_graph.py` — DAG
- `DependencyGraph(artifacts, links)` builds `producer → consumer` adjacency
  and a reverse map.
- `topological_sort()` — Kahn's algorithm, leaves first.
- `detect_cycles()` — DFS with recursion-stack cycle extraction.
- `validate() -> (errors, warnings)` — produces `Diagnostic`s for
  `GRAPH_CYCLE`, `ROOT_INLINE`, `MISSING_PRODUCER`, `DUPLICATE_VIEW_NAME`.
- `build_summary() -> GraphSummary` — what the UI consumes.
- `auto_resolve_links(artifacts)` — proposes `DependencyLink`s by exact
  `cv_canonical_id` match across artifacts. The frontend then confirms/edits
  these via the linkage modals.

### `mapping_service.py` — global mapping service
- `MappingService(artifacts, global_mappings)` indexes by
  `(source_ref_canonical, source_column_raw)`.
- `detect_conflicts() -> [Diagnostic]` — flags same-source/different-target
  rows as `CONFLICTING_MAPPING`.
- `detect_invalid_identifiers() -> [Diagnostic]` — `EMPTY_TARGET_TABLE`,
  `EMPTY_TARGET_COLUMN`, `INVALID_TARGET_TABLE_NAME`.
- `validate()` = conflicts + invalid identifiers. Called from
  `/api/nested/sessions/<id>/validate`.
- Internal-only helper `MappingService._build_index()` runs in `__init__`.
  Earlier `add_artifact_mappings` and `get_consolidated_mappings` methods
  were dead code (no callers) and have been removed.

### `tasks.py` — generation lifecycle (the iron rule lives here)
- `start_generation_task(session) -> NestedTask` — spawns a daemon thread
  running `asyncio.run(_run_generation(task_id, session_id))`. Returns
  immediately with a `PENDING` `NestedTask`.
- `_run_generation` is the dispatcher. **Today it has two branches:**
  - **Single artifact with `sql_info_raw`** — calls
    `mapping_sql_generator.generate_sql_from_mapping(...)` directly. Stores
    `result[0]` in `task.result_content` (CTE SQL or notebook JSON).
  - **Multi-artifact** — delegates to `orchestrator.compose_for_session`,
    which renders each artifact through the same
    `generate_sql_from_mapping` call and chains the results. Do NOT re-add
    composer modules. See "Generation pipeline".

---

## Generation Pipeline (current + planned)

### Single-artifact (working today)

```
tasks._run_generation(task_id, session_id)
  → MappingEntry[] filtered to this artifact
  → from mapping_sql_generator import generate_sql_from_mapping
  → sql_content = generate_sql_from_mapping(
        artifact.sql_info_raw,
        mappings_list,
        session.target_dialect,
        output_format=session.output_format,
    )
  → task.result_content = result[0]
```

Returns either `(cte_sql, temp_table_sql)` for SQL output, or
`(notebook_json, "")` for PySpark output. The frontend renders `result[0]`
directly in CodeMirror. The download endpoint streams it back via
`io.BytesIO`.

### Multi-artifact (working today)

`orchestrator.compose_for_session(session)` returns `(content, output_format)`.
It picks one of three chaining shapes:

| `output_format` / dialect | Composer | Shape |
|---|---|---|
| `sql`, any dialect except Datasphere | `compose_chained_sql` | one flat `WITH … SELECT * FROM __final__;` |
| `sql`, Datasphere/HANA | `compose_chained_datasphere` | `var = SELECT …;` chain ending in one `RETURN` |
| `pyspark` | `compose_chained_pyspark` | one script, one SparkSession, one `display()` |

All three walk `DependencyGraph.topological_sort()` leaves-first, render each
artifact through `generate_sql_from_mapping(...)`, and wire artifact *N*'s
output into artifact *N+1*'s input. The last artifact in topological order is
the root.

#### Four non-obvious things this code has to get right

**1. `sourceTable` must use the workbook's own casing.**
`flask_app.nested_add_cv_from_xlsx` stores `source_ref_canonical =
src_table.upper()`, but `mapping_sql_generator.match_mapping` inner-joins
`Flattened_Source.SourceTable` (the raw `SourceTable_mapping_fields` key, e.g.
`cv_base_sales`) against `mappings_list[].sourceTable` **case-sensitively**.
Passing the canonical form matches zero rows and every mapping is silently
dropped — no error, just SQL with the user's mappings ignored and the upstream
CTE never wired in. `orchestrator.source_table_casing_map(artifact)` resolves
the canonical ref back to the workbook spelling; `tasks.py` uses it too, on the
single-artifact path. **Don't "simplify" it back to `m.source_ref_canonical`.**

**2. Chunks with no `SourceTable_mapping_fields` need a second pass.**
The mapping step can only rewrite tables it matched. The final aggregate chunk
of a CV usually has a blank/`nan` `SourceTable_mapping_fields` cell, so it comes
back still naming the raw upstream CV — which is not defined anywhere in the
chained document. `_rewrite_producer_refs` fixes those up after render.

**3. Artifact bodies are whole `WITH … SELECT` statements.**
Nesting one verbatim inside an outer `WITH cte AS ( … )` yields a WITH-inside-a-
CTE, which Fabric/T-SQL, Redshift and Datasphere reject, and makes every
artifact's identical `projection_1` shadow the last one's. `_hoist_inner_ctes`
lifts them into the outer `WITH` list under an artifact-scoped prefix
(`cte_<id>_<name>__projection_1`). It is a textual splitter, not sqlglot,
**on purpose** — a sqlglot parse/regenerate round-trip drops the `-- Step N:`
comments the renderer emits. It returns `([], body)` unchanged on any shape it
doesn't recognise (including `WITH RECURSIVE`), so the caller falls back to
nesting rather than producing garbage.

**4. PySpark artifacts each declare `projection_1` / `aggregation`.**
Concatenated unprefixed, the later artifact clobbers the earlier one and the
chain computes the wrong answer. `_namespace_pyspark_vars` prefixes every
assigned name — via `_sub_outside_strings`, because rewriting inside string
literals would turn `KNA1 = spark.table("KNA1")` into a read of a table that
doesn't exist. `_bind_producer_dataframes` then swaps the consumer's
`spark.table("cte_…")` for the producer's DataFrame variable, and markdown
cells are dropped (`cell_type == "code"` only) since the output is a script,
not a notebook.

### Wrapper-only rule (verbatim from user)

> *"you will reuse `C:\Users\logav\Downloads\h2s-local-deploy\backend\mapping_sql_generator.py`
> and `C:\Users\logav\Downloads\h2s-local-deploy\backend\pyspark.py` functions
> and create wrapper over these functions…never create logic from scratch.
> Because I can't maintain code in two places."*

There were earlier `sql_composer.py` and `pyspark_composer.py` modules here
that re-implemented dialect adapters, CTE assembly, and UNION splitting.
Both were deleted in the last cleanup. **Don't reintroduce them.** If you
find yourself reaching for a new helper that duplicates behaviour in those
files, surface it instead — there's probably already a function for it.

### Function inventory for the orchestrator to reuse

`mapping_sql_generator.py` (canonical per-artifact entry is **always**
`generate_sql_from_mapping`):

| Function | Purpose |
|---|---|
| `generate_sql_from_mapping(sql_info_list, mappings_list, database_name, target=None, output_format="sql")` (async) | Single source of truth for per-artifact SQL/PySpark rendering. Returns `(cte_sql, temp_table_sql)` or `(notebook_json, "")`. |
| `convert_list_to_df`, `flatten_source_fields`, `match_mapping` | Pre-processing helpers used by `generate_sql_from_mapping`. |
| `refactor_sql(sql_query, mappings, dialect)` | sqlglot-based identifier substitution (used by mapping engine). |
| `consolidated_sql_from_df`, `consolidated_sql_from_df_dsp` | Build the final chained SQL across rows of a DataFrame (the closest existing analogue to multi-CV chaining). |
| `update_target_sql_parallel`, `update_comments_parallel`, `update_pyspark_code_parallel` | Bounded-concurrency wrappers (`asyncio.Semaphore`) — copy this pattern for the orchestrator. |

`pyspark.py` (only ever reached transitively through
`generate_sql_from_mapping` unless we add a post-processing step):

| Function | Purpose |
|---|---|
| `convert_cte_to_pyspark(cte_sql, base_tables=None)` | CTE → PySpark DataFrame code. |
| `_extract_cte`, `_split_unions`, `_generate_union_code` | UNION-into-DataFrame helpers. |

---

## Session Lifecycle (full path)

```
POST   /api/nested/sessions                          → session_id
POST   /api/nested/sessions/<id>/cvs (multipart)     → artifact_id (one at a time; file_content in body)
PATCH  /api/nested/sessions/<id>/cvs/<artifactId>    → rename, change emission_mode / target_view_name
DELETE /api/nested/sessions/<id>/cvs/<artifactId>    → cascade (server-side also drops inbound links)
PUT    /api/nested/sessions/<id>/links               → save DependencyLink resolutions
PUT    /api/nested/sessions/<id>/mappings            → save MappingEntry list
POST   /api/nested/sessions/<id>/validate            → DependencyGraph.validate() + MappingService.validate()
POST   /api/nested/sessions/<id>/generate            → NestedTask (status=PENDING; start_generation_task)
GET    /api/nested/tasks/<taskId>                    → poll until COMPLETED
GET    /api/nested/tasks/<taskId>/download?filename= → io.BytesIO from task.result_content
DELETE /api/nested/tasks/<taskId>                    → request cancel (worker bails on next _check_cancel)
```

Single-artifact sessions complete in one pass through `generate_sql_from_mapping`.
Multi-artifact sessions fan out through `orchestrator.compose_for_session`,
one `generate_sql_from_mapping` call per artifact in topological order.

> **If you ever see "Multi-artifact generation is not implemented yet." again,
> the server is running stale code.** That string was removed when the
> orchestrator landed; it exists nowhere in the source. `flask_app.py` runs
> under waitress with no auto-reload, so a process started before a code change
> keeps serving the old module. Restart the backend (and rebuild the image, if
> containerised) before debugging anything else.

---

## In-Memory Only — Never Touch `OUTPUT_DIR`

Per the root `CLAUDE.md`:

> `OUTPUT_DIR` is **exclusively for HANA CV Converter** results.
> NestedCVTool results are held in-memory and shown in the browser editor —
> they never touch `OUTPUT_DIR`.

`NestedTask.result_content` holds the generated SQL/PySpark as a Python
string. The download endpoint streams it via `io.BytesIO` with a sanitized
filename. **Do not add `open(OUTPUT_DIR / …)` calls in this folder** — the
container has no permission to write under `OUTPUT_DIR` for nested sessions,
and even if it did, it would violate the storage contract.

---

## Key Conventions

### Naming
- File `claude.md` (lowercase) is the per-folder doc for Claude sessions.
- `nested_cv` (snake_case) is the Python package name.
- IDs: `artifact_id` / `session_id` / `task_id` are `uuid.uuid4()` strings.
  `cv_canonical_id` is upper-cased by the parser.
- `source_ref_canonical` is the upper-cased canonical form; `source_ref_raw`
  is the original string the user uploaded.

### Mapping-row ownership
Every `MappingEntry` carries `artifact_id`. The parser stamps it at
parse-time. The frontend's per-CV "Column Mapping" editor filters by
`m.artifact_id === thisArtifact.id` (see
`frontend/components/nested-cv/claude.md` for the full rationale). Never
let mapping rows travel between artifacts without that stamp.

### Mapping-info sheet column rename (upload side)
The XLSX `mapping info` sheet uses Excel-native column names
(`Original Table / Original Column / New Table / New Column`). The
*frontend-side* rename to `sourceTable / sourceField / targetTable /
targetField` happens in `flask_app._inspect_xlsx_workbook` BEFORE
serialization, so both the inspect endpoint and the upload endpoint see
the same shape on the JSON side. Once the JSON reaches
`artifact_parser.parse_mapping_content`, the fields are already
`source_ref_canonical` / `source_column` / `target_table` / `target_column`.

### Diagnostics surface in the UI
`Diagnostic(level, code, message, field)` is the contract for cross-CV
validation messages from `DependencyGraph.validate()` and
`MappingService.validate()`. Codes you can emit:

| Code | Module | Severity |
|---|---|---|
| `GRAPH_CYCLE` | dependency_graph | error |
| `ROOT_INLINE` | dependency_graph | warning |
| `MISSING_PRODUCER` | dependency_graph | error |
| `DUPLICATE_VIEW_NAME` | dependency_graph | error |
| `CONFLICTING_MAPPING` | mapping_service | error |
| `EMPTY_TARGET_TABLE` | mapping_service | error |
| `EMPTY_TARGET_COLUMN` | mapping_service | error |
| `INVALID_TARGET_TABLE_NAME` | mapping_service | warning |
| `LEGACY_FORMAT` | artifact_parser | warning |

If you add a new code, add a row here so frontend filters can keep up.

### Cancel protocol
`NestedSessionStore.request_cancel(task_id)` flips
`task._cancel_requested = True`. The worker checks via
`is_cancel_requested(task_id)` inside `_check_cancel()` between every
progress update and after each `await`. Always `await` something between
cancel checks — if you write a tight CPU loop, cancellation can't fire.

### Concurrency cap
Use `asyncio.Semaphore(N)` with `N ≈ 4–8` when the multi-artifact
orchestrator fans out parallel calls to
`generate_sql_from_mapping(...)`. `mapping_sql_generator` already uses
~50 concurrent slots for its own parallel paths; for the cross-CV
orchestrator the bottleneck is the Gemini API (if the user enables LLM
refinement) so we want a smaller cap.

---

## API Contracts (server-side)

| Method | Endpoint | Notes |
|---|---|---|
| `POST` | `/api/nested/sessions` | `{target_dialect, output_format}` → `NestedSession.to_dict()` |
| `GET` | `/api/nested/sessions/<id>` | Full session incl. revisions, artifacts, links, mappings |
| `DELETE` | `/api/nested/sessions/<id>` | Wipes in-memory entry |
| `POST` | `/api/nested/sessions/<id>/cvs` | Multipart upload. Parses with `parse_mapping_content`; cascades downstream links if needed |
| `PATCH` | `/api/nested/sessions/<id>/cvs/<artifactId>` | Body: `{cv_display_name?, emission_mode?, target_view_name?}` |
| `DELETE` | `/api/nested/sessions/<id>/cvs/<artifactId>` | Removes artifact + drops inbound/outbound `DependencyLink`s |
| `PUT` | `/api/nested/sessions/<id>/links` | Replaces the entire `dependency_links` list (not partial) |
| `PUT` | `/api/nested/sessions/<id>/mappings` | Replaces the entire `global_mappings` list (full-mirror save) |
| `POST` | `/api/nested/sessions/<id>/validate` | `build_graph + DependencyGraph.validate + MappingService.validate` → `{valid, errors, warnings}` |
| `POST` | `/api/nested/sessions/<id>/generate` | Calls `start_generation_task` → `{task_id}` |
| `GET` | `/api/nested/tasks/<taskId>` | Status, progress, message, optional `result_content`, `output_format` |
| `GET` | `/api/nested/tasks/<taskId>/download?filename=…` | Streams `task.result_content` via `io.BytesIO` with `secure_filename` |
| `DELETE` | `/api/nested/tasks/<taskId>` | Cancel alias |
| `POST` | `/api/nested/tasks/<taskId>/cancel` | Cancel alias |

Session TTL 24h, task TTL 1h. Beyond TTL → 404-ish (session: filtered as
expired; task: `cleanup_expired`).

---

## Maintenance Checklist

When you change code in this folder, update this file:

- [ ] New/removed module? → update File Inventory
- [ ] New public re-export? → update Public API
- [ ] New `Diagnostic` code? → add a row under "Diagnostics surface in the UI"
- [ ] New API route or contract change? → update API Contracts
- [ ] Generation-pipeline change? → update Generation Pipeline (current + planned)
- [ ] Found a non-obvious gotcha? → add a short note explaining WHY so the
      next session doesn't accidentally re-break it
- [ ] Anything that duplicates logic in `mapping_sql_generator.py` /
      `pyspark.py`? → don't merge it; flag it
