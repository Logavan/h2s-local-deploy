# Nested CV Merger — Revised Implementation Plan

## 1. Objective

> **Scope Clarification:** This is a **completely standalone Nested CV Flattener tool**. It is NOT integrated into the existing Mapping Tool UI/frontend. The existing Conversion Tool and Mapping Tool remain completely untouched.

**Entry Point:**
1. User is working in the **Mapping Tool** (after conversion, single or bulk).
2. User clicks on a **source table/view** within the Mapping Tool.
3. A prompt appears: "Paste your nested CV mapping file."
4. User pastes the converted mapping workbook content.
5. A **separate standalone visual mapping window** opens for that nested CV.
6. System auto-maps columns by default; user can verify and override any mapping.
7. Bottom-up resolution: child CVs resolved first, then parents.
8. Output: consolidated flattened `.sql` or `.pyspark`.

**Key Principles:**
- **NEVER touch** the existing Conversion Tool UI/frontend.
- **NEVER touch** the existing Mapping Tool UI/frontend.
- **Standalone UI**: Nested CV Flattener is its own complex, isolated interface.
- **Inline / Merge enabled**: the CV is not emitted as a standalone view; it is inlined into each consumer as a CTE.
- **Inline / Merge disabled**: the CV is emitted as its own `CREATE VIEW` statement, and consumers reference that emitted view.
- **Bottom-up composition**: Leaves (source CVs) are resolved first, then parents that depend on them.
- **Auto-mapping by default**: System maps columns automatically based on lineage; user verifies in visual window.

The implementation must not rely on upload order or filename equality to determine dependencies.

---

## 2. Scope and Output Contract

### In scope — Mapping Tool Consolidation Phase

1. Upload multiple encrypted mapping XLSX artifacts **from the Mapping Tool** (already converted, already mapped).
2. Parse and validate each artifact to extract dependency metadata.
3. Identify or explicitly resolve physical-table, external-CV, and uploaded-CV references from the workbook metadata.
4. Build and validate a directed dependency graph (bottom-up resolution).
5. Present one deduplicated mapping editor for physical sources and unresolved external CVs.
6. Generate consolidated target-dialect query bodies for each CV.
7. Inline selected CVs as statement-local CTEs (bottom-up composition).
8. Emit non-inlined CVs as views in dependency order.
9. Download one merged `.sql` file OR one consolidated `.pyspark` file (two output modes).
10. **Reuses existing mapping Python scripts** — does not re-invoke conversion logic.

### Initially out of scope

- Executing generated SQL against the target database.
- Automatically resolving ambiguous legacy dependencies without user confirmation.
- Recursive/cyclic Calculation Views.
- Renaming an inlined CV's output contract independently of all its consumers.
- Sharing one CTE between separate `CREATE VIEW` statements; SQL CTE scope does not allow this.
- Re-parsing original XML — all lineage comes from the mapping workbook metadata.

### Output semantics

The result is one `.sql` or `.pyspark` file that may contain multiple `CREATE VIEW` / `CREATE TABLE` statements. Every statement has its own local `WITH` clause (SQL) or `with` block (PySpark).

- Statements are emitted in topological order (bottom-up: children first, then parents).
- An inlined child used by multiple emitted roots is repeated in each applicable statement.
- A CV configured as `emit_view` is emitted once and referenced by its target view name.
- A CV configured as `inline_cte` is not emitted separately.
- A graph root with no consumer cannot be `inline_cte`; validation must require or automatically set it to `emit_view`.
- **PySpark output**: Uses `DataFrame` API with explicit column selection, not raw SQL.

---

## 3. Blocking Artifact-Format Requirement

> **Context:** These workbooks are outputs from the Mapping Tool (already converted), not raw XML inputs. The merger reads metadata from these workbooks to build the dependency graph without re-parsing original source files.

The current mapping workbook has only `sql info` and `mapping info`. Those sheets do not reliably identify:

