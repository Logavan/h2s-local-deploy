"use server"

import { api } from "@/lib/api-client"

export async function validateXml(xmlContent: string, fileType = "hana") {
  try {
    const result = await api.validateXml(xmlContent, fileType)
    return result
  } catch (error) {
    console.error("Error validating XML:", error)
    return {
      valid: false,
      message: `Error validating XML: ${error instanceof Error ? error.message : "Unknown error"}`,
      fileType,
    }
  }
}
