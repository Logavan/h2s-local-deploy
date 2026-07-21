"use client"

import { motion } from "framer-motion"
import { Download, CheckCircle } from "lucide-react"

interface SuccessStateProps {
  processingState: string
  conversionMode?: "single" | "bulk"
  fileName: string
  onDownload: () => void
  onReset: () => void
}

export function SuccessState({ processingState, conversionMode, fileName, onDownload, onReset }: SuccessStateProps) {
  // Only show for single file mode success
  if (processingState !== "success" || conversionMode !== "single") {
    return null
  }

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.97 }}
      animate={{ opacity: 1, scale: 1 }}
      exit={{ opacity: 0, scale: 0.97 }}
      transition={{ duration: 0.25 }}
      className="mt-6 mb-6 p-6 bg-green-50 border border-green-200 rounded-lg"
    >
      <div className="text-center">
        <div className="flex items-center justify-center mb-3">
          <CheckCircle className="h-6 w-6 text-green-600 mr-2" />
          <h3 className="text-lg font-semibold text-green-800">
            Conversion Complete
          </h3>
        </div>
        <p className="text-green-700 mb-6 text-sm">
          Your {fileName} has been converted to SQL.
        </p>

        {/* Download Button */}
        <button
          onClick={onDownload}
          className="inline-flex items-center px-6 py-3 bg-blue-600 text-white font-medium rounded-lg hover:bg-blue-700 transition-colors duration-200 shadow-sm"
        >
          <Download className="w-5 h-5 mr-2" />
          Download ZIP File
        </button>

        <p className="text-xs text-green-600 mt-3">
          Your converted SQL file is packaged in a ZIP archive
        </p>
      </div>
    </motion.div>
  )
}
