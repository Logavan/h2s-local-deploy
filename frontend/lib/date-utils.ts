/**
 * Utility functions for date and time handling
 */

/**
 * Get current date and time in IST (Indian Standard Time, UTC+5:30)
 * @returns Object containing formatted IST datetime and date
 */
export function getISTDateTime() {
  const now = new Date()

  // Convert to IST (UTC+5:30)
  const istTime = new Date(now.getTime() + 5.5 * 60 * 60 * 1000)

  // Format for joined_datetime (full ISO string)
  const istDateTime = istTime.toISOString()

  // Format for joined_date (YYYY-MM-DD)
  const istDate = istTime.toISOString().split("T")[0]

  return {
    istDateTime, // Full ISO timestamp in IST
    istDate, // YYYY-MM-DD in IST
    istTimeObj: istTime, // JavaScript Date object in IST
  }
}

/**
 * Format a date string to IST display format
 * @param dateString - ISO date string
 * @returns Formatted date string in IST
 */
export function formatISTDate(dateString: string | null | undefined) {
  if (!dateString) return "N/A"

  try {
    // Parse the date string
    const date = new Date(dateString)

    // Check if the date is valid
    if (isNaN(date.getTime())) {
      console.error("Invalid date:", dateString)
      return "N/A"
    }

    // Convert to IST
    const istDate = new Date(date.getTime() + 5.5 * 60 * 60 * 1000)

    // Format the date
    return istDate.toLocaleDateString("en-IN", {
      year: "numeric",
      month: "short",
      day: "numeric",
    })
  } catch (error) {
    console.error("Error formatting date:", error)
    return "N/A"
  }
}
