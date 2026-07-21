"use client"

import { useState, useEffect } from "react"
import { debug } from "../lib/debug-utils"
import { Button } from "./ui/button"
import { Bug } from "lucide-react"

export function DebugToggle() {
  const [isEnabled, setIsEnabled] = useState(false)

  // Initialize state on mount
  useEffect(() => {
    setIsEnabled(debug.isEnabled())
  }, [])

  const handleToggle = () => {
    const newState = debug.toggle()
    setIsEnabled(newState)

    // Force reload to apply debug mode changes
    if (typeof window !== "undefined") {
      window.location.reload()
    }
  }

  // Only show in development
  if (process.env.NODE_ENV !== "development") {
    return null
  }

  return (
    <Button
      variant="outline"
      size="sm"
      onClick={handleToggle}
      className={`fixed bottom-4 right-4 z-50 min-h-[44px] min-w-[44px] ${isEnabled ? "bg-yellow-100 border-yellow-500" : ""}`}
      title={isEnabled ? "Disable Debug Mode" : "Enable Debug Mode"}
    >
      <Bug className={`h-4 w-4 ${isEnabled ? "text-yellow-600" : ""}`} />
      <span className="hidden sm:inline ml-2">{isEnabled ? "Debug: ON" : "Debug: OFF"}</span>
    </Button>
  )
}
