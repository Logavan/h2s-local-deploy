// Nested CV Flattener - shared types

export type NestedPhase =
  | "intro"
  | "uploading"
  | "resolving"
  | "mapping"
  | "validating"
  | "generating"
  | "done"
  | "error"

export type OutputFormat = "sql" | "pyspark"

export type EmissionMode = "inline_cte" | "emit_view"

export type ObjectKind = "physical_table" | "calculation_view" | "unknown"

export interface OutputColumn {
  ordinal: number
  column_name: string
  data_type?: string
  nullable?: boolean
}

export interface SourceReference {
  source_ref_raw: string
  source_ref_canonical: string
  object_kind: ObjectKind
  referenced_by_node: string
  required_columns_json: string[] // JSON array
}

export interface CvArtifact {
  artifact_id: string
  cv_canonical_id: string | null
  cv_display_name: string
  file_name: string
  format_version: number
  emission_mode: EmissionMode
  target_view_name: string
  sql_chunks: SqlChunk[]
  dependencies: SourceReference[]
  output_schema: OutputColumn[]
  mapping_rows: MappingEntry[]
  warnings: Diagnostic[]
}

export interface SqlChunk {
  chunk_id: string
  sql_content: string
  node_name?: string
}

export interface MappingEntry {
  source_ref_canonical: string
  source_column_raw: string
  target_table: string
  target_column: string
  artifact_id?: string
}

export interface DependencyLink {
  consumer_artifact_id: string
  source_ref_canonical: string
  resolution: "uploaded_cv" | "physical_table" | "external_cv"
  producer_artifact_id: string | null
}

export interface Diagnostic {
  level: "error" | "warning" | "info"
  code: string
  message: string
  field?: string
}

export interface GraphSummary {
  nodes: { artifact_id: string; display_name: string; emission_mode: EmissionMode }[]
  edges: { from: string; to: string }[]
  roots: string[]
  topological_order: string[]
  has_cycles: boolean
}

export interface NestedSession {
  session_id: string
  target_dialect: string
  phase: NestedPhase
  revision: number
  artifacts: Record<string, CvArtifact>
  dependency_links: DependencyLink[]
  global_mappings: MappingEntry[]
  graph_summary: GraphSummary | null
  output_format: OutputFormat
  created_at: string
  updated_at: string
  expires_at: string
}

export interface NestedTask {
  task_id: string
  session_id: string
  status: "PENDING" | "IN_PROGRESS" | "COMPLETED" | "FAILED" | "CANCELLED"
  progress: number
  message: string
  result_url?: string
  diagnostics: Diagnostic[]
  created_at: string
  updated_at: string
}

// API request/response types

export interface CreateSessionRequest {
  target_dialect: string
  output_format: OutputFormat
}

export interface CreateSessionResponse {
  success: boolean
  session_id?: string
  error?: string
}

export interface AddCvRequest {
  file_content: string
  file_name: string
  password?: string
}

export interface AddCvResponse {
  success: boolean
  artifact?: CvArtifact
  error?: string
}

export interface UpdateCvRequest {
  emission_mode?: EmissionMode
  target_view_name?: string
  cv_display_name?: string
}

export interface ResolveLinksRequest {
  links: DependencyLink[]
}

export interface UpdateMappingsRequest {
  mappings: MappingEntry[]
}

export interface ValidateResponse {
  success: boolean
  valid: boolean
  errors: Diagnostic[]
  warnings: Diagnostic[]
  graph_summary?: GraphSummary
}

export interface GenerateResponse {
  success: boolean
  task_id?: string
  error?: string
}

export interface TaskStatusResponse {
  task_id: string
  status: NestedTask["status"]
  progress: number
  message: string
  result_url?: string
  diagnostics: Diagnostic[]
}