- the canonical CV object;
- whether a source is a physical table or Calculation View;
- dependency edges between uploaded CVs;
- the CV's output-column contract;
- the workbook schema version.

A flat list of table strings and a filename are therefore insufficient for safe automatic merging.

### 3.1 Version 2 workbook format (for Mapping Tool export)

Update Mapping Tool export so new workbooks retain the existing sheets and add the following metadata sheets. These sheets are what the merger reads to resolve lineage — no re-parsing of original XML required.

#### `artifact manifest`

| Field | Description |
|---|---|
| `schema_version` | Integer; start with `2` |
| `artifact_id` | UUID generated during conversion |
| `cv_canonical_id` | Stable HANA identifier, including package/schema when available |
| `cv_display_name` | User-facing name |
| `default_output_view_name` | Suggested target view name |
| `source_format` | For example `hana_calculation_view` |
| `created_at` | ISO-8601 timestamp |

#### `dependencies`

One row per external object reference:

| Column | Description |
|---|---|
| `source_ref_raw` | Exact identifier found in XML/SQL |
| `source_ref_canonical` | Quote-aware normalized identifier |
| `object_kind` | `physical_table`, `calculation_view`, or `unknown` |
| `referenced_by_node` | Internal node/chunk that uses the source |
| `required_columns_json` | JSON array of referenced columns |

#### `output schema`

One row per final output column:

| Column | Description |
|---|---|
| `ordinal` | Stable output order |
| `column_name` | Exposed output alias |
| `data_type` | Optional when unavailable |
| `nullable` | Optional when unavailable |

Metadata extraction must be deterministic from the source XML/node model. LLM output must not decide object identity or dependency kind. Preserve both raw and canonical identifiers so dialect-specific case and quoting rules are not lost.

### 3.2 Legacy workbook compatibility

Version 1 workbooks remain uploadable with a **legacy** warning:

1. Infer a provisional CV display name from the filename, but never treat it as a confirmed canonical identity.
2. Extract candidate source references from `mapping info` and SQL chunks.
3. Mark source kinds as `unknown` unless they can be proven deterministically.
4. Require the user to resolve every unknown source as one of:
   - physical table;
   - external/unuploaded CV;
   - a specific uploaded CV.
5. Derive final `SELECT` aliases as a provisional output schema; require confirmation if parsing fails or aliases are ambiguous.
6. Do not allow generation while a dependency is ambiguous or unresolved.

---

## 4. Domain Model

Use typed models/dataclasses rather than unstructured dictionaries.

```python
NestedSession = {
    "session_id": str,
    "owner_id": str,
    "target_dialect": str,
    "phase": "uploading" | "resolving" | "mapping" | "validated" | "generating" | "completed" | "failed",
    "revision": int,
    "artifacts": dict[str, CvArtifact],
    "dependency_links": list[DependencyLink],
    "global_mappings": list[MappingEntry],
    "graph_summary": GraphSummary | None,
    "created_at": datetime,
    "updated_at": datetime,
    "expires_at": datetime,
}
```

```python
CvArtifact = {
    "artifact_id": str,
    "cv_canonical_id": str | None,
    "cv_display_name": str,
    "file_name": str,
    "format_version": int,
    "emission_mode": "inline_cte" | "emit_view",
    "target_view_name": str,
    "sql_chunks": list[SqlChunk],
    "dependencies": list[SourceReference],
    "output_schema": list[OutputColumn],
    "mapping_rows": list[MappingEntry],
    "warnings": list[Diagnostic],
}
```

```python
DependencyLink = {
    "consumer_artifact_id": str,
    "source_ref_canonical": str,
    "resolution": "uploaded_cv" | "physical_table" | "external_cv",
    "producer_artifact_id": str | None,
}
```

Important invariants:

- Graph edges are `producer -> consumer`.
- Artifact IDs are opaque UUIDs and are distinct from display names.
- Canonical IDs must be unique within a session.
- A source may link to an uploaded producer only when the producer output contract satisfies the consumer's required columns.
- Every mutation increments `revision` to prevent stale UI updates.

