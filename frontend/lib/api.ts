// lib/api.ts



// Dynamic API URL based on environment variables
import { config } from "./config"

const getApiBaseUrl = (): string => {
  return config.api.baseUrl!
}

// Enhanced fetch with timeout and better error handling
async function fetchWithTimeout(url: string, options: RequestInit = {}, timeout = 30000): Promise<Response> {
  const controller = new AbortController()
  const id = setTimeout(() => controller.abort("Request timed out"), timeout)

  const fetchOptions = {
    ...options,
    signal: controller.signal,
  }

  try {
    const response = await fetch(url, fetchOptions)
    clearTimeout(id)
    return response
  } catch (error) {
    clearTimeout(id)
    console.error(`Network error fetching ${url}:`, error)

    // Enhance error message based on type
    if (error instanceof TypeError && error.message === "Failed to fetch") {
      throw new Error(`Unable to connect to the server. Please try later.`)
    } else if (error instanceof DOMException && error.name === "AbortError") {
      // DOMException may have a reason property in some environments
      const reason = (error as DOMException & { reason?: string }).reason || `Request timed out after ${timeout}ms`
      throw new Error(reason)
    }

    throw error
  }
}

async function handleResponse<T>(response: Response): Promise<T> {
  if (!response.ok) {
    let errorMessage = `Server error (${response.status})`

    try {
      const errorData = await response.json()
      // Prefer the structured error field from backend
      if (errorData && typeof errorData === 'object') {
        errorMessage = errorData.error || errorData.message || errorData.detail || errorMessage
      }
    } catch (e) {
      // If we can't parse JSON, try to get text
      try {
        const errorText = await response.text()
        if (errorText && errorText.length > 0 && errorText.length < 500) {
          // Only use text response if it's reasonable length (not a huge HTML error page)
          errorMessage = errorText.substring(0, 200)
        }
      } catch (textError) {
        console.error("Failed to parse error response:", textError)
      }
    }

    // Filter out internal errors - only pass validation-like errors to user
    const isInternalError = (
      // Python/JavaScript stack traces
      errorMessage.includes('Traceback') ||
      errorMessage.includes('File "') ||
      errorMessage.includes('line ') ||
      errorMessage.includes('module ') ||
      // Memory/resource errors
      errorMessage.includes('MemoryError') ||
      errorMessage.includes('OutOfMemory') ||
      errorMessage.includes('maximum recursion') ||
      errorMessage.includes('RecursionError') ||
      // Windows socket errors
      errorMessage.includes('[WinError') ||
      errorMessage.includes('non-blocking socket') ||
      errorMessage.includes('WSAEWOULDBLOCK') ||
      // Internal codes
      errorMessage.includes('0x') ||
      errorMessage.includes('errno =') ||
      // Stack trace patterns
      errorMessage.includes('    at ') ||
      errorMessage.includes('__internal') ||
      // Generic server crash indicators
      errorMessage.includes('internal server error') ||
      errorMessage.includes('unexpected error') ||
      errorMessage.includes('stack trace')
    )

    if (isInternalError) {
      console.error("Internal server error:", errorMessage)
      errorMessage = `Something went wrong. Please try again.`
    }

    throw new Error(errorMessage)
  }

  try {
    return (await response.json()) as T // Explicitly cast to T
  } catch (e) {
    console.error("Failed to parse JSON response:", e)
    throw new Error("Invalid response format from server")
  }
}

// Conversion-related API functions
export async function checkBackendHealth(): Promise<{ status: "alive" | "down" }> {
  try {
    const apiUrl = getApiBaseUrl()
    const response = await fetchWithTimeout(`${apiUrl}/health`, {}, 10000)
    const isOk = response.ok
    return { status: isOk ? "alive" : "down" }
  } catch (error) {
    console.error("Backend health check failed:", error)
    return { status: "down" }
  }
}

