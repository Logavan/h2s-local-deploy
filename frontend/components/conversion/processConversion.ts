export async function processConversion(formData: FormData): Promise<any> {
  try {
    const result = await fetch("/api/convert", {
      method: "POST",
      body: formData,
    })

    if (!result.ok) {
      throw new Error(`HTTP error! status: ${result.status}`)
    }

    const data = await result.json()
    return data
  } catch (error: any) {
    console.error("Conversion process failed:", error)
    throw error
  }
}
