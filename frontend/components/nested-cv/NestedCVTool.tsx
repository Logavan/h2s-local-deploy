"use client"
import { useState, useCallback } from "react"
import { motion, AnimatePresence } from "framer-motion"
import { GitMerge, Upload, FileText, CheckCircle, AlertCircle, Loader2, Download, Trash2 } from "lucide-react"
import type {
  NestedPhase,
  OutputFormat,
  NestedSession,
  CvArtifact,
  DependencyLink,
  MappingEntry,
  GraphSummary,
  Diagnostic,
} from "@/lib/nested-cv-types"
import {
  nestedCreateSession,
  nestedGetSession,
  nestedAddCv,
  nestedUpdateCv,
  nestedDeleteCv,
  nestedResolveLinks,
  nestedUpdateMappings,
  nestedValidate,
  nestedGenerate,
  nestedGetTaskStatus,
  nestedDownloadResult,
  nestedDeleteSession,
} from "@/lib/api"

// Supported target dialects
const TARGET_DIALECTS = [
  { value: "bigquery", label: "Google BigQuery" },
  { value: "snowflake", label: "Snowflake" },
  { value: "databricks", label: "Databricks SQL" },
  { value: "fabric", label: "Microsoft Fabric" },
  { value: "redshift", label: "Amazon Redshift" },
  { value: "datasphere", label: "SAP Datasphere" },
]

interface Props {}

