"use client"
import { useState, useCallback } from "react"
import { motion } from "framer-motion"
import Image from "next/image"
import {
  GitMerge,
  Upload,
  CheckCircle,
  AlertCircle,
  Loader2,
  Download,
  Trash2,
  ArrowRight,
  Check,
  FileSpreadsheet,
  X,
} from "lucide-react"
import { cn } from "@/lib/utils"
import type {
  NestedPhase,
  OutputFormat,
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

interface DatabasePlatform {
  id: string
  name: string
  logo: string
  description: string
}

const DATABASE_PLATFORMS: DatabasePlatform[] = [
  {
    id: "bigquery",
    name: "Google BigQuery",
    logo: "/google-bigquery-logo.png",
    description: "Transform your merged CV for Google BigQuery",
  },
  {
    id: "snowflake",
    name: "Snowflake Cloud Data Platform",
    logo: "/snowflake-logo.png",
    description: "Map merged CVs to Snowflake's cloud platform",
  },
  {
    id: "databricks",
    name: "Databricks Lakehouse Platform (PySpark Available)",
    logo: "/databricks_logo.png",
    description: "Convert merged CVs for Databricks Lakehouse Platform",
  },
  {
    id: "fabric",
    name: "Microsoft Fabric (PySpark Available)",
    logo: "/fabric.png",
    description: "Adapt merged CVs for Microsoft Fabric",
  },
  {
    id: "redshift",
    name: "Amazon Redshift",
    logo: "/amazon-redshift-logo.png",
    description: "Convert merged CVs for Amazon's cloud data warehouse",
  },
  {
    id: "datasphere",
    name: "SAP Datasphere (SQL View)",
    logo: "/sap-datasphere-logo.png",
    description: "Optimize merged CVs for SAP Datasphere",
  },
]

export default function NestedCVTool() {
  // Session state
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [phase, setPhase] = useState<NestedPhase>("intro")
  const [selectedPlatform, setSelectedPlatform] = useState<string | null>(null)
  const [selectedPlatformName, setSelectedPlatformName] = useState("")
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
  const [isDraggingOver, setIsDraggingOver] = useState(false)

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
        if (status.status === "COMPLETED") setPhase("done")
      }
    }, 3000)
    return interval
  }, [])

  // Start a new session
  const handleStart = async () => {
    if (!selectedPlatform) {
      setError("Please select a target platform first.")
      return
    }
    setLoading(true)
    setError(null)
    try {
      const res = await nestedCreateSession({ target_dialect: selectedPlatform, output_format: outputFormat })
      if (!res.success || !res.session_id) {
        setError(res.error || "Failed to create session")
        setLoading(false)
        return
      }
      setSessionId(res.session_id)
      setPhase("uploading")
    } catch (e) {
      setError(e instanceof Error ? e.message : "Unknown error")
    }
    setLoading(false)
  }

  // Paste and add a CV
  const handlePasteCv = async () => {
    if (!sessionId || !pasteContent.trim()) return
    setLoading(true)
    setError(null)
    try {
      const res = await nestedAddCv(sessionId, {
        file_content: pasteContent,
        file_name: `nested_cv_${Date.now()}.json`,
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
    if (!sessionId) return
    await nestedUpdateCv(sessionId, artifactId, { emission_mode: mode })
    setArtifacts(prev => prev.map(a => a.artifact_id === artifactId ? { ...a, emission_mode: mode } : a))
  }

  // Update target view name
  const handleUpdateTargetViewName = async (artifactId: string, name: string) => {
    if (!sessionId) return
    await nestedUpdateCv(sessionId, artifactId, { target_view_name: name })
    setArtifacts(prev => prev.map(a => a.artifact_id === artifactId ? { ...a, target_view_name: name } : a))
  }

  // Remove artifact
  const handleRemoveArtifact = async (artifactId: string) => {
    if (!sessionId) return
    await nestedDeleteCv(sessionId, artifactId)
    setArtifacts(prev => prev.filter(a => a.artifact_id !== artifactId))
  }

  // Validate
  const handleValidate = async () => {
    if (!sessionId) return
    setLoading(true)
    setError(null)
    try {
      const res = await nestedValidate(sessionId)
      setValidationErrors(res.errors || [])
      setValidationWarnings(res.warnings || [])
      if (res.graph_summary) setGraphSummary(res.graph_summary)
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
    if (!sessionId) return
    setLoading(true)
    setError(null)
    try {
      const res = await nestedGenerate(sessionId)
      if (!res.success || !res.task_id) {
        setError(res.error || "Failed to start generation")
        setLoading(false)
        return
      }
      setTaskId(res.task_id)
      setPhase("generating")
      await pollTask(res.task_id)
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
    if (sessionId) await nestedDeleteSession(sessionId)
    setSessionId(null)
    setPhase("intro")
    setArtifacts([])
    setLinks([])
    setMappings([])
    setGraphSummary(null)
    setTaskId(null)
    setValidationErrors([])
    setValidationWarnings([])
    setError(null)
    setSelectedPlatform(null)
    setSelectedPlatformName("")
  }

  const handlePlatformSelect = (platformId: string) => {
    setSelectedPlatform(platformId)
    const platform = DATABASE_PLATFORMS.find(p => p.id === platformId)
    if (platform) setSelectedPlatformName(platform.name)
  }

  const handleOutputFormatSelect = (format: OutputFormat) => {
    setOutputFormat(format)
    setPhase("uploading")
  }

  // ============ Render helpers ============

  const renderIntro = () => (
    <div className="mt-8">
      <h2 className="text-xl font-semibold text-primary mb-4">Select Target Platform</h2>
      <p className="text-gray-600 mb-6">Choose the database platform for your merged CV output:</p>
      <div className="grid grid-cols-1 gap-3 sm:gap-4">
        {DATABASE_PLATFORMS.map(platform => (
          <div
            key={platform.id}
            onClick={() => handlePlatformSelect(platform.id)}
            className={cn(
              "flex items-center p-3 sm:p-4 border rounded-lg cursor-pointer transition-all min-h-[72px] sm:min-h-[80px]",
              selectedPlatform === platform.id
                ? "border-secondary bg-secondary/5 shadow-md"
                : "border-gray-200 hover:border-secondary/50 hover:bg-gray-50"
            )}
          >
            <div className="relative w-16 h-8 sm:w-24 sm:h-12 flex-shrink-0 mr-3 sm:mr-4">
              <Image
                src={platform.logo || "/placeholder.svg"}
                alt={platform.name}
                fill
                className="object-contain"
                onError={(e) => { e.currentTarget.style.display = "none" }}
              />
            </div>
            <div className="flex-grow">
              <h3 className="font-medium text-primary text-sm sm:text-base">{platform.name}</h3>
              <p className="text-xs sm:text-sm text-gray-500">{platform.description}</p>
            </div>
            <div className="ml-2">
              {selectedPlatform === platform.id ? (
                <div className="w-5 h-5 sm:w-6 sm:h-6 rounded-full bg-secondary flex items-center justify-center">
                  <Check className="w-3 h-3 sm:w-4 sm:h-4 text-white" />
                </div>
              ) : (
                <ArrowRight className="w-4 h-4 sm:w-5 sm:h-5 text-gray-400" />
              )}
            </div>
          </div>
        ))}
      </div>

      {selectedPlatform && (
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          className="mt-6"
        >
          <h3 className="text-lg font-semibold text-primary mb-3">Choose Output Format</h3>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
            <div
              onClick={() => setOutputFormat("sql")}
              className={cn(
                "flex flex-col items-center p-4 sm:p-6 border-2 rounded-xl cursor-pointer transition-all min-h-[140px]",
                outputFormat === "sql"
                  ? "border-secondary bg-secondary/5 shadow-md"
                  : "border-gray-200 hover:border-secondary/50 hover:bg-gray-50"
              )}
            >
              <div className="w-12 h-12 sm:w-16 sm:h-16 rounded-full bg-blue-100 flex items-center justify-center mb-3 sm:mb-4">
                <svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#2563eb" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M4 6h16" /><path d="M4 12h16" /><path d="M4 18h12" />
                </svg>
              </div>
              <h3 className="font-semibold text-primary text-base sm:text-lg mb-1">SQL</h3>
              <p className="text-xs sm:text-sm text-gray-500 text-center">CREATE VIEW statements</p>
            </div>
            <div
              onClick={() => setOutputFormat("pyspark")}
              className={cn(
                "flex flex-col items-center p-4 sm:p-6 border-2 rounded-xl cursor-pointer transition-all min-h-[140px]",
                outputFormat === "pyspark"
                  ? "border-secondary bg-secondary/5 shadow-md"
                  : "border-gray-200 hover:border-secondary/50 hover:bg-gray-50"
              )}
            >
              <div className="w-12 h-12 sm:w-16 sm:h-16 rounded-full bg-orange-100 flex items-center justify-center mb-3 sm:mb-4">
                <svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#ea580c" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M12 2L2 7l10 5 10-5-10-5z" /><path d="M2 17l10 5 10-5" /><path d="M2 12l10 5 10-5" />
                </svg>
              </div>
              <h3 className="font-semibold text-primary text-base sm:text-lg mb-1">PySpark</h3>
              <p className="text-xs sm:text-sm text-gray-500 text-center">DataFrame API output</p>
            </div>
          </div>

          <button
            onClick={handleStart}
            disabled={loading}
            className="mt-6 w-full py-3 bg-secondary text-white rounded-lg font-semibold hover:bg-secondary/90 transition-all disabled:opacity-50 flex items-center justify-center gap-2"
          >
            {loading ? <Loader2 className="w-5 h-5 animate-spin" /> : <GitMerge className="w-5 h-5" />}
            Start Nested CV Session
          </button>
        </motion.div>
      )}
    </div>
  )

  const renderUploading = () => (
    <div className="mt-8 space-y-6">
      <div>
        <h2 className="text-xl font-semibold text-primary mb-2">Paste Mapping File Content</h2>
        <p className="text-gray-600 text-sm mb-3">
          Paste the content of your converted mapping workbook (JSON exported from the Mapping Tool).
        </p>
        <textarea
          value={pasteContent}
          onChange={e => setPasteContent(e.target.value)}
          placeholder={`Paste your nested CV mapping file content here...\n\nExample:\n{\n  "cv_name": "MY_CV",\n  "dependencies": [...],\n  "mapping": [...]\n}`}
          className="w-full h-32 px-4 py-3 border border-gray-200 rounded-lg font-mono text-sm focus:ring-2 focus:ring-secondary focus:border-secondary resize-none"
        />
        <button
          onClick={handlePasteCv}
          disabled={loading || !pasteContent.trim()}
          className="mt-3 px-6 py-2.5 bg-secondary text-white rounded-lg font-semibold hover:bg-secondary/90 transition-all disabled:opacity-50 flex items-center gap-2"
        >
          {loading ? <Loader2 className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />}
          Add CV to Session
        </button>
      </div>

      {artifacts.length > 0 && (
        <div>
          <h3 className="text-lg font-semibold text-primary mb-3">Added Calculation Views ({artifacts.length})</h3>
          <div className="space-y-3">
            {artifacts.map(artifact => (
              <div key={artifact.artifact_id} className="border border-gray-200 rounded-lg p-4">
                <div className="flex items-start justify-between">
                  <div className="flex-1">
                    <p className="font-semibold text-primary">{artifact.cv_display_name}</p>
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
                    <select
                      value={artifact.emission_mode}
                      onChange={e => handleUpdateEmissionMode(artifact.artifact_id, e.target.value as "inline_cte" | "emit_view")}
                      className="text-sm border border-gray-300 rounded-lg px-2 py-1"
                    >
                      <option value="inline_cte">Inline as CTE</option>
                      <option value="emit_view">Emit as View</option>
                    </select>
                    <input
                      type="text"
                      value={artifact.target_view_name}
                      onChange={e => handleUpdateTargetViewName(artifact.artifact_id, e.target.value)}
                      placeholder="target_view"
                      className="text-sm border border-gray-300 rounded-lg px-2 py-1 w-36"
                    />
                    <button
                      onClick={() => handleRemoveArtifact(artifact.artifact_id)}
                      className="p-1.5 text-red-500 hover:bg-red-50 rounded-lg"
                    >
                      <Trash2 className="w-4 h-4" />
                    </button>
                  </div>
                </div>
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
          <button
            onClick={handleValidate}
            disabled={loading}
            className="mt-4 w-full py-2.5 bg-secondary text-white rounded-lg font-semibold hover:bg-secondary/90 transition-all disabled:opacity-50 flex items-center justify-center gap-2"
          >
            {loading ? <Loader2 className="w-5 h-5 animate-spin" /> : <CheckCircle className="w-5 h-5" />}
            Validate & Continue
          </button>
        </div>
      )}

      {artifacts.length === 0 && (
        <div className="text-center py-8 text-gray-500">
          <FileSpreadsheet className="w-12 h-12 mx-auto mb-3 text-gray-300" />
          <p>No CVs added yet. Paste a mapping file above to begin.</p>
        </div>
      )}
    </div>
  )

  const renderError = () => (
    <div className="mt-8">
      <div className="bg-white border border-red-200 rounded-xl p-6 shadow-sm">
        <div className="flex items-center gap-3 mb-4">
          <AlertCircle className="w-6 h-6 text-red-500" />
          <h3 className="text-lg font-semibold text-red-600">Validation Failed</h3>
        </div>
        <div className="space-y-2 mb-4">
          {validationErrors.map((e, i) => (
            <div key={i} className="p-3 bg-red-50 border border-red-200 rounded-xl text-sm text-red-700">
              <span className="font-medium">[{e.code}]</span> {e.message}
            </div>
          ))}
        </div>
        {validationWarnings.length > 0 && (
          <div className="mb-4">
            <p className="text-sm font-medium text-yellow-700 mb-2">Warnings:</p>
            {validationWarnings.map((w, i) => (
              <div key={i} className="p-3 bg-yellow-50 border border-yellow-200 rounded-xl text-sm text-yellow-700 mb-1">
                {w.message}
              </div>
            ))}
          </div>
        )}
        <div className="flex gap-3">
          <button
            onClick={() => setPhase("uploading")}
            className="px-6 py-2.5 bg-secondary text-white rounded-lg font-semibold hover:bg-secondary/90 transition-all"
          >
            Go Back & Fix
          </button>
          <button
            onClick={handleDiscard}
            className="px-6 py-2.5 border border-gray-300 text-gray-600 rounded-lg font-medium hover:bg-gray-50 transition-all"
          >
            Discard
          </button>
        </div>
      </div>
    </div>
  )

  const renderMapping = () => (
    <div className="mt-8">
      <div className="bg-white border border-gray-200 rounded-xl p-6 shadow-sm">
        <div className="flex items-center gap-3 mb-4">
          <CheckCircle className="w-6 h-6 text-green-500" />
          <div>
            <h3 className="text-lg font-semibold text-primary">Validation Passed</h3>
            <p className="text-sm text-gray-500">{artifacts.length} CVs ready for merging</p>
          </div>
        </div>

        {graphSummary && (
          <div className="mb-4 p-4 bg-gray-50 rounded-xl">
            <p className="text-sm font-medium text-gray-700 mb-1">Dependency Graph</p>
            <p className="text-xs text-gray-500">
              {graphSummary.nodes.length} CVs • {graphSummary.edges.length} edges •{" "}
              {graphSummary.has_cycles ? "⚠️ Cycles detected" : "✅ No cycles"}
            </p>
            {graphSummary.roots.length > 0 && (
              <p className="text-xs text-gray-500 mt-1">
                Roots: {graphSummary.roots.map(r => artifacts.find(a => a.artifact_id === r)?.cv_display_name || r).join(", ")}
              </p>
            )}
          </div>
        )}

        <div className="flex gap-3">
          <button
            onClick={handleGenerate}
            disabled={loading}
            className="flex-1 py-3 bg-secondary text-white rounded-lg font-semibold hover:bg-secondary/90 transition-all disabled:opacity-50 flex items-center justify-center gap-2"
          >
            {loading ? <Loader2 className="w-5 h-5 animate-spin" /> : <GitMerge className="w-5 h-5" />}
            Generate Merged Output
          </button>
          <button
            onClick={handleDiscard}
            className="px-6 py-3 border border-gray-300 text-gray-600 rounded-lg font-medium hover:bg-gray-50 transition-all"
          >
            Discard
          </button>
        </div>
      </div>
    </div>
  )

  const renderGenerating = () => (
    <div className="mt-8">
      <div className="bg-white border border-gray-200 rounded-xl p-8 shadow-sm text-center">
        <Loader2 className="w-12 h-12 mx-auto mb-4 text-secondary animate-spin" />
        <h3 className="text-xl font-semibold text-primary mb-2">Generating Merged Output...</h3>
        <p className="text-gray-500 mb-4">{taskMessage || "Processing your CVs"}</p>
        <div className="w-full max-w-md mx-auto bg-gray-100 rounded-full h-2.5 mb-4">
          <div
            className="bg-secondary h-2.5 rounded-full transition-all"
            style={{ width: `${taskProgress}%` }}
          />
        </div>
        <p className="text-sm text-gray-500">{taskProgress}% complete</p>
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
    </div>
  )

  const renderDone = () => (
    <div className="mt-8">
      <div className="bg-white border border-gray-200 rounded-xl p-8 shadow-sm text-center">
        <CheckCircle className="w-16 h-16 mx-auto mb-4 text-green-500" />
        <h3 className="text-2xl font-bold text-primary mb-2">Generation Complete!</h3>
        <p className="text-gray-500 mb-6">Your merged CV file is ready for download.</p>
        <div className="flex gap-3 justify-center">
          <button
            onClick={handleDownload}
            className="px-8 py-3 bg-secondary text-white rounded-lg font-semibold hover:bg-secondary/90 transition-all flex items-center gap-2"
          >
            <Download className="w-5 h-5" />
            Download Merged File
          </button>
          <button
            onClick={handleDiscard}
            className="px-6 py-3 border border-gray-300 text-gray-600 rounded-lg font-medium hover:bg-gray-50 transition-all"
          >
            Start Over
          </button>
        </div>
      </div>
    </div>
  )

  // ============ Main render ============
  return (
    <div className="w-full">
      {/* Header */}
      <div className="mb-6">
        <div className="flex items-center gap-3 mb-1">
          <div className="p-2 bg-secondary/10 rounded-lg">
            <GitMerge className="w-5 h-5 text-secondary" />
          </div>
          <div>
            <h2 className="text-2xl font-bold text-primary">Nested CV Flattener</h2>
            <p className="text-sm text-gray-500">Merge multiple calculation view mappings into one consolidated output</p>
          </div>
        </div>
      </div>

      {/* Error */}
      {error && (
        <motion.div
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          className="mb-6 p-4 bg-red-50 border border-red-200 rounded-xl flex items-start gap-3"
        >
          <AlertCircle className="w-5 h-5 text-red-500 mt-0.5 flex-shrink-0" />
          <div>
            <p className="font-medium text-red-700">{error}</p>
          </div>
        </motion.div>
      )}

      {/* Phase content */}
      {phase === "intro" && renderIntro()}
      {(phase === "uploading" || phase === "resolving") && renderUploading()}
      {phase === "error" && renderError()}
      {phase === "mapping" && renderMapping()}
      {phase === "generating" && renderGenerating()}
      {phase === "done" && renderDone()}
    </div>
  )
}
