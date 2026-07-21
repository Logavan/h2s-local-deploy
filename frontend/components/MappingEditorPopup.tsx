"use client"

import type React from "react"

import { useState, useEffect } from "react"
import { X, Save, AlertCircle, CheckCircle, Clipboard, FileText, Download } from "lucide-react"
import { motion } from "framer-motion"

interface MappingRow {
  sourceTable: string
  sourceField: string
  targetTable: string
  targetField: string
}

interface MappingEditorPopupProps {
  isOpen: boolean
  onClose: () => void
  platformName: string
  sqlContent: string
  zipContents: {
    sqlFiles: Record<string, string>
    mappingFileContent: MappingRow[]
    textFileName: string
  }
  onSave: (mappings: MappingRow[], processedSql: string, fileName: string) => void
}

export default function MappingEditorPopup({
  isOpen,
  onClose,
  platformName,
  sqlContent,
  zipContents,
  onSave,
}: MappingEditorPopupProps) {
  const [mappings, setMappings] = useState<MappingRow[]>([])
  const [isLoading, setIsLoading] = useState(true)
  const [hasChanges, setHasChanges] = useState(false)
  const [processedSql, setProcessedSql] = useState("")
  const [isSaving, setIsSaving] = useState(false)
  const [saveSuccess, setSaveSuccess] = useState(false)
  const [showPasteHelp, setShowPasteHelp] = useState(false)
  const [outputFileName, setOutputFileName] = useState("")
  const [validationError, setValidationError] = useState("")

  const handleDownloadMappings = () => {
    if (mappings.length === 0) {
      alert("No mapping data to download.")
      return
    }

    const headers = ["sourceTable", "sourceField", "targetTable", "targetField"]
    const csvRows = []

    // Add headers
    csvRows.push(headers.join(","))

    // Add data rows
    mappings.forEach(row => {
      const values = headers.map(header => {
        const escaped = (row as any)[header].replace(/"/g, '""')
        return `"${escaped}"`
      })
      csvRows.push(values.join(","))
    })

    const csvString = csvRows.join("\n")
    const blob = new Blob([csvString], { type: "text/csv;charset=utf-8;" })
    const link = document.createElement("a")
    link.href = URL.createObjectURL(blob)
    link.setAttribute("download", `${outputFileName || "mapping_data"}.csv`)
    document.body.appendChild(link)
    link.click()
    document.body.removeChild(link)
    URL.revokeObjectURL(link.href)
  }

  // Load the mapping data when the popup opens
  useEffect(() => {
    if (isOpen) {
      setIsLoading(true)
      setValidationError("")

      // Use the mapping data from the ZIP file
      if (zipContents.mappingFileContent && zipContents.mappingFileContent.length > 0) {
        setMappings(zipContents.mappingFileContent)
        setHasChanges(true) // Enable apply button after loading data
        console.log("Loaded mapping data:", zipContents.mappingFileContent)
      } else {
        // Fallback to mock data if no mapping file was found
        const mockMappings: MappingRow[] = [
          { sourceTable: "Employee", sourceField: "Eid", targetTable: "Emp", targetField: "Employee_id_id" },
          { sourceTable: "Employee", sourceField: "Name", targetTable: "Emp", targetField: "Employee_name_id" },
        ]
        setMappings(mockMappings)
        console.log("Using mock mapping data")
        setHasChanges(true) // Mark as changed when mock data is loaded
      }

      // Generate the output file name
      setOutputFileName(`${zipContents.textFileName} ${platformName}`)

      setTimeout(() => {
        setIsLoading(false)
        // setHasChanges(false) // Removed this line as it would override the above
        setSaveSuccess(false)
      }, 500)
    }
  }, [isOpen, platformName, zipContents])

  // Removed the second useEffect as its logic is now integrated above
  // useEffect(() => {
  //   if (!isLoading && mappings.length === 0) {
  //     setMappings([
  //       { sourceTable: "Employee", sourceField: "Eid", targetTable: "Emp", targetField: "Employee_id_id" },
  //       { sourceTable: "Employee", sourceField: "Name", targetTable: "Emp", targetField: "Employee_name_id" },
  //     ])
  //   }
  // }, [isLoading, mappings.length])

  // Validate mappings and return true if valid, false if invalid
  const validateMappings = (): boolean => {
    const sourceTableMap = new Map<string, string>()

    for (const mapping of mappings) {
      // New validation: Check for empty targetTable or targetField
      if (!mapping.targetTable.trim()) {
        setValidationError(`All '${platformName} Table/View' cells must have a value.`)
        return false
      }
      if (!mapping.targetField.trim()) {
        setValidationError(`All '${platformName} Field' cells must have a value.`)
        return false
      }

      // Existing validation: Check for consistent targetTable names
      if (!sourceTableMap.has(mapping.sourceTable)) {
        sourceTableMap.set(mapping.sourceTable, mapping.targetTable)
      } else if (sourceTableMap.get(mapping.sourceTable) !== mapping.targetTable) {
        setValidationError(
          `Inconsistent mapping detected for table "${mapping.sourceTable}". All rows with the same HANA Table/View must have the same target table name.`,
        )
        return false
      }
    }

    setValidationError("")
    return true
  }

  const handleTargetTableChange = (index: number, value: string) => {
    const updatedMappings = [...mappings]
    const sourceTable = updatedMappings[index].sourceTable

    // Find all rows with the same source table and update them with the new target table name
    updatedMappings.forEach((mapping, i) => {
      if (mapping.sourceTable === sourceTable) {
        updatedMappings[i].targetTable = value
      }
    })

    setMappings(updatedMappings)
    setHasChanges(true)
    setValidationError("") // Clear any validation errors when user makes changes
  }

  const handleTargetFieldChange = (index: number, value: string) => {
    const updatedMappings = [...mappings]
    updatedMappings[index].targetField = value
    setMappings(updatedMappings)
    setHasChanges(true)
  }

  // Handle paste events for table and field columns
  const handlePaste = (
    e: React.ClipboardEvent<HTMLInputElement>,
    columnType: "targetTable" | "targetField",
    index: number,
  ) => {
    e.preventDefault()

    // Get clipboard data
    const clipboardData = e.clipboardData.getData("text")

    // Check if this looks like multiple values (from Excel)
    const lines = clipboardData.trim().split(/[\r\n]+/)

    if (lines.length > 1) {
      // This is a multi-line paste, likely from Excel
      const updatedMappings = [...mappings]

      if (columnType === "targetTable") {
        // Get the source table for the current row
        const sourceTable = updatedMappings[index].sourceTable

        // Update all rows with the same source table
        const newTargetTable = lines[0].trim() // Use only the first line for the target table

        updatedMappings.forEach((mapping, i) => {
          if (mapping.sourceTable === sourceTable) {
            mapping.targetTable = newTargetTable
          }
        })
      } else {
        // For target fields, update normally
        lines.forEach((line, i) => {
          const rowIndex = index + i
          if (rowIndex < updatedMappings.length) {
            updatedMappings[rowIndex].targetField = line.trim()
          }
        })
      }

      setMappings(updatedMappings)
      setHasChanges(true)
      setValidationError("") // Clear any validation errors when user makes changes
    } else {
      // Handle single value paste
      if (columnType === "targetTable") {
        // For target tables, update all rows with the same source table
        const sourceTable = mappings[index].sourceTable
        const newTargetTable = clipboardData.trim()

        const updatedMappings = [...mappings]
        updatedMappings.forEach((mapping, i) => {
          if (mapping.sourceTable === sourceTable) {
            mapping.targetTable = newTargetTable
          }
        })

        setMappings(updatedMappings)
      } else {
        // For target fields, update normally
        handleTargetFieldChange(index, clipboardData)
      }

      setHasChanges(true)
      setValidationError("") // Clear any validation errors when user makes changes
    }
  }

  // Process SQL replacements based on mappings using the optimized logic
  const processSql = (sql: string, mappings: MappingRow[]): string => {
    // Convert the flat mappings array to the nested structure as provided
    const hanaToTargetMapping: Record<string, Record<string, string>> = {}

    // Group mappings by source table
    mappings.forEach((mapping) => {
      if (!hanaToTargetMapping[mapping.sourceTable]) {
        hanaToTargetMapping[mapping.sourceTable] = {}
      }
      hanaToTargetMapping[mapping.sourceTable][mapping.sourceField] = mapping.targetField
    })

    let mappedQuery = sql

    // Loop through the mapping and replace the table/field names
    for (const hanaTable in hanaToTargetMapping) {
      const fieldMapping = hanaToTargetMapping[hanaTable]

      // Replace table name if needed
      const targetTableName = mappings.find((m) => m.sourceTable === hanaTable)?.targetTable
      if (targetTableName && targetTableName !== hanaTable) {
        const tableRegex = new RegExp(`\\b${hanaTable}\\b`, "g")
        mappedQuery = mappedQuery.replace(tableRegex, targetTableName)
      }

      // Replace fields using the optimized approach
      for (const hanaField in fieldMapping) {
        const targetField = fieldMapping[hanaField]

        // Regex to match the field exactly (not as part of another field name)
        const regex = new RegExp(`\\b${hanaField}\\b`, "g")
        mappedQuery = mappedQuery.replace(regex, targetField)
      }
    }

    return mappedQuery
  }

  // Handle bulk paste for an entire column
  const handleBulkColumnPaste = async (columnType: "targetTable" | "targetField") => {
    try {
      const text = await navigator.clipboard.readText()
      const lines = text.trim().split(/[\r\n]+/)

      if (lines.length > 0) {
        const updatedMappings = [...mappings]

        if (columnType === "targetTable") {
          // Create a map of source tables to their new target tables
          const sourceToTargetMap: Record<string, string> = {}

          // First pass: build the mapping
          lines.forEach((line, i) => {
            if (i < updatedMappings.length) {
              const sourceTable = updatedMappings[i].sourceTable
              sourceToTargetMap[sourceTable] = line.trim()
            }
          })

          // Second pass: apply the mapping to all rows
          updatedMappings.forEach((mapping, i) => {
            const sourceTable = mapping.sourceTable
            if (sourceToTargetMap[sourceTable]) {
              mapping.targetTable = sourceToTargetMap[sourceTable]
            }
          })
        } else {
          // For target fields, just update normally
          lines.forEach((line, i) => {
            if (i < updatedMappings.length) {
              updatedMappings[i].targetField = line.trim()
            }
          })
        }

        setMappings(updatedMappings)
        setHasChanges(true)
        setValidationError("") // Clear any validation errors when user makes changes
      }
    } catch (err) {
      console.error("Failed to read clipboard contents: ", err)
      alert("Unable to paste from clipboard. Please check your browser permissions.")
    }
  }

  const handleSave = async () => {
    // Validate mappings before saving
    if (!validateMappings()) {
      return // Stop if validation fails
    }

    setIsSaving(true)

    try {
      // Get the SQL content for the selected platform
      const platformSqlContent = zipContents.sqlFiles[platformName.toLowerCase()] || sqlContent

      // Process the SQL with the current mappings
      const newSql = processSql(platformSqlContent, mappings)
      setProcessedSql(newSql)

      // Simulate processing delay
      await new Promise((resolve) => setTimeout(resolve, 1000))

      // Show success state briefly
      setSaveSuccess(true)

      // // Call the onSave callback with the processed SQL and filename
      // console.log("MappingEditorPopup: Mappings being sent to onSave:", mappings);
      // console.log("MappingEditorPopup: newSql being sent to onSave:", newSql);
      // console.log("MappingEditorPopup: outputFileName being sent to onSave:", outputFileName);

      setTimeout(() => {
        onSave(mappings, newSql, outputFileName)
      }, 1000)
    } catch (error) {
      console.error("Error processing SQL:", error)
      alert("An error occurred while processing the SQL file.")
    } finally {
      setIsSaving(false)
    }
  }

  const handleCloseWithConfirmation = () => {
    if (hasChanges && !saveSuccess) {
      if (window.confirm("You have unsaved changes. Are you sure you want to close?")) {
        onClose()
      }
    } else {
      onClose()
    }
  }

  if (!isOpen) return null

  return (
    <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50 flex items-center justify-center p-2 sm:p-4">
      <motion.div
        initial={{ scale: 0.9, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        exit={{ scale: 0.9, opacity: 0 }}
        className="bg-white rounded-lg shadow-xl w-full max-w-5xl max-h-[90vh] flex flex-col overflow-hidden"
      >
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-gray-200">
          <h2 className="text-xl font-semibold text-primary">Edit {platformName} SQL Mapping</h2>
          <div className="flex items-center space-x-2">
            <button
              onClick={handleDownloadMappings}
              className="px-3 py-2 text-xs sm:text-sm font-medium text-gray-700 bg-gray-100 rounded-lg hover:bg-gray-200 transition-colors flex items-center min-h-[44px]"
              title="Download Current Mapping"
            >
              <Download className="h-4 w-4 mr-1" />
              <span className="hidden sm:inline">Download Current mapping</span>
              <span className="sm:hidden">Download</span>
            </button>
            <button onClick={handleCloseWithConfirmation} className="text-gray-500 hover:text-gray-700 transition-colors p-2 min-w-[44px] min-h-[44px] flex items-center justify-center rounded-lg hover:bg-gray-100">
              <X className="h-5 w-5" />
            </button>
          </div>
        </div>

        {/* Instructions - More compact */}
        <div className="bg-secondary/10 p-2 mx-4 mt-2 rounded-md">
          <div className="flex items-start">
            <AlertCircle className="h-4 w-4 text-secondary mt-0.5 mr-1.5 flex-shrink-0" />
            <p className="text-xs text-gray-700">
              The first two columns are fixed. All rows with the same HANA Table/View name must share the same target
              table name.
            </p>
          </div>
        </div>

        {/* Output File Info */}
        {/* <div className="bg-blue-50 p-2 mx-4 mt-1 rounded-md">
          <div className="flex items-start">
            <FileText className="h-4 w-4 text-blue-500 mt-0.5 mr-1.5 flex-shrink-0" />
            <div className="text-xs text-gray-700 flex-grow">
              <span className="font-medium mr-2">Output file:</span>
              <input
                type="text"
                value={outputFileName}
                onChange={(e) => setOutputFileName(e.target.value)}
                className="text-blue-700 bg-white px-2 py-0.5 rounded border border-blue-200 focus:outline-none focus:ring-1 focus:ring-blue-400 w-64"
                placeholder="Enter filename"
              />
              <span className="ml-1">.sql</span>
            </div>
          </div>
        </div> */}

        {/* Validation Error */}
        {validationError && (
          <div className="bg-red-50 p-2 mx-4 mt-1 rounded-md">
            <div className="flex items-start">
              <AlertCircle className="h-4 w-4 text-red-500 mt-0.5 mr-1.5 flex-shrink-0" />
              <p className="text-xs text-red-700">{validationError}</p>
            </div>
          </div>
        )}

        {/* Paste Help Button */}
        <div className="flex justify-end px-4 mt-1">
          <button
            onClick={() => setShowPasteHelp(!showPasteHelp)}
            className="text-xs text-secondary hover:text-secondary/80 flex items-center"
          >
            <Clipboard className="h-3 w-3 mr-1" />
            {showPasteHelp ? "Hide paste tips" : "Show paste tips"}
          </button>
        </div>

        {/* Paste Help */}
        {showPasteHelp && (
          <div className="bg-blue-50 p-2 mx-4 mt-1 rounded-md">
            <div className="flex items-start">
              <Clipboard className="h-4 w-4 text-blue-500 mt-0.5 mr-1.5 flex-shrink-0" />
              <div className="text-xs text-gray-700">
                <p className="font-medium mb-1">Copy-Paste Tips:</p>
                <ul className="list-disc list-inside space-y-0.5">
                  <li>You can paste values directly from Excel into individual cells</li>
                  <li>Use the "Paste Column" buttons to paste an entire column at once</li>
                </ul>
              </div>
            </div>
          </div>
        )}

        {/* Table */}
        <div className="flex-1 overflow-auto p-2 sm:p-4 mt-1">
          {isLoading ? (
            <div className="flex items-center justify-center h-64">
              <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-secondary"></div>
            </div>
          ) : mappings.length > 0 ? (
            <div className="border border-gray-200 rounded-lg overflow-x-auto">
              <table className="w-full min-w-[600px]">
                <thead>
                  <tr className="bg-gray-100 border-b border-gray-200">
                    <th className="px-4 py-2 text-left text-sm font-semibold text-primary border-r border-gray-200">HANA Table/View</th>
                    <th className="px-4 py-2 text-left text-sm font-semibold text-primary border-r border-gray-200">HANA Field</th>
                    <th className="px-4 py-2 text-left text-sm font-semibold text-primary border-r border-gray-200">
                      <div className="flex items-center justify-between">
                        <span>{platformName} Table/View</span>
                        <button
                          onClick={() => handleBulkColumnPaste("targetTable")}
                          className="text-xs bg-gray-200 hover:bg-gray-300 px-2 py-0.5 rounded flex items-center"
                          title="Paste entire column from clipboard"
                        >
                          <Clipboard className="h-3 w-3 mr-1" />
                          Paste
                        </button>
                      </div>
                    </th>
                    <th className="px-4 py-2 text-left text-sm font-semibold text-primary">
                      <div className="flex items-center justify-between">
                        <span>{platformName} Field</span>
                        <button
                          onClick={() => handleBulkColumnPaste("targetField")}
                          className="text-xs bg-gray-200 hover:bg-gray-300 px-2 py-0.5 rounded flex items-center"
                          title="Paste entire column from clipboard"
                        >
                          <Clipboard className="h-3 w-3 mr-1" />
                          Paste
                        </button>
                      </div>
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {mappings.map((row, index) => (
                    <tr key={index} className="border-t border-gray-200 hover:bg-gray-50">
                      {/* Non-editable columns */}
                      <td className="px-4 py-2 text-sm text-gray-700 border-r border-gray-200 bg-gray-50 truncate max-w-[100px] sm:max-w-none">{row.sourceTable}</td>
                      <td className="px-4 py-2 text-sm text-gray-700 border-r border-gray-200 bg-gray-50 truncate max-w-[100px] sm:max-w-none">{row.sourceField}</td>

                      {/* Editable columns */}
                      <td className="px-4 py-2 text-sm border-r border-gray-200">
                        <input
                          type="text"
                          value={row.targetTable || ""}
                          onChange={(e) => handleTargetTableChange(index, e.target.value)}
                          onPaste={(e) => handlePaste(e, "targetTable", index)}
                          className="w-full p-2 sm:p-1 border border-gray-200 rounded focus:outline-none focus:ring-1 focus:ring-secondary text-sm sm:text-base"
                        />
                      </td>
                      <td className="px-4 py-2 text-sm">
                        <input
                          type="text"
                          value={row.targetField || ""}
                          onChange={(e) => handleTargetFieldChange(index, e.target.value)}
                          onPaste={(e) => handlePaste(e, "targetField", index)}
                          className="w-full p-2 sm:p-1 border border-gray-200 rounded focus:outline-none focus:ring-1 focus:ring-secondary text-sm sm:text-base"
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center h-64 bg-gray-50 rounded-lg border border-gray-200">
              <p className="text-gray-500 mb-4">No mapping data found</p>
              <button
                onClick={() => {
                  setMappings([
                    { sourceTable: "Employee", sourceField: "Eid", targetTable: "Emp", targetField: "Employee_id_id" },
                    {
                      sourceTable: "Employee",
                      sourceField: "Name",
                      targetTable: "Emp",
                      targetField: "Employee_name_id",
                    },
                  ])
                  setHasChanges(true)
                }}
                className="px-4 py-2 bg-secondary text-primary rounded-md hover:bg-secondary/90 transition-colors"
              >
                Add Sample Mappings
              </button>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="p-3 sm:p-4 border-t border-gray-200 flex flex-col sm:flex-row justify-end gap-2 sm:gap-3">
          <button
            onClick={handleCloseWithConfirmation}
            className="px-4 py-3 sm:py-2 text-sm sm:text-sm font-medium text-gray-700 bg-gray-100 rounded-lg hover:bg-gray-200 transition-colors min-h-[44px]"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={!hasChanges || isSaving || saveSuccess || mappings.length === 0 || !!validationError}
            className={`px-4 py-3 sm:py-2 text-sm sm:text-sm font-medium rounded-lg transition-colors flex items-center justify-center min-h-[44px] ${saveSuccess ? "bg-green-100 text-green-700" : "bg-secondary text-primary hover:bg-secondary/90"
              } disabled:opacity-50 disabled:cursor-not-allowed`}
          >
            {saveSuccess ? (
              <>
                <CheckCircle className="h-4 w-4 mr-1 sm:mr-2" />
                <span className="whitespace-nowrap">Saved Successfully</span>
              </>
            ) : isSaving ? (
              <>
                <div className="h-4 w-4 mr-1 sm:mr-2 rounded-full border-2 border-primary border-t-transparent animate-spin"></div>
                <span className="whitespace-nowrap">Processing...</span>
              </>
            ) : (
              <>
                <Save className="h-4 w-4 mr-1 sm:mr-2" />
                <span className="whitespace-nowrap">Save & Generate Code</span>
              </>
            )}
          </button>
        </div>
      </motion.div>
    </div>
  )
}
