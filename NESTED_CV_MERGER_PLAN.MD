# Nested CV Merger — Revised Implementation Plan

## 1. Objective

SAP HANA Calculation Views (CVs) may reference other CVs, forming a dependency graph. The Nested CV Merger will accept mapping workbooks from separately converted CVs, resolve their relationships, apply one consistent target mapping, and produce one target-dialect SQL file.

The user-facing rule is:

- **Inline / Merge enabled**: the CV is not emitted as a standalone view; it is inlined into each consumer as a CTE.
- **Inline / Merge disabled**: the CV is emitted as its own `CREATE VIEW` statement, and consumers reference that emitted view.

The implementation must not rely on upload order or filename equality to determine dependencies.

---

## 2. Scope and Output Contract

### In scope

1. Upload multiple encrypted mapping XLSX artifacts produced by this application.
2. Parse and validate each artifact.
3. Identify or explicitly resolve physical-table, external-CV, and uploaded-CV references.
4. Build and validate a directed dependency graph.
5. Present one deduplicated mapping editor for physical sources and unresolved external CVs.
6. Generate target-dialect query bodies for each CV.
7. Inline selected CVs as statement-local CTEs.
8. Emit non-inlined CVs as views in dependency order.
9. Download one merged `.sql` file.

### Initially out of scope

- Executing generated SQL against the target database.
- Automatically resolving ambiguous legacy dependencies without user confirmation.
- Recursive/cyclic Calculation Views.
- Renaming an inlined CV's output contract independently of all its consumers.
- Sharing one CTE between separate `CREATE VIEW` statements; SQL CTE scope does not allow this.

### Output semantics

The result is one `.sql` file that may contain multiple `CREATE VIEW` statements. Every statement has its own local `WITH` clause.

- Statements are emitted in topological order.
- An inlined child used by multiple emitted roots is repeated in each applicable statement.
- A CV configured as `emit_view` is emitted once and referenced by its target view name.
- A CV configured as `inline_cte` is not emitted separately.
- A graph root with no consumer cannot be `inline_cte`; validation must require or automatically set it to `emit_view`.

---

## 3. Blocking Artifact-Format Requirement

The current mapping workbook has only `sql info` and `mapping info`. Those sheets do not reliably identify:

- the canonical CV object;
- whether a source is a physical table or Calculation View;
- dependency edges between uploaded CVs;
- the CV's output-column contract;
- the workbook schema version.

A flat list of table strings and a filename are therefore insufficient for safe automatic merging.

### 3.1 Version 2 workbook format

Update conversion export so new workbooks retain the existing sheets and add the following metadata sheets.

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
- AST read/write dialect names.

Supported values must match the current mapping engine, for example BigQuery, Fabric/Azure Synapse, Snowflake, Redshift, Databricks, and SAP Datasphere. Unsupported combinations fail during validation rather than silently falling back.

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

### 10.1 Phases

```typescript
type NestedPhase =
  | "intro"
  | "uploading"
  | "resolving-dependencies"
  | "mapping"
  | "validating"
  | "generating"
  | "done"
  | "error"
```

Server session state is authoritative. The frontend stores the session ID, capability/auth context, revision, and presentation state; it does not become the only source of dependency or mapping truth.

### 10.2 Components

```text
NestedCVTool.tsx
├── NestedIntro.tsx
├── CvUploadList.tsx
│   └── CvUploadCard.tsx
├── DependencyGraphSummary.tsx
├── DependencyResolver.tsx
├── AggregatedSourceList.tsx
├── FinalMappingEditor.tsx
├── ValidationSummary.tsx
└── GenerationProgress.tsx
```

Reuse or extract the existing mapping-grid behavior for bulk paste, validation, and target-table propagation rather than maintaining two unrelated editors.

### 10.3 UI flow

1. Open the **Nested CV Merger** tab.
2. Select target platform and click **Start**.
3. Upload one or more mapping workbooks.
4. Show each CV, format version, dependencies, output schema, warnings, and emission mode.
5. Resolve all legacy/ambiguous dependency references.
6. Show graph roots and topological order.
7. Choose **Inline into consumers (CTE)** or **Emit as view** per CV.
8. Continue to the unified mapping editor.
9. Resolve mapping conflicts and edit target names.
10. Validate; display blocking errors and acknowledged warnings.
11. Start asynchronous generation and poll task status.
12. Download `nested_cv_merged_<timestamp>.sql`.

On reset/unmount, cancel polling. Explicitly delete the session when the user chooses **Discard**; do not destroy a valid session merely because a component rerenders.

### 10.4 Tab integration

Update all places that currently assume only `converter | mapper`:

- `HomeClient.tsx` active-tab state and render branch;
- URL query parsing (`?tab=nested`);
- the actual tab-switcher component imported by the current branch;
- slider/hero props and calls that use a two-value union;
- responsive styling and analytics events.

The current checkout should be built before feature work because `HomeClient.tsx` imports several components not visible in this repository snapshot. Resolve that baseline mismatch first so Nested CV changes are tested against a compiling frontend.

---

## 11. Files to Add or Modify

### Backend

