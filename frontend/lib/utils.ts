import { type ClassValue, clsx } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs))
}

/**
 * Formats a date string to a human-readable format
 * @param dateString - ISO date string
 * @param formatString - date-fns format string
 * @returns Formatted date string
 */
export function formatDate(dateString: string, formatString = "MMM d, yyyy"): string {
  try {
    const date = new Date(dateString)
    if (isNaN(date.getTime())) {
      return "Invalid date"
    }
    return date.toLocaleDateString("en-US", {
      year: "numeric",
      month: "short",
      day: "numeric",
    })
  } catch (error) {
    console.error("Error formatting date:", error)
    return "Invalid date"
  }
}
