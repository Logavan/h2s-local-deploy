"use client"

import { motion } from "framer-motion"
import Link from "next/link"
import { Lightbulb, RotateCcw } from "lucide-react"

type ProcessingState = "idle" | "analyzing" | "checking-limits" | "initiating-conversion" | "polling-status" | "success" | "error"
type ConversionMode = "single" | "bulk"

interface ConversionHeaderProps {
  isLoggedIn: boolean
  credits: number
  conversionMode: ConversionMode
  isProcessing: boolean
  processingState: ProcessingState
  onModeChange: (mode: ConversionMode) => void
  onReset: () => void
}

export function ConversionHeader({
  isLoggedIn,
  credits,
  conversionMode,
  isProcessing,
  processingState,
  onModeChange,
  onReset,
}: ConversionHeaderProps) {
  return (
    <>
      <div className="flex flex-col sm:flex-row sm:justify-between sm:items-center gap-2 sm:gap-4 mb-4 sm:mb-6">
        <div className="flex flex-col sm:flex-row sm:items-center gap-1 sm:gap-4">
          <h1 className="text-xl sm:text-2xl md:text-3xl font-bold text-primary">HANA CV to SQL Converter</h1>
          <Link
            href="/how-to-use#hana-cv-to-sql-converter"
            className="inline-flex items-center px-3 py-1.5 text-sm font-medium text-primary bg-secondary/10 rounded-full hover:bg-secondary/20 transition-colors self-start sm:self-auto"
          >
            <Lightbulb className="w-4 h-4 mr-1.5 text-secondary" />
            How to Use
          </Link>
        </div>

        <div className="flex gap-2">
          {(isProcessing || processingState === "analyzing" || processingState === "checking-limits" || processingState === "initiating-conversion" || processingState === "polling-status" || processingState === "success") && (
            <button
              onClick={onReset}
              disabled={
                isProcessing ||
                processingState === "analyzing" ||
                processingState === "checking-limits" ||
                processingState === "initiating-conversion" ||
                processingState === "polling-status"
              }
              className={`flex items-center gap-2 px-3 py-1.5 text-xs font-medium rounded-md transition-colors ${
                processingState === "analyzing" ||
                processingState === "checking-limits" ||
                processingState === "initiating-conversion" ||
                processingState === "polling-status"
                  ? "text-gray-400 bg-gray-100 cursor-not-allowed"
                  : "text-gray-600 bg-gray-100 hover:bg-gray-200"
              }`}
            >
              <RotateCcw className="w-4 h-4 mr-1.5" />
              Reset
            </button>
          )}
        </div>
      </div>

      {isLoggedIn && (
        <div className="mb-4 p-3 bg-gray-50 rounded-md">
          <div className="flex justify-between items-center">
            <div>
              <span className="text-sm text-gray-500">Available Credits:</span>
              <span className="ml-2 font-medium">{credits}</span>
            </div>
            <Link href="/pricing" className="text-sm text-primary hover:text-primary/80 transition-colors">
              Purchase Credits
            </Link>
          </div>
        </div>
      )}

      {/* Conversion Mode Toggle */}
      <div className="mb-6">
        <div className="flex items-center justify-center gap-1 p-1 bg-gray-100 rounded-lg w-fit mx-auto animate-fadeIn flex-wrap">
          <button
            onClick={() => onModeChange("single")}
            disabled={isProcessing || processingState === "polling-status"}
            className={`px-4 sm:px-6 py-3 sm:py-2.5 min-h-11 rounded-md text-xs sm:text-sm font-medium transition-all duration-300 transform hover:scale-105 ${
              conversionMode === "single"
                ? "bg-white text-primary shadow-md scale-105"
                : "text-gray-600 hover:text-gray-800 hover:bg-gray-50"
            }`}
          >
            <span className="flex items-center gap-1.5 sm:gap-2">
              <svg className="w-3.5 h-3.5 sm:w-4 sm:h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
              </svg>
              Single File
            </span>
          </button>
          <button
            onClick={() => onModeChange("bulk")}
            disabled={isProcessing || processingState === "polling-status"}
            className={`px-4 sm:px-6 py-3 sm:py-2.5 min-h-11 rounded-md text-xs sm:text-sm font-medium transition-all duration-300 transform hover:scale-105 ${
              conversionMode === "bulk"
                ? "bg-white text-primary shadow-md scale-105"
                : "text-gray-600 hover:text-gray-800 hover:bg-gray-50"
            }`}
          >
            <span className="flex items-center gap-1.5 sm:gap-2">
              <svg className="w-3.5 h-3.5 sm:w-4 sm:h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 8h14M5 8a2 2 0 110-4h14a2 2 0 110 4M5 8v10a2 2 0 002 2h10a2 2 0 002-2V8m-9 4h4" />
              </svg>
              Bulk (ZIP)
            </span>
          </button>
        </div>
        <p className="text-xs text-gray-500 text-center mt-3 animate-fadeIn px-2" style={{ animationDelay: '0.1s' }}>
          {conversionMode === "single"
            ? "Convert one XML/TXT file at a time"
            : "Upload a ZIP file containing multiple XML/TXT files for batch conversion"}
        </p>
      </div>

      <style jsx>{`
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

        .animate-fadeIn {
          animation: fadeIn 0.3s ease-out;
        }
      `}</style>
    </>
  )
}
