const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL || "http://localhost:5000"

/**
 * Generic API client for making requests to the Flask backend
 */
export const apiClient = {
  /**
   * Make a GET request to the API
   * @param endpoint The API endpoint
   * @param params Optional query parameters
   * @returns The response data
   */
  async get<T>(endpoint: string, params?: Record<string, string>): Promise<T> {
    const url = new URL(`${API_BASE_URL}${endpoint}`)

    if (params) {
      Object.entries(params).forEach(([key, value]) => {
        url.searchParams.append(key, value)
      })
    }

    const response = await fetch(url.toString(), {
      method: "GET",
      headers: {
        "Content-Type": "application/json",
      },
    })

    if (!response.ok) {
      throw new Error(`API error: ${response.status} ${response.statusText}`)
    }

    return response.json()
  },

  /**
   * Make a POST request to the API
   * @param endpoint The API endpoint
   * @param data The request body
   * @returns The response data
   */
  async post<T>(endpoint: string, data: any): Promise<T> {
    const response = await fetch(`${API_BASE_URL}${endpoint}`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify(data),
    })

    if (!response.ok) {
      throw new Error(`API error: ${response.status} ${response.statusText}`)
    }

    return response.json()
  },
}

/**
 * API functions for specific endpoints
 */
export const api = {
  /**
   * Validate XML content
   * @param xmlContent The XML content to validate
   * @param fileType The type of XML file
   * @returns The validation result
   */
  validateXml: (xmlContent: string, fileType: string) => {
    return apiClient.post("/api/validate-xml", { xmlContent, fileType })
  },

  /**
   * Check if the API is healthy
   * @returns The health check result
   */
  healthCheck: () => {
    return apiClient.get("/api/health")
  },
}
