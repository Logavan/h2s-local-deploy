"use client"

import { useState, useCallback, useEffect, useRef } from "react"
import { motion, AnimatePresence } from "framer-motion"
import { useRouter } from "next/navigation"
import { useAuth } from "@/contexts/AuthContext"
import { useToastContext } from "./ui/toast-provider"
import { useLineCounter } from "@/hooks/useLineCounter"
import { BulkFileUploadSection, BulkFileInfo } from "./conversion/BulkFileUploadSection"
import { ConversionDashboard } from "./conversion/ConversionDashboard"
import { ConversionSteps } from "./conversion/ConversionSteps"
import { ConversionHeader } from "./conversion/ConversionHeader"
import { FileUploader } from "./conversion/FileUploader"
import { AnalysisResults } from "./conversion/AnalysisResults"
import { ConversionActions } from "./conversion/ConversionActions"
import { SuccessState } from "./conversion/SuccessState"
import { ErrorState } from "./conversion/ErrorState"
import { VisualizationSection } from "./conversion/VisualizationSection"
import { analyzeXmlFile, startConversion, getConversionStatus, downloadConvertedFile, downloadBulkResult, checkBackendHealth, checkConversionRunningStatus, startBulkConversion, getBulkConversionStatus } from "@/lib/api"
import { Download, CheckCircle } from "lucide-react"

const ENTERPRISE_EMAIL = "enterprise@local.deploy"

type ProcessingState = "idle" | "analyzing" | "initiating-conversion" | "polling-status" | "success" | "error"


interface AnalysisResult {
  success: boolean;
  node_count?: number;
  complexity?: string;
  conversion_type?: string;
  session_id?: string;
  line_count?: number;
  error?: string;
  dig_mapping_dot_string?: string;
}


export interface ConversionToolProps {
  onConversionSuccess?: () => void
}

type ConversionMode = "single" | "bulk"

