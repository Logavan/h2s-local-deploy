// Get the API URL based on environment
export function getApiUrl() {
  // Check if we're in the v0 preview environment
  if (
    typeof window !== "undefined" &&
    (window.location.hostname.includes("v0.dev") || window.location.hostname.includes("vercel-v0"))
  ) {
    return "https://mock-api.example.com" // This won't actually be used, just a placeholder
  }

  // Check for environment variable
  if (process.env.NEXT_PUBLIC_API_BASE_URL) {
    return process.env.NEXT_PUBLIC_API_BASE_URL
  }

  // Default to local development URL if not in production
  if (process.env.NODE_ENV !== "production") {
    // Check if we're running in a development environment with the Flask backend
    // Try both common Flask development ports
    return "http://localhost:5000" // Flask default port
  }

  // In production, use window.location.origin to get the current domain
  if (typeof window !== "undefined") {
    // For production, we'll assume the API is at the same origin but with /api prefix
    return `${window.location.origin}/api`
  }

  // Fallback for server-side rendering
  return "/api"
}

// Check if the API is reachable
export async function checkApiConnectivity() {
  // In v0 preview, always return true
  if (
    typeof window !== "undefined" &&
    (window.location.hostname.includes("v0.dev") || window.location.hostname.includes("vercel-v0"))
  ) {
    return true
  }

  try {
    const controller = new AbortController()
    const timeoutId = setTimeout(() => controller.abort(), 100000000) // 10 second timeout

    // First try the health endpoint
    const apiUrl = getApiUrl()
    console.log(`Checking API connectivity at ${apiUrl}/health`)

    let response
    try {
      response = await fetch(`${apiUrl}/health`, {
        signal: controller.signal,
        method: "GET",
        headers: {
          "Cache-Control": "no-cache",
        },
      })
    } catch (error) {
      console.warn(`Health endpoint not found at ${apiUrl}/health, trying root endpoint`)
      // If health endpoint fails, try the root endpoint
      response = await fetch(apiUrl, {
        signal: controller.signal,
        method: "GET",
        headers: {
          "Cache-Control": "no-cache",
        },
      })
    }

    clearTimeout(timeoutId)
    return response.ok
  } catch (error) {
    console.error("API connectivity check failed:", error)
    return false
  }
}

// Format a date string
export function formatDate(dateString: string) {
  const date = new Date(dateString)
  return new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "long",
    day: "numeric",
  }).format(date)
}

// Format a datetime string
export function formatDateTime(dateTimeString: string) {
  const date = new Date(dateTimeString)
  return new Intl.DateTimeFormat("en-US", {
    year: "numeric",
    month: "long",
    day: "numeric",
    hour: "numeric",
    minute: "numeric",
    second: "numeric",
    hour12: true,
  }).format(date)
}

export async function isUrlReachable(url: string): Promise<boolean> {
  // In v0 preview, always return true for mock URLs
  if (
    typeof window !== "undefined" &&
    (window.location.hostname.includes("v0.dev") || window.location.hostname.includes("vercel-v0"))
  ) {
    return true
  }

  try {
    const controller = new AbortController()
    const timeoutId = setTimeout(() => controller.abort(), 5000) // 5 second timeout

    const response = await fetch(url, {
      method: "HEAD",
      mode: "no-cors", // This is important for cross-origin requests
      signal: controller.signal,
    })

    clearTimeout(timeoutId)
    return response.status >= 200 && response.status < 300
  } catch (error) {
    console.error("Error checking URL reachability:", error)
    return false
  }
}
