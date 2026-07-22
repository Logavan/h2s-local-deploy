"use client"

import { motion } from "framer-motion"
import { CheckCircle, XCircle, Loader2, Clock } from "lucide-react"

interface BulkFileInfo {
  id: string
  name: string
  content?: string
  nodes: number
  status: "pending" | "analyzing" | "processing" | "completed" | "failed"
  error?: string
}

interface ConversionDashboardProps {
  bulkFiles: BulkFileInfo[]
  bulkProgress: {
    completed: number
    total: number
    failed: number
  }
  isSuccess: boolean
}

export function ConversionDashboard({
  bulkFiles,
  bulkProgress,
  isSuccess
}: ConversionDashboardProps) {
  const processing = (bulkProgress.total || 0) - (bulkProgress.completed || 0) - (bulkProgress.failed || 0)
  const progressPercent = (bulkProgress.total || 0) > 0
    ? (((bulkProgress.completed || 0) + (bulkProgress.failed || 0)) / bulkProgress.total) * 100
    : 0

  return (
    <div className="mt-6 p-4 bg-gradient-to-br from-blue-50 to-indigo-50 border border-blue-200 rounded-lg shadow-sm">
      {/* Header */}
      <div className="flex items-center justify-between mb-4">
        <h3 className="font-semibold text-blue-800 text-lg">
          Conversion Summary
        </h3>
        <span className="text-xs text-blue-600 bg-blue-100 px-2 py-1 rounded-full">
          {isSuccess ? "Completed" : "In Progress"}
        </span>
      </div>

      {/* Stats Grid */}
      <div className="grid grid-cols-4 gap-3 mb-4">
        <div className="bg-white rounded-lg p-3 shadow-sm border border-gray-100">
          <div className="text-2xl font-bold text-gray-800">{bulkProgress.total || 0}</div>
          <div className="text-xs text-gray-500">Total Files</div>
        </div>
        <div className="bg-white rounded-lg p-3 shadow-sm border border-gray-100">
          <div className="text-2xl font-bold text-blue-600">{processing}</div>
          <div className="text-xs text-gray-500">Processing</div>
        </div>
        <div className="bg-white rounded-lg p-3 shadow-sm border border-gray-100">
          <div className="text-2xl font-bold text-green-600">{bulkProgress.completed || 0}</div>
          <div className="text-xs text-gray-500">Completed</div>
        </div>
        <div className="bg-white rounded-lg p-3 shadow-sm border border-gray-100">
          <div className="text-2xl font-bold text-red-500">{bulkProgress.failed || 0}</div>
          <div className="text-xs text-gray-500">Failed</div>
        </div>
      </div>

      {/* Progress bar */}
      <div className="mb-4">
        <div className="flex justify-between text-xs text-gray-600 mb-1">
          <span>Progress</span>
          <span>{Math.round(progressPercent)}%</span>
        </div>
        <div className="w-full bg-gray-200 rounded-full h-3">
          <div
            className="bg-gradient-to-r from-blue-500 to-green-500 h-3 rounded-full transition-all duration-500"
            style={{ width: `${progressPercent}%` }}
          />
        </div>
      </div>

      {/* Individual file status - expanded list */}
      <div className="mt-4">
        <div className="text-sm font-medium text-gray-700 mb-2 flex items-center gap-2">
          <span>File Status ({bulkFiles.filter(f => f.status === "completed").length}/{bulkFiles.length} completed)</span>
        </div>
        <div className="space-y-1.5 max-h-96 overflow-y-auto bg-white rounded-lg p-2 border border-gray-200">
          {bulkFiles.map((file, index) => (
            <div key={file.id} className={`flex items-center gap-3 text-sm p-2 rounded transition-all ${
              file.status === "completed" ? "bg-green-50 border border-green-100" :
              file.status === "failed" ? "bg-red-50 border border-red-100" :
              file.status === "processing" ? "bg-blue-50 border border-blue-100" :
              "bg-gray-50 border border-gray-100"
            }`}>
              <span className="text-xs text-gray-400 w-6">#{index + 1}</span>
              <div className="flex-shrink-0">
                {file.status === "completed" && <CheckCircle className="w-4 h-4 text-green-500" />}
                {file.status === "failed" && <XCircle className="w-4 h-4 text-red-500" />}
                {file.status === "processing" && <Loader2 className="w-4 h-4 text-blue-500 animate-spin" />}
                {file.status === "pending" && <Clock className="w-4 h-4 text-gray-400" />}
                {file.status === "analyzing" && <Loader2 className="w-4 h-4 text-yellow-500 animate-spin" />}
              </div>
              <div className="flex-grow min-w-0">
                <div className={`font-medium truncate ${file.status === "failed" ? "text-red-700" : "text-gray-700"}`}>
                  {file.name}
                </div>
                {file.status === "failed" && file.error && (
                  <div className="text-xs text-red-500 truncate">{file.error}</div>
                )}
              </div>
              <div className="flex-shrink-0">
                {file.status === "completed" && (
                  <span className="text-xs text-green-600 bg-green-100 px-2 py-0.5 rounded-full font-medium">Done</span>
                )}
                {file.status === "failed" && (
                  <span className="text-xs text-red-600 bg-red-100 px-2 py-0.5 rounded-full font-medium">Failed</span>
                )}
                {file.status === "processing" && (
                  <span className="text-xs text-blue-600 bg-blue-100 px-2 py-0.5 rounded-full font-medium">Processing</span>
                )}
                {file.status === "pending" && (
                  <span className="text-xs text-gray-500 bg-gray-100 px-2 py-0.5 rounded-full font-medium">Pending</span>
                )}
                {file.status === "analyzing" && (
                  <span className="text-xs text-yellow-600 bg-yellow-100 px-2 py-0.5 rounded-full font-medium">Analyzing</span>
                )}
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
