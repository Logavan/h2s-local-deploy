"use client"

import { useEffect, useRef, useState } from "react"
import { X, Save, AlertCircle } from "lucide-react"
import { motion } from "framer-motion"

export interface TableMappingEntry {
  sourceTable: string
  targetTable: string
}

interface TableMappingModalProps {
  isOpen: boolean
  onClose: () => void
  platformName: string
  nodeName: string
  /** Unique source tables with their current target mappings */
  entries: TableMappingEntry[]
  onSave: (entries: TableMappingEntry[]) => void | Promise<void>
}

export default function TableMappingModal({
  isOpen,
  onClose,
  platformName,
  nodeName,
  entries,
  onSave,
}: TableMappingModalProps) {
  const [rows, setRows] = useState<TableMappingEntry[]>([])
  const [isSaving, setIsSaving] = useState(false)
  const [validationError, setValidationError] = useState("")
  const [hasChanges, setHasChanges] = useState(false)

  // Initialize rows when the modal opens with a new entries list. We compare
  // the serialized entries rather than the array reference so a parent
  // re-render with an identical list does NOT wipe unsaved edits.
  const entriesKey = JSON.stringify(entries)
  const lastEntriesKeyRef = useRef<string>("")
  useEffect(() => {
    if (!isOpen) {
      lastEntriesKeyRef.current = ""
      return
    }
    if (entriesKey === lastEntriesKeyRef.current) return
    lastEntriesKeyRef.current = entriesKey
    setRows(entries.map(e => ({ ...e })))
    setHasChanges(false)
    setValidationError("")
    setIsSaving(false)
  }, [isOpen, entriesKey, entries])

  const handleTargetChange = (index: number, value: string) => {
    const updated = [...rows]
    updated[index] = { ...updated[index], targetTable: value }
    setRows(updated)
    setHasChanges(true)
    setValidationError("")
  }

  const validate = (): boolean => {
    for (const row of rows) {
      if (!row.targetTable.trim()) {
        setValidationError(`All target table cells must have a value.`)
        return false
      }
    }
    setValidationError("")
    return true
  }

  const handleSave = async () => {
    if (!validate()) return
    setIsSaving(true)
    try {
      await onSave(rows)
    } finally {
      setIsSaving(false)
    }
  }

  const handleCloseWithConfirmation = () => {
    if (hasChanges && !isSaving) {
      if (window.confirm("You have unsaved changes. Are you sure you want to close?")) {
        onClose()
      }
    } else {
      onClose()
    }
  }

  // Backdrop click uses the same dirty-state guard as Cancel.
  function handleBackdropClick(event: React.MouseEvent<HTMLDivElement>) {
    if (event.target !== event.currentTarget) return
    handleCloseWithConfirmation()
  }

  if (!isOpen) return null

  return (
    // eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-static-element-interactions
    <div
      className="fixed inset-0 bg-black/50 backdrop-blur-sm z-50 flex items-center justify-center p-2 sm:p-4"
      onClick={handleBackdropClick}
    >
      <motion.div
        initial={{ scale: 0.95, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        exit={{ scale: 0.95, opacity: 0 }}
        className="bg-white rounded-lg shadow-xl w-full max-w-3xl max-h-[90vh] flex flex-col overflow-hidden"
      >
        {/* Header */}
        <div className="flex items-center justify-between p-4 border-b border-gray-200">
          <div>
            <h2 className="text-lg sm:text-xl font-semibold text-primary">Table Mapping — {platformName}</h2>
            <p className="text-xs sm:text-sm text-gray-500 mt-0.5 truncate">
              <span className="font-medium">{nodeName}</span>
              <span className="mx-1">·</span>
              <span>{rows.length} unique {rows.length === 1 ? "table" : "tables"}</span>
            </p>
          </div>
          <button
            onClick={handleCloseWithConfirmation}
            className="text-gray-500 hover:text-gray-700 transition-colors p-2 min-w-[44px] min-h-[44px] flex items-center justify-center rounded-lg hover:bg-gray-100"
            aria-label="Close"
          >
            <X className="h-5 w-5" />
          </button>
        </div>

        {/* Info banner */}
        <div className="bg-secondary/10 mx-4 mt-3 p-2 rounded-md">
          <div className="flex items-start">
            <AlertCircle className="h-4 w-4 text-secondary mt-0.5 mr-1.5 flex-shrink-0" />
            <p className="text-xs text-gray-700">
              Edit the target table name for each source table. Changes apply to <strong>all fields</strong> within that table.
            </p>
          </div>
        </div>

        {/* Validation Error */}
        {validationError && (
          <div className="bg-red-50 p-2 mx-4 mt-2 rounded-md">
            <div className="flex items-start">
              <AlertCircle className="h-4 w-4 text-red-500 mt-0.5 mr-1.5 flex-shrink-0" />
              <p className="text-xs text-red-700">{validationError}</p>
            </div>
          </div>
        )}

        {/* Table */}
        <div className="flex-1 overflow-auto p-4 mt-2">
          {rows.length === 0 ? (
            <div className="text-center py-12 text-gray-500">
              <p>No table mappings found.</p>
              <p className="text-xs mt-1">Save column mappings first to create table entries.</p>
            </div>
          ) : (
            <div className="border border-gray-200 rounded-lg overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="bg-gray-100 border-b border-gray-200">
                    <th className="px-4 py-2 text-left text-sm font-semibold text-primary border-r border-gray-200 w-1/2">
                      Source Table
                    </th>
                    <th className="px-4 py-2 text-left text-sm font-semibold text-primary w-1/2">
                      {platformName} Table
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {rows.map((row, index) => (
                    <tr key={row.sourceTable} className="border-b border-gray-100 last:border-b-0 hover:bg-gray-50">
                      <td className="px-4 py-2 text-sm text-gray-700 font-mono border-r border-gray-200">
                        {row.sourceTable}
                      </td>
                      <td className="px-4 py-2">
                        <input
                          type="text"
                          value={row.targetTable}
                          onChange={e => handleTargetChange(index, e.target.value)}
                          placeholder={`Enter ${platformName} table name`}
                          className="w-full px-2 py-1 text-sm border border-gray-300 rounded focus:outline-none focus:ring-1 focus:ring-secondary focus:border-secondary"
                        />
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Footer */}
        <div className="flex items-center justify-end gap-2 p-4 border-t border-gray-200 bg-gray-50">
          <button
            onClick={handleCloseWithConfirmation}
            disabled={isSaving}
            className="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-md hover:bg-gray-100 transition-colors disabled:opacity-50 min-h-[40px]"
          >
            Cancel
          </button>
          <button
            onClick={handleSave}
            disabled={isSaving || rows.length === 0 || !hasChanges}
            className="px-4 py-2 text-sm font-medium text-primary bg-secondary rounded-md hover:bg-secondary/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center min-h-[40px]"
          >
            {isSaving ? (
              <>
                <span className="inline-block w-4 h-4 mr-2 border-2 border-white border-t-transparent rounded-full animate-spin" />
                Saving…
              </>
            ) : (
              <>
                <Save className="h-4 w-4 mr-1.5" />
                Save Table Mappings
              </>
            )}
          </button>
        </div>
      </motion.div>
    </div>
  )
}
