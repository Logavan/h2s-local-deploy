// Helper functions
export const textToXmlFile = (text: string, filename: string): File => {
  const blob = new Blob([text], { type: "application/xml" })
  return new File([blob], filename, { type: "application/xml" })
}

// Validate file type
export const validateFileType = (file: File): boolean => {
  const allowedTypes = [".xml", ".txt"]
  const fileExtension = file.name.toLowerCase().substring(file.name.lastIndexOf("."))
  return allowedTypes.includes(fileExtension)
}

// Read file content with improved error handling
export const readFileContent = async (file: File): Promise<string> => {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()

    reader.onload = (event) => {
      if (event.target?.result) {
        resolve(event.target.result as string)
      } else {
        console.error("FileReader loaded but result is null or undefined")
        reject(new Error("Failed to read file content: No result"))
      }
    }

    reader.onerror = (error) => {
      console.error("FileReader error:", error)
      reject(error)
    }

    reader.onabort = () => {
      console.error("FileReader aborted")
      reject(new Error("File reading was aborted"))
    }

    try {
      reader.readAsText(file)
    } catch (error) {
      console.error("Exception during readAsText:", error)
      reject(error)
    }
  })
}

// Validate XML content
export const validateXmlContent = async (content: string): Promise<boolean> => {
  try {
    const parser = new DOMParser()
    // Parse with explicit error checking - DO NOT use parseFromString with
    // a custom entity resolver that could enable XXE attacks
    const doc = parser.parseFromString(content, "text/xml")
    const parserErrors = doc.getElementsByTagName("parsererror")

    // Also check for parse errors in the document
    if (parserErrors.length > 0) {
      return false
    }

    // Check if the XML has any external entity declarations
    if (content.includes('<!ENTITY') || content.includes('SYSTEM "') || content.includes("SYSTEM '")) {
      console.warn("XML contains potential external entity declaration")
      return false
    }

    return true
  } catch (error) {
    console.error("XML parsing error:", error)
    return false
  }
}