---

## 5. Mapping Model

Do not aggregate mappings solely by display-string table names.

### 5.1 Mapping categories

1. **Physical-source mappings**: target table and target column mappings for physical tables.
2. **External-CV mappings**: target view and column mappings for CVs that were not uploaded or are intentionally treated as external.
3. **Emitted-view names**: target view names for uploaded CVs configured as `emit_view`.

An uploaded CV linked as `inline_cte` is collapsed in the final mapping UI and is not shown as a remappable physical source. Its underlying physical sources are still shown.

### 5.2 Stable mapping key

Use a key equivalent to:

```text
(source_kind, source_ref_canonical, source_column_raw)
```

Keep lineage back to every artifact and node that contributed the row.

### 5.3 Deduplication and conflict handling

- Identical keys are shown once.
- If workbooks propose different target tables or columns for the same key, show one explicit conflict that the user must resolve.
- All rows for the same source object must use one target object name.
- Duplicate target view names are validation errors.
- Empty target identifiers, invalid identifiers, and reserved-word conflicts are rejected or quoted through the selected dialect adapter.
- Parent references to an uploaded child must be checked against the child's output schema.

For the first release, preserve non-root CV output aliases. If output-column renaming is later added, it must update every consumer reference through the SQL AST and revalidate the output contract.

---

## 6. Dependency Resolution and Validation

### 6.1 Graph construction

1. Parse all artifacts independently.
2. Match only exact canonical IDs automatically.
3. Present unresolved/ambiguous references in a dependency-resolution UI.
4. Build edges only after links are confirmed.
5. Run a deterministic topological sort; never use upload order.

### 6.2 Validation gates

Generation is blocked for:

- cycles, including self-dependencies;
- missing or unresolved producers;
- ambiguous canonical IDs;
- duplicate artifact IDs or target view names;
- a root configured as `inline_cte`;
- missing required child output columns;
- invalid or conflicting mappings;
- unsupported target dialect;
- unparseable query chunks that are required for safe identifier rewriting.

Warnings, which require acknowledgement but may not block generation, include:

- legacy workbook inference;
- unknown data types;
- duplicate inline expansion across multiple roots;
- target-dialect constructs that could not be semantically verified.

The validation response must include the topological order, roots, unresolved references, errors, and warnings.

---

## 7. SQL Generation Design

Do not implement merging as string concatenation or global regular-expression replacement. Use a structured intermediate representation and SQL AST transformations wherever supported.

### 7.1 Refactor the existing mapper

The current mapping generator wraps one workbook and uses a placeholder view name. Extract a reusable function that returns a **pure target-dialect query body** for one artifact:

```python
async def generate_cv_query_body(
    artifact: CvArtifact,
    mappings: list[MappingEntry],
    target_dialect: str,
) -> GeneratedQueryBody:
    ...
```

Before dialect conversion or any LLM call, parse resolved uploaded-CV references and replace them with reserved dependency tokens such as `__NCV_DEP_<link_id>__`. The token map is part of the intermediate representation. The conversion step must preserve every token exactly; missing, duplicated, or altered tokens are blocking errors. This prevents an LLM or dialect formatter from changing a dependency name before composition.

`GeneratedQueryBody` must contain:

- internal CTE definitions;
- final `SELECT` AST/query;
- the dependency-token map;
- output columns;
- diagnostics;
- no header comment;
- no `CREATE VIEW` wrapper;
- no trailing statement delimiter.

Remove the hard-coded `your_view_name` behavior from the nested path. Existing single-workbook behavior may keep its wrapper through a separate dialect adapter.

### 7.2 Namespacing

Internal node/CTE names from separate workbooks may collide. Assign deterministic aliases such as:

```text
cv_<artifact_short_id>__<sanitized_node_name>
```

Rewrite all references through the AST. Also assign a deterministic CTE alias for each inlined child reference. Do not use user-controlled text directly without identifier validation.

