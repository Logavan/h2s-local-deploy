"use client"

import { AlertCircle, X } from "lucide-react"
import { motion, AnimatePresence } from "framer-motion"
import { useEffect } from "react"

interface FileUploadErrorProps {
  message: string
  isVisible: boolean
  onClose: () => void
  duration?: number // in milliseconds
}

export default function FileUploadError({
  message,
  isVisible,
  onClose,
  duration = 3000, // default 3 seconds
}: FileUploadErrorProps) {
  useEffect(() => {
    let timer: NodeJS.Timeout

    if (isVisible) {
      timer = setTimeout(() => {
        onClose()
      }, duration)
    }

    return () => {
      if (timer) {
        clearTimeout(timer)
      }
    }
  }, [isVisible, duration, onClose])

  return (
    <AnimatePresence>
      {isVisible && (
        <motion.div
          initial={{ opacity: 0, y: -20 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -20, transition: { duration: 0.5 } }}
          className="fixed top-4 right-4 z-50"
        >
          <div className="bg-red-50 border-l-4 border-red-400 p-4 rounded shadow-lg max-w-md">
            <div className="flex items-start">
              <AlertCircle className="h-5 w-5 text-red-400 mt-0.5" />
              <div className="ml-3">
                <p className="text-sm text-red-700">{message}</p>
              </div>
              <button
                onClick={onClose}
                className="ml-auto -mx-1.5 -my-1.5 bg-red-50 text-red-500 rounded-lg focus:ring-2 focus:ring-red-400 p-1.5 hover:bg-red-100 inline-flex items-center justify-center min-w-[44px] min-h-[44px]"
              >
                <span className="sr-only">Dismiss</span>
                <X className="h-4 w-4" />
              </button>
            </div>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
