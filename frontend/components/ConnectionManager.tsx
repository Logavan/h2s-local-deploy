"use client"

import { useState, useEffect, useRef } from "react"
import { getApiUrl } from "@/lib/api"

export function ConnectionManager() {
  const [apiUrl, setApiUrl] = useState("")
  const [isConnected, setIsConnected] = useState(false)
  const mounted = useRef(true)

  useEffect(() => {
    mounted.current = true
    const url = getApiUrl()
    setApiUrl(url)

    const checkConnection = async () => {
      try {
        const controller = new AbortController()
        const timeoutId = setTimeout(() => controller.abort(), 5000)
        const response = await fetch(`${url}/health`, { signal: controller.signal })
        clearTimeout(timeoutId)
        if (mounted.current) {
          setIsConnected(response.ok)
        }
      } catch {
        if (mounted.current) {
          setIsConnected(false)
        }
      }
    }

    checkConnection()
    // Reduced interval to 60s, initial check only on mount
    const interval = setInterval(checkConnection, 60000)
    return () => {
      mounted.current = false
      clearInterval(interval)
    }
  }, [])

  return null // Hidden component - only used for background health checks
}
