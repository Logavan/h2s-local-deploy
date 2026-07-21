"use client"

import { useState, useRef, useEffect } from "react"
import { Upload, FileUp, FileCode, ArrowUpDown, Loader2, Code, FileText, FileJson } from 'lucide-react'
import type React from "react"

interface FileUploadSectionProps {
  viewXmlFile: File | null
  setViewXmlFile: (file: File | null) => void
  viewFileContent: string
  setViewFileContent: (content: string) => void
  setError: (message: string | null) => void
  setProcessingState: (state: "idle" | "analyzing" | "checking-limits" | "initiating-conversion" | "polling-status" | "success" | "error") => void
  viewFileLineCount: number
  isViewFileProcessing: boolean
  disabled?: boolean
}

export function FileUploadSection({
  viewXmlFile,
  setViewXmlFile,
  viewFileContent,
  setViewFileContent,
  setError,
  setProcessingState,
  viewFileLineCount,
  isViewFileProcessing,
  disabled = false,
}: FileUploadSectionProps) {
  const fileInputRef = useRef<HTMLInputElement>(null)
  const [isDragging, setIsDragging] = useState(false)
  const [isHovering, setIsHovering] = useState(false)
  const [animationStep, setAnimationStep] = useState(0)
  const [fileInputKey, setFileInputKey] = useState(0) // Key for forcing re-render
  const [showSuccessAnimation, setShowSuccessAnimation] = useState(false)

  // Animation effect for the upload icon
  useEffect(() => {
    const interval = setInterval(() => {
      setAnimationStep((prev) => (prev + 1) % 6) // Increased animation steps
    }, 1200) // Slightly faster animation
    return () => clearInterval(interval)
  }, [])

  // Reset function that increments the key to force re-render of file input
  const resetFileInput = () => {
    setFileInputKey((prev) => prev + 1)
    setViewXmlFile(null)
    setViewFileContent("")
  }

  // Validate file type
  const validateFileType = (file: File): boolean => {
    const allowedTypes = [".xml", ".txt"]
    const fileExtension = file.name.toLowerCase().substring(file.name.lastIndexOf("."))
    return allowedTypes.includes(fileExtension)
  }

  // Read file content with improved error handling
  const readFileContent = async (file: File): Promise<string> => {
    return new Promise((resolve, reject) => {
      const reader = new FileReader()

      reader.onload = (event) => {
        if (event.target?.result) {
          resolve(event.target.result as string)
        } else {
          console.error("FileReader loaded but result is null or undefined")
          reject(new Error("Failed to read file content: No result"))
        }
      }

      reader.onerror = (error) => {
        console.error("FileReader error:", error)
        reject(error)
      }

      reader.onabort = () => {
        console.error("FileReader aborted")
        reject(new Error("File reading was aborted"))
      }

      try {
        reader.readAsText(file)
      } catch (error) {
        console.error("Exception during readAsText:", error)
        reject(error)
      }
    })
  }

  // Handle file change with improved error handling
  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    if (disabled) return

    if (e.target.files && e.target.files[0]) {
      setError(null) // Clear any previous errors
      setProcessingState("idle") // Reset processing state
      const file = e.target.files[0]

      // Check file size (limit to 10MB for example)
      if (file.size > 10 * 1024 * 1024) {
        setError("File is too large. Maximum size is 10MB.")
        e.target.value = ""
        return
      }

      if (!validateFileType(file)) {
        setError("Only .xml and .txt files are allowed")
        e.target.value = ""
        return
      }

      try {
        // console.log("Reading file:", file.name, "Size:", file.size, "Type:", file.type)
        const content = await readFileContent(file)

        if (!content || content.length === 0) {
          throw new Error("File appears to be empty")
        }

        setViewXmlFile(file)
        setViewFileContent(content)

        // Show success animation
        setShowSuccessAnimation(true)
        setTimeout(() => setShowSuccessAnimation(false), 1500)
      } catch (error: any) {
        console.error("Error reading file:", error)
        setError(`Error reading file: ${error.message || "Unknown error"}`)
        e.target.value = ""
      }
    }
  }

  // Handle file drop with improved error handling
  const handleDrop = async (e: React.DragEvent<HTMLDivElement>) => {
    if (disabled) return

    e.preventDefault()
    e.stopPropagation()
    setIsDragging(false)

    setError(null) // Clear any previous errors
    setProcessingState("idle") // Reset processing state

    const files = Array.from(e.dataTransfer.files)
    if (files.length > 1) {
      setError("Please upload only one file at a time")
      return
    }

    const file = files[0]

    // Check file size (limit to 10MB for example)
    if (file.size > 10 * 1024 * 1024) {
      setError("File is too large. Maximum size is 10MB.")
      return
    }

    if (!validateFileType(file)) {
      setError("Only .xml and .txt files are allowed")
      return
    }

    try {
      console.log("Reading dropped file:", file.name, "Size:", file.size, "Type:", file.type)
      const content = await readFileContent(file)

      if (!content || content.length === 0) {
        throw new Error("File appears to be empty")
      }

      setViewXmlFile(file)
      setViewFileContent(content)

      // Show success animation
      setShowSuccessAnimation(true)
      setTimeout(() => setShowSuccessAnimation(false), 1500)
    } catch (error: any) {
      console.error("Error reading dropped file:", error)
      setError(`Error reading file: ${error.message || "Unknown error"}`)
    }
  }

  const handleDragOver = (e: React.DragEvent<HTMLDivElement>) => {
    if (disabled) return
    e.preventDefault()
    e.stopPropagation()
    if (!isDragging) setIsDragging(true)
  }

  const handleDragEnter = (e: React.DragEvent<HTMLDivElement>) => {
    if (disabled) return
    e.preventDefault()
    e.stopPropagation()
    setIsDragging(true)
  }

  const handleDragLeave = (e: React.DragEvent<HTMLDivElement>) => {
    if (disabled) return
    e.preventDefault()
    e.stopPropagation()
    setIsDragging(false)
  }

  const handleFileUploadClick = () => {
    if (disabled) return
    if (fileInputRef.current) {
      fileInputRef.current.click()
    }
  }

  // Get the appropriate icon based on animation step
  const getAnimatedIcon = () => {
    if (showSuccessAnimation) {
      return (
        <div className="relative">
          <div className="absolute inset-0 bg-green-500 rounded-full scale-150 animate-ping opacity-30"></div>
          <FileCode className="w-12 h-12 sm:w-16 sm:h-16 text-green-500 animate-bounce" />
        </div>
      )
    }

    if (isDragging) {
      return (
        <div className="relative">
          <ArrowUpDown className="w-12 h-12 sm:w-16 sm:h-16 text-primary animate-bounce" />
          <div className="absolute inset-0 border-4 border-primary rounded-full scale-150 animate-ping opacity-30"></div>
        </div>
      )
    }

    switch (animationStep) {
      case 0:
        return <Upload className="w-12 h-12 sm:w-16 sm:h-16 text-primary/70 transition-all duration-500 transform hover:scale-110" />
      case 1:
        return <FileUp className="w-12 h-12 sm:w-16 sm:h-16 text-primary/80 transition-all duration-500 transform hover:rotate-6" />
      case 2:
        return <FileCode className="w-12 h-12 sm:w-16 sm:h-16 text-primary transition-all duration-500 transform hover:scale-110" />
      case 3:
        return <Code className="w-12 h-12 sm:w-16 sm:h-16 text-primary/90 transition-all duration-500 transform hover:-rotate-6" />
      case 4:
        return <FileText className="w-12 h-12 sm:w-16 sm:h-16 text-primary/85 transition-all duration-500 transform hover:scale-110" />
      case 5:
        return <FileJson className="w-12 h-12 sm:w-16 sm:h-16 text-primary/75 transition-all duration-500 transform hover:rotate-6" />
      default:
        return <Upload className="w-12 h-12 sm:w-16 sm:h-16 text-primary/70 transition-all duration-500" />
    }
  }

  return (
    <div className="w-full mb-6">
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
        onClick={disabled ? undefined : handleFileUploadClick}
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
          id="viewXmlInput"
          accept=".xml,.txt"
          ref={fileInputRef}
          key={fileInputKey} // This forces re-render when key changes
          disabled={disabled}
        />

        <div className="flex flex-col items-center justify-center h-full min-h-[240px] p-4 sm:p-8 relative z-10">
          {/* Animated icon */}
          <div
            className={`mb-6 transition-transform duration-300 ease-in-out ${disabled ? "opacity-50" : "transform hover:scale-110"}`}
          >
            {getAnimatedIcon()}
          </div>

          {/* Text content */}
          <div className="text-center">
            <h3
              className={`text-xl font-semibold mb-2 transition-all duration-300 ${disabled ? "text-gray-400" : "text-gray-700 dark:text-gray-200 hover:text-primary"}`}
            >
              {viewXmlFile ? "File Loaded" : "AI-Powered XML Analysis"}
            </h3>
            <p
              className={`mb-2 transition-all duration-300 ${disabled ? "text-gray-400" : "text-gray-500 dark:text-gray-400"}`}
            >
              {viewXmlFile ? (
                <span className={`font-medium ${disabled ? "text-gray-400" : "text-primary animate-pulse"}`}>
                  {viewXmlFile.name}
                </span>
              ) : (
                <span className="relative group">
                  Let our AI Agent analyze your XML. Click to upload or drag and drop
                  {!disabled && (
                    <span className="absolute bottom-0 left-0 w-0 h-0.5 bg-primary transition-all duration-300 group-hover:w-full group-active:w-full"></span>
                  )}
                </span>
              )}
            </p>
            <p
              className={`text-xs transition-all duration-300 ${disabled ? "text-gray-400" : "text-gray-400 dark:text-gray-500"}`}
            >
              Supported formats for AI Agent: <span className="font-mono">.xml</span>, <span className="font-mono">.txt</span>
            </p>
          </div>
        </div>
      </div>

      {/* File info and line count */}
      {viewFileContent && (
        <div className="mt-3 flex flex-col sm:flex-row justify-between items-start sm:items-center">
          <div
            className={`flex items-center text-sm mb-2 sm:mb-0 transition-all duration-300 ${disabled ? "text-gray-400" : "text-gray-500 hover:text-gray-700"}`}
          >
            <FileCode className={`w-4 h-4 mr-1.5 ${disabled ? "text-gray-400" : "text-primary animate-pulse"}`} />
            <span>
              File loaded:{" "}
              <span className={`font-medium ${disabled ? "text-gray-400" : "text-gray-700"}`}>{viewXmlFile?.name}</span>
            </span>
          </div>
          <div
            className={`flex items-center rounded-full px-3 py-1.5 transition-all duration-300 ${disabled ? "bg-gray-100" : "bg-primary/10 hover:bg-primary/20 hover:shadow-sm"}`}
          >
            {isViewFileProcessing ? (
              <div className="flex items-center">
                <Loader2 className={`w-3.5 h-3.5 animate-spin mr-1.5 ${disabled ? "text-gray-400" : "text-primary"}`} />
                <span className={`text-sm font-medium ${disabled ? "text-gray-400" : "text-primary"}`}>
                  Counting lines...
                </span>
              </div>
            ) : (
              <div className="flex items-center">
                <span className={`text-sm font-medium mr-1 ${disabled ? "text-gray-400" : "text-primary"}`}>
                  {viewFileLineCount.toLocaleString()}
                </span>
                <span className={`text-sm ${disabled ? "text-gray-400" : "text-primary/80"}`}>lines</span>
              </div>
            )}
          </div>
        </div>
      )}

    </div>
  )
}