// Fixed analyzeXmlFile function to use actual file content
export async function analyzeXmlFile(
  xmlContent: string,
  fileName: string,
  userEmail: string,
): Promise<{
  success: boolean
  node_count?: number
  complexity?: string
  session_id?: string
  line_count?: number
  error?: string
  dig_mapping_dot_string?: string // Add this new field for the DIG mapping
}> {
  try {
    const apiUrl = getApiBaseUrl()
    // console.log(`Analyzing XML file: ${fileName} for user: ${userEmail}`); // Log the email
    // console.log(`XML content length: ${xmlContent.length} characters`)
    // console.log(`XML content preview: ${xmlContent.substring(0, 100)}...`)

    const response = await fetchWithTimeout(`${apiUrl}/api/analyze`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        xmlContent: xmlContent, // Use the actual XML content
        fileName: fileName, // Use the actual file name
        email: userEmail, // Pass the user email
      }),
    })

    const result = await handleResponse<{
      success: boolean
      node_count?: number
      complexity?: string
      session_id?: string
      line_count?: number
      error?: string
      dig_mapping_dot_string?: string
    }>(response)

    return result
  } catch (error) {
    console.error("XML analysis failed:", error)
    return {
      success: false,
      error: error instanceof Error ? error.message : "Analysis failed",
    }
  }
}

export async function startConversion(
  xmlContent: string,
  fileName: string,
  userEmail: string,
  nodeCount?: number,
): Promise<{
  success: boolean
  message?: string
  task_id?: string
  node_count?: number
  dig_mapping_dot_string?: string
  error?: string
}> {
  try {
    const apiUrl = getApiBaseUrl()

    const response = await fetchWithTimeout(`${apiUrl}/api/start-conversion`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        xmlContent,
        fileName,
        email: userEmail,
        nodeCount,
      }),
    })

    const result = await handleResponse<{
      success: boolean
      message?: string
      task_id?: string
      node_count?: number
      dig_mapping_dot_string?: string
      error?: string
    }>(response)
    // console.log(`Start conversion result:`, result)
    return result
  } catch (error) {
    console.error("Failed to start conversion:", error)
    return {
      success: false,
      error: error instanceof Error ? error.message : "Failed to start conversion",
    }
  }
}

export async function getConversionStatus(
  taskId: string,
): Promise<{
  status: string
  progress: number
  message: string
  result?: {
    sql_url: string
    data_mapping_url: string
    download_name: string
  }
  error?: string
}> {
  try {
    const apiUrl = getApiBaseUrl()
    const response = await fetchWithTimeout(`${apiUrl}/api/conversion-status/${taskId}`, {
      method: "GET",
    })
    const result = await handleResponse<{
      status: string
      progress: number
      message: string
      result?: {
        sql_url: string
        data_mapping_url: string
        download_name: string
      }
      error?: string
    }>(response)
    return result
  } catch (error) {
    console.error(`Failed to get status for task ${taskId}:`, error)
    return {
      status: "FAILED",
      progress: 0,
      message: error instanceof Error ? error.message : "Failed to fetch status",
      error: error instanceof Error ? error.message : "Failed to fetch status",
    }
  }
}

export async function downloadTxtFile(): Promise<boolean> {
  try {
    const apiUrl = getApiBaseUrl()
    const response = await fetchWithTimeout(`${apiUrl}/download`)

    if (!response.ok) {
      console.error("Download failed:", response.status)
      return false
    }

    const blob = await response.blob()
    const url = URL.createObjectURL(blob)
    const a = document.createElement("a")
    a.href = url
    a.download = "converted.sql"
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)

    return true
  } catch (error) {
    console.error("Download error:", error)
    return false
  }
}

export function getApiUrl(): string {
  return getApiBaseUrl()
}

export async function getNodeCount(): Promise<{ nodeCount: number }> {
  try {
    // Simulate node count generation for demo purposes
    // In a real implementation, this would analyze the XML content
    const nodeCount = Math.floor(Math.random() * 20) + 1

    return { nodeCount }
  } catch (error) {
    console.error("Node count generation failed:", error)
    return { nodeCount: 5 } // Default fallback
  }
}

