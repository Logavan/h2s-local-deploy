"use client"

import { Loader2 } from "lucide-react"
import { ConversionSteps } from "./ConversionSteps"
import type { BulkFileInfo } from "./BulkFileUploadSection"

type ProcessingState = "idle" | "analyzing" | "checking-limits" | "initiating-conversion" | "polling-status" | "success" | "error"
type ConversionMode = "single" | "bulk"

interface ConversionActionsProps {
  conversionMode: ConversionMode
  processingState: ProcessingState
  isProcessing: boolean
  bulkAnalysisComplete: boolean
  bulkFiles: BulkFileInfo[]
  onStartBulkConversion: () => void
  onHandleProcessClick: () => void
  viewXmlFile: File | null
  error: string | null
}

export function ConversionActions({
  conversionMode,
  processingState,
  isProcessing,
  bulkAnalysisComplete,
  bulkFiles,
  onStartBulkConversion,
  onHandleProcessClick,
  viewXmlFile,
  error,
}: ConversionActionsProps) {
  // Single file mode - Process button with spinner
  const getButtonContent = () => {
    switch (processingState) {
      case "analyzing":
        return "Analyzing XML structure by AI Agent..."
      case "checking-limits":
        return "Checking conversion limits by AI Agent..."
      case "initiating-conversion":
        return "Initiating conversion by AI Agent..."
      case "polling-status":
        return (
          <span className="flex items-center justify-center">
            <Loader2 className="mr-2 h-5 w-5 animate-spin" />
            Conversion in progress...
          </span>
        )
      case "success":
        return "Ready for Download"
      case "error":
        return "Error"
      default:
        return "Process"
    }
  }

  const isButtonDisabled =
    isProcessing ||
    processingState === "analyzing" ||
    processingState === "checking-limits" ||
    processingState === "initiating-conversion" ||
    processingState === "polling-status" ||
    processingState === "success" ||
    !viewXmlFile

  // Bulk mode - Main Action Button
  if (conversionMode === "bulk" && processingState !== "error" && bulkFiles.length > 0) {
    return (
      <div className="mt-6">
        <button
          onClick={onStartBulkConversion}
          disabled={isProcessing || !bulkAnalysisComplete}
          className={`w-full py-3 sm:py-4 min-h-11 rounded-md font-medium text-sm sm:text-base transition-colors ${
            processingState === "success"
              ? "bg-green-100 text-green-800 border border-green-300 cursor-not-allowed"
              : bulkAnalysisComplete && !isProcessing
                ? "bg-secondary text-primary hover:bg-secondary/90 disabled:bg-gray-200 disabled:text-gray-400 disabled:cursor-not-allowed"
                : "bg-gray-200 text-gray-400 cursor-not-allowed"
          }`}
        >
          {isProcessing ? (
            <span className="flex items-center justify-center">
              <Loader2 className="mr-2 h-5 w-5 animate-spin" />
              Converting...
            </span>
          ) : bulkAnalysisComplete ? (
            "Start Bulk Conversion"
          ) : (
            "Analyze Files First"
          )}
        </button>
      </div>
    )
  }

  // Single file mode - Process button with ConversionSteps
  if (conversionMode === "single" && processingState !== "error") {
    return (
      <div className="mt-6">
        <button
          onClick={onHandleProcessClick}
          disabled={isButtonDisabled}
          className={`w-full py-3 sm:py-4 min-h-11 rounded-md font-medium text-sm sm:text-base transition-colors ${
            processingState === "success"
              ? "bg-green-100 text-green-800 border border-green-300 cursor-not-allowed"
              : "bg-secondary text-primary hover:bg-secondary/90 disabled:bg-gray-200 disabled:text-gray-400 disabled:cursor-not-allowed"
          }`}
        >
          {getButtonContent()}
        </button>

        {/* Step progress indicator */}
        <ConversionSteps processingState={processingState} />
      </div>
    )
  }

  return null
}