export default function NestedCVTool(_props: Props) {
  // Session state
  const [session, setSession] = useState<NestedSession | null>(null)
  const [phase, setPhase] = useState<NestedPhase>("intro")
  const [targetDialect, setTargetDialect] = useState("bigquery")
  const [outputFormat, setOutputFormat] = useState<OutputFormat>("sql")

  // Artifact state
  const [artifacts, setArtifacts] = useState<CvArtifact[]>([])
  const [links, setLinks] = useState<DependencyLink[]>([])
  const [mappings, setMappings] = useState<MappingEntry[]>([])
  const [graphSummary, setGraphSummary] = useState<GraphSummary | null>(null)

  // Task state
  const [taskId, setTaskId] = useState<string | null>(null)
  const [taskStatus, setTaskStatus] = useState<string>("")
  const [taskProgress, setTaskProgress] = useState(0)
  const [taskMessage, setTaskMessage] = useState("")
  const [taskDiagnostics, setTaskDiagnostics] = useState<Diagnostic[]>([])

  // Validation state
  const [validationErrors, setValidationErrors] = useState<Diagnostic[]>([])
  const [validationWarnings, setValidationWarnings] = useState<Diagnostic[]>([])

  // UI state
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [pasteContent, setPasteContent] = useState("")

  // Poll task status
  const pollTask = useCallback(async (tid: string) => {
    const interval = setInterval(async () => {
      const status = await nestedGetTaskStatus(tid)
      setTaskStatus(status.status)
      setTaskProgress(status.progress)
      setTaskMessage(status.message)
      setTaskDiagnostics(status.diagnostics)

      if (status.status === "COMPLETED" || status.status === "FAILED" || status.status === "CANCELLED") {
        clearInterval(interval)
        if (status.status === "COMPLETED") {
          setPhase("done")
        }
      }
    }, 3000)
    return interval
  }, [])

  // Start a new session
  const handleStart = async () => {
    setLoading(true)
    setError(null)
    try {
      const res = await nestedCreateSession({ target_dialect: targetDialect, output_format: outputFormat })
      if (!res.success || !res.session_id) {
        setError(res.error || "Failed to create session")
        setLoading(false)
        return
      }
      // Fetch the session
      const sessionRes = await nestedGetSession(res.session_id)
      if (sessionRes.success && sessionRes.session) {
        setSession(sessionRes.session)
        setPhase("uploading")
      } else {
        setError(sessionRes.error || "Failed to get session")
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error")
    }
    setLoading(false)
  }

  // Paste and add a CV
  const handlePasteCv = async () => {
    if (!session || !pasteContent.trim()) return
    setLoading(true)
    setError(null)
    try {
      const res = await nestedAddCv(session.session_id, {
        file_content: pasteContent,
        file_name: `pasted_cv_${Date.now()}.xlsx`,
      })
      if (!res.success) {
        setError(res.error || "Failed to add CV")
        setLoading(false)
        return
      }
      if (res.artifact) {
        setArtifacts(prev => [...prev, res.artifact!])
        setPasteContent("")
        setPhase("resolving")
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error")
    }
    setLoading(false)
  }

  // Update emission mode
  const handleUpdateEmissionMode = async (artifactId: string, mode: "inline_cte" | "emit_view") => {
    if (!session) return
    await nestedUpdateCv(session.session_id, artifactId, { emission_mode: mode })
    setArtifacts(prev =>
      prev.map(a => a.artifact_id === artifactId ? { ...a, emission_mode: mode } : a)
    )
  }

  // Update target view name
  const handleUpdateTargetViewName = async (artifactId: string, name: string) => {
    if (!session) return
    await nestedUpdateCv(session.session_id, artifactId, { target_view_name: name })
    setArtifacts(prev =>
      prev.map(a => a.artifact_id === artifactId ? { ...a, target_view_name: name } : a)
    )
  }

  // Remove artifact
  const handleRemoveArtifact = async (artifactId: string) => {
    if (!session) return
    await nestedDeleteCv(session.session_id, artifactId)
    setArtifacts(prev => prev.filter(a => a.artifact_id !== artifactId))
  }

  // Validate
  const handleValidate = async () => {
    if (!session) return
    setLoading(true)
    setError(null)
    try {
      const res = await nestedValidate(session.session_id)
      setValidationErrors(res.errors || [])
      setValidationWarnings(res.warnings || [])
      if (res.graph_summary) {
        setGraphSummary(res.graph_summary)
      }
      if (res.valid) {
        setPhase("mapping")
      } else {
        setPhase("error")
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "Validation failed")
    }
    setLoading(false)
  }

  // Generate
  const handleGenerate = async () => {
    if (!session) return
    setLoading(true)
    setError(null)
    try {
      const res = await nestedGenerate(session.session_id)
      if (!res.success || !res.task_id) {
        setError(res.error || "Failed to start generation")
        setLoading(false)
        return
      }
      setTaskId(res.task_id)
      setPhase("generating")
      const interval = await pollTask(res.task_id)
      // Store interval id for cleanup (simplified - just let it run)
    } catch (e) {
      setError(e instanceof Error ? e.message : "Generation failed")
    }
    setLoading(false)
  }

  // Download
  const handleDownload = async () => {
    if (!taskId) return
    await nestedDownloadResult(taskId)
  }

  // Discard / reset
  const handleDiscard = async () => {
    if (session) {
      await nestedDeleteSession(session.session_id)
    }
    setSession(null)
    setPhase("intro")
    setArtifacts([])
    setLinks([])
    setMappings([])
    setGraphSummary(null)
    setTaskId(null)
    setValidationErrors([])
    setValidationWarnings([])
    setError(null)
  }

  return (
    <div className="w-full max-w-5xl mx-auto">
      {/* Header */}
      <div className="mb-8">
        <div className="flex items-center gap-3 mb-2">
          <div className="p-2 bg-amber-100 rounded-lg">
            <GitMerge className="w-6 h-6 text-amber-600" />
          </div>
          <div>
            <h2 className="text-2xl font-bold text-gray-900">Nested CV Flattener</h2>
            <p className="text-sm text-gray-500">Merge multiple calculation view mappings into one consolidated output</p>
          </div>
        </div>
      </div>

      {/* Error display */}
      <AnimatePresence>
        {error && (
          <motion.div
            initial={{ opacity: 0, y: -10 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0 }}
            className="mb-6 p-4 bg-red-50 border border-red-200 rounded-xl flex items-start gap-3"
          >
            <AlertCircle className="w-5 h-5 text-red-500 mt-0.5 flex-shrink-0" />
            <div>
              <p className="font-medium text-red-700">{error}</p>
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* Phase: Intro */}
      {phase === "intro" && (
        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="space-y-6">
          <div className="bg-white border border-gray-200 rounded-2xl p-6 shadow-sm">
            <h3 className="text-lg font-semibold mb-4">Configure Output</h3>

            {/* Target Dialect */}
            <div className="mb-4">
              <label className="block text-sm font-medium text-gray-700 mb-2">Target Platform</label>
              <select
                value={targetDialect}
                onChange={e => setTargetDialect(e.target.value)}
                className="w-full px-4 py-2.5 border border-gray-300 rounded-xl focus:ring-2 focus:ring-amber-500 focus:border-amber-500"
              >
                {TARGET_DIALECTS.map(d => (
                  <option key={d.value} value={d.value}>{d.label}</option>
                ))}
              </select>
            </div>

            {/* Output Format */}
            <div className="mb-6">
              <label className="block text-sm font-medium text-gray-700 mb-2">Output Format</label>
              <div className="grid grid-cols-2 gap-3">
                <button
                  onClick={() => setOutputFormat("sql")}
                  className={`p-4 rounded-xl border-2 transition-all ${
                    outputFormat === "sql"
                      ? "border-amber-500 bg-amber-50 text-amber-700"
                      : "border-gray-200 hover:border-gray-300"
                  }`}
                >
                  <FileText className={`w-6 h-6 mb-2 ${outputFormat === "sql" ? "text-amber-500" : "text-gray-400"}`} />
                  <p className="font-semibold">SQL</p>
                  <p className="text-xs text-gray-500 mt-1">CREATE VIEW statements</p>
                </button>
                <button
                  onClick={() => setOutputFormat("pyspark")}
                  className={`p-4 rounded-xl border-2 transition-all ${
                    outputFormat === "pyspark"
                      ? "border-amber-500 bg-amber-50 text-amber-700"
                      : "border-gray-200 hover:border-gray-300"
                  }`}
                >
                  <FileText className={`w-6 h-6 mb-2 ${outputFormat === "pyspark" ? "text-amber-500" : "text-gray-400"}`} />
                  <p className="font-semibold">PySpark</p>
                  <p className="text-xs text-gray-500 mt-1">DataFrame API output</p>
                </button>
              </div>
            </div>

            <button
              onClick={handleStart}
              disabled={loading}
              className="w-full py-3 bg-gradient-to-r from-amber-500 to-amber-600 text-white rounded-xl font-semibold hover:from-amber-600 hover:to-amber-700 transition-all disabled:opacity-50"
            >
              {loading ? <Loader2 className="w-5 h-5 animate-spin mx-auto" /> : "Start Nested CV Session"}
            </button>
          </div>
        </motion.div>
      )}

      {/* Phase: Uploading */}
      {(phase === "uploading" || phase === "resolving") && (
        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="space-y-6">

          {/* Paste CV Content */}
          <div className="bg-white border border-gray-200 rounded-2xl p-6 shadow-sm">
            <h3 className="text-lg font-semibold mb-4 flex items-center gap-2">
              <Upload className="w-5 h-5 text-amber-500" />
              Paste Mapping File Content
            </h3>
            <p className="text-sm text-gray-500 mb-3">
              Paste the content of your converted mapping workbook (JSON or text format exported from the Mapping Tool).
            </p>
            <textarea
              value={pasteContent}
              onChange={e => setPasteContent(e.target.value)}
              placeholder={`Paste your nested CV mapping file content here...\n\nExample:\n{\n  "cv_name": "MY_CV",\n  "dependencies": [...],\n  "mapping": [...]\n}`}
              className="w-full h-40 px-4 py-3 border border-gray-300 rounded-xl font-mono text-sm focus:ring-2 focus:ring-amber-500 focus:border-amber-500 resize-none"
            />
            <button
              onClick={handlePasteCv}
              disabled={loading || !pasteContent.trim()}
              className="mt-3 px-6 py-2.5 bg-amber-500 text-white rounded-xl font-semibold hover:bg-amber-600 transition-all disabled:opacity-50"
            >
              {loading ? <Loader2 className="w-5 h-5 animate-spin mx-auto" /> : "Add CV to Session"}
            </button>
          </div>

          {/* Uploaded CVs List */}
          {artifacts.length > 0 && (
            <div className="bg-white border border-gray-200 rounded-2xl p-6 shadow-sm">
              <h3 className="text-lg font-semibold mb-4">Added Calculation Views ({artifacts.length})</h3>
              <div className="space-y-3">
                {artifacts.map(artifact => (
                  <div key={artifact.artifact_id} className="border border-gray-200 rounded-xl p-4">
                    <div className="flex items-start justify-between">
                      <div className="flex-1">
                        <p className="font-semibold text-gray-900">{artifact.cv_display_name}</p>
                        <p className="text-sm text-gray-500">
                          Version {artifact.format_version} • {artifact.dependencies.length} dependencies
                        </p>
                        {artifact.warnings.length > 0 && (
                          <div className="mt-2 flex flex-wrap gap-2">
                            {artifact.warnings.map((w, i) => (
                              <span key={i} className="px-2 py-0.5 bg-yellow-50 text-yellow-700 text-xs rounded-full">
                                {w.message}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                      <div className="flex items-center gap-2 ml-4">
                        {/* Emission Mode Toggle */}
                        <select
                          value={artifact.emission_mode}
                          onChange={e => handleUpdateEmissionMode(artifact.artifact_id, e.target.value as "inline_cte" | "emit_view")}
                          className="text-sm border border-gray-300 rounded-lg px-2 py-1"
                        >
                          <option value="inline_cte">Inline as CTE</option>
                          <option value="emit_view">Emit as View</option>
                        </select>
                        {/* Target View Name */}
                        <input
                          type="text"
                          value={artifact.target_view_name}
                          onChange={e => handleUpdateTargetViewName(artifact.artifact_id, e.target.value)}
                          placeholder="target_view_name"
                          className="text-sm border border-gray-300 rounded-lg px-2 py-1 w-40"
                        />
                        <button
                          onClick={() => handleRemoveArtifact(artifact.artifact_id)}
                          className="p-1.5 text-red-500 hover:bg-red-50 rounded-lg"
                        >
                          <Trash2 className="w-4 h-4" />
                        </button>
                      </div>
                    </div>

                    {/* Output Schema */}
                    {artifact.output_schema.length > 0 && (
                      <div className="mt-3 pt-3 border-t border-gray-100">
                        <p className="text-xs font-medium text-gray-500 mb-2">Output Columns</p>
                        <div className="flex flex-wrap gap-1.5">
                          {artifact.output_schema.map(col => (
                            <span key={col.ordinal} className="px-2 py-0.5 bg-gray-100 text-gray-700 text-xs rounded">
                              {col.column_name}
                            </span>
                          ))}
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>

              <div className="mt-4 flex gap-3">
                <button
                  onClick={handleValidate}
                  disabled={loading}
                  className="flex-1 py-2.5 bg-amber-500 text-white rounded-xl font-semibold hover:bg-amber-600 transition-all disabled:opacity-50"
                >
                  {loading ? <Loader2 className="w-5 h-5 animate-spin mx-auto" /> : "Validate & Continue"}
                </button>
                <button
                  onClick={handleDiscard}
                  className="px-6 py-2.5 border border-gray-300 text-gray-600 rounded-xl font-medium hover:bg-gray-50 transition-all"
                >
                  Discard
                </button>
              </div>
            </div>
          )}

          {artifacts.length === 0 && (
            <div className="text-center py-8 text-gray-500">
              <FileText className="w-12 h-12 mx-auto mb-3 text-gray-300" />
              <p>No CVs added yet. Paste a mapping file above to begin.</p>
            </div>
          )}
        </motion.div>
      )}

      {/* Phase: Mapping */}
      {phase === "mapping" && (
        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="space-y-6">
          <div className="bg-white border border-gray-200 rounded-2xl p-6 shadow-sm">
            <h3 className="text-lg font-semibold mb-4 flex items-center gap-2">
              <CheckCircle className="w-5 h-5 text-green-500" />
              Validation Passed
            </h3>
            {graphSummary && (
              <div className="mb-4 p-4 bg-gray-50 rounded-xl">
                <p className="text-sm font-medium text-gray-700 mb-2">Dependency Graph</p>
                <p className="text-xs text-gray-500">
                  {graphSummary.nodes.length} CVs • {graphSummary.edges.length} edges •{" "}
                  {graphSummary.has_cycles ? "⚠️ Cycles detected" : "✅ No cycles"}
                </p>
                {graphSummary.roots.length > 0 && (
                  <p className="text-xs text-gray-500 mt-1">
                    Roots: {graphSummary.roots.join(", ")}
                  </p>
                )}
              </div>
            )}
            <div className="mb-4">
              <h4 className="text-sm font-medium text-gray-700 mb-2">Consolidated Mappings</h4>
              <p className="text-xs text-gray-500">
                {mappings.length} mapping entries ready for generation.
              </p>
            </div>
            <div className="flex gap-3">
              <button
                onClick={handleGenerate}
                disabled={loading}
                className="flex-1 py-3 bg-gradient-to-r from-amber-500 to-amber-600 text-white rounded-xl font-semibold hover:from-amber-600 hover:to-amber-700 transition-all disabled:opacity-50"
              >
                {loading ? <Loader2 className="w-5 h-5 animate-spin mx-auto" /> : "Generate Merged Output"}
              </button>
              <button
                onClick={handleDiscard}
                className="px-6 py-3 border border-gray-300 text-gray-600 rounded-xl font-medium hover:bg-gray-50 transition-all"
              >
                Discard
              </button>
            </div>
          </div>
        </motion.div>
      )}

      {/* Phase: Generating */}
      {phase === "generating" && (
        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="space-y-6">
          <div className="bg-white border border-gray-200 rounded-2xl p-8 shadow-sm text-center">
            <Loader2 className="w-12 h-12 mx-auto mb-4 text-amber-500 animate-spin" />
            <h3 className="text-xl font-semibold mb-2">Generating Merged Output...</h3>
            <p className="text-gray-500 mb-4">{taskMessage || "Processing your CVs"}</p>
            <div className="w-full max-w-md mx-auto bg-gray-100 rounded-full h-2.5 mb-4">
              <div
                className="bg-amber-500 h-2.5 rounded-full transition-all"
                style={{ width: `${taskProgress}%` }}
              />
            </div>
            <p className="text-sm text-gray-500">{taskProgress}% complete</p>

            {/* Diagnostics */}
            {taskDiagnostics.length > 0 && (
              <div className="mt-6 text-left">
                {taskDiagnostics.map((d, i) => (
                  <div key={i} className={`text-sm mb-1 ${d.level === "error" ? "text-red-600" : d.level === "warning" ? "text-yellow-600" : "text-gray-600"}`}>
                    [{d.level.toUpperCase()}] {d.message}
                  </div>
                ))}
              </div>
            )}
          </div>
        </motion.div>
      )}

      {/* Phase: Done */}
      {phase === "done" && (
        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="space-y-6">
          <div className="bg-white border border-gray-200 rounded-2xl p-8 shadow-sm text-center">
            <CheckCircle className="w-16 h-16 mx-auto mb-4 text-green-500" />
            <h3 className="text-2xl font-bold mb-2">Generation Complete!</h3>
            <p className="text-gray-500 mb-6">Your merged CV file is ready for download.</p>
            <div className="flex gap-3 justify-center">
              <button
                onClick={handleDownload}
                className="px-8 py-3 bg-gradient-to-r from-amber-500 to-amber-600 text-white rounded-xl font-semibold hover:from-amber-600 hover:to-amber-700 transition-all flex items-center gap-2"
              >
                <Download className="w-5 h-5" />
                Download Merged File
              </button>
              <button
                onClick={handleDiscard}
                className="px-6 py-3 border border-gray-300 text-gray-600 rounded-xl font-medium hover:bg-gray-50 transition-all"
              >
                Start Over
              </button>
            </div>
          </div>
        </motion.div>
      )}

      {/* Phase: Error */}
      {phase === "error" && (
        <motion.div initial={{ opacity: 0, y: 10 }} animate={{ opacity: 1, y: 0 }} className="space-y-6">
          <div className="bg-white border border-red-200 rounded-2xl p-6 shadow-sm">
            <h3 className="text-lg font-semibold mb-4 flex items-center gap-2 text-red-600">
              <AlertCircle className="w-5 h-5" />
              Validation Failed
            </h3>
            <div className="space-y-2 mb-4">
              {validationErrors.map((e, i) => (
                <div key={i} className="p-3 bg-red-50 border border-red-200 rounded-xl text-sm text-red-700">
                  <span className="font-medium">[{e.code}]</span> {e.message}
                </div>
              ))}
            </div>
            {validationWarnings.length > 0 && (
              <div className="mb-4">
                <p className="text-sm font-medium text-yellow-700 mb-2">Warnings (can be acknowledged):</p>
                {validationWarnings.map((w, i) => (
                  <div key={i} className="p-3 bg-yellow-50 border border-yellow-200 rounded-xl text-sm text-yellow-700 mb-1">
                    {w.message}
                  </div>
                ))}
              </div>
            )}
            <div className="flex gap-3">
              <button
                onClick={() => setPhase("resolving")}
                className="px-6 py-2.5 bg-amber-500 text-white rounded-xl font-semibold hover:bg-amber-600 transition-all"
              >
                Go Back & Fix
              </button>
              <button
                onClick={handleDiscard}
                className="px-6 py-2.5 border border-gray-300 text-gray-600 rounded-xl font-medium hover:bg-gray-50 transition-all"
              >
                Discard
              </button>
            </div>
          </div>
        </motion.div>
      )}
    </div>
  )
}