export async function downloadConvertedFile(sessionId: string, fileName: string): Promise<{ type: "success" | "error"; message?: string }> {
  try {
    const apiUrl = getApiBaseUrl();
    console.log(`Attempting to download converted file for session ID: ${sessionId}`);

    const response = await fetchWithTimeout(`${apiUrl}/api/download/${sessionId}?type=sql`, {
      method: "GET",
    });

    if (!response.ok) {
      let errorMessage = `HTTP error! status: ${response.status}`;
      try {
        const errorData = await response.json();
        errorMessage = errorData.message || errorData.error || errorMessage;
      } catch (e) {
        const errorText = await response.text();
        if (errorText) errorMessage = errorText;
      }
      throw new Error(errorMessage);
    }

    const blob = await response.blob();
    const downloadUrl = URL.createObjectURL(blob);

    const link = document.createElement("a");
    link.href = downloadUrl;
    link.download = `${fileName.replace(".xml", "")}_converted.zip`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    URL.revokeObjectURL(downloadUrl);

    return {
      type: "success",
      message: "File downloaded successfully.",
    };
  } catch (error) {
    console.error("Download failed:", error);
    return {
      type: "error",
      message: error instanceof Error ? error.message : "Download failed",
    };
  }
}

export async function processXlsxFileForMapping(
  xlsxFile: File,
  selectedPlatform: string,
): Promise<{
  success: boolean;
  sqlContent?: string;
  mappingSchema?: {
    sqlInfo?: any;
    mappingFileContent?: any[];
    fileName?: string;
    sessionId?: string;
  };
  mappingFileContent?: any[];
  textFileName?: string;
  sessionId?: string;
  error?: string;
}> {
  try {
    const apiUrl = getApiBaseUrl();
    const formData = new FormData();
    formData.append("xlsxFile", xlsxFile);
    formData.append("selectedPlatform", selectedPlatform);

    console.log(`Sending XLSX file for mapping to backend for platform: ${selectedPlatform}`);

    const response = await fetchWithTimeout(`${apiUrl}/api/mapping/upload_and_generate_schema`, {
      method: "POST",
      body: formData,
    });

    const result = await handleResponse<{
      success: boolean;
      sqlContent?: string;
      mappingFileContent?: any[];
      textFileName?: string;
      sessionId?: string;
      error?: string;
    }>(response);

    console.log("Backend processing result:", result);
    return result;
  } catch (error) {
    console.error("Error processing XLSX file for mapping:", error);
    return {
      success: false,
      error: error instanceof Error ? error.message : "Failed to process XLSX file for mapping",
    };
  }
}

export async function applyMappingChanges(
  updatedMappings: any[], // Adjust type as per actual backend response
  fileName: string,
  selectedPlatform: string,
  sessionId: string, // Added sessionId parameter
  format?: string,
): Promise<{
  success: boolean;
  outputFileUrl?: string;
  error?: string;
  pysparkNotebookContent?: string | null;
  cteSqlContent?: string | null;
  tempTableSqlContent?: string | null;
  fileName?: string;
}> {
  try {
    const apiUrl = getApiBaseUrl();
    console.log(`Applying mapping changes to backend for file: ${fileName}, platform: ${selectedPlatform}, session: ${sessionId}`);

    const response = await fetchWithTimeout(`${apiUrl}/api/mapping/apply_changes_and_generate_output`, { // Corrected endpoint
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        updatedMappings, // Use updatedMappings as per backend expectation
        fileName,
        selectedPlatform,
        sessionId,
        outputFormat: format,  // Map 'format' to 'outputFormat' for backend
      }),
    }, 120000); // 120 second timeout for mapping generation

    if (!response.ok) {
      let errorMessage = `HTTP error! status: ${response.status}`;
      try {
        const errorData = await response.json();
        errorMessage = errorData.message || errorData.error || errorMessage;
      } catch (e) {
        const errorText = await response.text();
        if (errorText) errorMessage = errorText;
      }
      throw new Error(errorMessage);
    }

    const contentType = response.headers.get("content-type") || "";
    if (contentType.includes("application/json")) {
      // Parse JSON response with content
      const data = await response.json();
      return {
        success: true,
        pysparkNotebookContent: data.pysparkNotebookContent || null,
        cteSqlContent: data.cteSqlContent || null,
        tempTableSqlContent: data.tempTableSqlContent || null,
        fileName: data.fileName || fileName,
        outputFileUrl: data.outputFileUrl,
      };
    }

    // Expect a direct file download (blob)
    const blob = await response.blob();
    const downloadUrl = URL.createObjectURL(blob);

    // The backend now directly sends the file, so we return a success with a dummy URL
    // The actual download will be handled by the browser via the response.
    return {
      success: true,
      outputFileUrl: downloadUrl, // Provide a temporary URL for the frontend to trigger download
    };
  } catch (error) {
    console.error("Error applying mapping changes:", error);
    return {
      success: false,
      error: error instanceof Error ? error.message : "Failed to apply mapping changes",
    };
  }
}