| File | Change |
|---|---|
| `backend/flask_app.py` | Thin route registration only; no SQL merge logic |
| `backend/sql_converter.py` | Export version 2 manifest, dependencies, and output schema sheets |
| `backend/file_processor.py` or new metadata extractor | Deterministically extract CV identity/source kinds from XML |
| `backend/nested_cv/models.py` | Typed session, artifact, mapping, graph, and diagnostic models |
| `backend/nested_cv/artifact_parser.py` | Decrypt and parse v1/v2 workbooks with strict validation |
| `backend/nested_cv/session_store.py` | Store abstraction, locking, ownership, revision, and TTL |
| `backend/nested_cv/dependency_graph.py` | Linking, topological sort, roots, cycles, and contract validation |
| `backend/nested_cv/mapping_service.py` | Deduplication, lineage, conflict detection, and validation |
| `backend/nested_cv/sql_composer.py` | Namespacing, AST rewriting, CTE composition, and wrappers |
| `backend/nested_cv/tasks.py` | Async generation task lifecycle and result storage |
| `backend/mapping_sql_generator.py` | Extract reusable pure-query generation path |

### Frontend

| File | Change |
|---|---|
| `frontend/lib/api.ts` | Add typed functions for all nested-session and task endpoints |
| `frontend/lib/nested-cv-types.ts` | Shared API/view-model types |
| `frontend/components/HomeClient.tsx` | Add third render branch and URL handling |
| actual tab-switcher and slider files | Add `nested` tab support and update unions |
| `frontend/components/nested/*` | Add workflow components listed above |
| existing mapping editor modules | Extract reusable grid/validation behavior where practical |

### Tests

| Path | Coverage |
|---|---|
| `backend/tests/nested_cv/test_artifact_parser.py` | v1/v2 parsing and workbook validation |
| `backend/tests/nested_cv/test_dependency_graph.py` | graph/linking/topological behavior |
| `backend/tests/nested_cv/test_mapping_service.py` | deduplication and conflicts |
| `backend/tests/nested_cv/test_sql_composer.py` | CTE/view composition and dialect rendering |
| `backend/tests/nested_cv/test_api.py` | lifecycle, ownership, revision, and async tasks |
| frontend test files | phase transitions, resolver, mappings, polling, reset, and download |

---

## 12. Implementation Sequence

### Phase 0 — Baseline and fixtures

1. Confirm the current backend and frontend build/test commands pass.
2. Resolve missing frontend imports in the current checkout.
3. Collect sanitized fixtures for:
   - a two-CV chain;
   - a three-level chain;
   - a diamond dependency;
   - multiple independent roots;
   - duplicate internal node names;
   - a legacy workbook.
4. Record expected SQL for at least two target dialects.

### Phase 1 — Artifact metadata

1. Implement deterministic metadata extraction during XML conversion.
2. Export version 2 workbook sheets.
3. Implement strict v1/v2 parser and diagnostics.
4. Add parser and round-trip tests.

### Phase 2 — Domain services

1. Add typed models and session store.
2. Implement canonical identity handling and explicit links.
3. Implement graph construction, topological sort, roots, and cycle detection.
4. Implement unified mapping aggregation, lineage, and conflict handling.
5. Add unit tests before routes/UI.

### Phase 3 — Pure query generation and SQL composition

1. Refactor existing mapping generation to return a pure query body.
2. Add deterministic CTE namespacing and reference rewriting.
3. Implement `inline_cte` and `emit_view` composition semantics.
4. Add dialect adapters and generated-SQL validation.
5. Add golden-file tests for all graph shapes and supported dialects.

### Phase 4 — API and asynchronous tasks

1. Add nested session routes and ownership/revision checks.
2. Add async task/status/download behavior.
3. Add TTL, cleanup, cancellation, and idempotency.
4. Add API lifecycle, malformed-input, auth, and concurrency tests.

### Phase 5 — Frontend

1. Add typed API functions and models.
2. Integrate the third tab and URL state.
3. Implement upload list and dependency resolver.
4. Implement graph summary, emission controls, and mapping editor.
5. Implement validation diagnostics, generation polling, download, and discard/reset.
6. Add component/integration tests and accessibility checks.

### Phase 6 — End-to-end verification

1. Run all backend and frontend tests.
2. Run each fixture through the complete UI flow.
3. Parse generated SQL in the selected dialect.
4. Execute against disposable target environments where available.
5. Verify session expiry, multi-user isolation, retry/idempotency, and worker restart behavior.
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

1. New conversion workbooks contain versioned identity, dependency, and output metadata.
2. Legacy workbooks cannot be generated until every ambiguous reference is resolved.
3. Upload order does not affect generated SQL.
4. Cycles, missing dependencies, contract mismatches, and mapping conflicts are clearly reported before generation.
5. Internal CTE names cannot collide across uploaded CVs.
6. Every inline/non-inline choice follows the documented statement-scope semantics.
7. Every emitted view uses the selected target dialect and a real validated view name.
8. The generated file passes deterministic structural validation.
9. Sessions/tasks are owner-scoped, revision-safe, bounded, and cleaned up.
10. The full chain, diamond, multi-root, legacy, conflict, and security test suites pass.
11. The browser downloads one merged `.sql` file with no placeholder identifiers.
