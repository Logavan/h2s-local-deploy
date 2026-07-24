"use client"

import { useEffect, useMemo, useState } from "react"
import { X, Loader2, ArrowRight, Wand2, CheckCircle2, AlertCircle } from "lucide-react"
import { motion } from "framer-motion"
import { cn } from "@/lib/utils"
import type { MappingEntry } from "@/lib/nested-cv-types"

interface NestedColumnMappingModalProps {
  isOpen: boolean
  onClose: () => void
  parentArtifactName: string
  sourceRef: string
  /** Columns the parent CV needs from this source (from required_columns_json) */
  parentRequiredColumns: string[]
  /** Columns the nested CV produces (from output_schema) */
  nestedOutputColumns: string[]
  /** Existing mappings for this artifact+source pair (pre-populate) */
  existingMappings?: MappingEntry[]
  /** Optional list of nested CV output column names (for parent dropdowns that come from this source) */
  parentOutputColumns?: string[]
  artifactId: string
  isSaving: boolean
  onSave: (rows: MappingEntry[]) => Promise<void> | void
  onSkip?: () => void
}

type RowState = {
  parentCol: string
  nestedCol: string
  /** Whether user has explicitly set this mapping (vs. auto) */
  explicit: boolean
}

function normalize(value: string): string {
  return value.trim().toUpperCase().replace(/^["`[]|["`\]]$/g, "")
}

/**
 * Auto-map by column name (case-insensitive, ignores case + simple punctuation).
 * For each parent column, find the best matching nested output column.
 */
function autoBuildRows(
  parentRequired: string[],
  nestedOutput: string[],
  existing: MappingEntry[]
): RowState[] {
  const result: RowState[] = []
  const usedNested = new Set<string>()

  // 1) Seed with explicit existing mappings (exact match by normalized name)
  const existingByParent = new Map<string, MappingEntry>()
  for (const m of existing) {
    existingByParent.set(normalize(m.source_column_raw), m)
  }

  for (const parentCol of parentRequired) {
    const parentKey = normalize(parentCol)
    const existingMatch = existingByParent.get(parentKey)
    if (existingMatch) {
      const nestedKey = normalize(existingMatch.target_column)
      if (nestedOutput.some(c => normalize(c) === nestedKey)) {
        usedNested.add(nestedKey)
        result.push({
          parentCol,
          nestedCol: nestedOutput.find(c => normalize(c) === nestedKey) || existingMatch.target_column,
          explicit: true,
        })
        continue
      }
    }

    // 2) Auto-match by exact normalized name
    const auto = nestedOutput.find(c => normalize(c) === parentKey)
    if (auto) {
      usedNested.add(parentKey)
      result.push({ parentCol, nestedCol: auto, explicit: false })
      continue
    }

    // 3) Auto-match by token overlap (e.g. CUSTOMER_ID ↔ CUST_ID) — best-effort
    const parentTokens = new Set(parentKey.split(/[^A-Z0-9]+/).filter(Boolean))
    let best: { col: string; score: number } | null = null
    for (const cand of nestedOutput) {
      const candTokens = cand.split(/[^A-Z0-9]+/).filter(Boolean)
      const candKeys = candTokens.map(t => t.toUpperCase())
      let score = 0
      for (const token of candKeys) {
        if (parentTokens.has(token)) score += 1
      }
      // Avoid matching the same nested column twice
      if (score > 0 && !usedNested.has(cand.toUpperCase()) && (!best || score > best.score)) {
        best = { col: cand, score }
      }
    }
    if (best) {
      usedNested.add(best.col.toUpperCase())
      result.push({ parentCol, nestedCol: best.col, explicit: false })
      continue
    }

    // 4) No match — leave empty
    result.push({ parentCol, nestedCol: "", explicit: false })
  }

  return result
}

export default function NestedColumnMappingModal({
  isOpen,
  onClose,
  parentArtifactName,
  sourceRef,
  parentRequiredColumns,
  nestedOutputColumns,
  existingMappings,
  artifactId,
  isSaving,
  onSave,
  onSkip,
}: NestedColumnMappingModalProps) {
  const [rows, setRows] = useState<RowState[]>([])
  const [error, setError] = useState("")
  const [initialized, setInitialized] = useState(false)

  // (Re)build rows whenever inputs change
  useEffect(() => {
    if (!isOpen) {
      setInitialized(false)
      return
    }
    const built = autoBuildRows(
      parentRequiredColumns,
      nestedOutputColumns,
      existingMappings || []
    )
    setRows(built)
    setError("")
    setInitialized(true)
  }, [isOpen, parentRequiredColumns, nestedOutputColumns, existingMappings])

  const autoMatched = useMemo(() => rows.filter(r => r.nestedCol && !r.explicit).length, [rows])
  const explicitMatched = useMemo(() => rows.filter(r => r.explicit && r.nestedCol).length, [rows])
  const unmatched = useMemo(() => rows.filter(r => !r.nestedCol).length, [rows])

  function updateRow(index: number, nestedCol: string) {
    setRows(prev => prev.map((r, i) => i === index ? { ...r, nestedCol, explicit: true } : r))
  }

  function resetRow(index: number) {
    setRows(prev => prev.map((r, i) => {
      if (i !== index) return r
      // Re-run auto-match for this single row
      const parentKey = normalize(r.parentCol)
      const auto = nestedOutputColumns.find(c => normalize(c) === parentKey)
      return { parentCol: r.parentCol, nestedCol: auto || "", explicit: false }
    }))
  }

  async function handleSave() {
    setError("")
    try {
      const validRows = rows.filter(r => r.nestedCol.trim() !== "")
      if (validRows.length === 0 && rows.length > 0) {
        setError("Map at least one column before saving, or click Skip.")
        return
      }
      const newMappings: MappingEntry[] = validRows.map(r => ({
        source_ref_canonical: sourceRef.toUpperCase(),
        source_column_raw: r.parentCol,
        target_table: parentArtifactName,
        target_column: r.nestedCol,
        artifact_id: artifactId,
      }))
      await onSave(newMappings)
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to save mappings")
    }
  }

  function reAutoMatchAll() {
    const rebuilt = autoBuildRows(parentRequiredColumns, nestedOutputColumns, existingMappings || [])
    setRows(rebuilt)
  }

  if (!isOpen) return null

  return (
    <div className="fixed inset-0 bg-black/50 backdrop-blur-sm z-[80] flex items-center justify-center p-4">
      <motion.div
        initial={{ scale: 0.95, opacity: 0 }}
        animate={{ scale: 1, opacity: 1 }}
        exit={{ scale: 0.95, opacity: 0 }}
        className="bg-white rounded-xl shadow-2xl w-full max-w-3xl max-h-[88vh] flex flex-col overflow-hidden"
        role="dialog"
        aria-label="Map columns between parent CV and nested CV"
      >
        <div className="flex items-start justify-between p-4 border-b border-gray-200">
          <div className="pr-4 flex-1 min-w-0">
            <h2 className="text-lg font-semibold text-gray-900">Map Columns Between CVs</h2>
            <p className="text-sm text-gray-600 mt-1">
              Link <span className="font-mono bg-gray-100 px-1 rounded text-gray-700">{sourceRef}</span> columns
              used by <span className="font-medium text-secondary">{parentArtifactName}</span> to the columns
              produced by the nested CV.
            </p>
          </div>
          <button
            onClick={onClose}
            className="p-2 text-gray-400 hover:text-gray-600 hover:bg-gray-100 rounded-lg transition-colors"
            aria-label="Close dialog"
          >
            <X className="w-5 h-5" />
          </button>
        </div>

        {initialized && (
          <div className="px-4 pt-3 pb-2 flex flex-wrap items-center gap-2 text-xs">
            <span className="inline-flex items-center gap-1 px-2 py-1 rounded-full bg-emerald-50 text-emerald-700 border border-emerald-200">
              <Wand2 className="w-3 h-3" /> {autoMatched} auto-matched
            </span>
            {explicitMatched > 0 && (
              <span className="inline-flex items-center gap-1 px-2 py-1 rounded-full bg-blue-50 text-blue-700 border border-blue-200">
                <CheckCircle2 className="w-3 h-3" /> {explicitMatched} already mapped
              </span>
            )}
            {unmatched > 0 && (
              <span className="inline-flex items-center gap-1 px-2 py-1 rounded-full bg-amber-50 text-amber-700 border border-amber-200">
                <AlertCircle className="w-3 h-3" /> {unmatched} unmapped
              </span>
            )}
            <button
              onClick={reAutoMatchAll}
              className="ml-auto inline-flex items-center gap-1 px-2 py-1 rounded text-secondary hover:bg-secondary/10 transition-colors"
              title="Re-run auto-match"
            >
              <Wand2 className="w-3 h-3" /> Re-run auto-match
            </button>
          </div>
        )}

        <div className="flex-1 overflow-y-auto px-4 py-3">
          {parentRequiredColumns.length === 0 ? (
            <div className="text-center py-12 text-gray-500">
              <p className="text-sm">Parent CV does not declare required columns for this source.</p>
              <p className="text-xs text-gray-400 mt-1">
                You can still configure mappings manually using the existing Column Mapping editor.
              </p>
            </div>
          ) : (
            <div className="border border-gray-200 rounded-lg overflow-hidden">
              <table className="w-full text-sm">
                <thead className="bg-gray-50 border-b border-gray-200 sticky top-0 z-10">
                  <tr>
                    <th className="text-left p-2 pl-3 font-medium text-gray-600 w-5/12">
                      Parent CV column (in <span className="font-mono text-xs">{sourceRef}</span>)
                    </th>
                    <th className="text-center p-2 font-medium text-gray-600 w-2/12">
                      <ArrowRight className="w-4 h-4 inline-block" />
                    </th>
                    <th className="text-left p-2 font-medium text-gray-600 w-5/12">Nested CV output column</th>
                    <th className="text-right p-2 pr-3 font-medium text-gray-600 w-12"></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100">
                  {rows.map((row, idx) => {
                    const isAuto = row.nestedCol && !row.explicit
                    const isExplicit = row.nestedCol && row.explicit
                    return (
                      <tr
                        key={`${row.parentCol}-${idx}`}
                        className={cn(
                          "transition-colors",
                          isAuto ? "bg-emerald-50/40" : isExplicit ? "bg-blue-50/40" : ""
                        )}
                      >
                        <td className="p-2 pl-3 font-mono text-gray-800 align-middle">
                          <div className="flex items-center gap-2">
                            <span className="truncate" title={row.parentCol}>{row.parentCol}</span>
                            {isAuto && (
                              <span className="text-[10px] uppercase font-semibold px-1.5 py-0.5 rounded bg-emerald-100 text-emerald-700">
                                auto
                              </span>
                            )}
                            {isExplicit && (
                              <span className="text-[10px] uppercase font-semibold px-1.5 py-0.5 rounded bg-blue-100 text-blue-700">
                                set
                              </span>
                            )}
                          </div>
                        </td>
                        <td className="p-2 text-center text-gray-400 align-middle">
                          <ArrowRight className="w-3.5 h-3.5 inline-block" />
                        </td>
                        <td className="p-2 align-middle">
                          <select
                            value={row.nestedCol}
                            onChange={e => updateRow(idx, e.target.value)}
                            className={cn(
                              "w-full px-2 py-1.5 border rounded text-sm font-mono",
                              row.nestedCol
                                ? "border-gray-300 bg-white text-gray-800"
                                : "border-dashed border-amber-300 bg-amber-50 text-amber-700",
                              "focus:outline-none focus:ring-2 focus:ring-secondary focus:border-secondary"
                            )}
                          >
                            <option value="">— unmapped —</option>
                            {nestedOutputColumns.map(col => (
                              <option key={col} value={col}>{col}</option>
                            ))}
                          </select>
                        </td>
                        <td className="p-2 pr-3 text-right align-middle">
                          {row.explicit && (
                            <button
                              onClick={() => resetRow(idx)}
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
          )}
        </div>

        {error && (
          <div className="mx-4 mb-2 flex items-start gap-2 p-3 bg-red-50 rounded-lg">
            <AlertCircle className="w-4 h-4 text-red-500 mt-0.5 shrink-0" />
            <p className="text-sm text-red-700">{error}</p>
          </div>
        )}

        <div className="flex items-center justify-between gap-3 p-4 border-t border-gray-200 bg-gray-50">
          <p className="text-xs text-gray-500">
            {rows.length === 0
              ? "No required columns declared."
              : `${rows.filter(r => r.nestedCol).length} of ${rows.length} columns mapped.`}
          </p>
          <div className="flex items-center gap-3">
            {onSkip && (
              <button
                onClick={onSkip}
                disabled={isSaving}
                className="px-4 py-2 text-sm font-medium text-gray-600 hover:text-gray-800 hover:bg-gray-200 rounded-lg transition-colors disabled:opacity-50"
              >
                Skip for now
              </button>
            )}
            <button
              onClick={onClose}
              disabled={isSaving}
              className="px-4 py-2 text-sm font-medium text-gray-600 hover:text-gray-800 hover:bg-gray-200 rounded-lg transition-colors disabled:opacity-50"
            >
              Cancel
            </button>
            <button
              onClick={handleSave}
              disabled={isSaving || !initialized}
              className="px-4 py-2 text-sm font-medium bg-secondary text-white rounded-lg hover:bg-secondary/90 transition-colors disabled:opacity-50 disabled:cursor-not-allowed flex items-center gap-2"
            >
              {isSaving && <Loader2 className="w-4 h-4 animate-spin" />}
              Save Mappings
            </button>
          </div>
        </div>
      </motion.div>
    </div>
  )
}