// ============ Bulk Conversion API Functions ============

export interface BulkFileAnalysis {
  file_name: string
  node_count: number
  line_count?: number
  content?: string  // XML content for conversion
}

export interface BulkAnalysisResult {
  success: boolean
  files: BulkFileAnalysis[]
  total_nodes: number
  error?: string
}

export async function analyzeBulkZip(
  zipFile: File,
  userEmail: string,
): Promise<BulkAnalysisResult> {
  try {
    const apiUrl = getApiBaseUrl()
    
    // If no API URL is configured, return empty result
    if (!apiUrl) {
      console.warn("API base URL not configured, skipping bulk ZIP analysis")
      return {
        success: false,
        files: [],
        total_nodes: 0,
        error: "API not configured",
      }
    }
    
    const formData = new FormData()
    formData.append("zipFile", zipFile)
    formData.append("email", userEmail)

    const response = await fetchWithTimeout(`${apiUrl}/api/bulk-analyze`, {
      method: "POST",
      body: formData,
    }, 120000) // 120 second timeout for bulk operations

    // Handle non-ok responses gracefully
    if (!response.ok) {
      let errorMessage = "Bulk analysis failed"
      try {
        const errorData = await response.json()
        errorMessage = errorData.error || errorData.message || errorMessage
      } catch {
        // If we can't parse JSON, use status text
        if (response.status === 404) {
          errorMessage = "Bulk analysis endpoint not available"
        } else {
          errorMessage = `HTTP ${response.status}`
        }
      }
      console.warn("Bulk ZIP analysis failed:", errorMessage)
      return {
        success: false,
        files: [],
        total_nodes: 0,
        error: errorMessage,
      }
    }

    const result = await handleResponse<BulkAnalysisResult>(response)
    return result
  } catch (error) {
    console.warn("Bulk ZIP analysis failed:", error instanceof Error ? error.message : "Unknown error")
    return {
      success: false,
      files: [],
      total_nodes: 0,
      error: error instanceof Error ? error.message : "Bulk analysis failed",
    }
  }
}

export interface BulkConversionFile {
  file_name: string
  content: string
}

export interface BulkConversionRequest {
  files: BulkConversionFile[]
  email: string
}

export interface BulkConversionResult {
  success: boolean
  bulk_task_id?: string
  total_files: number
  message?: string
  error?: string
}

export async function startBulkConversion(
  files: BulkConversionFile[],
  userEmail: string,
): Promise<BulkConversionResult> {
  try {
    const apiUrl = getApiBaseUrl()

    // Bulk conversion needs longer timeout (120 seconds)
    const response = await fetchWithTimeout(`${apiUrl}/api/bulk-conversion`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        files,
        email: userEmail,
      }),
    }, 120000) // 120 second timeout for bulk operations

    const result = await handleResponse<BulkConversionResult>(response)
    return result
  } catch (error) {
    console.error("Failed to start bulk conversion:", error)
    return {
      success: false,
      total_files: files.length,
      error: error instanceof Error ? error.message : "Failed to start bulk conversion",
    }
  }
}

export interface BulkConversionStatus {
  status: string // "PENDING" | "PROCESSING" | "COMPLETED" | "FAILED" | "PARTIAL"
  progress: number // 0-100
  total_files: number
  completed_files: number
  failed_files: number
  results?: {
    file_name: string
    status: "completed" | "failed"
    sql_url?: string
    error?: string
  }[]
  message: string
}