### 7.3 Composition algorithm

For each `emit_view` artifact in topological order:

1. Compute the dependency closure needed by that artifact.
2. For each dependency in producer-first order:
   - if `inline_cte`, namespace its internal CTEs and append them to the current statement;
   - create a CTE representing its final query;
   - replace the consumer's reserved dependency token with that CTE alias;
   - if `emit_view`, replace the token with its validated target view name instead.
3. Namespace and append the current artifact's internal CTEs.
4. Append the current artifact's final `SELECT`.
5. Validate that all table/column references and CTE names resolve.
6. Wrap the query using target-specific `CREATE VIEW` syntax and identifier quoting.
7. Terminate the statement according to the target dialect.

Each emitted statement gets an independent `WITH` clause. Shared inline closures are duplicated because CTEs are statement-scoped.

### 7.4 Dialect adapter

Centralize:

- identifier quoting;
- qualified-name rendering;
- `CREATE OR REPLACE VIEW` / `CREATE OR ALTER VIEW` syntax;
- reserved words;
- statement terminators;
- AST read/write dialect names;
- PySpark DataFrame output generation.

Supported SQL dialects: BigQuery, Fabric/Azure Synapse, Snowflake, Redshift, Databricks, SAP Datasphere.
Supported PySpark dialects: Databricks PySpark, EMR PySpark, local Spark.

Unsupported combinations fail during validation rather than silently falling back.

### 7.5 PySpark output mode

Instead of SQL `CREATE VIEW` statements, PySpark output uses the DataFrame API:

```python
# Generated structure (conceptual)
from pyspark.sql import SparkSession
from pyspark.sql import functions as F

spark = SparkSession.builder.getOrCreate()

# Each emitted CV becomes a named DataFrame
df_child_cv = spark.table("source_schema.source_table") \
    .select("col_a", "col_b")

df_parent_cv = df_child_cv.select("col_a", F.col("col_b").alias("renamed_col"))
```

- Bottom-up composition: child DataFrames defined first, parents reference them
- Column-level lineage: explicit `.select()` for every output column
- No raw SQL strings — all DataFrame operations
- Target schema names from consolidated mapping

### 7.5 Final verification

Before marking a task complete:

1. Parse every generated statement with the selected SQL parser/dialect where supported.
2. Verify unique emitted view names.
3. Verify CTE references and dependency substitutions.
4. Verify final output columns against each artifact's contract.
5. Return structured diagnostics with artifact and source-reference context.

LLM-generated SQL must still pass these deterministic checks. A failed check produces a failed task, not a downloadable best-effort file without warning.

---

## 8. Backend API

Use resource-oriented endpoints. Upload analysis and session insertion happen once in the same request; do not parse the same workbook through separate `analyze` and `add` calls.

| Method | Endpoint | Purpose |
|---|---|---|
| `POST` | `/api/nested/sessions` | Create an empty session with `targetDialect` |
| `GET` | `/api/nested/sessions/<session_id>` | Return current session summary and revision |
| `POST` | `/api/nested/sessions/<session_id>/cvs` | Decrypt, validate, analyze, and add one XLSX |
| `PATCH` | `/api/nested/sessions/<session_id>/cvs/<artifact_id>` | Update emission mode, display name, or target view name |
| `DELETE` | `/api/nested/sessions/<session_id>/cvs/<artifact_id>` | Remove an artifact and affected links |
| `PUT` | `/api/nested/sessions/<session_id>/links` | Save dependency resolutions |
| `PUT` | `/api/nested/sessions/<session_id>/mappings` | Save unified mappings |
| `POST` | `/api/nested/sessions/<session_id>/validate` | Validate/finalize graph and mappings |
| `POST` | `/api/nested/sessions/<session_id>/generate` | Enqueue generation and return `202 + taskId` |
| `GET` | `/api/nested/tasks/<task_id>` | Return task status, progress, and diagnostics |
| `GET` | `/api/nested/tasks/<task_id>/download` | Download the completed `.sql` file |
| `DELETE` | `/api/nested/sessions/<session_id>` | Explicit cleanup/cancel |

