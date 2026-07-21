"use client"

import React, { useEffect, useState } from "react"
import { motion } from "framer-motion" // Added motion import

import {
  Upload,
  Loader2,
  RotateCcw,
  Lightbulb,
  CheckCircle,
  ArrowRight,
  FileSpreadsheet,
  AlertCircle,
  Check,
  History, // Added History icon
  Pencil, // Added Pencil icon for rename
  Save, // Added Save icon for saving rename
} from "lucide-react"
import { cn } from "../lib/utils"
import FileUploadError from "./FileUploadError"
import Link from "next/link"
import Image from "next/image"
import MappingEditorPopup from "./MappingEditorPopup"
import SqlEditor from "./CodeEditor" // Import SqlEditor (renamed from CodeEditor)
import NotebookRenderer from "./NotebookRenderer"
import { Button } from "@/components/ui/button" // Import Button component
import ConversionHistoryModal from "./ConversionHistoryModal" // Added import
import { useAuth } from "@/contexts/AuthContext"
import { useRouter } from "next/navigation"
import { processXlsxFileForMapping, applyMappingChanges } from "@/lib/api"
// Removed JSZip as it's no longer needed for XLSX processing

type ProcessingState =
  | "idle"
  | "validating"
  | "processing"
  | "success"
  | "target-selection"
  | "file-upload"
  | "mapping-editor"
  | "output-format-selection" // New state: choose SQL or PySpark
  | "display-sql" // New state for displaying SQL
  | "error"

interface DatabasePlatform {
  id: string
  name: string
  logo: string
  description: string
}

interface MappingRow {
  sourceTable: string
  sourceField: string
  targetTable: string
  targetField: string
}

interface XlsxContents { // Renamed from ZipContents
  sqlFiles: Record<string, string> // Still keep this structure for consistency, though SQL is directly from XLSX
  mappingFileContent: MappingRow[]
  textFileName: string
  hasMappingFile: boolean
}

