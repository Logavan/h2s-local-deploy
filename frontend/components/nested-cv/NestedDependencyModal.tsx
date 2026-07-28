"use client"

import { useEffect, useMemo, useRef, useState } from "react"
import { X, Upload, History, Loader2, CheckCircle, AlertCircle, Search, FileSpreadsheet, Wand2, ArrowRight } from "lucide-react"
import { motion } from "framer-motion"
import { cn } from "@/lib/utils"
import {
  downloadPreviousMapping, listPreviousConversations, nestedInspectPreviousConversion,
  type PreviousConversion,
} from "@/lib/api"

interface SqlInfoRow {
  "Source Table Name"?: string
  source_table_name?: string
  "Node Name"?: string
  node_name?: string
  [key: string]: string | undefined
}

interface SourceTableRow {
  source_table_name: string
  source_field?: string
  target_table?: string
}

export interface ColumnMappingRow {
  /** Column the parent CV needs (from required_columns_json). */
  parentCol: string
  /** Column the nested CV produces (from output_columns / last chunk SELECT aliases). */
  nestedCol: string
  /** Whether this row was set automatically (true) or manually by user (false). */
  isAuto: boolean
}

export type NestedDependencyMode = "root" | "nested"

function normalizeCol(value: string): string {
  return value.trim().toUpperCase().replace(/^["`[]|["`\]]$/g, "")
}

/**
 * Build initial column mappings between parent required columns and nested CV output columns.
 * Auto-match tiers (best → fallback):
 *   1. Exact normalized name (case-insensitive, ignores simple quotes/brackets)
 *   2. Token overlap (e.g. CALMONTH_S ↔ CALMONTH share "CALMONTH")
 *   3. Unmapped (empty nestedCol)
 *
 * Each nested column is used at most once.
 */
function autoBuildColumnMappings(
  parentRequired: string[],
  nestedOutput: string[],
): ColumnMappingRow[] {
  const result: ColumnMappingRow[] = []
  const usedNested = new Set<string>()

  for (const parentCol of parentRequired) {
    const parentKey = normalizeCol(parentCol)
    if (!parentKey) {
      result.push({ parentCol, nestedCol: "", isAuto: false })
      continue
    }

    // 1) Exact match (case-insensitive, not already used)
    const exact = nestedOutput.find(
      c => normalizeCol(c) === parentKey && !usedNested.has(normalizeCol(c)),
    )
    if (exact) {
      usedNested.add(normalizeCol(exact))
      result.push({ parentCol, nestedCol: exact, isAuto: true })
      continue
    }

    // 2) Token overlap best-match
    const parentTokens = new Set(parentKey.split(/[^A-Z0-9]+/).filter(Boolean))
    let best: { col: string; score: number } | null = null
    for (const cand of nestedOutput) {
      const candKey = normalizeCol(cand)
      if (usedNested.has(candKey)) continue
      const candTokens = candKey.split(/[^A-Z0-9]+/).filter(Boolean)
      let score = 0
      for (const t of candTokens) {
        if (parentTokens.has(t)) score += 1
      }
      if (score > 0 && (!best || score > best.score)) {
        best = { col: cand, score }
      }
    }
    if (best) {
      usedNested.add(normalizeCol(best.col))
      result.push({ parentCol, nestedCol: best.col, isAuto: true })
      continue
    }

    // 3) No match
    result.push({ parentCol, nestedCol: "", isAuto: false })
  }

  return result
}

interface NestedDependencyModalProps {
  isOpen: boolean
  onClose: () => void
  mode: NestedDependencyMode
  /** The source_ref_canonical of the table/view we are resolving (parent side). Ignored in root mode. */
  parentRef?: string
  /** Display name of the parent artifact. Ignored in root mode. */
  parentName?: string
  /** Parent CV's required columns for this source. Used for auto-matching against output_columns. */
  parentRequiredColumns?: string[]
  /** Called when user confirms with a parsed workbook. */
  onConfirm: (
    file: File,
    selectedSource: string,
    columnMappings?: ColumnMappingRow[],
  ) => void
  /** Upload XLSX and return sql_info rows + source_tables + output_columns (non-mutating inspect). */
  onUploadXlsx: (file: File) => Promise<{
    success: boolean
    sql_info?: SqlInfoRow[]
    source_tables?: SourceTableRow[]
    output_columns?: string[]
    last_chunk_sql?: string
    last_chunk_sources?: string[]
    error?: string
  }>
  isLoading?: boolean
}

type Tab = "upload" | "history"

export default function NestedDependencyModal({
  isOpen,
  onClose,
  mode,
  parentRef = "",
  parentName = "",
  parentRequiredColumns = [],
  onConfirm,
  onUploadXlsx,
  isLoading = false,
}: NestedDependencyModalProps) {
  const [activeTab, setActiveTab] = useState<Tab>("upload")
  const [file, setFile] = useState<File | null>(null)
  const [isUploading, setIsUploading] = useState(false)
  const [uploadError, setUploadError] = useState("")
  const [sqlInfo, setSqlInfo] = useState<SqlInfoRow[]>([])
  const [sourceTables, setSourceTables] = useState<SourceTableRow[]>([])
  const [outputColumns, setOutputColumns] = useState<string[]>([])
  const [lastChunkSql, setLastChunkSql] = useState<string>("")
  const [selectedSource, setSelectedSource] = useState("")
  const [columnMappings, setColumnMappings] = useState<ColumnMappingRow[]>([])
  const [hasAutoBuiltMappings, setHasAutoBuiltMappings] = useState(false)
  const [isDragging, setIsDragging] = useState(false)
  const [history, setHistory] = useState<PreviousConversion[]>([])
  const [historyLoading, setHistoryLoading] = useState(false)
  const [historyDownloadingId, setHistoryDownloadingId] = useState<string | null>(null)
  const [historySearch, setHistorySearch] = useState("")
  const [isEditingSourceRef, setIsEditingSourceRef] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  // Reset state ONLY when the modal opens (false → true transition) or when
  // the parent context (mode/parentRef) changes. Switching tabs or other
  // user actions inside the modal should NOT wipe a user's manual selection.
  const lastModalContextRef = useRef<string>("")
  useEffect(() => {
    const contextKey = `${isOpen}|${mode}|${parentRef}`
    if (lastModalContextRef.current === contextKey) return
    lastModalContextRef.current = contextKey
    if (!isOpen) return
    setFile(null)
    setSqlInfo([])
    setSourceTables([])
    setOutputColumns([])
    setLastChunkSql("")
    setSelectedSource("")
    setColumnMappings([])
    setHasAutoBuiltMappings(false)
    setUploadError("")
    setActiveTab("upload")
    setHistorySearch("")
    setIsEditingSourceRef(false)
  }, [isOpen, mode, parentRef])

  // ── Fetch sql_info + source_tables + output_columns when a file is provided ──────
  const fetchSqlInfo = async (f: File) => {
    setIsUploading(true)
    setUploadError("")
    setSqlInfo([])
    setSourceTables([])
    setOutputColumns([])
    setLastChunkSql("")
    setSelectedSource("")
    try {
      const result = await onUploadXlsx(f)
      if (!result.success) {
        setUploadError(result.error || "Failed to parse workbook")
        return
      }

      // Store sql_info for node lookup
      if (result.sql_info) {
        setSqlInfo(result.sql_info)
      }
      // Store source_tables (from mapping_info) — input tables of this CV
      if (result.source_tables) {
        setSourceTables(result.source_tables)
      }
      // Store output_columns (from last chunk SELECT aliases) — output of this CV
      // These are the actual linkage columns between parent and nested CV.
      if (result.output_columns) {
        setOutputColumns(result.output_columns)
        // Auto-build column linkages: parent's required columns ↔ nested output columns
        setColumnMappings(autoBuildColumnMappings(parentRequiredColumns, result.output_columns))
        setHasAutoBuiltMappings(true)
      } else {
        setColumnMappings([])
      }
      if (result.last_chunk_sql) {
        setLastChunkSql(result.last_chunk_sql)
      }

      // Build a unified list of source names for auto-selection
      const fromSqlInfo = (result.sql_info || [])
        .map(row => (row["Source Table Name"] || row.source_table_name || "").trim())
        .filter(Boolean)
      const fromSourceTables = (result.source_tables || [])
        .map(row => row.source_table_name)
        .filter(Boolean)
      const allSourceNames = Array.from(new Set([...fromSqlInfo, ...fromSourceTables]))

      if (allSourceNames.length === 0) {
        if (mode === "nested") {
          // No sources found — keep error cleared, let user type manually
        }
        return
      }

      if (mode === "root") {
        setSelectedSource(allSourceNames[0])
      } else {
        // In nested mode, try exact match against parentRef first
        const matched = allSourceNames.find(
          name => name.toUpperCase() === parentRef.toUpperCase()
        )
        if (matched) {
          setSelectedSource(matched)
        } else {
          // Fallback: pre-fill with parentRef so the button is enabled
          // User can change via table or edit the input
          setSelectedSource(parentRef || allSourceNames[0])
        }
      }
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Upload failed")
    } finally {
      setIsUploading(false)
    }
  }

  // ── Local file selection ─────────────────────────────────────────
  const handleFileChange = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const f = e.target.files?.[0]
    if (!f) return
    setFile(f)
    await fetchSqlInfo(f)
  }

  const handleDrop = async (e: React.DragEvent) => {
    e.preventDefault()
    setIsDragging(false)
    const f = e.dataTransfer.files?.[0]
    if (!f) return
    if (!f.name.endsWith(".xlsx") && !f.name.endsWith(".xls")) {
      setUploadError("Only .xlsx or .xls files are supported")
      return
    }
    setFile(f)
    await fetchSqlInfo(f)
  }

  // ── History loading ──────────────────────────────────────────────
  const loadHistory = async () => {
    setHistoryLoading(true)
    setUploadError("")
    try {
      const result = await listPreviousConversations()
      if (result.success) {
        setHistory(result.conversions)
      } else {
        setHistory([])
        setUploadError(result.error || "Failed to load conversion history")
      }
    } catch (err) {
      setHistory([])
      setUploadError(err instanceof Error ? err.message : "Failed to load conversion history")
    } finally {
      setHistoryLoading(false)
    }
  }

  useEffect(() => {
    if (isOpen && activeTab === "history" && history.length === 0 && !historyLoading) {
      loadHistory()
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isOpen, activeTab])

  const handleHistorySelect = async (conversion: PreviousConversion) => {
    setHistoryDownloadingId(conversion.task_id)
    setUploadError("")
    try {
      // Two parallel calls (saves a round-trip vs sequential download→upload):
      //   1. Download the file — we still need this blob for the final
      //      confirm step that uploads to nested_add_cv_from_xlsx.
      //   2. Inspect — ask the server to parse the file from disk so we
      //      don't have to re-upload it just to populate the column UI.
      const [downloadResult, inspectResult] = await Promise.all([
        downloadPreviousMapping(conversion.task_id),
        nestedInspectPreviousConversion(conversion.task_id),
      ])

      if (downloadResult.type !== "success") {
        setUploadError(downloadResult.message || "Failed to download file")
        return
      }
      if (!inspectResult.success) {
        setUploadError(inspectResult.error || "Failed to inspect file")
        return
      }

      const fileName = `${conversion.file_name}.xlsx`
      const file = new File([downloadResult.file], fileName, {
        type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      })
      setFile(file)
      setActiveTab("upload")
      applyInspectResult({
        success: true,
        sql_info: inspectResult.sql_info,
        source_tables: inspectResult.source_tables,
        output_columns: inspectResult.output_columns,
        last_chunk_sql: inspectResult.last_chunk_sql,
        last_chunk_sources: inspectResult.last_chunk_sources,
        file_name: conversion.file_name,
      })
    } catch (err) {
      setUploadError(err instanceof Error ? err.message : "Failed to load from history")
    } finally {
      setHistoryDownloadingId(null)
    }
  }

  // Reusable: take the parsed payload from either the upload endpoint or the
  // inspect-previous endpoint and populate the modal state. Centralizes the
  // state-set so the upload path and the history path stay in sync.
  function applyInspectResult(payload: {
    success: boolean
    sql_info?: SqlInfoRow[]
    source_tables?: SourceTableRow[]
    output_columns?: string[]
    last_chunk_sql?: string
    last_chunk_sources?: string[]
    file_name?: string
  }) {
    if (!payload.success) {
      setUploadError("Failed to parse workbook")
      return
    }
    if (payload.sql_info) setSqlInfo(payload.sql_info)
    if (payload.source_tables) setSourceTables(payload.source_tables)
    if (payload.output_columns) {
      setOutputColumns(payload.output_columns)
      setColumnMappings(autoBuildColumnMappings(parentRequiredColumns, payload.output_columns))
      setHasAutoBuiltMappings(true)
    } else {
      setColumnMappings([])
    }
    if (payload.last_chunk_sql) setLastChunkSql(payload.last_chunk_sql)

    const fromSqlInfo = (payload.sql_info || [])
      .map(row => (row["Source Table Name"] || row.source_table_name || "").trim())
      .filter(Boolean)
    const fromSourceTables = (payload.source_tables || [])
      .map(row => row.source_table_name)
      .filter(Boolean) as string[]
    const allSourceNames = Array.from(new Set([...fromSqlInfo, ...fromSourceTables]))

    if (allSourceNames.length === 0) return

    if (mode === "root") {
      setSelectedSource(allSourceNames[0])
    } else {
      const matched = allSourceNames.find(
        name => name.toUpperCase() === parentRef.toUpperCase()
      )
      if (matched) {
        setSelectedSource(matched)
      } else {
        setSelectedSource(parentRef || allSourceNames[0])
      }
    }
  }

  // ── Confirm ──────────────────────────────────────────────────────
  const handleConfirm = () => {
    if (!file || isUploading || isLoading) return
    const effectiveSource = selectedSource || (isRoot ? "root" : "")
    if (!effectiveSource) return
    // Only send mappings that have an actual mapping selected (skip unmapped rows).
    const validMappings = columnMappings.filter(r => r.nestedCol.trim() !== "")
    onConfirm(file, effectiveSource, validMappings)
  }

  const handleCancel = () => {
    setFile(null)
    setSqlInfo([])
    setSourceTables([])
    setOutputColumns([])
    setLastChunkSql("")
    setSelectedSource("")
    setColumnMappings([])
    setHasAutoBuiltMappings(false)
    setUploadError("")
    setActiveTab("upload")
    setIsEditingSourceRef(false)
    onClose()
  }

  // ── Column mapping helpers ───────────────────────────────────────
  function updateMappingRow(index: number, nestedCol: string) {
    setColumnMappings(prev => prev.map((r, i) => i === index ? { ...r, nestedCol, isAuto: false } : r))
  }

  function resetMappingRow(index: number) {
    setColumnMappings(prev => prev.map((r, i) => {
      if (i !== index) return r
      // Re-run auto-match for this single row only
      const parentKey = normalizeCol(r.parentCol)
      const auto = outputColumns.find(c => normalizeCol(c) === parentKey)
      return { parentCol: r.parentCol, nestedCol: auto || "", isAuto: true }
    }))
  }

  function reAutoMatchAll() {
    setColumnMappings(autoBuildColumnMappings(parentRequiredColumns, outputColumns))
  }

  if (!isOpen) return null

  // Combine sources from both sql_info (legacy) and source_tables (from mapping_info sheet).
  // source_tables is more reliable because it comes from the mapping_info sheet which
  // every workbook has — even when the sql_info sheet is empty or uses different columns.
  const sourceOptions = Array.from(new Set(
    [
      ...sqlInfo.map(row => (row["Source Table Name"] || row.source_table_name || "").trim()),
      ...sourceTables.map(row => row.source_table_name?.trim() || ""),
    ].filter(Boolean)
  ))
  const parentMatched = mode === "nested" && !!parentRef && sourceOptions.some(s => s.toUpperCase() === parentRef.toUpperCase())
  const isRoot = mode === "root"

  // Auto-match output_columns against parent's required columns (case-insensitive)
  const requiredLower = new Set(parentRequiredColumns.map(c => c.toLowerCase()))
  const matchedOutputColumns = outputColumns.filter(col => requiredLower.has(col.toLowerCase()))
  const unmatchedOutputColumns = outputColumns.filter(col => !requiredLower.has(col.toLowerCase()))
  const unmatchedRequiredColumns = parentRequiredColumns.filter(
    c => !outputColumns.some(oc => oc.toLowerCase() === c.toLowerCase())
  )
  const hasOutputColumns = outputColumns.length > 0
  const hasParentRequired = parentRequiredColumns.length > 0

  const titleText = isRoot ? "Add Root Calculation View" : "Resolve Nested Dependency"
  const helperText = isRoot
    ? "Upload or select a previous HANA mapping workbook to add it as a new root calculation view."
    : <>This CV will resolve <span className="font-mono bg-gray-100 px-1 rounded text-gray-700">{parentRef}</span> referenced by <span className="font-medium text-gray-700">{parentName}</span>. Auto-mapped columns will become the linkage between parent and nested CV.</>
  const confirmLabel = isRoot ? "Add Root CV" : "Add Nested Dependency"
  const confirmDisabled = isRoot
    ? !file || isUploading || isLoading
    : !file || isUploading || isLoading || !selectedSource
  const confirmHelper = !file
    ? "Upload or select a workbook to continue."
    : isUploading
      ? "Parsing workbook…"
      : !isRoot && !selectedSource
        ? "Pick the source that should resolve this dependency."
        : ""

  const filteredHistory = history.filter(c => c.file_name.toLowerCase().includes(historySearch.toLowerCase()))

  // Backdrop click closes the modal unless the user has selected a file —
  // losing a picked workbook silently is worse than forcing one extra click.
  function handleBackdropClick(event: React.MouseEvent<HTMLDivElement>) {
    // Only close when the click hit the backdrop itself, not a child of the dialog.
    if (event.target !== event.currentTarget) return
    if (file) {
      if (window.confirm("Close without uploading? Your selected workbook will be cleared.")) {
        handleCancel()
      }
      return
    }
    handleCancel()
  }

  return (
    // eslint-disable-next-line jsx-a11y/click-events-have-key-events, jsx-a11y/no-static-element-interactions
    <div
      className="fixed inset-0 bg-black/50 backdrop-blur-sm z-[70] flex items-center justify-center p-4"
      onClick={handleBackdropClick}
    >
      <motion.div
        initial={{ scale: 0.9, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        exit={{ scale: 0.9, opacity: 0 }}
        className="bg-white rounded-xl shadow-2xl w-full max-w-2xl max-h-[85vh] flex flex-col overflow-hidden"
        role="dialog"
        aria-label={titleText}
      >
        <div className="flex items-center justify-between p-4 border-b border-gray-200">
          <div className="pr-4">
            <h2 className="text-lg font-semibold text-gray-900">{titleText}</h2>
            <p className="text-sm text-gray-500 mt-1">{helperText}</p>
          </div>
          <button onClick={handleCancel} className="p-2 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-lg transition-colors" aria-label="Close dialog">
            <X className="w-5 h-5" />
          </button>
        </div>

        {!file && (
          <div className="flex border-b border-gray-200">
            {(["upload", "history"] as Tab[]).map(tab => (
              <button
                key={tab}
                onClick={() => { setActiveTab(tab); setUploadError("") }}
                className={cn(
                  "flex-1 py-3 text-sm font-medium transition-colors relative",
                  activeTab === tab ? "text-secondary" : "text-gray-500 hover:text-gray-700"
                )}
              >
                <div className="flex items-center justify-center gap-2">
                  {tab === "upload" ? <Upload className="w-4 h-4" /> : <History className="w-4 h-4" />}
                  {tab === "upload" ? "Upload New File" : "Select from History"}
                </div>
                {activeTab === tab && <div className="absolute bottom-0 left-0 right-0 h-0.5 bg-secondary" />}
              </button>
            ))}
          </div>
        )}

        <div className="flex-1 overflow-y-auto p-4 space-y-4">
          {activeTab === "upload" && (
            <>
              <div
                onDragOver={e => { e.preventDefault(); setIsDragging(true) }}
                // Only clear the dragging state when the drag leaves THIS element
                // entirely (not when crossing into a child node — a common React
                // drag-drop pitfall that causes flicker).
                onDragLeave={event => {
                  if (!event.currentTarget.contains(event.relatedTarget as Node | null)) {
                    setIsDragging(false)
                  }
                }}
                onDrop={handleDrop}
                onClick={() => fileInputRef.current?.click()}
                className={cn(
                  "border-2 border-dashed rounded-xl p-6 text-center cursor-pointer transition-all",
                  isDragging ? "border-secondary bg-secondary/5" : "border-gray-300 hover:border-secondary/50 hover:bg-gray-50",
                  file ? "border-green-400 bg-green-50" : ""
                )}
              >
                <input ref={fileInputRef} type="file" accept=".xlsx,.xls" onChange={handleFileChange} className="hidden" />
                {isUploading ? (
                  <div className="flex flex-col items-center py-4">
                    <Loader2 className="w-8 h-8 text-secondary animate-spin mb-2" />
                    <p className="text-sm text-gray-600">Parsing XLSX...</p>
                  </div>
                ) : file ? (
                  <div className="flex flex-col items-center">
                    <CheckCircle className="w-8 h-8 text-green-500 mb-2" />
                    <p className="text-sm font-medium text-gray-900">{file.name}</p>
                    <p className="text-xs text-gray-400 mt-1">Click to replace</p>
                  </div>
                ) : (
                  <div className="flex flex-col items-center py-4">
                    <Upload className="w-8 h-8 text-gray-400 mb-2" />
                    <p className="text-sm font-medium text-gray-700">Drop .xlsx file or click to browse</p>
                    <p className="text-xs text-gray-400 mt-1">Supports .xlsx and .xls</p>
                  </div>
                )}
              </div>

              {uploadError && (
                <div className="flex items-start gap-2 p-3 bg-red-50 rounded-lg">
                  <AlertCircle className="w-4 h-4 text-red-500 mt-0.5 shrink-0" />
                  <p className="text-sm text-red-700">{uploadError}</p>
                </div>
              )}

              {file && !isRoot && (
                <div className="space-y-3">
                  {/* ── Column Linkage Table (Expected ↔ Available) ──────────────────────── */}
                  {(columnMappings.length > 0 || hasOutputColumns) && (
                    <div className="border border-gray-200 rounded-lg overflow-hidden bg-white">
                      <div className="px-3 py-2 border-b border-gray-200 bg-gray-50 flex items-center justify-between flex-wrap gap-2">
                        <div>
                          <p className="text-xs font-semibold text-gray-700 uppercase tracking-wide">Column Linkage</p>
                          <p className="text-[11px] text-gray-500 mt-0.5">
                            Map parent's required columns <span className="font-mono">{parentRef}</span> to this CV's output columns. Default is auto-matched; change dropdowns to remap.
                          </p>
                        </div>
                        <div className="flex items-center gap-1.5 text-[10px] flex-wrap">
                          <span className="text-emerald-700 bg-emerald-100 px-2 py-0.5 rounded-full font-medium">
                            ✓ {columnMappings.filter(r => r.nestedCol && r.isAuto).length} auto
                          </span>
                          {columnMappings.some(r => r.nestedCol && !r.isAuto) && (
                            <span className="text-blue-700 bg-blue-100 px-2 py-0.5 rounded-full font-medium">
                              {columnMappings.filter(r => r.nestedCol && !r.isAuto).length} manual
                            </span>
                          )}
                          {columnMappings.some(r => !r.nestedCol) && (
                            <span className="text-amber-700 bg-amber-100 px-2 py-0.5 rounded-full font-medium">
                              {columnMappings.filter(r => !r.nestedCol).length} unmapped
                            </span>
                          )}
                          {parentRequiredColumns.length > 0 && outputColumns.length > 0 && (
                            <button
                              type="button"
                              onClick={reAutoMatchAll}
                              className="text-secondary hover:underline flex items-center gap-1 ml-1 px-1.5 py-0.5 rounded hover:bg-secondary/10"
                              title="Re-run auto-match for all rows"
                            >
                              <Wand2 className="w-3 h-3" /> Re-auto
                            </button>
                          )}
                        </div>
                      </div>
                      <div className="overflow-x-auto max-h-72 overflow-y-auto">
                        <table className="w-full text-sm">
                          <thead className="bg-gray-100 border-b border-gray-200 sticky top-0 z-10">
                            <tr>
                              <th className="text-left p-2 pl-3 font-medium text-gray-700 w-5/12">
                                Expected <span className="text-gray-500 font-normal text-[10px]">(parent uses)</span>
                              </th>
                              <th className="text-center p-2 font-medium text-gray-700 w-2/12"></th>
                              <th className="text-left p-2 font-medium text-gray-700 w-5/12">
                                Available <span className="text-gray-500 font-normal text-[10px]">(nested CV outputs)</span>
                              </th>
                              <th className="text-right p-2 pr-3 font-medium text-gray-700 w-12"></th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-gray-100 bg-white">
                            {columnMappings.length === 0 ? (
                              <tr>
                                <td colSpan={4} className="p-4 text-center text-xs text-gray-500">
                                  Parent CV did not declare required columns for <span className="font-mono">{parentRef}</span>.
                                  You can still create mappings manually using a different parent CV, or skip and proceed.
                                </td>
                              </tr>
                            ) : columnMappings.map((row, idx) => {
                              // Build used-set for disabling duplicate nested columns in OTHER rows
                              const usedByOther = new Set(
                                columnMappings
                                  .filter((r, i) => i !== idx && r.nestedCol)
                                  .map(r => r.nestedCol)
                              )
                              const isAuto = row.nestedCol && row.isAuto
                              const isMapped = row.nestedCol && !row.isAuto
                              return (
                                <tr
                                  key={`${row.parentCol}-${idx}`}
                                  className={cn(
                                    "transition-colors",
                                    isAuto ? "bg-emerald-50/40" : isMapped ? "bg-blue-50/40" : ""
                                  )}
                                >
                                  <td className="p-2 pl-3 font-mono text-gray-800 align-middle">
                                    <div className="flex items-center gap-2">
                                      <span className="truncate" title={row.parentCol}>{row.parentCol}</span>
                                      {isAuto && (
                                        <span className="text-[10px] uppercase font-semibold px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-700 shrink-0">auto</span>
                                      )}
                                      {isMapped && (
                                        <span className="text-[10px] uppercase font-semibold px-1.5 py-0.5 rounded bg-blue-100 text-blue-700 shrink-0">set</span>
                                      )}
                                    </div>
                                  </td>
                                  <td className="p-2 text-center text-gray-400 align-middle">
                                    <ArrowRight className="w-3.5 h-3.5 inline-block" />
                                  </td>
                                  <td className="p-2 align-middle">
                                    <select
                                      value={row.nestedCol}
                                      onChange={e => updateMappingRow(idx, e.target.value)}
                                      className={cn(
                                        "w-full px-2 py-1.5 border rounded text-sm font-mono focus:outline-none focus:ring-2 focus:ring-secondary focus:border-secondary",
                                        row.nestedCol
                                          ? "border-gray-300 bg-white text-gray-800"
                                          : "border-dashed border-amber-300 bg-amber-50 text-amber-700"
                                      )}
                                    >
                                      <option value="">— unmapped —</option>
                                      {outputColumns.map(col => {
                                        const usedElsewhere = usedByOther.has(col) && col !== row.nestedCol
                                        return (
                                          <option key={col} value={col} disabled={usedElsewhere}>
                                            {col}{usedElsewhere ? " (used elsewhere)" : ""}
                                          </option>
                                        )
                                      })}
                                    </select>
                                  </td>
                                  <td className="p-2 pr-3 text-right align-middle">
                                    {!row.isAuto && (
                                      <button
                                        type="button"
                                        onClick={() => resetMappingRow(idx)}
                                        className="text-xs text-gray-500 hover:text-secondary"
                                        title="Reset to auto-match"
                                      >
                                        reset
                                      </button>
                                    )}
                                  </td>
                                </tr>
                              )
                            })}
                          </tbody>
                        </table>
                      </div>
                      {/* Unused output columns hint */}
                      {hasOutputColumns && columnMappings.length > 0 && (() => {
                        const used = new Set(columnMappings.map(r => r.nestedCol).filter(Boolean))
                        const extras = outputColumns.filter(c => !used.has(c))
                        if (extras.length === 0) return null
                        return (
                          <div className="px-3 py-2 border-t border-gray-100 bg-gray-50 text-[11px] text-gray-500 flex items-center gap-1.5">
                            <span className="font-medium text-gray-600">Extras from this CV (not mapped):</span>
                            {extras.map(col => (
                              <span key={col} className="font-mono px-1.5 py-0.5 bg-white border border-gray-200 rounded text-gray-700">{col}</span>
                            ))}
                          </div>
                        )
                      })()}
                    </div>
                  )}

                  {/* ── Source table fallback (when neither output_columns nor parent required cols are available) ── */}
                  {!hasOutputColumns && columnMappings.length === 0 && sourceOptions.length > 0 && (
                    <div className="space-y-2">
                      <div className="flex items-center justify-between">
                        <p className="text-sm font-medium text-gray-700">
                          Which source in <span className="font-semibold text-secondary">{file.name}</span> maps to{" "}
                          <span className="font-mono bg-gray-100 px-1 rounded">{parentRef}</span>?
                        </p>
                        {parentMatched && <span className="text-xs text-green-600 font-medium">✓ auto-matched</span>}
                      </div>
                      <div className="border border-gray-200 rounded-lg overflow-hidden">
                        <table className="w-full text-sm">
                          <thead className="bg-gray-50 border-b border-gray-200">
                            <tr>
                              <th className="text-left p-2 pl-3 font-medium text-gray-600 w-8"></th>
                              <th className="text-left p-2 font-medium text-gray-600">Source Table / View</th>
                              <th className="text-left p-2 font-medium text-gray-600">Node</th>
                            </tr>
                          </thead>
                          <tbody className="divide-y divide-gray-100">
                            {sourceOptions.map(src => {
                              const isMatch = src.toUpperCase() === parentRef.toUpperCase()
                              const sqlRow = sqlInfo.find(item => (item["Source Table Name"] || item.source_table_name || "").trim() === src)
                              const stRow = sourceTables.find(item => item.source_table_name === src)
                              const displayNode = sqlRow?.["Node Name"] || sqlRow?.node_name || stRow?.target_table || stRow?.source_field || ""
                              return (
                                <tr
                                  key={src}
                                  onClick={() => setSelectedSource(src)}
                                  className={cn("cursor-pointer transition-colors hover:bg-secondary/5", selectedSource === src ? "bg-secondary/10" : "")}
                                >
                                  <td className="p-2 pl-3">
                                    <div className={cn("w-4 h-4 rounded-full border-2 flex items-center justify-center", selectedSource === src ? "border-secondary bg-secondary" : "border-gray-300")}>
                                      {selectedSource === src && <div className="w-2 h-2 bg-white rounded-full" />}
                                    </div>
                                  </td>
                                  <td className="p-2 font-medium text-gray-800">
                                    <span className="font-mono">{src}</span>
                                    {isMatch && <span className="ml-2 text-xs text-green-600 font-medium">← match</span>}
                                  </td>
                                  <td className="p-2 text-gray-500 text-xs">
                                    {displayNode}
                                  </td>
                                </tr>
                              )
                            })}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  )}

                  {/* ── Source reference (read-only badge by default, editable on demand) ──
                      The default is auto-set to parentRef. Most users don't need to override
                      it, so we present it as a static badge and only reveal an editable
                      input when the user explicitly clicks "Change". */}
                  <div className="pt-2">
                    <p className="text-xs font-medium text-gray-600 mb-1">Source reference</p>
                    {!isEditingSourceRef ? (
                      <div className="flex items-center gap-2 px-3 py-2 border border-gray-200 rounded-lg bg-gray-50">
                        <span className="font-mono text-sm text-gray-800 flex-1 truncate" title={selectedSource || parentRef}>
                          {selectedSource || parentRef || <span className="text-gray-400 italic">not set</span>}
                        </span>
                        <span className="text-[10px] uppercase font-semibold px-1.5 py-0.5 rounded bg-gray-200 text-gray-600">
                          auto
                        </span>
                        <button
                          type="button"
                          onClick={() => setIsEditingSourceRef(true)}
                          className="text-xs text-secondary hover:underline"
                        >
                          Change
                        </button>
                      </div>
                    ) : (
                      <div className="space-y-1">
                        <input
                          id="manual-source"
                          type="text"
                          value={selectedSource}
                          onChange={e => setSelectedSource(e.target.value)}
                          placeholder={parentRef || "Source table or view name"}
                          className="w-full px-3 py-2 text-sm border border-gray-200 rounded-lg font-mono focus:outline-none focus:ring-2 focus:ring-secondary focus:border-secondary"
                        />
                        <div className="flex items-center justify-between gap-2">
                          <p className="text-[11px] text-gray-500">
                            Defaults to <span className="font-mono">{parentRef}</span>.
                          </p>
                          <button
                            type="button"
                            onClick={() => {
                              setSelectedSource(parentRef || "")
                              setIsEditingSourceRef(false)
                            }}
                            className="text-[11px] text-gray-500 hover:text-secondary"
                          >
                            Reset to default
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                </div>
              )}
            </>
          )}

          {activeTab === "history" && (
            <div className="space-y-3">
              <div className="flex items-center justify-end">
                <button onClick={loadHistory} disabled={historyLoading} className="text-xs text-secondary hover:underline disabled:opacity-50">Refresh</button>
              </div>
              <div className="relative">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
                <input
                  placeholder="Search by file name..."
                  value={historySearch}
                  onChange={e => setHistorySearch(e.target.value)}
                  className="w-full pl-9 pr-3 py-2 border border-gray-200 rounded-lg text-sm"
                />
              </div>
              {historyLoading ? (
                <div className="flex flex-col items-center justify-center h-40">
                  <Loader2 className="h-8 w-8 animate-spin text-primary mb-2" />
                  <p className="text-gray-500 text-sm">Loading history…</p>
                </div>
              ) : filteredHistory.length === 0 ? (
                <div className="flex flex-col items-center justify-center h-40 text-gray-500">
                  <FileSpreadsheet className="h-10 w-10 mb-2 opacity-20" />
                  <p className="text-sm">No mapping files found.</p>
                </div>
              ) : (
                <div className="border rounded-md overflow-x-auto">
                  <table className="w-full text-sm min-w-[400px]">
                    <thead className="bg-gray-50 border-b sticky top-0">
                      <tr>
                        <th className="text-left p-2 font-medium text-gray-600">Name</th>
                        <th className="text-left p-2 font-medium text-gray-600 hidden sm:table-cell">Date</th>
                        <th className="text-right p-2 font-medium text-gray-600">Action</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y">
                      {filteredHistory.map(conversion => (
                        <tr key={conversion.task_id} className="hover:bg-gray-50 transition-colors">
                          <td className="p-2">
                            <div className="font-medium text-gray-900 truncate max-w-[200px]">{conversion.file_name}</div>
                            <div className="text-xs text-gray-500 sm:hidden">{new Date(conversion.modified_at).toLocaleString()}</div>
                          </td>
                          <td className="p-2 hidden sm:table-cell text-gray-600">{new Date(conversion.modified_at).toLocaleString()}</td>
                          <td className="p-2 text-right">
                            <button
                              onClick={() => handleHistorySelect(conversion)}
                              disabled={!!historyDownloadingId || !!isLoading}
                              className="inline-flex items-center gap-1 px-3 py-1.5 text-xs font-semibold rounded-md bg-secondary text-white hover:bg-secondary/90 disabled:opacity-50 min-h-[36px]"
                            >
                              {historyDownloadingId === conversion.task_id && <Loader2 className="h-3 w-3 animate-spin" />}
                              Select
                            </button>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
              {uploadError && (
                <div className="flex items-start gap-2 p-3 bg-red-50 rounded-lg">
                  <AlertCircle className="w-4 h-4 text-red-500 mt-0.5 shrink-0" />
                  <p className="text-sm text-red-700">{uploadError}</p>
                </div>
              )}
            </div>
          )}
        </div>

        <div className="flex items-center justify-between gap-3 p-4 border-t border-gray-200 bg-gray-50">
          <p className="text-xs text-gray-500 min-h-[16px]">{confirmHelper}</p>
          <div className="flex items-center gap-3">
            <button onClick={handleCancel} className="px-4 py-2 text-sm font-medium text-gray-600 hover:text-gray-800 hover:bg-gray-200 rounded-lg transition-colors">Cancel</button>
            <button
              onClick={handleConfirm}
              disabled={confirmDisabled}
              className="px-4 py-2 text-sm font-medium bg-secondary text-white rounded-lg hover:bg-secondary/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
            >
              {isLoading && <Loader2 className="w-4 h-4 animate-spin" />}
              {confirmLabel}
            </button>
          </div>
        </div>
      </motion.div>
    </div>
  )
}
