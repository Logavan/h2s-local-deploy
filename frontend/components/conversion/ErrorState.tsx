"use client"

import { motion, AnimatePresence } from "framer-motion"
import { AlertCircle } from "lucide-react"

interface ErrorStateProps {
  processingState: string
  error: string | null
  onReset: () => void
}

export function ErrorState({ processingState, error, onReset }: ErrorStateProps) {
  return (
    <AnimatePresence>
      {processingState === "error" && error && (
        <motion.div
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.2 }}
          className="mt-4 p-3 bg-red-50 border border-red-200 rounded-md"
        >
          <div className="flex items-center">
            <AlertCircle className="w-5 h-5 text-red-500 mr-2" />
            <p className="text-sm text-red-700 font-medium">Error: {error}</p>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
