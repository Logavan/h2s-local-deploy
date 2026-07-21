"use client"

import { useState, useCallback } from "react"
import { FileUploadSection } from "./FileUploadSection"
import type { FileUploadSectionProps } from "./FileUploadSection"

interface FileUploaderProps {
  onFileSelect: (file: File, content: string) => void
  isProcessing: boolean
  viewFileName?: string | null
}

export function FileUploader({ onFileSelect, isProcessing, viewFileName }: FileUploaderProps) {
  const [viewXmlFile, setViewXmlFile] = useState<File | null>(null)
  const [viewFileContent, setViewFileContent] = useState("")
  const [viewFileLineCount, setViewFileLineCount] = useState(0)
  const [isViewFileProcessing, setIsViewFileProcessing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [processingState, setProcessingState] = useState<FileUploadSectionProps["setProcessingState"] extends (state: infer S) => void ? S : never>("idle" as FileUploadSectionProps["setProcessingState"] extends (state: infer S) => void ? S : never)

  // Handle file change from FileUploadSection - properly pass the file argument
  const handleFileChange = useCallback((file: File | null) => {
    if (file) {
      // Read content and call parent callback
      const reader = new FileReader()
      reader.onload = (e) => {
        const content = e.target?.result as string
        setViewXmlFile(file)
        setViewFileContent(content)
        setViewFileLineCount(content.split("\n").length)
        // Call the parent callback with file and content
        onFileSelect(file, content)
      }
      reader.readAsText(file)
    } else {
      setViewXmlFile(null)
      setViewFileContent("")
      setViewFileLineCount(0)
    }
  }, [onFileSelect])

  return (
    <FileUploadSection
      viewXmlFile={viewXmlFile}
      setViewXmlFile={handleFileChange}
      viewFileContent={viewFileContent}
      setViewFileContent={(content) => {
        setViewFileContent(content)
        setViewFileLineCount(content.split("\n").length)
      }}
      setError={setError}
      setProcessingState={setProcessingState}
      viewFileLineCount={viewFileLineCount}
      isViewFileProcessing={isViewFileProcessing}
      disabled={isProcessing}
    />
  )
}
