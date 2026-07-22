"use client"

import { motion, AnimatePresence } from "framer-motion"

interface AnalysisResult {
  success: boolean
  node_count?: number
  complexity?: string
  session_id?: string
  line_count?: number
  error?: string
  dig_mapping_dot_string?: string
}

interface AnalysisResultsProps {
  analysisResult: AnalysisResult | null
}

export function AnalysisResults({
  analysisResult,
}: AnalysisResultsProps) {
  return (
    <AnimatePresence>
      {analysisResult && (
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -10 }}
          transition={{ duration: 0.2 }}
          className="mt-4 bg-blue-50 border border-blue-200 text-blue-700 px-4 py-3 rounded-md mb-4"
        >
          <h3 className="font-medium">XML Analysis Results (Processed by AI Agent)</h3>
          <div className="text-sm space-y-1">
            <p>
              <strong>Nodes:</strong> {analysisResult.node_count}
            </p>
            {analysisResult.line_count && (
              <p>
                <strong>Lines:</strong> {analysisResult.line_count}
              </p>
            )}
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
