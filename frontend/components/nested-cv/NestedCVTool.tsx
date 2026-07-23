"use client"
import { useState, useCallback } from "react"
import { motion } from "framer-motion"
import Image from "next/image"
import Link from "next/link"
import {
  GitMerge,
  Upload,
  RotateCcw,
  Lightbulb,
  CheckCircle,
  ArrowRight,
  FileSpreadsheet,
  AlertCircle,
  Check,
  History,
  Loader2,
  Download,
  Trash2,
  Pencil,
  Save,
  X,
} from "lucide-react"
import { cn } from "@/lib/utils"
import SqlEditor from "@/components/CodeEditor"
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
  { id: "bigquery", name: "Google BigQuery", logo: "/google-bigquery-logo.png", description: "Transform your merged CV for Google BigQuery" },
  { id: "snowflake", name: "Snowflake Cloud Data Platform", logo: "/snowflake-logo.png", description: "Map merged CVs to Snowflake's cloud platform" },
  { id: "databricks", name: "Databricks Lakehouse Platform (PySpark Available)", logo: "/databricks_logo.png", description: "Convert merged CVs for Databricks Lakehouse Platform" },
  { id: "fabric", name: "Microsoft Fabric (PySpark Available)", logo: "/fabric.png", description: "Adapt merged CVs for Microsoft Fabric" },
  { id: "redshift", name: "Amazon Redshift", logo: "/amazon-redshift-logo.png", description: "Convert merged CVs for Amazon's cloud data warehouse" },
  { id: "datasphere", name: "SAP Datasphere (SQL View)", logo: "/sap-datasphere-logo.png", description: "Optimize merged CVs for SAP Datasphere" },
]

type NestedUIState =
  | "intro"
  | "platform-selected"
  | "format-selected"
  | "uploading"
  | "resolving"
  | "validating"
  | "mapping"
  | "generating"
  | "display-result"
  | "error"

