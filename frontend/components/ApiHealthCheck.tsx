"use client"

import { useState, useEffect } from "react"
import { checkBackendHealth } from "@/lib/api"
import { getApiUrl, isUrlReachable } from "@/lib/client-utils"
import { Button } from "@/components/ui/button"
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert"
import { CheckCircle, XCircle, RefreshCw } from "lucide-react"

export function ApiHealthCheck() {
  const [status, setStatus] = useState<"loading" | "healthy" | "error">("loading")
  const [message, setMessage] = useState<string>("")
  const [isChecking, setIsChecking] = useState(false)
  const [apiUrl, setApiUrl] = useState<string>("")

  const checkHealth = async () => {
    setIsChecking(true)
    setStatus("loading")
    setMessage("Checking API connection...")

    try {
      // Get the API URL for display
      const url = getApiUrl()
      setApiUrl(url)

      // First try a simple URL check
      const isReachable = await isUrlReachable(`${url}/health`)

      if (!isReachable) {
        setStatus("error")
        setMessage(`Cannot reach API at ${url}/health. Please check your network connection.`)
        setIsChecking(false)
        return
      }

      // Then do a full health check
      const isHealthy = await checkBackendHealth()

      if (isHealthy) {
        setStatus("healthy")
        setMessage(`API is connected at ${url}`)
      } else {
        setStatus("error")
        setMessage(
          `API at ${url} is reachable but not responding correctly. The service might be starting up or experiencing issues.`,
        )
      }
    } catch (error) {
      setStatus("error")
      setMessage(error instanceof Error ? error.message : "Failed to connect to API")
    } finally {
      setIsChecking(false)
    }
  }

  useEffect(() => {
    checkHealth()
  }, [])

  return (
    <div className="mb-4">
      <Alert variant={status === "healthy" ? "default" : "destructive"}>
        <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-3">
          <div className="flex items-center gap-2">
            {status === "healthy" ? (
              <CheckCircle className="h-4 w-4 flex-shrink-0" />
            ) : status === "loading" ? (
              <RefreshCw className="h-4 w-4 animate-spin flex-shrink-0" />
            ) : (
              <XCircle className="h-4 w-4 flex-shrink-0" />
            )}
            <AlertTitle className="text-sm sm:text-base">
              {status === "loading"
                ? "Checking API connection..."
                : status === "healthy"
                  ? "API Connected"
                  : "API Connection Error"}
            </AlertTitle>
          </div>
          <Button variant="outline" size="sm" onClick={checkHealth} disabled={isChecking} className="min-h-[44px] w-full sm:w-auto">
            <RefreshCw className={`h-4 w-4 mr-1 ${isChecking ? "animate-spin" : ""}`} />
            Refresh
          </Button>
        </div>
        <AlertDescription>
          <div className="mt-2 text-sm">
            {message}
            {status === "error" && (
              <div className="mt-2 text-sm overflow-hidden">
                <p>Troubleshooting steps:</p>
                <ol className="list-decimal pl-5 mt-1">
                  <li>Check if the backend server is running</li>
                  <li>Verify your network connection</li>
                  <li>Check if there are any firewall restrictions</li>
                  <li>Try refreshing the page</li>
                </ol>
              </div>
            )}
          </div>
        </AlertDescription>
      </Alert>
    </div>
  )
}
