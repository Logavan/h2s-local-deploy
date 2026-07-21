"use client"

import { useState, useEffect } from "react"
import { AlertCircle, CheckCircle, RefreshCw } from "lucide-react"
import { checkBackendHealth, getApiUrl } from "@/lib/api"

export function BackendConnectionStatus() {
  const [status, setStatus] = useState<"checking" | "connected" | "disconnected">("checking")
  const [lastChecked, setLastChecked] = useState<Date | null>(null)
  const [isChecking, setIsChecking] = useState(false)
  const [apiUrl, setApiUrl] = useState("")

  const checkConnection = async () => {
    setIsChecking(true)
    setStatus("checking")

    try {
      // Get API URL dynamically from environment
      const url = getApiUrl()
      setApiUrl(url)

      // Check if backend is reachable
      const isHealthy = await checkBackendHealth()

      setStatus(isHealthy ? "connected" : "disconnected")
      setLastChecked(new Date())
    } catch (error) {
      console.error("Connection check failed:", error)
      setStatus("disconnected")
    } finally {
      setIsChecking(false)
    }
  }

  useEffect(() => {
    checkConnection()

    // Set up periodic checking
    const interval = setInterval(() => {
      if (!isChecking) {
        checkConnection()
      }
    }, 60000) // Check every minute

    return () => clearInterval(interval)
  }, [])

  return (
    <div
      className={`mb-4 p-3 rounded-md border ${
        status === "connected"
          ? "bg-green-50 border-green-200"
          : status === "checking"
            ? "bg-slate-50 border-slate-200"
            : "bg-red-50 border-red-200"
      }`}
    >
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          {status === "connected" ? (
            <CheckCircle className="h-5 w-5 text-green-500" />
          ) : status === "checking" ? (
            <RefreshCw className="h-5 w-5 text-slate-500 animate-spin" />
          ) : (
            <AlertCircle className="h-5 w-5 text-red-500" />
          )}

          <div>
            <p className="font-medium">
              {status === "connected"
                ? "Backend Connected"
                : status === "checking"
                  ? "Checking Connection..."
                  : "Backend Disconnected"}
            </p>
            <p className="text-xs text-gray-500">
              {status === "disconnected" ? "Unable to connect to the server. Please try later." : apiUrl}
              {lastChecked && status !== "checking" && <span> • Last checked: {lastChecked.toLocaleTimeString()}</span>}
            </p>
          </div>
        </div>

        <button
          onClick={checkConnection}
          disabled={isChecking}
          className="px-3 py-2 min-h-[44px] min-w-[44px] text-xs rounded-md bg-slate-100 hover:bg-slate-200 transition-colors flex items-center justify-center"
        >
          <RefreshCw className={`h-3 w-3 ${isChecking ? "animate-spin" : ""}`} />
        </button>
      </div>

      {status === "disconnected" && (
        <div className="mt-2 text-sm text-red-700">
          <p>Unable to connect to the server. Please try later.</p>
          <ul className="list-disc pl-5 mt-1 space-y-1">
            <li>The backend server is running</li>
            <li>The configured API URL is correct</li>
            <li>There are no network issues or firewall restrictions</li>
          </ul>
        </div>
      )}
    </div>
  )
}
