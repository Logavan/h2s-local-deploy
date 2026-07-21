"use client"

import { useState, useEffect } from "react"

export function useLineCounter(text: string) {
  const [lineCount, setLineCount] = useState<number>(0)
  const [isProcessing, setIsProcessing] = useState(false)

  useEffect(() => {
    if (!text) {
      setLineCount(0)
      setIsProcessing(false)
      return
    }

    setIsProcessing(true)

    // Debug log
    // console.log("Counting lines for text:", text.substring(0, 50) + "...")

    // Reference the worker from the public directory after build
    const worker = new Worker("/workers/lineCounter.js")

    worker.onmessage = (e) => {
      setLineCount(e.data)
      setIsProcessing(false)
    }

    worker.postMessage(text)

    return () => {
      worker.terminate()
    }
  }, [text])

  return { lineCount, isProcessing }
}
