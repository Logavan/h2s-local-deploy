"use client"

import { useState, useRef, useCallback, useEffect } from "react"
import { Upload, FileArchive, FileCode, CheckCircle, XCircle, Clock, Loader2, AlertTriangle, Package, Archive, FileStack } from "lucide-react"
import type React from "react"

export interface BulkFileInfo {
  id: string
  name: string
  content: string
  nodes: number
  status: "pending" | "analyzing" | "processing" | "completed" | "failed"
  error?: string
}

interface BulkFileUploadSectionProps {
  onFilesExtracted: (files: BulkFileInfo[]) => void
  onAnalysisComplete: (files: BulkFileInfo[]) => void
  disabled?: boolean
  userEmail?: string
}

export function BulkFileUploadSection({
  onFilesExtracted,
  onAnalysisComplete,
  disabled = false,
  userEmail = "",
}: BulkFileUploadSectionProps) {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [isDragging, setIsDragging] = useState(false)
  const [isHovering, setIsHovering] = useState(false)
  const [animationStep, setAnimationStep] = useState(0)
  const [showSuccessAnimation, setShowSuccessAnimation] = useState(false)
  const [zipFile, setZipFile] = useState<File | null>(null)
  const [extractedFiles, setExtractedFiles] = useState<BulkFileInfo[]>([])
  const [isExtracting, setIsExtracting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [isAnalyzing, setIsAnalyzing] = useState(false)
  const [zipFileRef, setZipFileRef] = useState<File | null>(null)
  const [hasTriggeredAnalysis, setHasTriggeredAnalysis] = useState(false)

  // Animation effect for the upload icon
  useEffect(() => {
    const interval = setInterval(() => {
      setAnimationStep((prev) => (prev + 1) % 6)
    }, 1200)
    return () => clearInterval(interval)
  }, [])

  // Generate unique ID
  const generateId = () => Math.random().toString(36).substring(2, 15)

  // Extract files from ZIP - just get file names without analysis
  const extractZipContents = useCallback(async (file: File): Promise<BulkFileInfo[]> => {
    setIsExtracting(true)
    setError(null)
    setHasTriggeredAnalysis(false)

    try {
      // Use JSZip or similar to read ZIP contents client-side
      // For now, we'll use a simple approach with the backend
      const JSZip = (await import('jszip')).default
      const zip = new JSZip()
      const zipData = await zip.loadAsync(file)
      
      const files: BulkFileInfo[] = []
      
      for (const [filename, zipEntry] of Object.entries(zipData.files)) {
        // Skip directories
        if (zipEntry.dir) continue
        
        // Only include XML and TXT files
        const lowerName = filename.toLowerCase()
        if (!lowerName.endsWith('.xml') && !lowerName.endsWith('.txt')) continue
        
        files.push({
          id: generateId(),
          name: filename,
          content: "", // Don't load content yet
          nodes: 0,    // Unknown until analysis
          status: "pending" as const,
        })
      }

      if (files.length === 0) {
        setError("No valid XML/TXT files found in the ZIP")
        return []
      }

      setExtractedFiles(files)
      setZipFileRef(file) // Store file reference for later analysis
      onFilesExtracted(files)
      return files
    } catch (err: any) {
      console.warn("ZIP extraction error:", err)
      setError(err.message || "Failed to extract files from ZIP")
      return []
    } finally {
      setIsExtracting(false)
    }
  }, [onFilesExtracted])

  // Analyze files - calls the backend API
  const analyzeFiles = useCallback(async () => {
    if (!zipFileRef || extractedFiles.length === 0) return
    
    setIsAnalyzing(true)
    setHasTriggeredAnalysis(true)
    setError(null)

    try {
      const { analyzeBulkZip } = await import("@/lib/api")
      const result = await analyzeBulkZip(zipFileRef, userEmail)
      
      if (!result.success) {
        const errorMessage = result.error || "Failed to analyze ZIP file"
        if (errorMessage.includes("No valid XML/TXT files found")) {
          setError("No valid XML/TXT files found in the ZIP")
        } else if (errorMessage.includes("Invalid ZIP")) {
          setError("The file appears to be corrupted")
        } else {
          setError(errorMessage)
        }
        return
      }

      // Map the API response to our BulkFileInfo format
      const analyzedFiles: BulkFileInfo[] = result.files.map((f: any) => {
        // Find matching file in extractedFiles by name
        const existingFile = extractedFiles.find(ef => ef.name === f.file_name)
        console.log(`[Analysis] Looking for existingFile with name="${f.file_name}" in extractedFiles:`, extractedFiles.map(ef => ef.name))
        console.log(`[Analysis] Match found:`, existingFile ? "YES" : "NO", existingFile?.name)
        console.log(`[Analysis] File content length:`, (f.content || "").length)
        return {
          id: existingFile?.id || generateId(),
          name: f.file_name,
          content: f.content || "",
          nodes: f.node_count || 0,
          status: "pending" as const,
        }
      })

      setExtractedFiles(analyzedFiles)
      onAnalysisComplete(analyzedFiles)
    } catch (err: any) {
      console.warn("Analysis error:", err)
      setError(err.message || "Failed to analyze files")
    } finally {
      setIsAnalyzing(false)
    }
  }, [zipFileRef, extractedFiles, userEmail, onAnalysisComplete])

  // Validate ZIP file
  const validateZipFile = (file: File): string | null => {
    // Check file type
    if (!file.name.toLowerCase().endsWith('.zip')) {
      return "Please upload a .zip file"
    }

    // Check file size (50MB limit)
    if (file.size > 50 * 1024 * 1024) {
      return "ZIP file is too large. Maximum size is 50MB"
    }

    return null
  }

  // Handle file selection
  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (disabled || !e.target.files?.[0]) return

    const file = e.target.files[0]
    
    // Validate
    const validationError = validateZipFile(file)
    if (validationError) {
      setError(validationError)
      return
    }

    setError(null)
    setZipFile(file)
    
    // Extract contents
    const files = await extractZipContents(file)
    
    if (files.length === 0) {
      setError("No valid XML/TXT files found in the ZIP")
    }
  }

  // Handle drag and drop
  const handleDrop = async (e: React.DragEvent<HTMLDivElement>) => {
    if (disabled) return
    e.preventDefault()
    e.stopPropagation()
    setIsDragging(false)

    const files = Array.from(e.dataTransfer.files)
    if (files.length !== 1) {
      setError("Please drop a single ZIP file")
      return
    }

    const file = files[0]
    
    // Validate
    const validationError = validateZipFile(file)
    if (validationError) {
      setError(validationError)
      return
    }

    setError(null)
    setZipFile(file)
    
    // Extract contents
    const extracted = await extractZipContents(file)
    
    if (extracted.length === 0) {
      setError("No valid XML/TXT files found in the ZIP")
    }
  }

  const handleDragOver = (e: React.DragEvent<HTMLDivElement>) => {
    if (disabled) return
    e.preventDefault()
    if (!isDragging) setIsDragging(true)
  }

  const handleDragEnter = (e: React.DragEvent<HTMLDivElement>) => {
    if (disabled) return
    e.preventDefault()
    setIsDragging(true)
  }

  const handleDragLeave = (e: React.DragEvent<HTMLDivElement>) => {
    if (disabled) return
    e.preventDefault()
    setIsDragging(false)
  }

  const handleClick = () => {
    if (disabled) return
    fileInputRef.current?.click()
  }

  // Get status icon
  const getStatusIcon = (status: BulkFileInfo["status"]) => {
    switch (status) {
      case "completed":
        return <CheckCircle className="w-4 h-4 text-green-500" />
      case "failed":
        return <XCircle className="w-4 h-4 text-red-500" />
      case "analyzing":
      case "processing":
        return <Loader2 className="w-4 h-4 text-blue-500 animate-spin" />
      default:
        return <Clock className="w-4 h-4 text-gray-400" />
    }
  }

  // Get the appropriate icon based on animation step (similar to single file)
  const getAnimatedIcon = () => {
    if (showSuccessAnimation) {
      return (
        <div className="relative">
          <div className="absolute inset-0 bg-green-500 rounded-full scale-150 animate-ping opacity-30"></div>
          <Package className="w-12 h-12 text-green-500 animate-bounce" />
        </div>
      )
    }

    if (isDragging) {
      return (
        <div className="relative">
          <Archive className="w-12 h-12 text-primary animate-bounce" />
          <div className="absolute inset-0 border-4 border-primary rounded-full scale-150 animate-ping opacity-30"></div>
        </div>
      )
    }

    switch (animationStep) {
      case 0:
        return <Upload className="w-12 h-12 text-primary/70 transition-all duration-500 transform hover:scale-110" />
      case 1:
        return <Archive className="w-12 h-12 text-primary/80 transition-all duration-500 transform hover:rotate-6" />
      case 2:
        return <Package className="w-12 h-12 text-primary transition-all duration-500 transform hover:scale-110" />
      case 3:
        return <FileStack className="w-12 h-12 text-primary/90 transition-all duration-500 transform hover:-rotate-6" />
      case 4:
        return <FileArchive className="w-12 h-12 text-primary/85 transition-all duration-500 transform hover:scale-110" />
      case 5:
        return <Archive className="w-12 h-12 text-primary/75 transition-all duration-500 transform hover:rotate-6" />
      default:
        return <Upload className="w-12 h-12 text-primary/70 transition-all duration-500" />
    }
  }

  // Calculate totals
  const totalNodes = extractedFiles.reduce((sum, f) => sum + f.nodes, 0)

  // Notify parent when analysis is complete
  const handleAnalysisComplete = () => {
    onAnalysisComplete(extractedFiles)
  }

  return (
    <div className="w-full mb-6">
      {/* Upload Area */}
      <div
        className={`relative overflow-hidden border-2 border-dashed rounded-lg transition-all duration-300 ${
          disabled
            ? "border-gray-200 bg-gray-50 cursor-not-allowed opacity-60"
            : isDragging
              ? "border-primary bg-primary/5 scale-[1.02] shadow-lg"
              : isHovering
                ? "border-primary/70 bg-primary/5 shadow-md"
                : "border-gray-300 hover:border-primary/50 hover:bg-gray-50/50"
        }`}
        onDragOver={disabled ? undefined : handleDragOver}
        onDragEnter={disabled ? undefined : handleDragEnter}
        onDragLeave={disabled ? undefined : handleDragLeave}
        onDrop={disabled ? undefined : handleDrop}
        onClick={disabled ? undefined : handleClick}
        onMouseEnter={() => !disabled && setIsHovering(true)}
        onMouseLeave={() => !disabled && setIsHovering(false)}
      >
        {/* Background animation */}
        <div
          className={`absolute inset-0 bg-gradient-to-r from-primary/10 via-secondary/5 to-primary/10 bg-[length:200%_100%] animate-gradient-x transition-opacity duration-500 ${
            (isDragging || isHovering) && !disabled ? "opacity-100" : "opacity-0"
          }`}
        ></div>

        {/* Animated particles for drag state */}
        {isDragging && !disabled && (
          <>
            <div className="absolute top-1/4 left-1/4 w-2 h-2 bg-primary rounded-full animate-float opacity-70"></div>
            <div className="absolute top-3/4 left-1/3 w-3 h-3 bg-secondary rounded-full animate-float-delayed opacity-60"></div>
            <div className="absolute top-1/3 right-1/4 w-2 h-2 bg-primary rounded-full animate-float-slow opacity-70"></div>
            <div className="absolute bottom-1/4 right-1/3 w-3 h-3 bg-secondary rounded-full animate-float-delayed-slow opacity-60"></div>
          </>
        )}

        <input
          type="file"
          onChange={handleFileChange}
          className="hidden"
          id="bulkZipInput"
          accept=".zip"
          ref={fileInputRef}
          disabled={disabled}
        />

        <div className="flex flex-col items-center justify-center h-full min-h-[240px] p-4 sm:p-8 relative z-10">
          {/* Animated icon */}
          <div
            className={`mb-6 transition-transform duration-300 ease-in-out ${disabled ? "opacity-50" : "transform hover:scale-110"}`}
          >
            {isExtracting ? (
              <Loader2 className="w-16 h-16 text-primary animate-spin" />
            ) : zipFile ? (
              <div className="relative">
                <FileArchive className="w-16 h-16 text-primary" />
              </div>
            ) : (
              getAnimatedIcon()
            )}
          </div>

          {/* Text content */}
          <div className="text-center">
            <h3
              className={`text-xl font-semibold mb-2 transition-all duration-300 ${disabled ? "text-gray-400" : "text-gray-700 dark:text-gray-200 hover:text-primary"}`}
            >
              {zipFile ? "ZIP Loaded" : "AI-Powered Bulk Analysis"}
            </h3>
            <p
              className={`mb-2 transition-all duration-300 ${disabled ? "text-gray-400" : "text-gray-500 dark:text-gray-400"}`}
            >
              {zipFile ? (
                <span className={`font-medium ${disabled ? "text-gray-400" : "text-primary animate-pulse"}`}>
                  {zipFile.name}
                </span>
              ) : (
                <span className="relative group">
                  Let our AI Agent analyze your ZIP. Click to upload or drag and drop
                  {!disabled && (
                    <span className="absolute bottom-0 left-0 w-0 h-0.5 bg-primary transition-all duration-300 group-hover:w-full group-active:w-full"></span>
                  )}
                </span>
              )}
            </p>
            <p
              className={`text-xs transition-all duration-300 ${disabled ? "text-gray-400" : "text-gray-400 dark:text-gray-500"}`}
            >
              Supported formats for AI Agent: <span className="font-mono">.zip</span> (contains .xml, .txt files)
            </p>
          </div>
        </div>
      </div>

      {/* Error Message */}
      {error && (
        <div className="mt-3 p-3 bg-red-50 border border-red-200 rounded-md flex items-center gap-2">
          <AlertTriangle className="w-4 h-4 text-red-500 flex-shrink-0" />
          <p className="text-sm text-red-700">{error}</p>
        </div>
      )}

      {/* Extracted Files Summary Table */}
      {extractedFiles.length > 0 && (
        <div className="mt-4 animate-slideUp">
          <div className="flex items-center justify-between mb-3">
            <h3 className="text-lg font-semibold text-gray-800 dark:text-gray-200">
              Files Summary ({extractedFiles.length} files)
            </h3>
            {isAnalyzing && (
              <button
                disabled
                className="px-4 py-2 sm:px-6 sm:py-3 bg-blue-600 text-white text-sm font-medium rounded-lg flex items-center gap-2 min-h-[44px]"
              >
                <Loader2 className="w-4 h-4 animate-spin" />
                Analyzing...
              </button>
            )}
            {!isAnalyzing && (
              <button
                onClick={analyzeFiles}
                className="px-4 py-2 sm:px-6 sm:py-3 bg-blue-600 text-white text-sm sm:text-base font-medium rounded-lg hover:bg-blue-700 transition-all duration-300 hover:scale-105 active:scale-95 min-h-[44px] touch-manipulation"
              >
                Analyze Files
              </button>
            )}
          </div>

          <div className="overflow-x-auto border border-gray-200 rounded-lg animate-fadeIn" style={{ animationDelay: '0.1s' }}>
            <table className="w-full text-xs sm:text-sm min-w-[500px]">
              <thead className="bg-gray-50 dark:bg-gray-800 border-b border-gray-200">
                <tr>
                  <th className="px-2 sm:px-4 py-2 sm:py-3 text-left font-medium text-gray-600 dark:text-gray-300">File Name</th>
                  <th className="px-2 sm:px-4 py-2 sm:py-3 text-center font-medium text-gray-600 dark:text-gray-300 hidden sm:table-cell">Nodes</th>
                  <th className="px-2 sm:px-4 py-2 sm:py-3 text-center font-medium text-gray-600 dark:text-gray-300">Status</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-gray-100 dark:divide-gray-700">
                {extractedFiles.map((file, index) => (
                  <tr key={file.id} className="hover:bg-gray-50 dark:hover:bg-gray-800/50 transition-colors animate-fadeIn" style={{ animationDelay: `${0.1 + index * 0.05}s` }}>
                    <td className="px-2 sm:px-4 py-2 sm:py-3">
                      <div className="flex items-center gap-1 sm:gap-2 max-w-[120px] sm:max-w-none overflow-hidden">
                        <FileCode className="w-3 h-3 sm:w-4 sm:h-4 text-primary flex-shrink-0" />
                        <span className="font-medium text-gray-700 dark:text-gray-200 truncate">{file.name}</span>
                      </div>
                    </td>
                    <td className="px-2 sm:px-4 py-2 sm:py-3 text-center hidden sm:table-cell">
                      {hasTriggeredAnalysis ? (
                        file.nodes
                      ) : (
                        <span className="text-gray-400">-</span>
                      )}
                    </td>
                    <td className="px-2 sm:px-4 py-2 sm:py-3 text-center">
                      <div className="flex items-center justify-center">
                        {getStatusIcon(file.status)}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
              <tfoot className="bg-gray-50 dark:bg-gray-800 border-t border-gray-200 font-medium">
                <tr>
                  <td className="px-2 sm:px-4 py-2 sm:py-3 text-left text-gray-700 dark:text-gray-200">TOTAL</td>
                  <td className="px-2 sm:px-4 py-2 sm:py-3 text-center text-gray-700 dark:text-gray-200 hidden sm:table-cell">
                    {hasTriggeredAnalysis ? totalNodes : "-"}
                  </td>
                  <td className="px-2 sm:px-4 py-2 sm:py-3"></td>
                </tr>
              </tfoot>
            </table>
          </div>

          {/* Quick Summary Card - Only show after analysis */}
          {hasTriggeredAnalysis && (
            <div className="mt-4 p-3 sm:p-4 bg-blue-50 border border-blue-200 rounded-lg">
              <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 sm:gap-4">
                <div className="grid grid-cols-2 gap-2 sm:gap-4 sm:flex-1">
                  <div className="text-center">
                    <p className="text-[10px] sm:text-xs text-blue-600 uppercase font-medium">Files</p>
                    <p className="text-lg sm:text-2xl font-bold text-blue-800">{extractedFiles.length}</p>
                  </div>
                  <div className="text-center">
                    <p className="text-[10px] sm:text-xs text-blue-600 uppercase font-medium">Nodes</p>
                    <p className="text-lg sm:text-2xl font-bold text-blue-800">{totalNodes}</p>
                  </div>
                </div>
              </div>
            </div>
          )}
        </div>
      )}

      {/* Disabled State Warning */}
      {disabled && extractedFiles.length === 0 && (
        <div className="mt-4 p-3 bg-yellow-50 border border-yellow-200 rounded-md">
          <div className="flex items-center">
            <svg className="w-5 h-5 text-yellow-600 mr-2" fill="currentColor" viewBox="0 0 20 20">
              <path fillRule="evenodd" d="M8.257 3.099c.765-1.36 2.722-1.36 3.486 0l5.58 9.92c.75 1.334-.213 2.98-1.742 2.98H4.42c-1.53 0-2.493-1.646-1.743-2.98l5.58-9.92zM11 13a1 1 0 11-2 0 1 1 0 012 0zm-1-8a1 1 0 00-1 1v3a1 1 0 002 0V6a1 1 0 00-1-1z" clipRule="evenodd" />
            </svg>
            <p className="text-sm text-yellow-700 font-medium">
              Bulk upload is disabled. Please reset to upload a new ZIP file.
            </p>
          </div>
        </div>
      )}

      {/* CSS Animations */}
      <style jsx>{`
        @keyframes fadeIn {
          from { opacity: 0; transform: translateY(10px); }
          to { opacity: 1; transform: translateY(0); }
        }
        
        @keyframes slideUp {
          from { opacity: 0; transform: translateY(20px); }
          to { opacity: 1; transform: translateY(0); }
        }

        @keyframes bounce {
          0%, 100% { transform: translateY(0); }
          50% { transform: translateY(-5px); }
        }

        @keyframes pulse {
          0%, 100% { opacity: 1; }
          50% { opacity: 0.8; }
        }

        @keyframes float {
          0% { transform: translateY(0px) rotate(0deg); }
          50% { transform: translateY(-20px) rotate(180deg); }
          100% { transform: translateY(0px) rotate(360deg); }
        }

        @keyframes gradientX {
          0% { background-position: 0% 50%; }
          50% { background-position: 100% 50%; }
          100% { background-position: 0% 50%; }
        }

        .animate-fadeIn {
          animation: fadeIn 0.4s ease-out forwards;
        }

        .animate-slideUp {
          animation: slideUp 0.5s ease-out forwards;
        }

        .animate-bounce {
          animation: bounce 1s ease-in-out infinite;
        }

        
        .animate-pulse {
          animation: pulse 2s ease-in-out infinite;
        }

        .animate-float {
          animation: float 3s ease-in-out infinite;
        }

        .animate-float-delayed {
          animation: float 3s ease-in-out infinite;
          animation-delay: 0.5s;
        }

        .animate-float-slow {
          animation: float 4s ease-in-out infinite;
          animation-delay: 1s;
        }

        .animate-float-delayed-slow {
          animation: float 4s ease-in-out infinite;
          animation-delay: 1.5s;
        }

        .animate-gradient-x {
          animation: gradientX 3s ease infinite;
        }
      `}</style>
    </div>
  )
}