### API rules

- Use consistent camelCase JSON at the API boundary and snake_case internally.
- Return stable error codes such as `INVALID_WORKBOOK`, `REVISION_CONFLICT`, `UNRESOLVED_DEPENDENCY`, and `GRAPH_CYCLE`.
- Return `409` for revision conflicts and graph/mapping state conflicts.
- Return `413` for configured upload limits.
- `generate` is always asynchronous; do not wait 30 seconds and then switch protocols.
- Generation is idempotent for the same session revision. Repeated requests return the existing task.
- The task status endpoint uses `PENDING`, `IN_PROGRESS`, `COMPLETED`, `FAILED`, or `CANCELLED`.
- The download endpoint verifies task ownership and completion.

---

## 9. Session Storage, Concurrency, and Security

Do not add another unguarded module-level dictionary directly to `flask_app.py`.

Create a `NestedSessionStore` abstraction:

- **Local/single-process adapter**: in-memory store protected by a lock, with configurable TTL and lazy/background cleanup.
- **Production adapter**: Redis/database/object-storage-backed session and task metadata so multiple workers and restarts are safe.

Required controls:

- Use opaque UUIDs for sessions, artifacts, and tasks.
- Derive `owner_id` from verified authentication claims; never trust a request-provided email as authorization.
- For anonymous local mode, use an unguessable capability token tied to the session.
- Verify ownership on every session, task, and download operation.
- Enforce configurable maximum file size, artifact count, row count, and total session size.
- Accept only `.xlsx`; validate ZIP/XLSX structure, required sheets, version, and required columns.
- Use the existing workbook decryption path and return a safe wrong-password/invalid-file error.
- Sanitize filenames and never use them as object identifiers.
- Do not log workbook contents, mappings, capability tokens, or generated SQL at info level.
- Ensure cleanup on expiry, explicit delete, cancellation, and failed generation.

---

## 10. Frontend Design

> **Key UX Decision:** The Nested CV Merger is a **completely standalone tool/section** — separate from both the Conversion Tool and Mapping Tool. It has its own complex UI that is never integrated into the existing tool frontends. This isolation ensures the existing tools remain untouched while the nested CV flattener evolves independently.

### 10.1 Entry Point

From the Mapping Tool, user clicks on a source table/view. A prompt appears:

```
┌────────────────────────────────────────────────────────────┐
│  Paste Nested CV Mapping                                    │
│  ─────────────────────────────────────────────────────────  │
│  [Paste your converted mapping file content here...]        │
│                                                             │
│                                    [Cancel]  [Open Mapper]   │
└────────────────────────────────────────────────────────────┘
```

Clicking **Open Mapper** opens the **Nested CV Flattener** as a standalone interface.

### 10.2 Top-Level Navigation

```
┌─────────────────────────────────────────────────────┐
│  [Converter]  |  [Mapper]  |  [Nested CV Flattener] │
└─────────────────────────────────────────────────────┘
```

The third tab is the **Nested CV Flattener** — fully standalone with its own UX, components, and state management.

### 10.3 Phases

```typescript
type NestedPhase =
  | "intro"              // Select output format (SQL / PySpark), start session
  | "uploading"         // Paste mapping workbook content per CV
  | "resolving"         // Visual mapping window — verify/override auto-mapped columns
  | "mapping"           // Edit consolidated mappings (physical sources, external CVs)
  | "validating"        // Validate graph, mappings, and output contracts
  | "generating"        // Async generation, poll task status
  | "done"              // Ready for download
  | "error"             // Blocking errors with resolution guidance
```

### 10.4 Components (Standalone)