export default function MappingTool() {
  const { user } = useAuth()
  const router = useRouter()
  const [xlsxFile, setXlsxFile] = useState<File | null>(null) // Changed from zipFile to xlsxFile
  const [processingState, setProcessingState] = useState<ProcessingState>("target-selection")
  const [errorMessage, setErrorMessage] = useState<string>("")
  const [selectedPlatform, setSelectedPlatform] = useState<string | null>(null)
  const [selectedPlatformName, setSelectedPlatformName] = useState<string>("")
  const [showMappingEditor, setShowMappingEditor] = useState(false)
  const [showHistoryModal, setShowHistoryModal] = useState(false) // Added state for history modal
  const [mappings, setMappings] = useState<MappingRow[]>([])
  const [sqlContent, setSqlContent] = useState<string>("") // This is for sqlInfo preview, not generated SQL
  const [generatedSqlContent, setGeneratedSqlContent] = useState<string | null>(null) // New state for generated SQL
  const [generatedSqlFileName, setGeneratedSqlFileName] = useState<string | null>(null) // New state for generated SQL filename
  const [isFileNameEditable, setIsFileNameEditable] = useState(false); // New state for file name editability
  const [xlsxContents, setXlsxContents] = useState<XlsxContents>({ // Changed from zipContents to xlsxContents
    sqlFiles: {},
    mappingFileContent: [],
    textFileName: "",
    hasMappingFile: false,
  })
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [isDraggingOver, setIsDraggingOver] = useState(false)
  const [isFullScreen, setIsFullScreen] = useState(false) // State for full screen
  const [isEditorCollapsed, setIsEditorCollapsed] = useState(false) // New state for collapsing the editor
  const [cteSqlContent, setCteSqlContent] = useState<string | null>(null) // State for CTE version SQL
  const [tempTableSqlContent, setTempTableSqlContent] = useState<string | null>(null) // State for CTE + Temp Tables version SQL
  const [activeSqlTab, setActiveSqlTab] = useState<"cte" | "tempTable">("cte") // State for active SQL tab, default to "cte"
  const [outputFormat, setOutputFormat] = useState<"sql" | "pyspark">("sql") // SQL or PySpark output
  const [pysparkNotebookContent, setPysparkNotebookContent] = useState<string | null>(null) // PySpark notebook JSON
  const [pendingMappings, setPendingMappings] = useState<{ updatedMappings: MappingRow[], processedSql: string, fileName: string } | null>(null) // Pending mappings for output format selection

  // Effect to manage body overflow when in full screen
  React.useEffect(() => {
    if (isFullScreen) {
      document.body.style.overflow = "hidden";
    } else {
      document.body.style.overflow = "";
    }
    return () => {
      document.body.style.overflow = ""; // Clean up on unmount
    };
  }, [isFullScreen]);

  // Database platforms data (unchanged)
  const databasePlatforms: DatabasePlatform[] = [
    {
      id: "bigquery",
      name: "Google BigQuery",
      logo: "/google-bigquery-logo.png",
      description: "Transform your queries for Google’s serverless data warehouse",
    },
    {
      id: "azure",
      name: "Microsoft Fabric (PySpark Available)",
      logo: "/fabric.png",
      description: "Adapt your SQL for Microsoft Fabric",
    },
    {
      id: "redshift",
      name: "Amazon Redshift",
      logo: "/amazon-redshift-logo.png",
      description: "Convert your queries to Amazon’s cloud data warehouse format",
    },
    {
      id: "snowflake",
      name: "Snowflake Cloud Data Platform",
      logo: "/snowflake-logo.png",
      description: "Map your SQL to Snowflake’s cloud data platform",
    },
    {
      id: "databricks",
      name: "Databricks Lakehouse Platform (PySpark Available)",
      logo: "/databricks_logo.png",
      description: "Convert your queries for Databricks Lakehouse Platform",
    },
    {
      id: "datasphere",
      name: "SAP Datasphere  (SQL View - Table Function)",
      logo: "/sap-datasphere-logo.png",
      description: "Optimize your SQL for SAP DataSphere's unique capabilities",
    },
  ]

  const validateFileType = (file: File): boolean => {
    return file.name.toLowerCase().endsWith(".xlsx") || file.name.toLowerCase().endsWith(".xls") // Allow .xlsx and .xls
  }

  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      const file = e.target.files[0]
      if (!validateFileType(file)) {
        setErrorMessage("Only .xlsx or .xls files are allowed") // Updated error message
        e.target.value = ""
        return
      }
      setXlsxFile(file) // Changed to setXlsxFile
    }
  }

  const handleDrop = async (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    e.stopPropagation()
    setIsDraggingOver(false) // Reset drag state

    const files = Array.from(e.dataTransfer.files)
    if (files.length > 1) {
      setErrorMessage("Please upload only one file at a time")
      return
    }

    const file = files[0]
    if (!validateFileType(file)) {
      setErrorMessage("Only .xlsx or .xls files are allowed") // Updated error message
      return
    }

    setXlsxFile(file) // Changed to setXlsxFile
  }

  const handleDragOver = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    e.stopPropagation()
    setIsDraggingOver(true) // Set drag state to true
  }

  const handleDragLeave = (e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault()
    e.stopPropagation()
    setIsDraggingOver(false) // Reset drag state
  }

  const handleProcess = async () => {
    if (!xlsxFile || !selectedPlatform) { // Changed from zipFile to xlsxFile
      setErrorMessage("Please select a platform and upload an XLSX file.") // Updated message
      setProcessingState("error")
      return
    }

    try {
      setProcessingState("validating")

      // Call the backend API to process the XLSX file
      const result = await processXlsxFileForMapping(xlsxFile, selectedPlatform) // Changed to processMappingXLSX

      if (result.success && result.mappingSchema) {
        // The backend returns sqlInfo as a list of dicts, convert to string for display
        setSqlContent(JSON.stringify(result.mappingSchema.sqlInfo, null, 2))
        setMappings(result.mappingSchema.mappingFileContent || [])
        const originalFileName = result.mappingSchema.fileName || "";
        const cleanedFileName = originalFileName.replace(/\.(xlsx|xls)$/i, '');

        // console.log("Original filename from backend:", originalFileName);
        // console.log("Cleaned filename (extension removed):", cleanedFileName);

        setXlsxContents({
          sqlFiles: {}, // Backend doesn't return sqlFiles directly here, so keep empty or adjust if needed
          mappingFileContent: result.mappingSchema.mappingFileContent || [],
          textFileName: cleanedFileName,
          hasMappingFile: (result.mappingSchema.mappingFileContent || []).length > 0,
        })
        setSessionId(result.mappingSchema.sessionId || null)

        // Simulate processing delay
        await new Promise((resolve) => setTimeout(resolve, 1000))

        // Open the mapping editor
        setProcessingState("mapping-editor")
        setShowMappingEditor(true)
      } else {
        setErrorMessage(result.error || "Failed to process XLSX file for mapping.") // Updated message
        setProcessingState("error")
      }
    } catch (error) {
      console.error("Error processing XLSX file:", error) // Updated message
      setErrorMessage(error instanceof Error ? error.message : "Upload correct .xlsx or .xls file") // Updated message
      setProcessingState("error")
    }
  }

  const handlePlatformSelect = (platformId: string) => {
    setSelectedPlatform(platformId)

    const platform = databasePlatforms.find((p) => p.id === platformId)
    if (platform) {
      setSelectedPlatformName(platform.name)
    }

    setProcessingState("file-upload")
  }

  const handleSaveMappings = async (updatedMappings: MappingRow[], processedSql: string, fileName: string) => {
    setMappings(updatedMappings)

    // For Databricks and Azure (MS Fabric), show output format selection
    if (selectedPlatform === "databricks" || selectedPlatform === "azure") {
      setPendingMappings({ updatedMappings, processedSql, fileName })
      setShowMappingEditor(false)
      setProcessingState("output-format-selection")
      return
    }

    // For other platforms, proceed directly with SQL generation
    await executeGeneration(updatedMappings, processedSql, fileName, "sql")
  }

  const handleOutputFormatSelect = async (format: "sql" | "pyspark") => {
    setOutputFormat(format)
    if (pendingMappings) {
      await executeGeneration(
        pendingMappings.updatedMappings,
        pendingMappings.processedSql,
        pendingMappings.fileName,
        format
      )
      setPendingMappings(null)
    }
  }

  const executeGeneration = async (updatedMappings: MappingRow[], processedSql: string, fileName: string, format: "sql" | "pyspark") => {
    setProcessingState("processing")

    try {
      if (!sessionId) {
        setErrorMessage("Session ID is missing. Please re-upload the file.");
        setProcessingState("error");
        return;
      }
      if (!updatedMappings || updatedMappings.length === 0) {
        console.error("MappingTool: updatedMappings is empty or null. Cannot proceed with save.");
        setErrorMessage("Mapping data is empty. Please ensure your XLSX has valid mapping info or add sample mappings.");
        setProcessingState("error");
        return;
      }

      console.log("MappingTool: Calling applyMappingChanges with:", { updatedMappings, fileName, selectedPlatform, sessionId, format });
      const result = await applyMappingChanges(updatedMappings, fileName, selectedPlatform!, sessionId, format);

      if (format === "pyspark" && result.success && result.pysparkNotebookContent && result.fileName) {
        setPysparkNotebookContent(result.pysparkNotebookContent);
        setCteSqlContent(null);
        setTempTableSqlContent(null);
        setGeneratedSqlFileName(result.fileName);
        setShowMappingEditor(false);
        setProcessingState("display-sql");
      } else if (result.success && result.cteSqlContent !== undefined && result.tempTableSqlContent !== undefined && result.fileName) {
        setCteSqlContent(result.cteSqlContent);
        setTempTableSqlContent(result.tempTableSqlContent);
        setPysparkNotebookContent(null);
        setGeneratedSqlFileName(result.fileName);
        setShowMappingEditor(false);
        setProcessingState("display-sql");
      } else {
        console.error("MappingTool: Condition for success not met.");
        setErrorMessage(result.error || "Failed to apply mapping changes. Missing expected content or filename.");
        setProcessingState("error");
      }
    } catch (error) {
      console.error("Error applying mapping changes:", error);
      setErrorMessage(error instanceof Error ? error.message : "Failed to apply mapping changes.");
      setProcessingState("error");
    }
  }

  const handleDownloadGeneratedSql = () => {
    const fileNameToDownload = generatedSqlFileName;

    // PySpark notebook download
    if (outputFormat === "pyspark" && pysparkNotebookContent && fileNameToDownload) {
      const blob = new Blob([pysparkNotebookContent], { type: "application/json" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = fileNameToDownload;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
      return;
    }

    // SQL download (existing)
    const contentToDownload = activeSqlTab === "cte" ? cteSqlContent : tempTableSqlContent;
    if (contentToDownload && fileNameToDownload) {
      const blob = new Blob([contentToDownload], { type: "text/plain" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = fileNameToDownload;
      document.body.appendChild(a);
      a.click();
      document.body.removeChild(a);
      URL.revokeObjectURL(url);
    }
  };

  const handleCloseMappingEditor = () => {
    setShowMappingEditor(false)
    if (processingState === "mapping-editor") {
      setProcessingState("target-selection")
    }
  }

  const handleHistoryFileSelect = (file: File) => {
    setXlsxFile(file)
    // Optional: You might want to reset platform selection or keep it if already selected
    // For now, we just set the file and ensure generic state is ready for platform selection or next step
    if (!selectedPlatform) {
      setProcessingState("target-selection")
    } else {
      setProcessingState("file-upload")
    }
  }

  const handleReset = () => {
    if (window.confirm("Are you sure you want to reset?")) {
      setXlsxFile(null)
      setProcessingState("target-selection")
      setSelectedPlatform(null)
      setShowMappingEditor(false)
      setSqlContent("")
      setCteSqlContent(null)
      setTempTableSqlContent(null)
      setPysparkNotebookContent(null)
      setGeneratedSqlFileName(null)
      setActiveSqlTab("cte")
      setOutputFormat("sql")
      setPendingMappings(null)
      setMappings([])
      setErrorMessage("")
      setXlsxContents({
        sqlFiles: {},
        mappingFileContent: [],
        textFileName: "",
        hasMappingFile: false,
      })
      setSessionId(null)
    }
  }

  const getButtonContent = () => {
    switch (processingState) {
      case "success":
        return (
          <div className="relative overflow-hidden w-full">
            <div className="flex items-center justify-center relative z-10 py-2">
              <CheckCircle className="mr-2 h-5 w-5 text-green-600" />
              <span className="text-primary font-semibold">SQL Mapping Complete!</span>
            </div>
            <div className="absolute inset-0 bg-gradient-to-r from-secondary/20 via-secondary/40 to-secondary/20 animate-gradient-x" />
          </div>
        )
      case "display-sql": // New case for displaying SQL
        return (
          <div className="relative overflow-hidden w-full">
            <div className="flex items-center justify-center relative z-10 py-2">
              <CheckCircle className="mr-2 h-5 w-5 text-green-600" />
              <span className="text-primary font-semibold">
                {outputFormat === "pyspark" ? "PySpark Notebook Generated!" : "SQL Generated!"}
              </span>
            </div>
            <div className="absolute inset-0 bg-gradient-to-r from-secondary/20 via-secondary/40 to-secondary/20 animate-gradient-x" />
          </div>
        )
      case "validating":
        return (
          <div className="relative overflow-hidden">
            <div className="flex items-center justify-center relative z-10">
              <Loader2 className="animate-spin mr-2 h-5 w-5 text-primary" />
              <span>Validating</span>
              <span className="animate-pulse">...</span>
            </div>
            <div className="absolute inset-0 bg-secondary/20">
              <div className="h-full bg-secondary/40 animate-progress-indeterminate" />
            </div>
          </div>
        )
      case "processing":
        return (
          <div className="relative overflow-hidden">
            <div className="flex items-center justify-center relative z-10">
              <Loader2 className="animate-spin mr-2 h-5 w-5 text-primary" />
              <span>Applying Changes</span>
              <span className="animate-pulse">...</span>
            </div>
            <div className="absolute inset-0 bg-secondary/20">
              <div className="h-full bg-secondary/40 animate-progress-indeterminate" />
            </div>
          </div>
        )
      case "mapping-editor":
        return (
          <div className="relative overflow-hidden">
            <div className="flex items-center justify-center relative z-10">
              <FileSpreadsheet className="mr-2 h-5 w-5 text-primary" />
              <span>Editing Mapping File</span>
            </div>
          </div>
        )
      case "error":
        return (
          <div className="relative overflow-hidden">
            <div className="flex items-center justify-center relative z-10">
              <AlertCircle className="mr-2 h-5 w-5 text-red-500" />
              <span>Error Processing Files</span>
            </div>
          </div>
        )
      case "output-format-selection":
        return (
          <div className="relative overflow-hidden w-full">
            <div className="flex items-center justify-center relative z-10 py-2">
              <FileSpreadsheet className="mr-2 h-5 w-5 text-primary" />
              <span className="text-primary font-semibold">Choose Output Format</span>
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

  const renderTargetSelectionScreen = () => {
    return (
      <div className="mt-8">
        <h2 className="text-xl font-semibold text-primary mb-4">Select Target Platform</h2>
        <p className="text-gray-600 mb-6">Choose the database platform you want to map your SQL to:</p>

        <div className="grid grid-cols-1 gap-3 sm:gap-4">
          {databasePlatforms.map((platform) => (
            <div
              key={platform.id}
              onClick={() => handlePlatformSelect(platform.id)}
              className={`
        flex items-center p-3 sm:p-4 border rounded-lg cursor-pointer transition-all min-h-[72px] sm:min-h-[80px]
        ${selectedPlatform === platform.id
                  ? "border-secondary bg-secondary/5 shadow-md"
                  : "border-gray-200 hover:border-secondary/50 hover:bg-gray-50"
                }
      `}
            >
              <div className="relative w-16 h-8 sm:w-24 sm:h-12 flex-shrink-0 mr-3 sm:mr-4">
                <Image
                  src={platform.logo || "/placeholder.svg"}
                  alt={platform.name}
                  fill
                  className="object-contain"
                  priority
                  onError={(e) => {
                    console.error(`Failed to load image: ${platform.logo}`)
                    e.currentTarget.style.display = "none"
                  }}
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
      </div>
    )
  }

  const renderOutputFormatSelection = () => {
    return (
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4 }}
        className="mt-8"
      >
        <h2 className="text-xl font-semibold text-primary mb-2">Choose Output Format</h2>
        <p className="text-gray-600 mb-6">Select how you want the generated code to be delivered:</p>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          {/* SQL Option */}
          <div
            onClick={() => handleOutputFormatSelect("sql")}
            className="flex flex-col items-center p-4 sm:p-6 border-2 rounded-xl cursor-pointer transition-all hover:border-secondary hover:bg-secondary/5 hover:shadow-md border-gray-200 min-h-[140px] sm:min-h-[160px]"
          >
            <div className="w-12 h-12 sm:w-16 sm:h-16 rounded-full bg-blue-100 flex items-center justify-center mb-3 sm:mb-4">
              <svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#2563eb" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M4 6h16" /><path d="M4 12h16" /><path d="M4 18h12" />
              </svg>
            </div>
            <h3 className="font-semibold text-primary text-base sm:text-lg mb-1">SQL</h3>
            <p className="text-xs sm:text-sm text-gray-500 text-center">Generate standard SQL — download as .sql file</p>
          </div>

          {/* PySpark Option */}
          <div
            onClick={() => handleOutputFormatSelect("pyspark")}
            className="flex flex-col items-center p-4 sm:p-6 border-2 rounded-xl cursor-pointer transition-all hover:border-secondary hover:bg-secondary/5 hover:shadow-md border-gray-200 min-h-[140px] sm:min-h-[160px]"
          >
            <div className="w-12 h-12 sm:w-16 sm:h-16 rounded-full bg-orange-100 flex items-center justify-center mb-3 sm:mb-4">
              <svg xmlns="http://www.w3.org/2000/svg" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="#ea580c" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 2L2 7l10 5 10-5-10-5z" /><path d="M2 17l10 5 10-5" /><path d="M2 12l10 5 10-5" />
              </svg>
            </div>
            <h3 className="font-semibold text-primary text-base sm:text-lg mb-1">PySpark</h3>
            <p className="text-xs sm:text-sm text-gray-500 text-center">Generate PySpark DataFrame API code — download as .ipynb notebook</p>
          </div>
        </div>
      </motion.div>
    )
  }

  const renderErrorMessage = () => {
    if (processingState !== "error") return null

    return (
      <div className="mt-8 p-4 bg-red-50 border border-red-200 rounded-lg">
        <div className="flex items-start">
          <AlertCircle className="h-5 w-5 text-red-500 mt-0.5 mr-1.5 flex-shrink-0" />
          <div>
            <h3 className="font-medium text-red-800 mb-1">Processing Error</h3>
            <p className="text-sm text-red-700">{errorMessage}</p>
          </div>
        </div>
      </div>
    )
  }

  return (
    <>
      <div className="max-w-6xl mx-auto bg-white dark:bg-gray-800 shadow-lg rounded-lg p-4 sm:p-8">
        {/* Header section */}
        <div className="flex flex-col sm:flex-row sm:justify-between sm:items-center gap-2 sm:gap-4 mb-4 sm:mb-6">
          <div className="flex flex-col sm:flex-row sm:items-center gap-1 sm:gap-4">
            <h1 className="text-xl sm:text-2xl md:text-3xl font-bold text-primary">SQL Mapping Engine</h1>
            <Link
              href="/how-to-use#sql-mapping-engine"
              className="inline-flex items-center px-3 py-1.5 text-xs sm:text-sm font-medium text-primary bg-secondary/10 rounded-full hover:bg-secondary/20 transition-colors self-start sm:self-auto"
            >
              <Lightbulb className="w-3 h-3 sm:w-4 sm:h-4 mr-1 sm:mr-1.5 text-secondary" />
              How to Use
            </Link>
          </div>

          {/* Reset Button */}
          {xlsxFile && (
            <button
              onClick={handleReset}
              className="flex items-center px-3 py-2 sm:px-4 sm:py-2 min-h-[44px] text-xs sm:text-sm font-medium text-gray-700 bg-gray-100 rounded-md hover:bg-gray-200 transition-colors self-start sm:self-auto"
            >
              <RotateCcw className="w-3 h-3 sm:w-4 sm:h-4 mr-1 sm:mr-2" />
              Reset
            </button>
          )}
        </div>

        {/* Target Selection Screen */}
        {processingState === "target-selection" && renderTargetSelectionScreen()}

        {/* Output Format Selection - only for Databricks and MS Fabric */}
        {processingState === "output-format-selection" && renderOutputFormatSelection()}

        {/* File Upload Area - Only show if in file-upload state */}
        {processingState === "file-upload" &&
          <div className="flex flex-col items-center">
            <motion.div
              initial={{ opacity: 0, scale: 0.95 }}
              animate={{ opacity: 1, scale: 1, y: ["0%", "-2%", "0%"] }}
              transition={{
                opacity: { duration: 0.3 },
                scale: { duration: 0.3 },
                y: {
                  duration: 3,
                  ease: "easeInOut",
                  repeat: Infinity,
                  repeatType: "loop",
                },
              }}
              whileHover={{
                scale: 1.02,
                boxShadow: "0 0 20px rgba(59, 130, 246, 0.5)", // Subtle blue glow
              }}
              className={cn(
                "border-2 border-dashed rounded-lg p-6 relative transition-all duration-200 w-full mb-10", // Changed mb-8 to mb-10
                isDraggingOver ? "border-secondary bg-secondary/5 shadow-md" : "border-gray-300",
                "animate-file-upload-entry",
                !xlsxFile && "animate-file-upload-pulse" // Only pulse if no file is selected
              )}
              onDragOver={handleDragOver}
              onDragLeave={handleDragLeave}
              onDrop={handleDrop}
              onClick={() => document.getElementById("xlsxFileInput")?.click()}
            >
              <input type="file" onChange={handleFileChange} className="hidden" id="xlsxFileInput" accept=".xlsx" /> {/* Changed id and accept */}
              <div
                className="cursor-pointer flex flex-col items-center justify-center h-full min-h-[200px]"
              >
                <Upload className="w-12 h-12 text-gray-400 mb-4" />
                <span className="text-gray-600 dark:text-gray-300 font-medium text-lg mb-2 text-center">AI-Powered SQL Generator</span> {/* Changed text */}
                <span className="text-sm text-gray-500 text-center">
                  {xlsxFile ? xlsxFile.name : "Let our AI Agent process your mapping file. Click to upload or drag and drop"} {/* Changed from zipFile to xlsxFile */}
                </span>
                <span className="text-xs text-gray-400 mt-2">Supported formats for AI Agent: .xlsx</span> {/* Changed text */}
              </div>
            </motion.div>

            <div className="text-center text-sm text-gray-600 mb-8"> {/* Changed mb-6 to mb-8 */}
              Or
            </div>
            <div>
              <Button
                variant="outline"
                className="flex items-center gap-2"
                onClick={() => {
                  if (!user) {
                    router.push('/login')
                  } else {
                    setShowHistoryModal(true)
                  }
                }}
              >
                <History className="h-4 w-4" />
                Select Mapping file from Previous conversions
              </Button>
            </div>
          </div> /* Closing parent div */
        }

        {/* Error Message */}
        {renderErrorMessage()}

        {/* Process Button - Only show if in file-upload, validating, processing, error, or display-sql state */}
        {(processingState === "file-upload" ||
          processingState === "validating" ||
          processingState === "processing" ||
          processingState === "error" ||
          processingState === "display-sql") && (
            <button
              onClick={handleProcess}
              disabled={!xlsxFile || !selectedPlatform || processingState === "validating" || processingState === "processing" || processingState === "display-sql"}
              className={`w-full py-3 sm:py-4 rounded-md font-medium text-sm sm:text-base transition-all duration-300 relative overflow-hidden mt-10 ${ /* Changed mt-8 to mt-10 */
                processingState === "file-upload"
                  ? "bg-secondary text-primary hover:bg-secondary/90 disabled:bg-gray-200 disabled:text-gray-400 disabled:cursor-not-allowed"
                  : "bg-gray-50 text-gray-700 cursor-wait"
                }`}
            >
              {getButtonContent()}
            </button>
          )}

        {/* Mapping Editor Popup */}
        <MappingEditorPopup
          isOpen={showMappingEditor}
          onClose={handleCloseMappingEditor}
          platformName={selectedPlatformName}
          sqlContent={sqlContent}
          zipContents={xlsxContents}
          onSave={handleSaveMappings}
        />

        <ConversionHistoryModal
          isOpen={showHistoryModal}
          onClose={() => setShowHistoryModal(false)}
          onSelectFile={handleHistoryFileSelect}
        />

        {/* Display Generated SQL Section */}
        {processingState === "display-sql" && (cteSqlContent || pysparkNotebookContent) && generatedSqlFileName && (
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.5 }}
            className={cn(
              "bg-gray-50 dark:bg-gray-700 rounded-lg shadow-inner", // Base styles
              !isFullScreen && "mt-8 p-4 sm:p-6", // Apply padding/margin only when NOT fullscreen
              isFullScreen && "fixed inset-0 z-[9999] bg-white dark:bg-gray-900 flex flex-col p-0" // Fullscreen styles
            )}
          >
            <div className="flex flex-col sm:flex-row sm:items-center mb-2 flex-shrink-0"> {/* Ensure header doesn't shrink */}
              <h2 className="text-xl font-semibold text-primary mr-2 mb-2 sm:mb-0">
                {outputFormat === "pyspark" ? "Generated PySpark Notebook:" : "Generated SQL:"}
              </h2>
              <input
                type="text"
                value={generatedSqlFileName ? generatedSqlFileName.replace(/\.(sql|ipynb)$/i, '') : ''}
                onChange={(e) => {
                  const newBaseName = e.target.value;
                  const ext = outputFormat === "pyspark" ? ".ipynb" : ".sql";
                  setGeneratedSqlFileName(newBaseName + ext);
                }}
                readOnly={!isFileNameEditable} // Make input read-only unless editing
                className={cn(
                  "flex-grow p-2 border rounded-md text-primary bg-white dark:bg-gray-800 mr-2",
                  isFileNameEditable ? "border-secondary ring-2 ring-secondary/50" : "border-gray-200"
                )}
              />
              <button
                onClick={() => setIsFileNameEditable(!isFileNameEditable)}
                className="flex items-center px-3 py-2 rounded-md text-sm font-medium transition-colors bg-gray-100 hover:bg-gray-200 text-gray-700 mr-2"
                title={isFileNameEditable ? "Save Filename" : "Rename Filename"}
              >
                {isFileNameEditable ? (
                  <Save className="w-4 h-4 mr-1" />
                ) : (
                  <Pencil className="w-4 h-4 mr-1" />
                )}
                {isFileNameEditable ? "Save" : "Rename"}
              </button>
              {outputFormat !== "pyspark" && (
                <div className="flex space-x-2 mt-2 sm:mt-0"> {/* Tabs on the same line as filename */}
                  <button
                    onClick={() => setActiveSqlTab("cte")}
                    className={`px-4 py-2 rounded-md text-sm font-medium transition-colors ${activeSqlTab === "cte"
                      ? "bg-secondary text-primary shadow-sm"
                      : "bg-gray-200 text-gray-700 hover:bg-gray-300"
                      }`}
                  >
                    CTE Version
                  </button>
                </div>
              )}
              {!isFullScreen && (
                <button
                  onClick={() => setIsEditorCollapsed(!isEditorCollapsed)}
                  className="ml-2 p-2 rounded-md bg-gray-100 hover:bg-gray-200 transition-colors"
                  title={isEditorCollapsed ? "Expand" : "Collapse"}
                >
                  {isEditorCollapsed ? (
                    <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="lucide lucide-chevrons-down"><path d="m7 6 5 5 5-5" /><path d="m7 13 5 5 5-5" /></svg>
                  ) : (
                    <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="lucide lucide-chevrons-up"><path d="m17 11-5-5-5 5" /><path d="m17 18-5-5-5 5" /></svg>
                  )}
                </button>
              )}
              <button
                onClick={() => setIsFullScreen(!isFullScreen)}
                className="ml-2 p-2 rounded-md bg-gray-100 hover:bg-gray-200 transition-colors"
                title={isFullScreen ? "Exit Fullscreen" : "Fullscreen"}
              >
                {isFullScreen ? (
                  <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="lucide lucide-minimize"><path d="M8 3v3a2 2 0 0 1-2 2H3m18 0h-3a2 2 0 0 1-2-2V3m0 18v-3a2 2 0 0 1 2-2h3m-18 0h3a2 2 0 0 1 2 2v3" /></svg>
                ) : (
                  <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="lucide lucide-maximize"><path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3m-18 0v3a2 2 0 0 0 2 2h3" /></svg>
                )}
              </button>
            </div>
            <div className={cn("w-full mb-4 border rounded-md overflow-hidden", isFullScreen ? "flex-grow overflow-y-auto" : isEditorCollapsed ? "h-20" : "h-[400px]")}>
              {outputFormat === "pyspark" ? (
                <div className={cn("w-full overflow-y-auto bg-gray-50 dark:bg-gray-800", isFullScreen ? "h-full" : isEditorCollapsed ? "h-20" : "h-[400px]")}>
                  {!isEditorCollapsed && (
                    <NotebookRenderer
                      content={pysparkNotebookContent || ""}
                      onChange={setPysparkNotebookContent}
                    />
                  )}
                </div>
              ) : (
                <SqlEditor
                  value={activeSqlTab === "cte" ? (cteSqlContent || "") : (tempTableSqlContent || "")}
                  onChange={activeSqlTab === "cte" ? setCteSqlContent : setTempTableSqlContent}
                  editorHeight={isFullScreen ? "100%" : isEditorCollapsed ? "20px" : "340px"}
                  isCollapsed={isEditorCollapsed}
                />
              )}
            </div>
            <button
              onClick={handleDownloadGeneratedSql}
              className="w-full py-3 sm:py-4 rounded-lg font-medium text-sm sm:text-base bg-secondary text-primary hover:bg-secondary/90 transition-colors flex-shrink-0 min-h-[48px] sm:min-h-[44px] flex items-center justify-center"
            >
              {outputFormat === "pyspark" ? "Download PySpark Notebook (.ipynb)" : "Download SQL File"}
            </button>
          </motion.div>
        )}

        <FileUploadError
          message={errorMessage}
          isVisible={!!errorMessage && processingState !== "error"}
          onClose={() => setErrorMessage("")}
          duration={3000}
        />
      </div>

      <style>{`
        @keyframes fadeIn {
          from {
            opacity: 0;
            transform: translateY(10px);
          }
          to {
            opacity: 1;
            transform: translateY(0);
          }
        }
        
        @keyframes fadeIn {
          from {
            opacity: 0;
            transform: translateY(10px);
          }
          to {
            opacity: 1;
            transform: translateY(0);
          }
        }

        @keyframes pulseBorder {
          0% {
            border-color: #d1d5db; /* gray-300 */
          }
          50% {
            border-color: #a7a7a7; /* A slightly darker gray for the pulse effect */
          }
          100% {
            border-color: #d1d5db;
          }
        }

        @keyframes scaleIn {
          from {
            transform: scale(0.95);
            opacity: 0.7;
          }
          to {
            transform: scale(1);
            opacity: 1;
          }
        }
        
        .animate-file-upload-entry {
          animation: fadeIn 0.5s ease-out, scaleIn 0.5s ease-out;
        }

        .animate-file-upload-pulse {
          animation: pulseBorder 2s infinite ease-in-out;
        }

        @keyframes pulseGreenGlow {
          0%, 100% {
            text-shadow: 0 0 5px rgba(34, 197, 94, 0.7); /* green-500 with opacity */
          }
          50% {
            text-shadow: 0 0 15px rgba(34, 197, 94, 1); /* brighter green glow */
          }
        }

        .animate-pulse-green-glow {
          animation: pulseGreenGlow 2s infinite ease-in-out;
        }
      `}</style>
    </>
  )
}