export async function getBulkConversionStatus(
  taskId: string,
): Promise<BulkConversionStatus> {
  try {
    const apiUrl = getApiBaseUrl()
    const response = await fetchWithTimeout(`${apiUrl}/api/bulk-status/${taskId}`, {
      method: "GET",
    })
    console.log(`[getBulkConversionStatus] Raw response for ${taskId}:`, response.status, response.statusText)
    const result = await handleResponse<BulkConversionStatus>(response)
    console.log(`[getBulkConversionStatus] Parsed result for ${taskId}:`, JSON.stringify(result, null, 2))
    return result
  } catch (error) {
    console.error(`Failed to get bulk status for task ${taskId}:`, error)
    return {
      status: "FAILED",
      progress: 0,
      total_files: 0,
      completed_files: 0,
      failed_files: 0,
      message: error instanceof Error ? error.message : "Failed to fetch status",
    }
  }
}

export async function downloadBulkResult(
  taskId: string,
): Promise<{ type: "success" | "error"; message?: string }> {
  try {
    const apiUrl = getApiBaseUrl()
    const response = await fetchWithTimeout(`${apiUrl}/api/bulk-download/${taskId}`, {
      method: "GET",
    })

    if (!response.ok) {
      let errorMessage = `HTTP error! status: ${response.status}`
      try {
        const errorData = await response.json()
        errorMessage = errorData.message || errorData.error || errorMessage
      } catch (e) {
        const errorText = await response.text()
        if (errorText) errorMessage = errorText
      }
      throw new Error(errorMessage)
    }

    const blob = await response.blob()
    const downloadUrl = URL.createObjectURL(blob)

    // Generate timestamp for filename
    const now = new Date()
    const timestamp = now.toISOString().replace(/[:.]/g, '-').slice(0, 19)
    const filename = `Bulk conversion_${timestamp}.zip`

    const link = document.createElement("a")
    link.href = downloadUrl
    link.download = filename
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(downloadUrl)

    return {
      type: "success",
      message: "Bulk conversion downloaded successfully.",
    }
  } catch (error) {
    console.error("Bulk download failed:", error)
    return {
      type: "error",
      message: error instanceof Error ? error.message : "Bulk download failed",
    }
  }
}

// ============ Conversion Running Status API ============

export async function checkConversionRunningStatus(
  userEmail: string,
): Promise<{ isRunning: boolean }> {
  try {
    const apiUrl = getApiBaseUrl()
    
    // If no API URL is configured, return false (no conversion running)
    if (!apiUrl) {
      console.warn("API base URL not configured, skipping conversion running status check")
      return { isRunning: false }
    }
    
    const response = await fetchWithTimeout(
      `${apiUrl}/api/conversion-running-status?email=${encodeURIComponent(userEmail)}`,
      {
        method: "GET",
      }
    )

    // If response is not ok but not a 404, throw to let handleResponse format the error
    // Only catch 404 errors gracefully - the endpoint might not exist in older deployments
    if (!response.ok) {
      if (response.status === 404) {
        console.warn("Conversion running status endpoint not found (404) - endpoint may not be available in this backend version")
        return { isRunning: false }
      }
      // For other errors (500, etc.), try to parse error message but don't throw
      try {
        const errorData = await response.json()
        console.warn("Conversion running status check failed:", errorData.message || errorData.error || `HTTP ${response.status}`)
      } catch {
        console.warn("Conversion running status check failed with status:", response.status)
      }
      return { isRunning: false }
    }

    const result = await handleResponse<{ isRunning: boolean }>(response)
    return result
  } catch (error) {
    console.warn("Failed to check conversion running status:", error instanceof Error ? error.message : "Unknown error")
    return { isRunning: false }
  }
}

// ============ Nested CV Flattener API Functions ============

