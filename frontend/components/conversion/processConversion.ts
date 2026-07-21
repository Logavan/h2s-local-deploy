export async function processConversion(formData: FormData): Promise<any> {
  try {
    // Remove timeout logic and let conversion run until completion
    const result = await fetch("/api/convert", {
      method: "POST",
      body: formData,
      // Remove any timeout configurations
    })

    if (!result.ok) {
      throw new Error(`HTTP error! status: ${result.status}`)
    }

    const data = await result.json()

    // Record the successful conversion in Supabase
    if (data.success) {
      try {
        const userEmail = formData.get("userEmail") as string
        const conversionName = (formData.get("conversionName") as string) || "Unnamed Conversion"
        const nodeCount = Number.parseInt((formData.get("nodeCount") as string) || "0")

        await recordConversionInSupabase(userEmail, conversionName, nodeCount)
      } catch (recordError) {
        console.error("Error recording conversion:", recordError)
        // Continue with the conversion result even if recording fails
      }
    }

    return data
  } catch (error: any) {
    console.error("Conversion process failed:", error)
    throw error
  }
}

async function recordConversionInSupabase(userEmail: string, conversionName: string, nodeCount: number): Promise<void> {
  try {
    const response = await fetch("/api/record-conversion", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        userEmail,
        conversionName,
        nodeCount,
        conversionType: "Paid", // or determine based on user subscription
      }),
    })

    if (!response.ok) {
      throw new Error(`Failed to record conversion: ${response.status}`)
    }

    console.log("Conversion successfully recorded in database")
  } catch (error) {
    console.error("Error recording conversion:", error)
    throw error
  }
}