export default function ConversionTool({ onConversionSuccess }: ConversionToolProps) {
  const [viewXmlFile, setViewXmlFile] = useState<File | null>(null)
  const [viewFileContent, setViewFileContent] = useState("")
  const { lineCount: viewFileLineCount, isProcessing: isViewFileProcessing } = useLineCounter(viewFileContent)

  // Bulk conversion state
  const [conversionMode, setConversionMode] = useState<ConversionMode>("single")
  const [bulkFiles, setBulkFiles] = useState<BulkFileInfo[]>([])
  const [bulkAnalysisComplete, setBulkAnalysisComplete] = useState(false)
  const [bulkTaskId, setBulkTaskId] = useState<string | null>(null)
  const [bulkProgress, setBulkProgress] = useState({ completed: 0, total: 0, failed: 0 })

  const [processingState, setProcessingState] = useState<ProcessingState>("idle")
  const [isProcessing, setIsProcessing] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [fileInputKey, setFileInputKey] = useState<number>(0)
  const [convertedContent, setConvertedContent] = useState<string | null>(null)

  const [analysisResult, setAnalysisResult] = useState<AnalysisResult | null>(null)
  const [lastConversionTime, setLastConversionTime] = useState<number>(0)
  const [visualizationDotString, setVisualizationDotString] = useState<string | null>(null);
  const [renderedSvgContent, setRenderedSvgContent] = useState<string | null>(null);
  const [showVisualization, setShowVisualization] = useState(false);
  const [hasAutoDownloadedSvg, setHasAutoDownloadedSvg] = useState(false);


  const { isLoggedIn, user } = useAuth()
  const router = useRouter()
  const toast = useToastContext()

  const pollTimeoutRef = useRef<NodeJS.Timeout | null>(null)


  // Reset conversion state when switching between Single and Bulk modes
  useEffect(() => {
    setProcessingState("idle")
    setError(null)
    setAnalysisResult(null)
    setConvertedContent(null)
    setBulkTaskId(null)
    setBulkProgress({ completed: 0, total: 0, failed: 0 })
    setBulkAnalysisComplete(false)
  }, [conversionMode])

  const hasMeaningfulGraphContent = useCallback(() => {
    if (!visualizationDotString || visualizationDotString.trim() === '' || visualizationDotString.trim() === 'digraph G {}') {
      return false;
    }
    return visualizationDotString.includes('->') || visualizationDotString.includes('[');
  }, [visualizationDotString]);

  const handleDownloadSvg = useCallback(() => {
    if (renderedSvgContent && hasMeaningfulGraphContent()) {
      const fileName = viewXmlFile?.name?.replace(/\.(xml|txt)$/i, '_SQL_FLOW.svg') || 'SQL_FLOW.svg';
      console.log(fileName)
      if (fileName.toLowerCase().endsWith('.xml') ||
        fileName.toLowerCase().endsWith('.txt')) {
        console.warn('Download prevented');
        return;
      }

      const blob = new Blob([renderedSvgContent], { type: "image/svg+xml" });
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = fileName;
      document.body.appendChild(a);
      a.style.display = 'none';

      setTimeout(() => {
        try {
          a.click();
        } catch (clickError) {
          console.error('Error simulating click for SVG download:', clickError);
          toast.error({
            title: "Download Blocked",
            description: "Your browser might have blocked the automatic SVG download. Please try downloading manually.",
          });
        } finally {
          document.body.removeChild(a);
          URL.revokeObjectURL(url);
        }
      }, 100);

    } else {
      console.warn('SVG download attempted but no meaningful graph content was available.');
    }
  }, [renderedSvgContent, viewXmlFile?.name, toast, hasMeaningfulGraphContent]);

  // Effect to manage delayed visualization rendering and auto-download
  useEffect(() => {
    const isMeaningful = visualizationDotString &&
      visualizationDotString.trim() !== "" &&
      visualizationDotString.trim() !== "digraph G {}" &&
      (visualizationDotString.includes("->") || visualizationDotString.includes("["));

    if (isMeaningful) {
      setShowVisualization(true);
      const hasDownloadedInSession = sessionStorage.getItem('svgDownloadedForSession') === 'true';
      if (renderedSvgContent && !hasAutoDownloadedSvg && !hasDownloadedInSession) {
        handleDownloadSvg();
        setHasAutoDownloadedSvg(true);
        sessionStorage.setItem('svgDownloadedForSession', 'true');
      }
    } else {
      setShowVisualization(false);
      setHasAutoDownloadedSvg(false);
      sessionStorage.removeItem('svgDownloadedForSession');
    }
  }, [visualizationDotString, processingState, renderedSvgContent, hasAutoDownloadedSvg, handleDownloadSvg]);

  const handleError = useCallback(
    async (error: Error | unknown, title = "Error") => {
      const errorMessage = error instanceof Error ? error.message : "An unexpected error occurred"
      console.error(error)
      setError(errorMessage)
      setProcessingState("error")
      setIsProcessing(false)
      if (pollTimeoutRef.current) {
        clearTimeout(pollTimeoutRef.current)
      }
    },
    [pollTimeoutRef],
  )

  const validatePrerequisites = useCallback(() => {
    if (!isLoggedIn) {
      router.push("/login")
      return false
    }

    if (!ENTERPRISE_EMAIL) {
      setError("Authentication Required: Please log in to use this feature.");
      return false
    }

    if (!viewFileContent || viewFileContent.trim() === "") {
      setError("Missing Content: Please provide View XML content.");
      return false
    }

    const now = Date.now()
    if (now - lastConversionTime < 5000) {
      setError("Please Wait: Please wait a few seconds before starting another conversion.");
      return false
    }

    return true
  }, [isLoggedIn, user, viewFileContent, router, lastConversionTime])

  // Add flow state tracking for debugging
  const [flowDebug, setFlowDebug] = useState<string[]>([])

  // Enhanced flow logging
  const logFlow = (step: string) => {
    setFlowDebug((prev) => [...prev.slice(-9), step])
  }

  // Define pollConversionStatus
  const pollConversionStatus = useCallback(
    async (taskId: string, fileName: string, initialAnalysisData: AnalysisResult) => {
      logFlow(`Polling status for task: ${taskId}`)
      setProcessingState("polling-status")

      const pollInterval = 3000
      const maxPollAttempts = 1200
      const maxRetries = 10
      let attempts = 0
      let retries = 0
      let currentDelay = pollInterval

      const checkStatus = async () => {
        attempts++
        if (attempts > maxPollAttempts) {
          clearTimeout(pollTimeoutRef.current!)
          handleError(new Error("Conversion status polling timed out."), "Conversion Timeout")
          return
        }

        try {
          let backendHealthy = false;
          try {
            // checkBackendHealth() returns boolean (true when /health responds).
            backendHealthy = await checkBackendHealth();
          } catch (healthError) {
            console.warn("Health check failed, will retry:", healthError);
            backendHealthy = false;
          }

          if (!backendHealthy) {
            logFlow("Backend health check failed, will retry...");
            pollTimeoutRef.current = setTimeout(checkStatus, currentDelay);
            return;
          }

          const statusResult = await getConversionStatus(taskId)
          logFlow(`Task ${taskId} status: ${statusResult.status}, Progress: ${statusResult.progress}%`)

          if (statusResult.status === "COMPLETED") {
            clearTimeout(pollTimeoutRef.current!)
            logFlow(`Task ${taskId} completed.`)

            setProcessingState("success")
            setConvertedContent(null)
            setIsProcessing(false)

            if (statusResult.result?.sql_url) {
              const downloadLink = document.createElement("a")
              downloadLink.href = statusResult.result.sql_url
              downloadLink.download = statusResult.result.download_name || `${fileName.replace(".xml", "")}_converted.zip`
              document.body.appendChild(downloadLink)
              downloadLink.click()
              document.body.removeChild(downloadLink)
              toast.success({
                title: "Conversion Successful!",
                description: `Your ${fileName} has been converted to SQL successfully and downloaded.`,
              })
            } else {
              toast.success({
                title: "Conversion Successful!",
                description: `Your ${fileName} has been converted to SQL successfully. Download link will be available in your account.`,
              })
            }
            return
          } else if (statusResult.status === "FAILED") {
            clearTimeout(pollTimeoutRef.current!)
            handleError(new Error(statusResult.message || statusResult.error || "Conversion failed during background processing."), "Conversion Failed")
          } else {
            pollTimeoutRef.current = setTimeout(checkStatus, currentDelay)
          }
        } catch (error: any) {
          console.error(`Polling error for task ${taskId}:`, error);
          const isNetworkError = error.message.includes("Cannot connect to backend server") || error.message.includes("timed out");
          const isTaskNotFound = error.message.includes("Task not found");

          if ((isNetworkError || isTaskNotFound) && retries < maxRetries) {
            retries++;
            currentDelay = Math.min(pollInterval * Math.pow(2, retries - 1), 30000);
            logFlow(`Retrying task ${taskId} status check in ${currentDelay / 1000}s (Retry ${retries}/${maxRetries})...`);
            pollTimeoutRef.current = setTimeout(checkStatus, currentDelay);
          } else {
            clearTimeout(pollTimeoutRef.current!);
            handleError(error, "Conversion Status Error");
          }
        }
      }
      pollTimeoutRef.current = setTimeout(checkStatus, currentDelay)
    },
    [user, toast, handleError, pollTimeoutRef],
  )

  // Bulk conversion status polling
  const pollBulkConversionStatus = useCallback(
    async (taskId: string) => {
      logFlow(`Bulk: Polling status for task: ${taskId}`)

      const pollInterval = 5000
      const maxPollAttempts = 720
      const maxRetries = 5
      let attempts = 0
      let retries = 0
      let currentDelay = pollInterval

      setBulkProgress(prev => ({
        ...prev,
        total: prev.total || bulkFiles.length,
        completed: 0,
        failed: 0
      }))

      setProcessingState("polling-status")

      const checkStatus = async () => {
        attempts++
        if (attempts > maxPollAttempts) {
          clearTimeout(pollTimeoutRef.current!)
          handleError(new Error("Bulk conversion status polling timed out."), "Bulk Conversion Timeout")
          return
        }

        try {
          // checkBackendHealth() returns boolean — drop the bogus .status check.
          const backendHealthy = await checkBackendHealth()
          if (!backendHealthy) {
            throw new Error("Backend server is unresponsive during bulk conversion.")
          }

          const statusResult = await getBulkConversionStatus(taskId)
          console.log("[Bulk Status Debug] statusResult:", JSON.stringify(statusResult, null, 2))
          logFlow(`Bulk: Task ${taskId} status: ${statusResult.status}, Progress: ${statusResult.progress}%`)

          if (statusResult.results && bulkFiles.length > 0) {
            const updatedBulkFiles = bulkFiles.map(file => {
              const result = statusResult.results?.find(r => r.file_name === file.name)
              if (result) {
                return {
                  ...file,
                  status: result.status as BulkFileInfo["status"],
                  error: result.error
                }
              }
              if (statusResult.status === "PROCESSING") {
                return { ...file, status: "processing" as const }
              }
              return file
            })
            setBulkFiles(updatedBulkFiles)
          }

          setBulkProgress({
            completed: statusResult.completed_files,
            total: statusResult.total_files,
            failed: statusResult.failed_files
          })

          if (statusResult.status === "COMPLETED" || statusResult.status === "PARTIAL") {
            clearTimeout(pollTimeoutRef.current!)
            logFlow(`Bulk: Task ${taskId} completed with status: ${statusResult.status}`)

            if (statusResult.results) {
              const finalBulkFiles = bulkFiles.map(file => {
                const result = statusResult.results?.find(r => r.file_name === file.name)
                if (result) {
                  return {
                    ...file,
                    status: result.status as BulkFileInfo["status"],
                    error: result.error
                  }
                }
                return { ...file, status: "completed" as const }
              })
              setBulkFiles(finalBulkFiles)
            }

            setProcessingState("success")
            setIsProcessing(false)

            toast.success({
              title: "Bulk Conversion Complete!",
              description: `${statusResult.completed_files} of ${statusResult.total_files} files converted successfully.`,
            })
            return
          } else if (statusResult.status === "FAILED") {
            clearTimeout(pollTimeoutRef.current!)
            console.error("[Bulk FAILED] statusResult:", JSON.stringify(statusResult, null, 2))
            handleError(new Error(statusResult.message || "Bulk conversion failed."), "Bulk Conversion Failed")
          } else {
            pollTimeoutRef.current = setTimeout(checkStatus, currentDelay)
          }
        } catch (error: any) {
          console.error(`Bulk polling error for task ${taskId}:`, error)
          const isNetworkError = error.message.includes("Cannot connect") || error.message.includes("timed out")
          const isTaskNotFound = error.message.includes("Task not found")

          if ((isNetworkError || isTaskNotFound) && retries < maxRetries) {
            retries++
            currentDelay = Math.min(pollInterval * Math.pow(2, retries - 1), 60000)
            logFlow(`Bulk: Retrying task ${taskId} in ${currentDelay / 1000}s (Retry ${retries}/${maxRetries})...`)
            pollTimeoutRef.current = setTimeout(checkStatus, currentDelay)
          } else {
            clearTimeout(pollTimeoutRef.current!)
            handleError(error, "Bulk Conversion Status Error")
          }
        }
      }
      pollTimeoutRef.current = setTimeout(checkStatus, currentDelay)
    },
    [bulkFiles, toast, handleError, pollTimeoutRef],
  )

  // Handle bulk conversion start
  const handleStartBulkConversion = useCallback(async () => {
    logFlow("Bulk: Starting bulk conversion")

    if (!ENTERPRISE_EMAIL) {
      setError("Authentication Required: User email is missing. Please log in again.")
      setIsProcessing(false)
      return
    }

    if (!bulkAnalysisComplete || bulkFiles.length === 0) {
      setError("Please analyze files first before starting conversion.")
      return
    }

    try {
      setIsProcessing(true)
      setError(null)
      setProcessingState("initiating-conversion")
      setVisualizationDotString(null)
      setShowVisualization(false)
      logFlow(`Bulk: Initiating conversion for ${bulkFiles.length} files`)

      const processingFiles = bulkFiles.map(f => ({ ...f, status: "processing" as const }))
      setBulkFiles(processingFiles)

      setBulkProgress({
        completed: 0,
        total: bulkFiles.length,
        failed: 0
      })

      await new Promise(resolve => setTimeout(resolve, 50))

      const filesForConversion = bulkFiles.map(f => ({
        file_name: f.name,
        content: f.content || "",
        conversion_type: f.conversionType,
      }))
      console.log("[Bulk Conversion] Files to convert:", filesForConversion.map(f => ({ name: f.file_name, contentLength: f.content.length })))

      const result = await startBulkConversion(filesForConversion, ENTERPRISE_EMAIL)

      if (!result.success || !result.bulk_task_id) {
        throw new Error(result.error || "Failed to start bulk conversion")
      }

      logFlow(`Bulk: Conversion started with task_id: ${result.bulk_task_id}`)
      setBulkTaskId(result.bulk_task_id)

      await pollBulkConversionStatus(result.bulk_task_id)

    } catch (err: unknown) {
      logFlow(`Bulk ERROR: ${err instanceof Error ? err.message : "Unknown error"}`)
      if (pollTimeoutRef.current) {
        clearTimeout(pollTimeoutRef.current)
      }
      handleError(err, "Bulk Conversion Error")
      setIsProcessing(false)

      const failedFiles = bulkFiles.map(f => ({ ...f, status: "failed" as const, error: err instanceof Error ? err.message : "Conversion failed" }))
      setBulkFiles(failedFiles)
    }
  }, [user, bulkFiles, bulkAnalysisComplete, pollBulkConversionStatus, handleError, pollTimeoutRef, setVisualizationDotString, setShowVisualization])

  const handleProcessClick = useCallback(async () => {
    logFlow("START: User clicked Start Converting")

    if (!validatePrerequisites()) {
      logFlow("ERROR: Prerequisites validation failed")
      return
    }

    if (!ENTERPRISE_EMAIL) {
      setError("Authentication Required: User email is missing. Please log in again.");
      setIsProcessing(false);
      return;
    }

    try {
      setIsProcessing(true)
      setError(null)

      try {
        const runningStatus = await checkConversionRunningStatus(ENTERPRISE_EMAIL)
        if (runningStatus.isRunning) {
          setError("Another conversion is already in progress. Please wait for it to complete.")
          setIsProcessing(false)
          setProcessingState("idle")
          return
        }
      } catch (e) {
        console.warn("Could not check conversion running status:", e)
      }

      logFlow("STEP Q: setState 'analyzing'")
      setProcessingState("analyzing")
      setLastConversionTime(Date.now())

      logFlow("STEP R&S: Calling analyzeXmlFile() - Python Backend")
      const analysis = await analyzeXmlFile(viewFileContent, viewXmlFile?.name || "input.xml", ENTERPRISE_EMAIL);
      logFlow(`STEP T: Analysis result - Success: ${analysis.success}`)

      if (!analysis.success) {
        logFlow("STEP U&V: Analysis failed - showing error")
        throw new Error(analysis.error || "Analysis failed")
      }

      setAnalysisResult({
        success: analysis.success,
        node_count: analysis.node_count,
        complexity: analysis.complexity,
        session_id: analysis.session_id,
        line_count: analysis.line_count,
        error: analysis.error,
        dig_mapping_dot_string: analysis.dig_mapping_dot_string,
      });

      logFlow(`STEP W: Analysis stored - Nodes: ${analysis.node_count}`)

      setVisualizationDotString(analysis.dig_mapping_dot_string || null);

      logFlow("STEP GG: Proceeding to initiate conversion")
      setProcessingState("initiating-conversion")

      logFlow("STEP LL: Calling startConversion()")
      const startConversionResult = await startConversion(
        viewFileContent,
        viewXmlFile?.name || "input.xml",
        ENTERPRISE_EMAIL,
        analysis.node_count
      )

      if (!startConversionResult.success || !startConversionResult.task_id) {
        throw new Error(startConversionResult.error || "Failed to initiate conversion task.")
      }

      setAnalysisResult(prev => ({ ...prev, session_id: startConversionResult.task_id, success: startConversionResult.success }));

      logFlow(`STEP MM: Conversion initiated, task_id: ${startConversionResult.task_id}. Starting polling.`)
      await pollConversionStatus(startConversionResult.task_id, viewXmlFile?.name || "input.xml", analysis)

    } catch (err: unknown) {
      logFlow(`ERROR: Process error - ${err instanceof Error ? err.message : "Unknown error"}`)
      if (pollTimeoutRef.current) {
        clearTimeout(pollTimeoutRef.current)
      }
      await handleError(err, "Processing Error")
    } finally {
      setIsProcessing(false)
      logFlow("CLEANUP: Processing finished")
    }
  }, [
    validatePrerequisites,
    handleError,
    viewFileContent,
    viewXmlFile,
    user,
    toast,
    setVisualizationDotString,
    pollConversionStatus,
    pollTimeoutRef,
    setShowVisualization,
    setAnalysisResult,
  ])


  const handleReset = useCallback(() => {
    if (!window.confirm("Are you sure you want to reset?")) return

    // Cancel any in-flight polling before clearing state so a late callback
    // can't overwrite our reset values.
    if (pollTimeoutRef.current) {
      clearTimeout(pollTimeoutRef.current)
      pollTimeoutRef.current = null
    }

    // Reset conversion / file state. The mode selection (single vs bulk)
    // is a user preference and is intentionally preserved.
    setViewXmlFile(null)
    setViewFileContent("")
    setProcessingState("idle")
    setIsProcessing(false)
    setError(null)
    setAnalysisResult(null)
    setConvertedContent(null)
    setBulkFiles([])
    setBulkAnalysisComplete(false)
    setBulkTaskId(null)
    setBulkProgress({ completed: 0, total: 0, failed: 0 })
    setLastConversionTime(0)

    // Visualization state — both the data and the auto-download guard.
    setVisualizationDotString(null)
    setRenderedSvgContent(null)
    setShowVisualization(false)
    setHasAutoDownloadedSvg(false)
    if (typeof sessionStorage !== "undefined") {
      sessionStorage.removeItem("svgDownloadedForSession")
    }

    // Reset the file input by remounting via key bump so the same file can
    // be re-uploaded without a manual page reload.
    setFileInputKey((prev) => prev + 1)
  }, [pollTimeoutRef])

  const handleDownload = useCallback(async () => {
    if (conversionMode === "bulk" && bulkTaskId) {
      try {
        const result = await downloadBulkResult(bulkTaskId);
        if (result.type === "success") {
          toast.success({
            title: "Download Initiated",
            description: "The ZIP file download should have started.",
          });
        } else {
          throw new Error(result.message || "Failed to initiate bulk download.");
        }
      } catch (error: unknown) {
        handleError(error, "Download Failed");
      }
      return;
    }

    if (!analysisResult?.session_id || !viewXmlFile?.name) {
      setError("Download Error: Session information or file name is missing. Please try converting again.");
      return;
    }

    try {
      const result = await downloadConvertedFile(analysisResult.session_id, viewXmlFile.name);
      if (result.type === "success") {
        toast.success({
          title: "Download Initiated",
          description: "The ZIP file download should have started.",
        });
      } else {
        throw new Error(result.message || "Failed to initiate download.");
      }
    } catch (error: unknown) {
      handleError(error, "Download Failed");
    }
  }, [analysisResult, viewXmlFile, toast, handleError, conversionMode, bulkTaskId]);


  // Handle file selection from FileUploader
  const handleFileSelect = useCallback((file: File, content: string) => {
    setViewXmlFile(file)
    setViewFileContent(content)
    setFileInputKey(prev => prev + 1)
  }, [])

  return (
    <>
      <div className="max-w-6xl mx-auto bg-white dark:bg-gray-800 shadow-lg rounded-lg p-4 sm:p-8">
        {/* Header section with title, mode toggle, reset */}
        <ConversionHeader
          isLoggedIn={isLoggedIn}
          conversionMode={conversionMode}
          isProcessing={isProcessing}
          processingState={processingState}
          onModeChange={setConversionMode}
          onReset={handleReset}
        />

        <AnimatePresence>
          {processingState === "idle" && (
            <motion.div
              key="upload-section"
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -10 }}
              transition={{ duration: 0.2 }}
            >
              {conversionMode === "single" ? (
                <FileUploader
                  onFileSelect={handleFileSelect}
                  isProcessing={isProcessing}
                  viewFileName={viewXmlFile?.name}
                />
              ) : (
                <BulkFileUploadSection
                  onFilesExtracted={(files) => setBulkFiles(files)}
                  onAnalysisComplete={(files) => {
                    setBulkFiles(files)
                    setBulkAnalysisComplete(true)
                  }}
                  disabled={isProcessing}
                  userEmail={ENTERPRISE_EMAIL || ""}
                />
              )}
            </motion.div>
          )}
        </AnimatePresence>

        {/* Error state */}
        <ErrorState
          processingState={processingState}
          error={error}
          onReset={handleReset}
        />

        {/* Bulk conversion - Show file summary after files extracted */}
        {conversionMode === "bulk" && bulkFiles.length > 0 && (
          <div className="mt-4 p-3 sm:p-4 bg-blue-50 border border-blue-200 rounded-lg overflow-x-auto">
            <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-2 sm:gap-0 mb-2 sm:mb-3">
              <h3 className="font-medium text-blue-800 text-sm sm:text-base">Ready to Convert {bulkFiles.length} Files</h3>
              <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs sm:text-sm text-blue-700">
                <span>Total: {bulkFiles.reduce((sum, f) => sum + f.nodes, 0)} nodes</span>
              </div>
            </div>
          </div>
        )}

        {/* Bulk conversion - Main Action Button */}
        <ConversionActions
          conversionMode={conversionMode}
          processingState={processingState}
          isProcessing={isProcessing}
          bulkAnalysisComplete={bulkAnalysisComplete}
          bulkFiles={bulkFiles}
          onStartBulkConversion={handleStartBulkConversion}
          onHandleProcessClick={handleProcessClick}
          viewXmlFile={viewXmlFile}
          error={error}
        />

        {/* Bulk conversion - Conversion Summary Dashboard */}
        {conversionMode === "bulk" && bulkFiles.length > 0 && (isProcessing || processingState === "success") && (
          <ConversionDashboard
            bulkFiles={bulkFiles}
            bulkProgress={bulkProgress}
            isSuccess={processingState === "success"}
          />
        )}

        {/* Single file mode - show analysis results */}
        <AnalysisResults
          analysisResult={analysisResult}
        />

        {/* Success State for single file mode only */}
        <SuccessState
          processingState={processingState}
          conversionMode={conversionMode}
          fileName={viewXmlFile?.name || "file"}
          onDownload={handleDownload}
          onReset={handleReset}
        />

        {/* Success State for bulk mode */}
        {processingState === "success" && conversionMode === "bulk" && (
          <div className="mt-6 mb-6 p-6 bg-green-50 border border-green-200 rounded-lg">
            <div className="text-center">
              <div className="flex items-center justify-center mb-3">
                <CheckCircle className="h-6 w-6 text-green-600 mr-2" />
                <h3 className="text-lg font-semibold text-green-800">
                  Bulk Conversion Complete
                </h3>
              </div>
              <p className="text-green-700 mb-6 text-sm">
                All {bulkFiles.length} files have been converted to SQL.
              </p>

              <button
                onClick={handleDownload}
                className="inline-flex items-center px-6 py-3 bg-blue-600 text-white font-medium rounded-lg hover:bg-blue-700 transition-colors duration-200 shadow-sm"
              >
                <Download className="w-5 h-5 mr-2" />
                Download All (ZIP)
              </button>

              <p className="text-xs text-green-600 mt-3">
                All converted SQL files are packaged in a ZIP archive
              </p>
            </div>
          </div>
        )}
      </div>


      {/* Visualization Section - only for single file mode */}
      <VisualizationSection
        conversionMode={conversionMode}
        viewXmlFile={viewXmlFile}
        processingState={processingState}
        visualizationDotString={visualizationDotString}
        renderedSvgContent={renderedSvgContent}
        onDownloadSvg={handleDownloadSvg}
        showVisualization={showVisualization}
        onSvgRendered={setRenderedSvgContent}
      />
    </>
  )
}