import type {
  CreateSessionRequest, CreateSessionResponse,
  AddCvRequest, AddCvResponse,
  UpdateCvRequest,
  ResolveLinksRequest, UpdateMappingsRequest,
  ValidateResponse, GenerateResponse, TaskStatusResponse,
  NestedSession, CvArtifact, DependencyLink, MappingEntry,
  GraphSummary, NestedTask, Diagnostic,
  OutputFormat,
} from "./nested-cv-types"

export async function nestedCreateSession(
  req: CreateSessionRequest
): Promise<CreateSessionResponse> {
  try {
    const response = await fetchWithTimeout(`${apiUrl}/api/nested/sessions`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    })
    return handleResponse<CreateSessionResponse>(response)
  } catch (error) {
    console.error("Failed to create nested session:", error)
    return { success: false, error: error instanceof Error ? error.message : "Failed to create session" }
  }
}

export async function nestedGetSession(sessionId: string): Promise<{ success: boolean; session?: NestedSession; error?: string }> {
  try {
    const response = await fetchWithTimeout(`${apiUrl}/api/nested/sessions/${sessionId}`, {
      method: "GET",
    })
    return handleResponse<{ success: boolean; session?: NestedSession; error?: string }>(response)
  } catch (error) {
    console.error("Failed to get nested session:", error)
    return { success: false, error: error instanceof Error ? error.message : "Failed to get session" }
  }
}

export async function nestedAddCv(
  sessionId: string,
  req: AddCvRequest
): Promise<AddCvResponse> {
  try {
    const response = await fetchWithTimeout(`${apiUrl}/api/nested/sessions/${sessionId}/cvs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    })
    return handleResponse<AddCvResponse>(response)
  } catch (error) {
    console.error("Failed to add CV:", error)
    return { success: false, error: error instanceof Error ? error.message : "Failed to add CV" }
  }
}

export async function nestedUpdateCv(
  sessionId: string,
  artifactId: string,
  req: UpdateCvRequest
): Promise<{ success: boolean; artifact?: CvArtifact; error?: string }> {
  try {
    const response = await fetchWithTimeout(`${apiUrl}/api/nested/sessions/${sessionId}/cvs/${artifactId}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(req),
    })
    return handleResponse(response)
  } catch (error) {
    console.error("Failed to update CV:", error)
    return { success: false, error: error instanceof Error ? error.message : "Failed to update CV" }
  }
}

export async function nestedDeleteCv(
  sessionId: string,
  artifactId: string
): Promise<{ success: boolean; error?: string }> {
  try {
    const response = await fetchWithTimeout(`${apiUrl}/api/nested/sessions/${sessionId}/cvs/${artifactId}`, {
      method: "DELETE",
    })
    return handleResponse(response)
  } catch (error) {
    console.error("Failed to delete CV:", error)
    return { success: false, error: error instanceof Error ? error.message : "Failed to delete CV" }
  }
}

export async function nestedResolveLinks(
  sessionId: string,
  links: DependencyLink[]
): Promise<{ success: boolean; error?: string }> {
  try {
    const response = await fetchWithTimeout(`${apiUrl}/api/nested/sessions/${sessionId}/links`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ links }),
    })
    return handleResponse(response)
  } catch (error) {
    console.error("Failed to resolve links:", error)
    return { success: false, error: error instanceof Error ? error.message : "Failed to resolve links" }
  }
}

export async function nestedUpdateMappings(
  sessionId: string,
  mappings: MappingEntry[]
): Promise<{ success: boolean; error?: string }> {
  try {
    const response = await fetchWithTimeout(`${apiUrl}/api/nested/sessions/${sessionId}/mappings`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mappings }),
    })
    return handleResponse(response)
  } catch (error) {
    console.error("Failed to update mappings:", error)
    return { success: false, error: error instanceof Error ? error.message : "Failed to update mappings" }
  }
}

export async function nestedValidate(
  sessionId: string
): Promise<ValidateResponse> {
  try {
    const response = await fetchWithTimeout(`${apiUrl}/api/nested/sessions/${sessionId}/validate`, {
      method: "POST",
    })
    return handleResponse<ValidateResponse>(response)
  } catch (error) {
    console.error("Failed to validate session:", error)
    return {
      success: false,
      valid: false,
      errors: [{ level: "error", code: "VALIDATION_FAILED", message: error instanceof Error ? error.message : "Validation failed" }],
      warnings: [],
    }
  }
}

