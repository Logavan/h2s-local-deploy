/**
 * Debug utilities for the application
 */

// Check if we're in development mode
const isDev = process.env.NODE_ENV === "development"

// Debug mode can only be enabled via localStorage in development
const isDebugEnabled = () => {
  if (typeof window === "undefined") return false

  // Only allow debug mode in non-production environments
  if (process.env.NODE_ENV === "production") return false

  // Check localStorage (only allowed in dev)
  const localStorageDebug = localStorage.getItem("debug_mode") === "true"

  return isDev && localStorageDebug
}

// Enable debug mode
export const enableDebugMode = () => {
  if (typeof window !== "undefined") {
    localStorage.setItem("debug_mode", "true")
    console.log("Debug mode enabled")
  }
}

// Disable debug mode
export const disableDebugMode = () => {
  if (typeof window !== "undefined") {
    localStorage.removeItem("debug_mode")
    console.log("Debug mode disabled")
  }
}

// Toggle debug mode
export const toggleDebugMode = () => {
  if (isDebugEnabled()) {
    disableDebugMode()
    return false
  } else {
    enableDebugMode()
    return true
  }
}

// Debug log - only logs in debug mode
export const debugLog = (...args: any[]) => {
  if (isDebugEnabled()) {
    console.log("[DEBUG]", ...args)
  }
}

// Debug error - only logs in debug mode
export const debugError = (...args: any[]) => {
  if (isDebugEnabled()) {
    console.error("[DEBUG ERROR]", ...args)
  }
}

// Debug warn - only logs in debug mode
export const debugWarn = (...args: any[]) => {
  if (isDebugEnabled()) {
    console.warn("[DEBUG WARN]", ...args)
  }
}

// Debug info - only logs in debug mode
export const debugInfo = (...args: any[]) => {
  if (isDebugEnabled()) {
    console.info("[DEBUG INFO]", ...args)
  }
}

// Debug group - only groups in debug mode
export const debugGroup = (label: string, fn: () => void) => {
  if (isDebugEnabled()) {
    console.group(`[DEBUG GROUP] ${label}`)
    try {
      fn()
    } finally {
      console.groupEnd()
    }
  }
}

// Debug timer - only times in debug mode
export const debugTime = (label: string, fn: () => any) => {
  if (isDebugEnabled()) {
    console.time(`[DEBUG TIMER] ${label}`)
    const result = fn()
    console.timeEnd(`[DEBUG TIMER] ${label}`)
    return result
  }
  return fn()
}

// Export debug state
export const debug = {
  isEnabled: isDebugEnabled,
  enable: enableDebugMode,
  disable: disableDebugMode,
  toggle: toggleDebugMode,
  log: debugLog,
  error: debugError,
  warn: debugWarn,
  info: debugInfo,
  group: debugGroup,
  time: debugTime,
}