```text
nested-cv/
├── NestedCVTool.tsx         # Main standalone container
├── NestedIntro.tsx          # Welcome, output format selection (SQL / PySpark)
├── CvUploadList.tsx        # List of pasted mapping files per CV
│   └── CvUploadCard.tsx     # Per-CV: name, version, dependency count, warnings
├── VisualMappingWindow.tsx  # Modal/panel for auto-mapping verification
│   ├── AutoMappingGrid.tsx   # Shows auto-mapped columns with override option
│   └── ColumnMapper.tsx      # Interactive column-level mapping override
├── DependencyGraph.tsx      # Visual dependency graph (bottom-up display)
├── DependencyResolver.tsx   # Inline vs Emit-as-view controls per CV
├── AggregatedSourceList.tsx # Unified physical-source + external-CV mapping
├── FinalMappingEditor.tsx   # Consolidated target name editor
├── ValidationSummary.tsx    # Blocking errors + acknowledged warnings
└── GenerationProgress.tsx    # Polling UI with cancel option
```

All components are isolated from the Conversion Tool and Mapping Tool. No shared state, no shared UI components.

### 10.5 UI flow (Standalone)

1. User navigates to the **Nested CV Flattener** tab.
2. Select output format: **SQL** or **PySpark**.
3. Paste mapping workbook content for the first CV (leaf node).
4. **Visual Mapping Window** opens automatically:
   - Shows auto-mapped columns (system-determined lineage)
   - User can verify and override any column mapping
5. Repeat for each CV in dependency order (bottom-up).
6. System resolves dependency graph automatically (exact canonical ID match).
7. Ambiguous or legacy references shown for manual resolution.
8. Graph summary shows roots, topological order, and any cycles.
9. Continue to consolidated mapping editor:
   - Physical sources deduplicated across all workbooks
   - External CVs mappable to target names
   - Conflicting mappings shown as explicit conflicts to resolve
10. Validate: display blocking errors and acknowledged warnings.
11. Start async generation (SQL or PySpark).
12. Poll task status; on completion, download `merged_cv_<timestamp>.sql` or `.pyspark`.
13. User can **Discard** (explicitly delete session) or **Start Over**.

On reset/unmount, cancel polling. Explicitly delete the session when the user chooses **Discard**.

### 10.6 Constraints

- **NEVER modify** `ConversionTool.tsx` or any conversion-related frontend files.
- **NEVER modify** `MappingTool.tsx` or any mapping-related frontend files.
- The Nested CV Flattener tab exists independently with its own route/state.
- Visual Mapping Window is a **required step** — user must verify auto-mapped columns before proceeding.

---

## 11. Files to Add or Modify

### Critical Constraint
> **NEVER touch** `frontend/components/ConversionTool.tsx`, `frontend/components/MappingTool.tsx`, or any related conversion/mapping frontend files. The Nested CV Flattener is completely standalone.

### Backend

| File | Change |
|---|---|
| `backend/flask_app.py` | Add nested session/task routes (thin registration) |
| `backend/mapping_sql_generator.py` | Extract reusable pure-query generation path |
| `backend/nested_cv/models.py` | Typed session, artifact, mapping, graph, and diagnostic models |
| `backend/nested_cv/artifact_parser.py` | Decrypt and parse v1/v2 mapping workbooks with strict validation |
| `backend/nested_cv/session_store.py` | Store abstraction, locking, revision, and TTL |
| `backend/nested_cv/dependency_graph.py` | Linking, topological sort (bottom-up), roots, cycles, contract validation |
| `backend/nested_cv/mapping_service.py` | Deduplication, lineage, conflict detection, and validation |
| `backend/nested_cv/sql_composer.py` | Namespacing, AST rewriting, CTE composition, SQL dialect adapters |
| `backend/nested_cv/pyspark_composer.py` | PySpark DataFrame composition (separate from SQL path) |
| `backend/nested_cv/tasks.py` | Async generation task lifecycle and result storage |

### Frontend (Standalone — isolated from existing tools)