export default function NestedCVTool() {
  const [uiState, setUiState] = useState<NestedUIState>("intro")
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [selectedPlatform, setSelectedPlatform] = useState<string | null>(null)
  const [selectedPlatformName, setSelectedPlatformName] = useState("")
  const [outputFormat, setOutputFormat] = useState<OutputFormat>("sql")

  // Artifact state
  const [artifacts, setArtifacts] = useState<CvArtifact[]>([])
  const [links, setLinks] = useState<DependencyLink[]>([])
  const [mappings, setMappings] = useState<MappingEntry[]>([])
  const [graphSummary, setGraphSummary] = useState<GraphSummary | null>(null)

  // Generated content
  const [generatedContent, setGeneratedContent] = useState<string | null>(null)
  const [generatedFileName, setGeneratedFileName] = useState<string | null>(null)
  const [isFullScreen, setIsFullScreen] = useState(false)
  const [isEditorCollapsed, setIsEditorCollapsed] = useState(false)
  const [isFileNameEditable, setIsFileNameEditable] = useState(false)
  const [activeTab, setActiveTab] = useState<"cte" | "tempTable">("cte")

  // Task state
  const [taskId, setTaskId] = useState<string | null>(null)
  const [taskProgress, setTaskProgress] = useState(0)
  const [taskMessage, setTaskMessage] = useState("")
  const [taskDiagnostics, setTaskDiagnostics] = useState<Diagnostic[]>([])

  // Validation state
  const [validationErrors, setValidationErrors] = useState<Diagnostic[]>([])
  const [validationWarnings, setValidationWarnings] = useState<Diagnostic[]>([])

  // UI state
  const [loading, setLoading] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string>("")
  const [pasteContent, setPasteContent] = useState("")
  const [isDraggingOver, setIsDraggingOver] = useState(false)

  // ---- Handlers ----

  const handlePlatformSelect = (platformId: string) => {
    setSelectedPlatform(platformId)
    const platform = DATABASE_PLATFORMS.find(p => p.id === platformId)
    if (platform) setSelectedPlatformName(platform.name)
    setUiState("platform-selected")
  }

  const handleOutputFormatSelect = (format: OutputFormat) => {
    setOutputFormat(format)
    setUiState("format-selected")
  }

  const handleStartSession = async () => {
    if (!selectedPlatform) {
      setErrorMessage("Please select a target platform first.")
      setUiState("error")
      return
    }
    setLoading(true)
    setErrorMessage("")
    try {
      const res = await nestedCreateSession({ target_dialect: selectedPlatform, output_format: outputFormat })
      if (!res.success || !res.session_id) {
        setErrorMessage(res.error || "Failed to create session")
        setUiState("error")
        setLoading(false)
        return
      }
      setSessionId(res.session_id)
      setUiState("uploading")
    } catch (e) {
      setErrorMessage(e instanceof Error ? e.message : "Unknown error")
      setUiState("error")
    }
    setLoading(false)
  }

  const handlePasteSubmit = async () => {
    if (!sessionId || !pasteContent.trim()) return
    setLoading(true)
    setErrorMessage("")
    try {
      const res = await nestedAddCv(sessionId, {
        file_content: pasteContent,
        file_name: `nested_cv_${Date.now()}.json`,
      })
      if (!res.success) {
        setErrorMessage(res.error || "Failed to add CV")
        setUiState("error")
        setLoading(false)
        return
      }
      if (res.artifact) {
        setArtifacts(prev => [...prev, res.artifact!])
        setPasteContent("")
        setUiState("resolving")
      }
    } catch (e) {
      setErrorMessage(e instanceof Error ? e.message : "Unknown error")
      setUiState("error")
    }
    setLoading(false)
  }

  const handleUpdateEmissionMode = async (artifactId: string, mode: "inline_cte" | "emit_view") => {
    if (!sessionId) return
    await nestedUpdateCv(sessionId, artifactId, { emission_mode: mode })
    setArtifacts(prev => prev.map(a => a.artifact_id === artifactId ? { ...a, emission_mode: mode } : a))
  }

  const handleUpdateTargetViewName = async (artifactId: string, name: string) => {
    if (!sessionId) return
    await nestedUpdateCv(sessionId, artifactId, { target_view_name: name })
    setArtifacts(prev => prev.map(a => a.artifact_id === artifactId ? { ...a, target_view_name: name } : a))
  }

  const handleRemoveArtifact = async (artifactId: string) => {
    if (!sessionId) return
    await nestedDeleteCv(sessionId, artifactId)
    setArtifacts(prev => prev.filter(a => a.artifact_id !== artifactId))
  }

  const handleValidate = async () => {
    if (!sessionId) return
    setLoading(true)
    setErrorMessage("")
    setUiState("validating")
    try {
      const res = await nestedValidate(sessionId)
      setValidationErrors(res.errors || [])
      setValidationWarnings(res.warnings || [])
      if (res.graph_summary) setGraphSummary(res.graph_summary)
      if (res.valid) {
        setUiState("mapping")
      } else {
        setUiState("error")
      }
    } catch (e) {
      setErrorMessage(e instanceof Error ? e.message : "Validation failed")
      setUiState("error")
    }
    setLoading(false)
  }

  const handleGenerate = async () => {
    if (!sessionId) return
    setLoading(true)
    setErrorMessage("")
    setUiState("generating")
    setTaskProgress(0)
    setTaskMessage("Starting generation...")
    try {
      const res = await nestedGenerate(sessionId)
      if (!res.success || !res.task_id) {
        setErrorMessage(res.error || "Failed to start generation")
        setUiState("error")
        setLoading(false)
        return
      }
      setTaskId(res.task_id)
      pollTask(res.task_id)
    } catch (e) {
      setErrorMessage(e instanceof Error ? e.message : "Generation failed")
      setUiState("error")
    }
    setLoading(false)
  }

  const pollTask = useCallback(async (tid: string) => {
    const interval = setInterval(async () => {
      const status = await nestedGetTaskStatus(tid)
      setTaskProgress(status.progress)
      setTaskMessage(status.message)
      setTaskDiagnostics(status.diagnostics)
      if (status.status === "COMPLETED") {
        clearInterval(interval)
        // Fetch the generated file
        const fileName = outputFormat === "pyspark"
          ? `nested_cv_${tid.slice(0, 8)}.pyspark`
          : `nested_cv_${tid.slice(0, 8)}.sql`
        setGeneratedFileName(fileName)
        setGeneratedContent(`/* Generated at ${new Date().toISOString()} */\n-- Status: COMPLETED`)
        setUiState("display-result")
      } else if (status.status === "FAILED" || status.status === "CANCELLED") {
        clearInterval(interval)
        setErrorMessage(status.message)
        setUiState("error")
      }
    }, 3000)
    return interval
  }, [outputFormat])

  const handleDownload = async () => {
    if (!taskId) return
    await nestedDownloadResult(taskId)
  }

  const handleReset = () => {
    if (!window.confirm("Are you sure you want to reset?")) return
    if (sessionId) nestedDeleteSession(sessionId)
    setSessionId(null)
    setUiState("intro")
    setArtifacts([])
    setLinks([])
    setMappings([])
    setGraphSummary(null)
    setTaskId(null)
    setGeneratedContent(null)
    setGeneratedFileName(null)
    setValidationErrors([])
    setValidationWarnings([])
    setErrorMessage("")
    setSelectedPlatform(null)
    setSelectedPlatformName("")
    setIsFullScreen(false)
    setIsEditorCollapsed(false)
    setIsFileNameEditable(false)
  }

  const handleDrop = async (e: React.DragEvent) => {
    e.preventDefault()
    setIsDraggingOver(false)
    const files = Array.from(e.dataTransfer.files)
    if (files.length === 0) return
    const file = files[0]
    try {
      const text = await file.text()
      setPasteContent(text)
    } catch {
      setErrorMessage("Failed to read file content")
      setUiState("error")
    }
  }

  // ---- Render helpers ----

  const getButtonContent = () => {
    switch (uiState) {
      case "validating":
        return (
          <div className="relative overflow-hidden w-full">
            <div className="flex items-center justify-center relative z-10 py-2">
              <Loader2 className="animate-spin mr-2 h-5 w-5 text-primary" />
              <span>Validating</span>
              <span className="animate-pulse">...</span>
            </div>
            <div className="absolute inset-0 bg-secondary/20">
              <div className="h-full bg-secondary/40 animate-progress-indeterminate" />
            </div>
          </div>
        )
      case "generating":
        return (
          <div className="relative overflow-hidden w-full">
            <div className="flex items-center justify-center relative z-10 py-2">
              <Loader2 className="animate-spin mr-2 h-5 w-5 text-primary" />
              <span>Generating</span>
              <span className="animate-pulse">...</span>
            </div>
            <div className="absolute inset-0 bg-secondary/20">
              <div className="h-full bg-secondary/40 animate-progress-indeterminate" />
            </div>
          </div>
        )
      case "display-result":
        return (
          <div className="relative overflow-hidden w-full">
            <div className="flex items-center justify-center relative z-10 py-2">
              <CheckCircle className="mr-2 h-5 w-5 text-green-600" />
              <span className="text-primary font-semibold">Merged CV Generated!</span>
            </div>
            <div className="absolute inset-0 bg-gradient-to-r from-secondary/20 via-secondary/40 to-secondary/20 animate-gradient-x" />
          </div>
        )
      case "error":
        return (
          <div className="relative overflow-hidden w-full">
            <div className="flex items-center justify-center relative z-10 py-2">
              <AlertCircle className="mr-2 h-5 w-5 text-red-500" />
              <span>Error — See Below</span>
            </div>
          </div>
        )
      default:
        return (
          <div className="flex items-center justify-center">
            <span>Process</span>
          </div>
        )
    }
  }

  const renderPlatformSelection = () => (
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
                : "border-gray-200 hover:border-secondary/50 hover:bg-gray-50 dark:border-gray-700 dark:hover:bg-gray-800"
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
              <p className="text-xs sm:text-sm text-gray-500 dark:text-gray-400">{platform.description}</p>
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
    </div>
  )

  const renderFormatSelection = () => (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4 }}
      className="mt-8"
    >
      <h2 className="text-xl font-semibold text-primary mb-2">Choose Output Format</h2>
      <p className="text-gray-600 mb-6">Select how you want the merged code to be delivered:</p>
      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div
          onClick={() => handleOutputFormatSelect("sql")}
          className={cn(
            "flex flex-col items-center p-4 sm:p-6 border-2 rounded-xl cursor-pointer transition-all min-h-[140px] sm:min-h-[160px]",
            outputFormat === "sql"
              ? "border-secondary bg-secondary/5 shadow-md"
              : "border-gray-200 hover:border-secondary/50 hover:bg-gray-50 dark:border-gray-700 dark:hover:bg-gray-800"
          )}
        >
          <div className="w-12 h-12 sm:w-16 sm:h-16 rounded-full bg-blue-100 flex items-center justify-center mb-3 sm:mb-4">
            <svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#2563eb" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M4 6h16" /><path d="M4 12h16" /><path d="M4 18h12" />
            </svg>
          </div>
          <h3 className="font-semibold text-primary text-base sm:text-lg mb-1">SQL</h3>
          <p className="text-xs sm:text-sm text-gray-500 text-center">Generate standard SQL — download as .sql file</p>
        </div>
        <div
          onClick={() => handleOutputFormatSelect("pyspark")}
          className={cn(
            "flex flex-col items-center p-4 sm:p-6 border-2 rounded-xl cursor-pointer transition-all min-h-[140px] sm:min-h-[160px]",
            outputFormat === "pyspark"
              ? "border-secondary bg-secondary/5 shadow-md"
              : "border-gray-200 hover:border-secondary/50 hover:bg-gray-50 dark:border-gray-700 dark:hover:bg-gray-800"
          )}
        >
          <div className="w-12 h-12 sm:w-16 sm:h-16 rounded-full bg-orange-100 flex items-center justify-center mb-3 sm:mb-4">
            <svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#ea580c" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 2L2 7l10 5 10-5-10-5z" /><path d="M2 17l10 5 10-5" /><path d="M2 12l10 5 10-5" />
            </svg>
          </div>
          <h3 className="font-semibold text-primary text-base sm:text-lg mb-1">PySpark</h3>
          <p className="text-xs sm:text-sm text-gray-500 text-center">Generate PySpark DataFrame API — download as .ipynb notebook</p>
        </div>
      </div>

      <button
        onClick={handleStartSession}
        disabled={loading}
        className="mt-6 w-full py-3 bg-secondary text-primary rounded-lg font-semibold hover:bg-secondary/90 transition-all disabled:opacity-50 flex items-center justify-center gap-2"
      >
        {loading ? <Loader2 className="w-5 h-5 animate-spin" /> : <GitMerge className="w-5 h-5" />}
        Start Nested CV Session
      </button>
    </motion.div>
  )

  const renderUploadArea = () => (
    <div className="flex flex-col items-center">
      <motion.div
        initial={{ opacity: 0, scale: 0.95 }}
        animate={{ opacity: 1, scale: 1 }}
        className={cn(
          "border-2 border-dashed rounded-lg p-6 relative transition-all duration-200 w-full mb-6 cursor-pointer",
          isDraggingOver
            ? "border-secondary bg-secondary/5 shadow-md"
            : "border-gray-300 hover:border-secondary/50 dark:border-gray-600 dark:hover:border-secondary/50",
          !pasteContent && "animate-file-upload-pulse"
        )}
        onDragOver={(e) => { e.preventDefault(); setIsDraggingOver(true) }}
        onDragLeave={() => setIsDraggingOver(false)}
        onDrop={handleDrop}
        onClick={() => {
          const input = document.getElementById("nested-paste-input") as HTMLTextAreaElement
          input?.focus()
        }}
      >
        <textarea
          id="nested-paste-input"
          value={pasteContent}
          onChange={e => setPasteContent(e.target.value)}
          placeholder={`Paste your nested CV mapping file content here...\n\nYou can also drag & drop a JSON file here.\n\nExample:\n{\n  "cv_name": "MY_CV",\n  "dependencies": [...],\n  "output_schema": [...]\n}`}
          className="w-full h-48 font-mono text-sm resize-none bg-transparent border-none outline-none text-gray-700 dark:text-gray-200 placeholder-gray-400 cursor-text"
        />
      </motion.div>

      <button
        onClick={handlePasteSubmit}
        disabled={loading || !pasteContent.trim()}
        className="w-full py-3 bg-secondary text-primary rounded-lg font-semibold hover:bg-secondary/90 transition-all disabled:opacity-50 flex items-center justify-center gap-2 mb-4"
      >
        {loading ? <Loader2 className="w-5 h-5 animate-spin" /> : <Upload className="w-5 h-5" />}
        Add CV to Session
      </button>

      {/* Or divider */}
      <div className="text-center text-sm text-gray-600 dark:text-gray-400 mb-4">— or —</div>

      <button
        className="flex items-center gap-2 px-4 py-2 text-sm font-medium text-gray-700 dark:text-gray-200 bg-gray-100 dark:bg-gray-800 rounded-md hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors"
      >
        <History className="w-4 h-4" />
        Select from Previous Sessions
      </button>
    </div>
  )

  const renderCVList = () => (
    <div className="space-y-3">
      {artifacts.map(artifact => (
        <div key={artifact.artifact_id} className="border border-gray-200 dark:border-gray-700 rounded-lg p-4">
          <div className="flex items-start justify-between">
            <div className="flex-1">
              <p className="font-semibold text-primary">{artifact.cv_display_name}</p>
              <p className="text-sm text-gray-500 dark:text-gray-400">
                v{artifact.format_version} • {artifact.dependencies.length} dependencies
                {artifact.output_schema.length > 0 && ` • ${artifact.output_schema.length} output columns`}
              </p>
              {artifact.warnings.length > 0 && (
                <div className="mt-2 flex flex-wrap gap-2">
                  {artifact.warnings.map((w, i) => (
                    <span key={i} className="px-2 py-0.5 bg-yellow-50 dark:bg-yellow-900/30 text-yellow-700 dark:text-yellow-400 text-xs rounded-full">
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
                className="text-sm border border-gray-300 dark:border-gray-600 rounded-lg px-2 py-1 bg-white dark:bg-gray-800 text-primary"
              >
                <option value="inline_cte">Inline as CTE</option>
                <option value="emit_view">Emit as View</option>
              </select>
              <input
                type="text"
                value={artifact.target_view_name}
                onChange={e => handleUpdateTargetViewName(artifact.artifact_id, e.target.value)}
                placeholder="target_view"
                className="text-sm border border-gray-300 dark:border-gray-600 rounded-lg px-2 py-1 w-36 bg-white dark:bg-gray-800 text-primary"
              />
              <button
                onClick={() => handleRemoveArtifact(artifact.artifact_id)}
                className="p-1.5 text-red-500 hover:bg-red-50 dark:hover:bg-red-900/20 rounded-lg"
              >
                <Trash2 className="w-4 h-4" />
              </button>
            </div>
          </div>
          {artifact.output_schema.length > 0 && (
            <div className="mt-3 pt-3 border-t border-gray-100 dark:border-gray-700">
              <p className="text-xs font-medium text-gray-500 dark:text-gray-400 mb-2">Output Columns</p>
              <div className="flex flex-wrap gap-1.5">
                {artifact.output_schema.map(col => (
                  <span key={col.ordinal} className="px-2 py-0.5 bg-gray-100 dark:bg-gray-800 text-gray-700 dark:text-gray-300 text-xs rounded">
                    {col.column_name}
                    {col.data_type && <span className="text-gray-400 ml-1">{col.data_type}</span>}
                  </span>
                ))}
              </div>
            </div>
          )}
        </div>
      ))}

      <button
        onClick={handleValidate}
        disabled={loading || artifacts.length === 0}
        className="w-full py-2.5 bg-secondary text-primary rounded-lg font-semibold hover:bg-secondary/90 transition-all disabled:opacity-50 flex items-center justify-center gap-2"
      >
        {loading ? <Loader2 className="w-5 h-5 animate-spin" /> : <CheckCircle className="w-5 h-5" />}
        Validate & Continue
      </button>
    </div>
  )

  const renderErrorMessage = () => {
    if (uiState !== "error") return null
    return (
      <div className="mt-6 p-4 bg-red-50 dark:bg-red-900/20 border border-red-200 dark:border-red-800 rounded-lg">
        <div className="flex items-start">
          <AlertCircle className="h-5 w-5 text-red-500 mt-0.5 mr-2 flex-shrink-0" />
          <div>
            <h3 className="font-medium text-red-800 dark:text-red-200 mb-1">Processing Error</h3>
            <p className="text-sm text-red-700 dark:text-red-300">{errorMessage}</p>
          </div>
        </div>
        {validationErrors.length > 0 && (
          <div className="mt-3 space-y-1">
            {validationErrors.map((e, i) => (
              <p key={i} className="text-xs text-red-600 dark:text-red-400">
                [{e.code}] {e.message}
              </p>
            ))}
          </div>
        )}
      </div>
    )
  }

  const renderDisplayResult = () => (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5 }}
      className={cn(
        "bg-gray-50 dark:bg-gray-800 rounded-lg shadow-inner",
        !isFullScreen && "mt-6 p-4 sm:p-6",
        isFullScreen && "fixed inset-0 z-[9999] bg-white dark:bg-gray-900 flex flex-col p-0"
      )}
    >
      {/* Result header */}
      <div className="flex flex-col sm:flex-row sm:items-center mb-4 flex-shrink-0 gap-2">
        <h2 className="text-xl font-semibold text-primary mr-2">
          {outputFormat === "pyspark" ? "Generated PySpark Notebook:" : "Generated SQL:"}
        </h2>
        <input
          type="text"
          value={generatedFileName ? generatedFileName.replace(/\.(sql|ipynb)$/i, '') : ''}
          onChange={(e) => {
            const ext = outputFormat === "pyspark" ? ".ipynb" : ".sql"
            setGeneratedFileName(e.target.value + ext)
          }}
          readOnly={!isFileNameEditable}
          className={cn(
            "flex-grow p-2 border rounded-md text-primary bg-white dark:bg-gray-800",
            isFileNameEditable ? "border-secondary ring-2 ring-secondary/50" : "border-gray-200 dark:border-gray-600"
          )}
        />
        <button
          onClick={() => setIsFileNameEditable(!isFileNameEditable)}
          className="flex items-center px-3 py-2 rounded-md text-sm font-medium transition-colors bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 text-gray-700 dark:text-gray-200"
        >
          {isFileNameEditable ? <Save className="w-4 h-4 mr-1" /> : <Pencil className="w-4 h-4 mr-1" />}
          {isFileNameEditable ? "Save" : "Rename"}
        </button>
        <button
          onClick={() => setIsEditorCollapsed(!isEditorCollapsed)}
          className="p-2 rounded-md bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors"
        >
          {isEditorCollapsed ? (
            <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m17 11-5-5-5"/><path d="m17 18-5-5-5"/><path d="m7 6 5 5 5-5"/><path d="m7 13 5 5 5-5"/></svg>
          ) : (
            <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="m18 15-5-5 5-5"/><path d="m18 9-5-5 5-5"/><path d="m7 18 5 5-5 5"/><path d="m7 9 5 5-5 5"/></svg>
          )}
        </button>
        <button
          onClick={() => setIsFullScreen(!isFullScreen)}
          className="p-2 rounded-md bg-gray-100 dark:bg-gray-700 hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors"
        >
          {isFullScreen ? (
            <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M8 3v3a2 2 0 0 1-2 2H3m18 0h-3a2 2 0 0 1-2-2V3m0 18v-3a2 2 0 0 1 2-2h3M3 16h3a2 2 0 0 1 2 2v3"/></svg>
          ) : (
            <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3M3 16v3a2 2 0 0 0 2 2h3"/></svg>
          )}
        </button>
      </div>

      {/* Code editor area */}
      <div className={cn(
        "w-full mb-4 border rounded-md overflow-hidden",
        isFullScreen ? "flex-grow overflow-y-auto" : isEditorCollapsed ? "h-16" : "h-[400px]"
      )}>
        <SqlEditor
          value={generatedContent || ""}
          onChange={setGeneratedContent}
          editorHeight={isFullScreen ? "100%" : isEditorCollapsed ? "20px" : "340px"}
          isCollapsed={isEditorCollapsed}
        />
      </div>

      {/* Download button */}
      <button
        onClick={handleDownload}
        className="w-full py-3 rounded-lg font-medium text-sm bg-secondary text-primary hover:bg-secondary/90 transition-colors flex items-center justify-center gap-2 flex-shrink-0"
      >
        <Download className="w-5 h-5" />
        {outputFormat === "pyspark" ? "Download PySpark Notebook (.ipynb)" : "Download SQL File (.sql)"}
      </button>
    </motion.div>
  )

  // ---- Main render ----
  return (
    <div className="max-w-6xl mx-auto bg-white dark:bg-gray-800 shadow-lg rounded-lg p-4 sm:p-8">

      {/* Header */}
      <div className="flex flex-col sm:flex-row sm:justify-between sm:items-center gap-2 sm:gap-4 mb-6">
        <div className="flex flex-col sm:flex-row sm:items-center gap-1 sm:gap-4">
          <h1 className="text-xl sm:text-2xl md:text-3xl font-bold text-primary">Nested CV Flattener</h1>
          <Link
            href="/how-to-use#nested-cv"
            className="inline-flex items-center px-3 py-1.5 text-xs sm:text-sm font-medium text-primary bg-secondary/10 rounded-full hover:bg-secondary/20 transition-colors self-start"
          >
            <Lightbulb className="w-3 h-3 sm:w-4 sm:h-4 mr-1 text-secondary" />
            How to Use
          </Link>
        </div>
        {sessionId && (
          <button
            onClick={handleReset}
            className="flex items-center px-3 py-2 text-xs sm:text-sm font-medium text-gray-700 dark:text-gray-200 bg-gray-100 dark:bg-gray-700 rounded-md hover:bg-gray-200 dark:hover:bg-gray-600 transition-colors self-start"
          >
            <RotateCcw className="w-3 h-3 sm:w-4 sm:h-4 mr-1 sm:mr-2" />
            Reset
          </button>
        )}
      </div>

      {/* Phase 1: Platform selection */}
      {uiState === "intro" && renderPlatformSelection()}

      {/* Phase 2: Format selection */}
      {uiState === "platform-selected" && (
        <>
          <div className="mt-6 p-3 bg-secondary/5 border border-secondary/20 rounded-lg">
            <div className="flex items-center gap-2">
              <CheckCircle className="w-4 h-4 text-secondary" />
              <span className="text-sm text-primary font-medium">Platform: {selectedPlatformName}</span>
            </div>
          </div>
          {renderFormatSelection()}
        </>
      )}

      {/* Phase 3: Upload */}
      {(uiState === "format-selected" || uiState === "uploading" || uiState === "resolving") && (
        <div className="mt-6 space-y-6">
          <div className="p-3 bg-secondary/5 border border-secondary/20 rounded-lg flex items-center gap-2">
            <CheckCircle className="w-4 h-4 text-secondary" />
            <span className="text-sm text-primary font-medium">
              {selectedPlatformName} • {outputFormat === "sql" ? "SQL output" : "PySpark output"}
            </span>
          </div>

          {artifacts.length === 0 && renderUploadArea()}

          {artifacts.length > 0 && (
            <div>
              <div className="flex items-center justify-between mb-3">
                <h3 className="text-lg font-semibold text-primary">Added Calculation Views ({artifacts.length})</h3>
                <button
                  onClick={() => setUiState("uploading")}
                  className="text-sm text-secondary hover:underline"
                >
                  + Add More
                </button>
              </div>
              {renderCVList()}
            </div>
          )}

          {artifacts.length === 0 && uiState !== "uploading" && (
            <div className="text-center py-4">
              <button
                onClick={() => setUiState("intro")}
                className="text-sm text-gray-500 hover:text-gray-700 dark:text-gray-400"
              >
                ← Change Platform
              </button>
            </div>
          )}
        </div>
      )}

      {/* Error message */}
      {renderErrorMessage()}

      {/* Generating progress */}
      {uiState === "generating" && (
        <div className="mt-6 text-center py-8">
          <Loader2 className="w-12 h-12 mx-auto mb-4 text-secondary animate-spin" />
          <h3 className="text-xl font-semibold text-primary mb-2">Generating Merged Output...</h3>
          <p className="text-gray-500 mb-4">{taskMessage}</p>
          <div className="w-full max-w-md mx-auto bg-gray-100 dark:bg-gray-700 rounded-full h-2.5 mb-4">
            <div
              className="bg-secondary h-2.5 rounded-full transition-all"
              style={{ width: `${taskProgress}%` }}
            />
          </div>
          <p className="text-sm text-gray-500">{taskProgress}% complete</p>
          {taskDiagnostics.filter(d => d.level === "error").length > 0 && (
            <div className="mt-4 text-left max-w-md mx-auto">
              {taskDiagnostics.filter(d => d.level === "error").map((d, i) => (
                <p key={i} className="text-xs text-red-600 dark:text-red-400">[{d.code}] {d.message}</p>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Display result */}
      {uiState === "display-result" && renderDisplayResult()}

      {/* Process button — shown when there's a state that needs it */}
      {(uiState === "uploading" || uiState === "resolving" || uiState === "generating" || uiState === "display-result" || uiState === "error") && (
        <button
          disabled
          className={cn(
            "w-full py-3 sm:py-4 rounded-md font-medium text-sm sm:text-base transition-all relative overflow-hidden mt-6",
            uiState === "error" || uiState === "uploading"
              ? "bg-gray-50 text-gray-700 cursor-not-allowed"
              : "bg-secondary text-primary"
          )}
        >
          {getButtonContent()}
        </button>
      )}

      <style>{`
        @keyframes fadeIn {
          from { opacity: 0; transform: translateY(10px); }
          to { opacity: 1; transform: translateY(0); }
        }
        @keyframes pulseBorder {
          0% { border-color: #d1d5db; }
          50% { border-color: #a7a7a7; }
          100% { border-color: #d1d5db; }
        }
        .animate-file-upload-pulse {
          animation: pulseBorder 2s infinite ease-in-out;
        }
        @keyframes gradientX {
          0%, 100% { background-position: 0% 50%; }
          50% { background-position: 100% 50%; }
        }
        .animate-gradient-x {
          animation: gradientX 3s ease infinite;
          background-size: 200% 100%;
        }
      `}</style>
    </div>
  )
}