export async function nestedGenerate(
  sessionId: string
): Promise<GenerateResponse> {
  try {
    const response = await fetchWithTimeout(`${apiUrl}/api/nested/sessions/${sessionId}/generate`, {
      method: "POST",
    })
    return handleResponse<GenerateResponse>(response)
  } catch (error) {
    console.error("Failed to start generation:", error)
    return { success: false, error: error instanceof Error ? error.message : "Failed to start generation" }
  }
}

export async function nestedGetTaskStatus(
  taskId: string
): Promise<TaskStatusResponse> {
  try {
    const response = await fetchWithTimeout(`${apiUrl}/api/nested/tasks/${taskId}`, {
      method: "GET",
    })
    return handleResponse<TaskStatusResponse>(response)
  } catch (error) {
    console.error("Failed to get task status:", error)
    return {
      task_id: taskId,
      status: "FAILED",
      progress: 0,
      message: error instanceof Error ? error.message : "Failed to get task status",
      diagnostics: [],
    }
  }
}

export async function nestedDownloadResult(
  taskId: string
): Promise<{ type: "success" | "error"; message?: string }> {
  try {
    const response = await fetchWithTimeout(`${apiUrl}/api/nested/tasks/${taskId}/download`, {
      method: "GET",
    })

    if (!response.ok) {
      let errorMessage = `HTTP ${response.status}`
      try {
        const errorData = await response.json()
        errorMessage = errorData.error || errorData.message || errorMessage
      } catch {}
      throw new Error(errorMessage)
    }

    const blob = await response.blob()
    const downloadUrl = URL.createObjectURL(blob)
    const now = new Date()
    const timestamp = now.toISOString().replace(/[:.]/g, "-").slice(0, 19)
    const filename = `nested_cv_merged_${timestamp}.sql`

    const link = document.createElement("a")
    link.href = downloadUrl
    link.download = filename
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(downloadUrl)

    return { type: "success", message: "Downloaded successfully" }
  } catch (error) {
    console.error("Nested CV download failed:", error)
    return { type: "error", message: error instanceof Error ? error.message : "Download failed" }
  }
}

export async function nestedDeleteSession(
  sessionId: string
): Promise<{ success: boolean; error?: string }> {
  try {
    const response = await fetchWithTimeout(`${apiUrl}/api/nested/sessions/${sessionId}`, {
      method: "DELETE",
    })
    return handleResponse(response)
  } catch (error) {
    console.error("Failed to delete session:", error)
    return { success: false, error: error instanceof Error ? error.message : "Failed to delete session" }
  }
}

// --- Previous Conversions (Mapping History) ---

export interface PreviousConversion {
  task_id: string
  file_name: string
  mapping_file: string
  modified_at: string
}

export async function listPreviousConversations(): Promise<{
  success: boolean
  conversions: PreviousConversion[]
  error?: string
}> {
  try {
    const response = await fetchWithTimeout(`${apiUrl}/api/previous-conversions`, {
      method: "GET",
      headers: { "Content-Type": "application/json" },
    })

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`)
    }

    const data = await response.json()
    return data
  } catch (error) {
    console.error("Failed to list previous conversions:", error)
    return { success: false, conversions: [], error: error instanceof Error ? error.message : "Unknown error" }
  }
}

export async function downloadPreviousMapping(
  taskId: string
): Promise<{ type: "success"; file: File } | { type: "error"; message: string }> {
  try {
    const response = await fetchWithTimeout(`${apiUrl}/api/download/${taskId}?type=mapping`, {
      method: "GET",
    })

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`)
    }

    const blob = await response.blob()
    const fileName = `${taskId}_mapping_sheet.xlsx`
    const file = new File([blob], fileName, {
      type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    })

    return { type: "success", file }
  } catch (error) {
    console.error("Failed to download previous mapping:", error)
    return { type: "error", message: error instanceof Error ? error.message : "Download failed" }
  }
}