| File | Change |
|---|---|
| `frontend/app/nested-cv/page.tsx` | Standalone page for Nested CV Flattener (own route) |
| `frontend/components/nested-cv/*` | All new standalone components (see Section 10.4) |
| `frontend/lib/api.ts` | Add typed functions for nested-session and task endpoints |
| `frontend/lib/nested-cv-types.ts` | Shared API/view-model types |
| `frontend/components/HomeClient.tsx` | Add third tab: `converter | mapper | nested` |

> **Tab structure:** Top-level tab becomes `converter | mapper | nested`. The nested tab is completely standalone with its own page route and state. Existing converter and mapper tabs remain untouched.

### Tests

| Path | Coverage |
|---|---|
| `backend/tests/nested_cv/test_artifact_parser.py` | v1/v2 parsing and workbook validation |
| `backend/tests/nested_cv/test_dependency_graph.py` | graph/linking/topological (bottom-up) behavior |
| `backend/tests/nested_cv/test_mapping_service.py` | deduplication and conflicts |
| `backend/tests/nested_cv/test_sql_composer.py` | CTE/view composition and SQL dialect rendering |
| `backend/tests/nested_cv/test_pyspark_composer.py` | PySpark DataFrame composition |
| `backend/tests/nested_cv/test_api.py` | lifecycle, ownership, revision, and async tasks |
| `frontend/test/nested-cv/*` | phase transitions, visual mapping, resolver, polling, download |

---

## 12. Implementation Sequence

### Phase 0 — Baseline and fixtures

1. Confirm the current backend and frontend build/test commands pass.
2. Resolve missing frontend imports in the current checkout.
3. **Understand the existing Mapping Tool**:
   - How does `MappingTool.tsx` render single-workbook mode?
   - How does the existing mapping editor work (grid, validation, target-table propagation)?
   - What API calls does the mapping tool make?
   - What Python scripts does it invoke for SQL generation?
4. Collect sanitized fixtures for:
   - a two-CV chain;
   - a three-level chain;
   - a diamond dependency;
   - multiple independent roots;
   - duplicate internal node names;
   - a legacy workbook.
5. Record expected SQL for at least two target dialects.

### Phase 1 — Artifact metadata (Mapping Tool export update)

1. Update Mapping Tool export to include version 2 workbook sheets (artifact manifest, dependencies, output schema).
2. Implement deterministic metadata extraction during mapping workbook export (not re-parsing original XML).
3. Implement strict v1/v2 parser and diagnostics in the merger parser.
4. Add parser and round-trip tests.

### Phase 2 — Domain services (backend)

1. Add typed models and session store (`nested_cv/models.py`, `nested_cv/session_store.py`).
2. Implement canonical identity handling and explicit links.
3. Implement graph construction, bottom-up topological sort, roots, and cycle detection.
4. Implement unified mapping aggregation, lineage, and conflict handling.
5. Add unit tests before routes/UI.

### Phase 3 — Pure query generation and composition (backend)

1. Refactor existing mapping generation to return a pure query body (reusable path).
2. Add deterministic CTE namespacing and reference rewriting.
3. Implement `inline_cte` and `emit_view` composition semantics.
4. Add SQL dialect adapters and generated-SQL validation.
5. Add PySpark DataFrame composer.
6. Add golden-file tests for all graph shapes, both output modes (SQL + PySpark), and all supported dialects.

### Phase 4 — API and asynchronous tasks (backend)

1. Add nested session routes and ownership/revision checks.
2. Add async task/status/download behavior.
3. Add TTL, cleanup, cancellation, and idempotency.
4. Add API lifecycle, malformed-input, auth, and concurrency tests.

### Phase 5 — Frontend (Mapping Tool mode integration)

1. Add typed API functions and models (`lib/api.ts`, `lib/nested-cv-types.ts`).
2. Update `MappingTool.tsx` with mode toggle: `single` vs `nested`.
3. Implement Nested CV Flattener components: upload list, dependency graph, resolver, consolidated mapping editor.
4. Implement validation diagnostics, generation polling, download, and discard/reset.
5. Ensure smooth UX transition: Convert → Map → (Optional: Merge Multiple Mappings).
6. Add component/integration tests and accessibility checks.

