"use client"

import { motion, AnimatePresence } from "framer-motion"

type ProcessingState = "idle" | "analyzing" | "checking-limits" | "initiating-conversion" | "polling-status" | "success" | "error"

interface ConversionStepsProps {
  processingState: ProcessingState
}

function StepItem({ label, isActive, isDone }: { label: string; isActive: boolean; isDone: boolean }) {
  return (
    <div className="flex items-center gap-2 text-xs">
      <div className={`w-4 h-4 rounded-full flex items-center justify-center flex-shrink-0 ${isDone ? "bg-green-500" : "bg-gray-200"}`}>
        {isDone ? (
          <svg className="w-2.5 h-2.5 text-white" fill="none" viewBox="0 0 24 24" stroke="currentColor"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={3} d="M5 13l4 4L19 7" /></svg>
        ) : (
          <div className={`w-1.5 h-1.5 rounded-full ${isActive ? "bg-yellow-400 animate-pulse" : "bg-gray-300"}`} />
        )}
      </div>
      <span className={isDone ? "text-green-700 font-medium" : isActive ? "text-yellow-700" : "text-gray-400"}>
        {label}
      </span>
    </div>
  )
}

export function ConversionSteps({ processingState }: ConversionStepsProps) {
  const _ps = processingState as ProcessingState
  const step1Done = _ps !== "analyzing" && (_ps === "checking-limits" || _ps === "initiating-conversion" || _ps === "polling-status" || _ps === "success")
  const step2Done = _ps === "initiating-conversion" || _ps === "polling-status" || _ps === "success"
  const step3Done = _ps === "polling-status" || _ps === "success"
  const step4Done = _ps === "success"

  return (
    <AnimatePresence>
      {(processingState === "analyzing" || processingState === "checking-limits" || processingState === "initiating-conversion" || processingState === "polling-status") && (
        <motion.div
          initial={{ opacity: 0, height: 0 }}
          animate={{ opacity: 1, height: "auto" }}
          exit={{ opacity: 0, height: 0 }}
          transition={{ duration: 0.2 }}
          className="overflow-hidden"
        >
          <div className="mt-3 space-y-1.5">
            <StepItem label="Validating XML structure" isActive={_ps === "analyzing"} isDone={step1Done} />
            <StepItem label="Checking conversion limits" isActive={_ps === "checking-limits"} isDone={step2Done} />
            <StepItem label="Initiating conversion" isActive={_ps === "initiating-conversion"} isDone={step3Done} />
            <StepItem label="Generating Files" isActive={_ps === "polling-status"} isDone={step4Done} />
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  )
}