### Phase 6 — End-to-end verification

1. Run all backend and frontend tests.
2. Run each fixture through the complete UI flow.
3. Parse generated SQL/PySpark in the selected dialect.
4. Execute against disposable target environments where available.
5. Verify session expiry, retry/idempotency, and worker restart behavior.
6. Document limits, legacy behavior, diagnostics, and recovery steps.

---

## 13. Required Test Matrix

At minimum, test:

### Graph behavior

- Upload order different from dependency order.
- Two-CV and three-CV chains.
- Diamond dependency with a shared child.
- Multiple independent roots.
- Missing producer.
- Ambiguous names across package/schema.
- Duplicate canonical IDs.
- Self-cycle and multi-node cycle.
- Root incorrectly marked inline.
- Removing a CV invalidates affected links.

### Mapping behavior

- Identical mappings deduplicate.
- Conflicting table mappings.
- Conflicting column mappings.
- Same short name in different schemas remains distinct.
- Uploaded inline CV is collapsed but its physical sources remain visible.
- External CV remains mappable.
- Child output contract missing a required parent column.
- Reserved words and quoted identifiers.

### SQL behavior

- One chunk and multiple chunks per CV.
- Duplicate internal CTE names across CVs.
- Child reference in `FROM` and `JOIN`.
- Shared inline child duplicated per emitted statement.
- Emitted child referenced as a view.
- Mixed inline/emitted chain.
- Multiple output statements in topological order.
- Dialect-specific view syntax and quoting.
- AST parse failure blocks unsafe rewriting.
- Generated output contains no placeholder view name.

### API/security/reliability

- Missing/wrong-password/malformed workbook.
- Missing sheets/columns and unsupported version.
- File/session limits.
- Session ownership and task-download isolation.
- Expired/deleted session.
- Stale revision conflict.
- Concurrent mutations.
- Duplicate generate requests are idempotent.
- Task failure, cancellation, retry, and worker restart.

### Frontend

- Third-tab URL navigation.
- Upload/add/remove flow.
- Legacy dependency resolution.
- Blocking diagnostics and warning acknowledgement.
- Mapping conflict resolution.
- Poll cleanup on unmount/reset.
- Successful and failed downloads.
- Keyboard navigation, labels, focus management, and responsive layout.

---

## 14. Acceptance Criteria

The feature is complete only when:

1. **Standalone tool**: The Nested CV Flattener is a completely standalone UI/tab, separate from Conversion Tool and Mapping Tool.
2. **No-touch constraint**: Existing Conversion Tool and Mapping Tool frontend files are never modified.
3. **Visual mapping window**: Dedicated window for auto-mapping verification and manual override during nested resolution.
4. **Two output modes**: User can generate consolidated `.sql` OR `.pyspark` file.
5. **Bottom-up composition**: Dependency graph is resolved bottom-up; leaves first, then parents.
6. **Reuses mapping scripts**: The merger invokes the mapping tool's SQL generator, not the XML converter.
7. New mapping workbooks contain versioned identity, dependency, and output metadata.
8. Legacy workbooks cannot be merged until every ambiguous reference is resolved.
9. Upload/paste order does not affect generated output.
10. Cycles, missing dependencies, contract mismatches, and mapping conflicts are clearly reported before generation.
11. Internal CTE/DataFrame names cannot collide across uploaded CVs.
12. Every inline/non-inline choice follows the documented statement-scope semantics.
13. Every emitted view uses the selected target dialect and a real validated view name.
14. The generated file passes deterministic structural validation.
15. Sessions/tasks are revision-safe, bounded, and cleaned up.
16. The full chain, diamond, multi-root, legacy, conflict test suites pass.
17. The browser downloads one merged `.sql` or `.pyspark` file with no placeholder identifiers.
