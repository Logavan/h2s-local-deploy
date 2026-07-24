"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import dynamic from "next/dynamic"
import Image from "next/image"
import Link from "next/link"
import { motion } from "framer-motion"
import {
  AlertCircle, ArrowRight, Check, CheckCircle, ChevronDown, ChevronRight, Download,
  FileSpreadsheet, GitMerge, Lightbulb, Loader2, Pencil, Plus, RotateCcw,
  Save, X,
} from "lucide-react"
import { cn } from "@/lib/utils"
import MappingEditorPopup from "@/components/MappingEditorPopup"
import SqlEditor from "@/components/CodeEditor" // Same SqlEditor used by MappingTool
import NotebookRenderer from "@/components/NotebookRenderer"
import { Checkbox } from "@/components/ui/checkbox"
import {
  nestedAddCvFromXlsx, nestedCreateSession, nestedDeleteCv, nestedDeleteSession,
  nestedDownloadResult, nestedGenerate, nestedGetSession, nestedGetTaskStatus,
  nestedUpdateCv, nestedUpdateMappings, nestedValidate,
} from "@/lib/api"
import type {
  CvArtifact, MappingEntry, NestedSession, ObjectKind, OutputFormat, SourceReference,
} from "@/lib/nested-cv-types"
import NestedDependencyModal from "./NestedDependencyModal"
import NestedColumnMappingModal from "./NestedColumnMappingModal"
import NodeContextMenu, { NodeMenuHint } from "./NodeContextMenu"
import TableMappingModal, { type TableMappingEntry } from "./TableMappingModal"
import type { LinkedSource } from "./CalculationNode"

const FlowBuilder = dynamic(() => import("./FlowBuilder"), { ssr: false })

const PYSPARK_CAPABLE_PLATFORMS = new Set(["azure", "databricks"])

const DATABASE_PLATFORMS = [
  {
    id: "bigquery",
    name: "Google BigQuery",
    logo: "/google-bigquery-logo.png",
    description: "Flatten calculation views into SQL optimized for Google's serverless data warehouse.",
  },
  {
    id: "azure",
    name: "Microsoft Fabric",
    logo: "/fabric.png",
    description: "Generate SQL or PySpark designed for Microsoft Fabric workloads.",
  },
  {
    id: "redshift",
    name: "Amazon Redshift",
    logo: "/amazon-redshift-logo.png",
    description: "Compose nested calculation views for Amazon's cloud data warehouse.",
  },
  {
    id: "snowflake",
    name: "Snowflake Cloud Data Platform",
    logo: "/snowflake-logo.png",
    description: "Build a flattened dependency chain for Snowflake's data platform.",
  },
  {
    id: "databricks",
    name: "Databricks Lakehouse Platform",
    logo: "/databricks_logo.png",
    description: "Generate SQL or PySpark DataFrame code for Databricks Lakehouse.",
  },
  {
    id: "datasphere",
    name: "SAP Datasphere",
    logo: "/sap-datasphere-logo.png",
    description: "Flatten nested HANA calculation views for SAP Datasphere SQL.",
  },
]

type MappingRow = {
  sourceTable: string
  sourceField: string
  targetTable: string
  targetField: string
}

type UiNodeKind = ObjectKind | "nested_resolved"

interface TreeNode {
  id: string
  name: string
  kind: UiNodeKind
  nodeType: "artifact" | "source"
  artifactId?: string
  ownerArtifactId: string
  producerArtifactId?: string
  sourceRefCanonical?: string
  sqlContent: string
  columns: string[]
  mappings: MappingEntry[]
  children: TreeNode[]
  parentId: string | null
  resolved: boolean
}

interface PendingNestedParent {
  nodeId: string
  consumerArtifactId?: string
  sourceRef: string
  nodeName: string
}

function sourceNodeId(artifactId: string, sourceRef: string) {
  return `source:${artifactId}:${encodeURIComponent(sourceRef)}`
}

function parseRequiredColumns(source: SourceReference): string[] {
  const value = source.required_columns_json
  if (Array.isArray(value)) return value
  try {
    const parsed = JSON.parse(value || "[]")
    return Array.isArray(parsed) ? parsed.map(String) : []
  } catch {
    return []
  }
}

function combinedSql(artifact: CvArtifact) {
  return (artifact.sql_chunks || []).map(chunk => chunk.sql_content).filter(Boolean).join("\n\n")
}

function buildTree(session: NestedSession): TreeNode[] {
  const artifacts = session.artifacts
  const producerIds = new Set(
    session.dependency_links
      .filter(link => link.resolution === "uploaded_cv" && link.producer_artifact_id)
      .map(link => link.producer_artifact_id as string)
  )

  const buildArtifact = (artifactId: string, parentId: string | null, path: Set<string>): TreeNode | null => {
    const artifact = artifacts[artifactId]
    if (!artifact || path.has(artifactId)) return null
    const nextPath = new Set(path).add(artifactId)
    const sqlContent = combinedSql(artifact)

    const children = (artifact.dependencies || []).map(dependency => {
      const id = sourceNodeId(artifactId, dependency.source_ref_canonical)
      const link = session.dependency_links.find(item =>
        item.consumer_artifact_id === artifactId
        && item.source_ref_canonical.toUpperCase() === dependency.source_ref_canonical.toUpperCase()
        && item.resolution === "uploaded_cv"
        && item.producer_artifact_id
      )
      const producer = link?.producer_artifact_id
        ? buildArtifact(link.producer_artifact_id, id, nextPath)
        : null
      const mappings = session.global_mappings.filter(mapping =>
        mapping.artifact_id === artifactId
        && mapping.source_ref_canonical.toUpperCase() === dependency.source_ref_canonical.toUpperCase()
      )
      return {
        id,
        name: dependency.source_ref_raw || dependency.source_ref_canonical,
        kind: producer ? "nested_resolved" as const : dependency.object_kind,
        nodeType: "source" as const,
        ownerArtifactId: artifactId,
        producerArtifactId: producer?.artifactId,
        sourceRefCanonical: dependency.source_ref_canonical,
        sqlContent,
        columns: parseRequiredColumns(dependency),
        mappings,
        children: producer ? [producer] : [],
        parentId: artifactId,
        resolved: Boolean(producer),
      }
    })

    return {
      id: artifactId,
      name: artifact.cv_display_name,
      kind: "calculation_view",
      nodeType: "artifact",
      artifactId,
      ownerArtifactId: artifactId,
      sqlContent,
      columns: (artifact.output_schema || []).map(column => column.column_name),
      mappings: session.global_mappings.filter(mapping => mapping.artifact_id === artifactId),
      children,
      parentId,
      resolved: parentId !== null,
    }
  }

  return Object.keys(artifacts)
    .filter(artifactId => !producerIds.has(artifactId))
    .map(artifactId => buildArtifact(artifactId, null, new Set()))
    .filter((node): node is TreeNode => Boolean(node))
}

function findNode(nodes: TreeNode[], id: string | null): TreeNode | null {
  if (!id) return null
  for (const node of nodes) {
    if (node.id === id) return node
    const child = findNode(node.children, id)
    if (child) return child
  }
  return null
}

function badgeFor(node: TreeNode, isCandidate: boolean) {
  if (node.kind === "nested_resolved") return { label: "NST", className: "bg-green-100 text-green-700" }
  if (isCandidate) return { label: "NST", className: "bg-emerald-100 text-emerald-700 ring-1 ring-emerald-300" }
  if (node.nodeType === "artifact") return { label: "CV", className: "bg-purple-100 text-purple-700" }
  if (node.kind === "physical_table") return { label: "TBL", className: "bg-blue-100 text-blue-700" }
  return { label: "VIEW", className: "bg-amber-100 text-amber-700" }
}

export default function NestedCVTool() {
  const [session, setSession] = useState<NestedSession | null>(null)
  const [targetDialect, setTargetDialect] = useState<string | null>(null)
  const [outputFormat, setOutputFormat] = useState<OutputFormat>("sql")
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [expandedIds, setExpandedIds] = useState<Set<string>>(new Set())
  const [candidateIds, setCandidateIds] = useState<Set<string>>(new Set())
  const [descriptions, setDescriptions] = useState<Record<string, string>>({})
  const [localColumns, setLocalColumns] = useState<Record<string, string[]>>({})
  const [editName, setEditName] = useState("")
  const [editColumns, setEditColumns] = useState<string[]>([])
  const [editDescription, setEditDescription] = useState("")
  const [showNestedModal, setShowNestedModal] = useState(false)
  const [pendingNestedParent, setPendingNestedParent] = useState<PendingNestedParent | null>(null)
  // Column mapping modal — shown automatically after linking a nested CV
  const [columnMappingContext, setColumnMappingContext] = useState<{
    artifactId: string
    consumerArtifactId: string
    sourceRef: string
    parentName: string
    parentRequiredColumns: string[]
    nestedOutputColumns: string[]
  } | null>(null)
  const [columnMappingSaving, setColumnMappingSaving] = useState(false)
  const [showMappingEditor, setShowMappingEditor] = useState(false)
  const [mappingNodeId, setMappingNodeId] = useState<string | null>(null)
  const [mappingRows, setMappingRows] = useState<MappingRow[]>([])
  const [mappingSql, setMappingSql] = useState("")
  const [mappingFileName, setMappingFileName] = useState("")
  const [showTableMappingModal, setShowTableMappingModal] = useState(false)
  const [tableMappingEntries, setTableMappingEntries] = useState<TableMappingEntry[]>([])
  const [loading, setLoading] = useState(false)
  const [generating, setGenerating] = useState(false)
  const [taskProgress, setTaskProgress] = useState(0)
  const [taskMessage, setTaskMessage] = useState("")
  const [taskId, setTaskId] = useState<string | null>(null)
  const [resultContent, setResultContent] = useState<string | null>(null)
  const [resultFileName, setResultFileName] = useState<string | null>(null)
  const [genErrors, setGenErrors] = useState<string[]>([])
  const [error, setError] = useState("")
  const pollGenerationRef = useRef(0)
  const [isEditorCollapsed, setIsEditorCollapsed] = useState(false)
  const [isFullScreen, setIsFullScreen] = useState(false)
  const [editableFileName, setEditableFileName] = useState("")
  const [isFileNameEditable, setIsFileNameEditable] = useState(false)

  const tree = useMemo(() => session ? buildTree(session) : [], [session])
  const selectedNode = useMemo(() => findNode(tree, selectedNodeId), [tree, selectedNodeId])

  useEffect(() => {
    if (!selectedNode) return
    setEditName(selectedNode.name)
    setEditColumns(localColumns[selectedNode.id] || selectedNode.columns)
    setEditDescription(descriptions[selectedNode.id] || "")
  }, [selectedNode, localColumns, descriptions])

  useEffect(() => () => { pollGenerationRef.current += 1 }, [])

  const refreshSession = useCallback(async (sessionId: string) => {
    const response = await nestedGetSession(sessionId)
    if (!response.success || !response.session) throw new Error(response.error || "Failed to refresh session")
    setSession(response.session)
    return response.session
  }, [])

  function invalidateResult() {
    pollGenerationRef.current += 1
    setResultContent(null)
    setResultFileName(null)
    setTaskId(null)
    setTaskProgress(0)
    setTaskMessage("")
  }

  function toggleExpand(id: string) {
    setExpandedIds(previous => {
      const next = new Set(previous)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  function setCandidate(id: string, enabled: boolean) {
    setCandidateIds(previous => {
      const next = new Set(previous)
      enabled ? next.add(id) : next.delete(id)
      return next
    })
  }

  async function createSession() {
    if (!targetDialect) return
    setLoading(true)
    setError("")
    try {
      const response = await nestedCreateSession({ target_dialect: targetDialect, output_format: outputFormat })
      if (!response.success || !response.session) throw new Error(response.error || "Failed to create session")
      setSession(response.session)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to create session")
    } finally {
      setLoading(false)
    }
  }

  function openRootUpload() {
    setPendingNestedParent({ nodeId: "root", sourceRef: "root", nodeName: "Root Calculation View" })
    setShowNestedModal(true)
  }

  function openNested(node: TreeNode) {
    if (node.nodeType !== "source" || !node.sourceRefCanonical || node.resolved) return
    setPendingNestedParent({
      nodeId: node.id,
      consumerArtifactId: node.ownerArtifactId,
      sourceRef: node.sourceRefCanonical,
      nodeName: node.name,
    })
    setShowNestedModal(true)
  }

  async function handleNestedConfirm(
    file: File,
    selectedSource: string,
    columnMappings?: Array<{ parentCol: string; nestedCol: string; isAuto: boolean }>,
  ) {
    if (!session || !pendingNestedParent) return
    setLoading(true)
    setError("")
    // Capture the parent context now (before async clears pendingNestedParent)
    const parentConsumerArtifactId = pendingNestedParent.consumerArtifactId
    const parentSourceRef = pendingNestedParent.sourceRef
    const parentNodeName = pendingNestedParent.nodeName
    const parentNodeId = pendingNestedParent.nodeId
    try {
      const response = await nestedAddCvFromXlsx(
        session.session_id,
        file,
        parentConsumerArtifactId ? parentSourceRef : undefined,
        parentConsumerArtifactId,
        false,
        selectedSource,
      )
      if (!response.success || !response.artifact) throw new Error(response.error || "Upload failed")
      invalidateResult()
      // Use the freshest session from the response
      const updatedSession = (response.session as NestedSession | undefined) || null
      if (updatedSession) {
        setSession(updatedSession)
      } else {
        await refreshSession(session.session_id)
      }
      setSelectedNodeId(response.artifact.artifact_id)
      if (parentNodeId !== "root") {
        setExpandedIds(previous => new Set(previous).add(parentNodeId))
        setCandidate(parentNodeId, false)
      }
      setShowNestedModal(false)

      // Save column linkages (if user provided any) immediately after upload.
      // We merge into the existing global_mappings: drop any prior mappings
      // for this (artifact, source_ref_canonical) pair, then append the new ones.
      if (parentConsumerArtifactId && columnMappings && columnMappings.length > 0) {
        const validMappings = columnMappings.filter(m => m.nestedCol && m.nestedCol.trim() !== "")
        if (validMappings.length > 0) {
          const sourceUpper = parentSourceRef.toUpperCase()
          const currentSession = session
          const parentArtifact = currentSession.artifacts[parentConsumerArtifactId]
          const unrelated = currentSession.global_mappings.filter(m => {
            if (m.artifact_id !== parentConsumerArtifactId) return true
            if ((m.source_ref_canonical || "").toUpperCase() !== sourceUpper) return true
            return false
          })
          const newMappings: MappingEntry[] = validMappings.map(m => ({
            source_ref_canonical: sourceUpper,
            source_column_raw: m.parentCol,
            target_table: parentArtifact?.cv_display_name || parentNodeName || "Unknown",
            target_column: m.nestedCol,
            artifact_id: parentConsumerArtifactId,
          }))
          const updateRes = await nestedUpdateMappings(session.session_id, [...unrelated, ...newMappings])
          if (!updateRes.success) throw new Error(updateRes.error || "Failed to save column mappings")
          invalidateResult()
          await refreshSession(session.session_id)
        }
      }

      // After linking a nested CV, automatically open column mapping modal
      // so user can confirm/edit mappings between parent's required columns and
      // the nested CV's output schema. (Only if user hasn't already configured them.)
      if (parentConsumerArtifactId) {
        const freshSession = await nestedGetSession(session.session_id)
        const latestSession = freshSession.session || updatedSession
        if (latestSession) {
          const newArtifactId = response.artifact.artifact_id
          const newArtifact = latestSession.artifacts[newArtifactId]
          const parentArtifact = latestSession.artifacts[parentConsumerArtifactId]
          if (parentArtifact && newArtifact) {
            const sourceUpper = parentSourceRef.toUpperCase()
            // Required columns on parent side for this source
            const parentDep = parentArtifact.dependencies.find(d => d.source_ref_canonical.toUpperCase() === sourceUpper)
            const requiredColumns: string[] = []
            if (parentDep) {
              // required_columns_json may arrive as either string[] (newer) or a JSON string (legacy)
              const raw = parentDep.required_columns_json
              if (Array.isArray(raw)) {
                requiredColumns.push(...raw.map(String))
              } else if (typeof raw === "string") {
                try {
                  const arr = JSON.parse(raw || "[]")
                  if (Array.isArray(arr)) requiredColumns.push(...arr.map(String))
                } catch { /* ignore */ }
              }
            }
            // Output columns on the new nested CV side
            const outputColumns = (newArtifact.output_schema || []).map(c => c.column_name)
            // Only show modal if there's something to map (avoid empty noise)
            if (requiredColumns.length > 0 && outputColumns.length > 0) {
              setColumnMappingContext({
                artifactId: newArtifactId,
                consumerArtifactId: parentConsumerArtifactId,
                sourceRef: parentSourceRef,
                parentName: parentArtifact.cv_display_name,
                parentRequiredColumns: requiredColumns,
                nestedOutputColumns: outputColumns,
              })
            }
          }
        }
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to add nested CV")
    } finally {
      setLoading(false)
    }
  }

  async function saveColumnMappings(rows: MappingEntry[]) {
    if (!session || !columnMappingContext) return
    setColumnMappingSaving(true)
    setError("")
    try {
      // Merge: keep all existing global mappings except those that match this
      // (artifact, source_ref_canonical) pair, then append the new ones.
      const sourceUpper = columnMappingContext.sourceRef.toUpperCase()
      const unrelated = session.global_mappings.filter(m => {
        if (m.artifact_id !== columnMappingContext.consumerArtifactId) return true
        if ((m.source_ref_canonical || "").toUpperCase() !== sourceUpper) return true
        return false
      })
      const response = await nestedUpdateMappings(session.session_id, [...unrelated, ...rows])
      if (!response.success) throw new Error(response.error || "Failed to save column mappings")
      invalidateResult()
      await refreshSession(session.session_id)
      setColumnMappingContext(null)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to save column mappings")
    } finally {
      setColumnMappingSaving(false)
    }
  }

  function openMappingEditor(node: TreeNode) {
    const rows = node.mappings.map(mapping => ({
      sourceTable: mapping.source_ref_canonical,
      sourceField: mapping.source_column_raw,
      targetTable: mapping.target_table,
      targetField: mapping.target_column,
    }))
    if (!rows.length) {
      setError(`No column mappings are available for ${node.name}. Use Adjust Mappings after mappings are saved.`)
      return
    }
    setMappingNodeId(node.id)
    setMappingRows(rows)
    setMappingSql(node.sqlContent)
    setMappingFileName(`${node.name} — columns`)
    setShowMappingEditor(true)
  }

  // Build a deduplicated list of unique source tables from node mappings
  function extractUniqueTables(node: TreeNode): TableMappingEntry[] {
    const seen = new Map<string, string>()
    for (const mapping of node.mappings) {
      const key = mapping.source_ref_canonical
      if (!seen.has(key)) {
        seen.set(key, mapping.target_table)
      }
    }
    return Array.from(seen, ([sourceTable, targetTable]) => ({ sourceTable, targetTable }))
  }

  function openTableMappingEditor(node: TreeNode) {
    const unique = extractUniqueTables(node)
    if (!unique.length) {
      setError(`No table mappings are available for ${node.name}. Save column mappings first to create table entries.`)
      return
    }
    setMappingNodeId(node.id)
    setTableMappingEntries(unique)
    setShowTableMappingModal(true)
  }

  async function saveTableMappings(entries: TableMappingEntry[]) {
    if (!session || !mappingNodeId) return
    const node = findNode(tree, mappingNodeId)
    if (!node) return
    setLoading(true)
    setError("")
    try {
      // Build a lookup of table-level target renames
      const tableTargetMap = new Map<string, string>()
      for (const entry of entries) {
        tableTargetMap.set(entry.sourceTable, entry.targetTable)
      }

      const scopedSource = node.nodeType === "source" ? node.sourceRefCanonical?.toUpperCase() : null
      const unrelated = session.global_mappings.filter(mapping => {
        if (mapping.artifact_id !== node.ownerArtifactId) return true
        if (!scopedSource) return false
        return mapping.source_ref_canonical.toUpperCase() !== scopedSource
      })

      // Expand: for every existing column mapping under this artifact, apply the table rename.
      // Also ensure there is at least one mapping per unique source table so the table exists.
      const relevantMappings = session.global_mappings.filter(mapping => {
        if (mapping.artifact_id !== node.ownerArtifactId) return false
        if (!scopedSource) return true
        return mapping.source_ref_canonical.toUpperCase() === scopedSource
      })

      const seenKeys = new Set<string>()
      const expanded: MappingEntry[] = relevantMappings.map(mapping => {
        seenKeys.add(`${mapping.source_ref_canonical}::${mapping.source_column_raw}`)
        return {
          ...mapping,
          target_table: tableTargetMap.get(mapping.source_ref_canonical) ?? mapping.target_table,
        }
      })

      // Ensure each unique table has at least one column mapping entry
      tableTargetMap.forEach((targetTable, sourceTable) => {
        const existingKey = `${sourceTable}::__placeholder__`
        if (!seenKeys.has(existingKey)) {
          // No column-level mapping exists for this table; create a placeholder row
          expanded.push({
            source_ref_canonical: sourceTable,
            source_column_raw: "*",
            target_table: targetTable,
            target_column: "*",
            artifact_id: node.ownerArtifactId,
          })
        }
      })

      const response = await nestedUpdateMappings(session.session_id, [...unrelated, ...expanded])
      if (!response.success) throw new Error(response.error || "Failed to save table mappings")
      invalidateResult()
      await refreshSession(session.session_id)
      setShowTableMappingModal(false)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to save table mappings")
    } finally {
      setLoading(false)
    }
  }

  async function saveMappings(rows: MappingRow[]) {
    if (!session || !mappingNodeId) return
    const node = findNode(tree, mappingNodeId)
    if (!node) return
    setLoading(true)
    setError("")
    try {
      const scopedSource = node.nodeType === "source" ? node.sourceRefCanonical?.toUpperCase() : null
      const unrelated = session.global_mappings.filter(mapping => {
        if (mapping.artifact_id !== node.ownerArtifactId) return true
        if (!scopedSource) return false
        return mapping.source_ref_canonical.toUpperCase() !== scopedSource
      })
      const updated: MappingEntry[] = rows.map(row => ({
        source_ref_canonical: row.sourceTable,
        source_column_raw: row.sourceField,
        target_table: row.targetTable,
        target_column: row.targetField,
        artifact_id: node.ownerArtifactId,
      }))
      const response = await nestedUpdateMappings(session.session_id, [...unrelated, ...updated])
      if (!response.success) throw new Error(response.error || "Failed to save mappings")
      invalidateResult()
      await refreshSession(session.session_id)
      setShowMappingEditor(false)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to save mappings")
    } finally {
      setLoading(false)
    }
  }

  async function removeNode(node: TreeNode) {
    if (!session) return
    if (node.nodeType === "source" && !node.producerArtifactId) {
      setCandidate(node.id, false)
      return
    }
    const artifactId = node.nodeType === "artifact" ? node.artifactId : node.producerArtifactId
    if (!artifactId || !window.confirm(`Remove ${node.name} and its nested links?`)) return
    setLoading(true)
    setError("")
    try {
      const response = await nestedDeleteCv(session.session_id, artifactId)
      if (!response.success) throw new Error(response.error || "Failed to remove CV")
      invalidateResult()
      await refreshSession(session.session_id)
      if (selectedNodeId === node.id || selectedNodeId === artifactId) setSelectedNodeId(null)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to remove CV")
    } finally {
      setLoading(false)
    }
  }

  async function saveNodeDetails() {
    if (!session || !selectedNode) return
    setLoading(true)
    setError("")
    try {
      if (selectedNode.nodeType === "artifact" && selectedNode.artifactId && editName !== selectedNode.name) {
        const response = await nestedUpdateCv(session.session_id, selectedNode.artifactId, { cv_display_name: editName })
        if (!response.success) throw new Error(response.error || "Failed to update CV")
        invalidateResult()
        await refreshSession(session.session_id)
      }
      setLocalColumns(previous => ({ ...previous, [selectedNode.id]: editColumns }))
      setDescriptions(previous => ({ ...previous, [selectedNode.id]: editDescription }))
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to save details")
    } finally {
      setLoading(false)
    }
  }

  async function handleGenerate() {
    if (!session) return
    setGenerating(true)
    setError("")
    setGenErrors([])
    try {
      const validation = await nestedValidate(session.session_id)
      if (!validation.success || !validation.valid) {
        const messages = validation.errors.map(item => item.message)
        setGenErrors(messages)
        throw new Error(messages[0] || "Resolve validation errors before generating")
      }
      const response = await nestedGenerate(session.session_id)
      if (!response.success || !response.task_id) throw new Error(response.error || "No task ID returned")
      setTaskId(response.task_id)
      const generation = ++pollGenerationRef.current
      for (let attempt = 0; attempt < 60 && pollGenerationRef.current === generation; attempt += 1) {
        await new Promise(resolve => setTimeout(resolve, 2000))
        const status = await nestedGetTaskStatus(response.task_id)
        setTaskProgress(status.progress)
        setTaskMessage(status.message)
        if (status.status === "COMPLETED") {
          setResultContent(status.result_content || "")
          const format = status.output_format || session.output_format
          setResultFileName(`nested_cv_${response.task_id.slice(0, 8)}.${format === "pyspark" ? "pyspark" : "sql"}`)
          return
        }
        if (status.status === "FAILED" || status.status === "CANCELLED") throw new Error(status.message)
      }
      throw new Error("Generation timed out")
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Generation failed")
    } finally {
      setGenerating(false)
    }
  }

  async function reset() {
    if (!session || !window.confirm("Reset this nested CV session?")) return
    pollGenerationRef.current += 1
    await nestedDeleteSession(session.session_id)
    setSession(null)
    setTargetDialect(null)
    setOutputFormat("sql")
    setError("")
    setSelectedNodeId(null)
    setExpandedIds(new Set())
    setCandidateIds(new Set())
    setResultContent(null)
    setTaskId(null)
  }

  function TreeRow({ node, depth = 0 }: { node: TreeNode; depth?: number }) {
    const isExpanded = expandedIds.has(node.id)
    const isSelected = selectedNodeId === node.id
    const isCandidate = candidateIds.has(node.id)
    const hasChildren = node.children.length > 0
    const badge = badgeFor(node, isCandidate)
    const canResolve = node.nodeType === "source" && !node.resolved

    const row = (
      <div
        tabIndex={0}
        className={cn(
          "flex items-center gap-1 rounded-md px-2 py-1.5 text-sm cursor-pointer group transition-colors focus:outline-none focus:ring-1 focus:ring-secondary",
          isSelected ? "bg-secondary/20 text-primary font-medium" : "text-gray-700 hover:bg-gray-100 dark:text-gray-200 dark:hover:bg-gray-800"
        )}
        style={{ paddingLeft: `${12 + depth * 20}px` }}
        onClick={() => setSelectedNodeId(node.id)}
      >
        <button onClick={event => { event.stopPropagation(); toggleExpand(node.id) }} className="p-0.5 rounded hover:bg-gray-200" aria-label={isExpanded ? "Collapse" : "Expand"}>
          {hasChildren ? isExpanded ? <ChevronDown className="h-3.5 w-3.5" /> : <ChevronRight className="h-3.5 w-3.5" /> : <span className="block h-3.5 w-3.5" />}
        </button>
        <span className={cn("rounded px-1.5 py-0.5 text-xs font-medium", badge.className)}>{badge.label}</span>
        <span className="flex-1 truncate font-mono text-xs">{node.name}</span>
        {canResolve && (
          <button
            onClick={event => { event.stopPropagation(); openNested(node) }}
            className={cn("rounded p-1 hover:bg-secondary/10", isCandidate ? "opacity-100 bg-emerald-50" : "opacity-0 group-hover:opacity-100 group-focus-within:opacity-100")}
            title="Resolve as nested CV"
          >
            <Plus className="h-3.5 w-3.5 text-secondary" />
          </button>
        )}
        <span className="opacity-0 group-hover:opacity-60 group-focus-within:opacity-60" title="Right-click for actions"><NodeMenuHint /></span>
      </div>
    )

    return (
      <div>
        <NodeContextMenu
          canAdjustMappings={node.mappings.length > 0}
          canResolveNested={canResolve}
          canRemove={node.nodeType === "artifact" || node.resolved || isCandidate}
          onAdjustMappings={() => openMappingEditor(node)}
          onResolveNested={() => openNested(node)}
          onRemove={() => removeNode(node)}
        >
          {row}
        </NodeContextMenu>
        {isExpanded && hasChildren && node.children.map(child => <TreeRow key={child.id} node={child} depth={depth + 1} />)}
      </div>
    )
  }

  const selectedCandidate = selectedNode ? candidateIds.has(selectedNode.id) : false
  const selectedBadge = selectedNode ? badgeFor(selectedNode, selectedCandidate) : null

  return (
    <div className="max-w-6xl mx-auto bg-white dark:bg-gray-800 shadow-lg rounded-lg p-4 sm:p-8">
      <div className="flex flex-col sm:flex-row sm:justify-between sm:items-center gap-2 sm:gap-4 mb-4 sm:mb-6">
        <div className="flex flex-col sm:flex-row sm:items-center gap-1 sm:gap-4">
          <h1 className="text-xl sm:text-2xl md:text-3xl font-bold text-primary">Nested CV Flattener</h1>
          <Link href="/how-to-use#nested-cv" className="inline-flex items-center px-3 py-1.5 text-xs sm:text-sm font-medium text-primary bg-secondary/10 rounded-full hover:bg-secondary/20 transition-colors self-start sm:self-auto">
            <Lightbulb className="w-3 h-3 sm:w-4 sm:h-4 mr-1 sm:mr-1.5 text-secondary" /> How to Use
          </Link>
        </div>
        {session && <button onClick={reset} className="flex items-center px-3 py-2 sm:px-4 min-h-[44px] text-xs sm:text-sm font-medium text-gray-700 bg-gray-100 rounded-md hover:bg-gray-200 transition-colors self-start sm:self-auto"><RotateCcw className="w-3 h-3 sm:w-4 sm:h-4 mr-1 sm:mr-2" /> Reset</button>}
      </div>

      {!session && (
        <div className="mt-8">
          <section aria-labelledby="nested-platform-heading">
            <h2 id="nested-platform-heading" className="text-xl font-semibold text-primary mb-2">Select Target Platform</h2>
            <p className="text-gray-600 dark:text-gray-300 mb-6">Choose where you want to generate and run the flattened calculation view.</p>
            <div role="radiogroup" aria-labelledby="nested-platform-heading" className="grid grid-cols-1 gap-3 sm:gap-4">
              {DATABASE_PLATFORMS.map(platform => {
                const selected = targetDialect === platform.id
                const selectPlatform = () => {
                  setTargetDialect(platform.id)
                  if (!PYSPARK_CAPABLE_PLATFORMS.has(platform.id)) {
                    setOutputFormat("sql")
                  }
                  setError("")
                }
                return (
                  <div
                    key={platform.id}
                    role="radio"
                    aria-checked={selected}
                    tabIndex={0}
                    onClick={selectPlatform}
                    onKeyDown={event => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault()
                        selectPlatform()
                      }
                    }}
                    className={cn(
                      "flex items-center p-3 sm:p-4 border rounded-lg cursor-pointer transition-all min-h-[72px] sm:min-h-[80px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-secondary focus-visible:ring-offset-2",
                      selected ? "border-secondary bg-secondary/5 shadow-md" : "border-gray-200 dark:border-gray-600 hover:border-secondary/50 hover:bg-gray-50 dark:hover:bg-gray-700/50"
                    )}
                  >
                    <div className="relative w-16 h-8 sm:w-24 sm:h-12 shrink-0 mr-3 sm:mr-4">
                      <Image src={platform.logo} alt={platform.name} fill className="object-contain" sizes="(max-width: 640px) 64px, 96px" />
                    </div>
                    <div className="flex-grow min-w-0">
                      <h3 className="font-medium text-primary text-sm sm:text-base">{platform.name}</h3>
                      <p className="text-xs sm:text-sm text-gray-500 dark:text-gray-400">{platform.description}</p>
                    </div>
                    <div className="ml-2 shrink-0">
                      {selected ? <div className="w-5 h-5 sm:w-6 sm:h-6 rounded-full bg-secondary flex items-center justify-center"><Check className="w-3 h-3 sm:w-4 sm:h-4 text-white" /></div> : <ArrowRight className="w-4 h-4 sm:w-5 sm:h-5 text-gray-400" />}
                    </div>
                  </div>
                )
              })}
            </div>
          </section>

          {targetDialect && PYSPARK_CAPABLE_PLATFORMS.has(targetDialect) && (
            <motion.section
              initial={{ opacity: 0, y: 20 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ duration: 0.4 }}
              aria-labelledby="nested-format-heading"
              className="mt-8"
            >
              <h2 id="nested-format-heading" className="text-xl font-semibold text-primary mb-2">Choose Output Format</h2>
              <p className="text-gray-600 dark:text-gray-300 mb-6">Select how you want the merged calculation view delivered.</p>
              <div role="radiogroup" aria-labelledby="nested-format-heading" className="grid grid-cols-1 sm:grid-cols-2 gap-4">
                {([
                  { id: "sql" as OutputFormat, title: "SQL", description: "Generate standard SQL and download it as a .sql file.", iconClass: "bg-blue-100 text-blue-600" },
                  { id: "pyspark" as OutputFormat, title: "PySpark", description: "Generate PySpark DataFrame code for a Lakehouse workflow.", iconClass: "bg-orange-100 text-orange-600" },
                ]).map(format => {
                  const selected = outputFormat === format.id
                  const selectFormat = () => {
                    setOutputFormat(format.id)
                    setError("")
                  }
                  return (
                    <div
                      key={format.id}
                      role="radio"
                      aria-checked={selected}
                      tabIndex={0}
                      onClick={selectFormat}
                      onKeyDown={event => {
                        if (event.key === "Enter" || event.key === " ") {
                          event.preventDefault()
                          selectFormat()
                        }
                      }}
                      className={cn(
                        "relative flex flex-col items-center p-4 sm:p-6 border-2 rounded-xl cursor-pointer transition-all min-h-[140px] sm:min-h-[160px] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-secondary focus-visible:ring-offset-2",
                        selected ? "border-secondary bg-secondary/5 shadow-md" : "border-gray-200 dark:border-gray-600 hover:border-secondary hover:bg-secondary/5 hover:shadow-md"
                      )}
                    >
                      {selected && <div className="absolute top-3 right-3 w-6 h-6 rounded-full bg-secondary flex items-center justify-center"><Check className="w-4 h-4 text-white" /></div>}
                      <div className={cn("w-12 h-12 sm:w-16 sm:h-16 rounded-full flex items-center justify-center mb-3 sm:mb-4", format.iconClass)}>
                        {format.id === "sql" ? <FileSpreadsheet className="w-7 h-7" /> : <GitMerge className="w-7 h-7" />}
                      </div>
                      <h3 className="font-semibold text-primary text-base sm:text-lg mb-1">{format.title}</h3>
                      <p className="text-xs sm:text-sm text-gray-500 dark:text-gray-400 text-center">{format.description}</p>
                    </div>
                  )
                })}
              </div>
            </motion.section>
          )}

          <div className="sr-only" aria-live="polite">
            {targetDialect ? `Selected platform ${DATABASE_PLATFORMS.find(platform => platform.id === targetDialect)?.name}; selected format ${outputFormat}.` : "No target platform selected."}
          </div>

          {targetDialect && (
            <button
              disabled={loading}
              onClick={createSession}
              className="w-full py-3 sm:py-4 rounded-md font-medium text-sm sm:text-base transition-all duration-300 mt-10 bg-secondary text-primary hover:bg-secondary/90 disabled:bg-gray-200 disabled:text-gray-400 disabled:cursor-not-allowed flex items-center justify-center gap-2"
            >
              {loading ? <><Loader2 className="w-5 h-5 animate-spin" /> Starting session…</> : <><GitMerge className="w-5 h-5" /> Start Nested CV Session</>}
            </button>
          )}
        </div>
      )}

      {session && <div className="flex items-center gap-2 mb-4 text-xs text-gray-500"><span>Platform: <strong>{session.target_dialect}</strong></span><span>·</span><span>Format: <strong>{session.output_format}</strong></span></div>}

      {error && (
        <div role="alert" aria-live="assertive" className="mt-8 mb-4 p-4 bg-red-50 border border-red-200 rounded-lg">
          <div className="flex items-start gap-3">
            <AlertCircle className="h-5 w-5 text-red-500 mt-0.5 shrink-0" />
            <div className="flex-1">
              <h3 className="font-medium text-red-800 mb-1">{!session ? "Couldn't start the Nested CV session" : "Nested CV action failed"}</h3>
              <p className="text-sm text-red-700">{error}</p>
              {!session && error.toLowerCase().includes("connect") && <p className="text-sm text-red-700 mt-1">Confirm that the backend service is running and the configured API URL is reachable.</p>}
              {!session && targetDialect && <button onClick={createSession} disabled={loading} className="mt-3 inline-flex items-center min-h-[40px] px-4 py-2 text-sm font-medium text-red-700 bg-white border border-red-300 rounded-md hover:bg-red-100 disabled:opacity-50">{loading ? <Loader2 className="w-4 h-4 mr-2 animate-spin" /> : <RotateCcw className="w-4 h-4 mr-2" />}Try Again</button>}
            </div>
          </div>
        </div>
      )}
      {genErrors.length > 1 && <ul className="mb-4 list-disc pl-8 text-sm text-red-700">{genErrors.slice(1).map(message => <li key={message}>{message}</li>)}</ul>}

      {session && tree.length > 0 && (
        <div className="mb-4">
          {(() => {
            // Use first root as main, collect all bases from all trees
            const root = tree[0]

            // Build linkedSources list from artifact's global_mappings (HANA tables/views actually used in the SQL).
            // global_mappings is the authoritative source — artifact.dependencies may include unused entries.
            const buildLinkedSourcesFor = (artifactId: string | undefined): LinkedSource[] => {
              if (!artifactId || !session) return []
              const artifact = session.artifacts[artifactId]
              if (!artifact) return []

              // Get all mappings for this artifact
              const artifactMappings = session.global_mappings.filter(
                m => m.artifact_id === artifactId
              )

              // Get unique source_ref_canonical values (case-insensitive)
              const seenCanonical = new Map<string, MappingEntry>()
              for (const mapping of artifactMappings) {
                const key = mapping.source_ref_canonical.toUpperCase()
                if (!seenCanonical.has(key)) {
                  seenCanonical.set(key, mapping)
                }
              }

              // Build a quick lookup of dependencies metadata for objectKind/raw names
              const depByCanonical = new Map<string, SourceReference>()
              for (const dep of artifact.dependencies || []) {
                depByCanonical.set(dep.source_ref_canonical.toUpperCase(), dep)
              }

              // Get all dependency_links for this artifact to determine which sources are already linked
              const linkedByCanonical = new Map<string, { producer_artifact_id: string; producer: CvArtifact | undefined }>()
              for (const link of session.dependency_links) {
                if (link.consumer_artifact_id !== artifactId) continue
                if (link.resolution !== "uploaded_cv" || !link.producer_artifact_id) continue
                const key = link.source_ref_canonical.toUpperCase()
                linkedByCanonical.set(key, {
                  producer_artifact_id: link.producer_artifact_id,
                  producer: session.artifacts[link.producer_artifact_id],
                })
              }

              // Build the LinkedSource list (one per unique canonical source used in mappings)
              const result: LinkedSource[] = []
              for (const [key, mapping] of Array.from(seenCanonical.entries())) {
                const dep = depByCanonical.get(key)
                const linked = linkedByCanonical.get(key)
                result.push({
                  sourceRef: mapping.source_ref_canonical,
                  sourceRefRaw: dep?.source_ref_raw || mapping.source_ref_canonical,
                  objectKind: dep?.object_kind || "calculation_view",
                  isLinked: Boolean(linked?.producer_artifact_id),
                  linkedArtifactId: linked?.producer_artifact_id ?? undefined,
                  linkedArtifactName: linked?.producer?.cv_display_name,
                })
              }

              // Sort alphabetically for stable display
              result.sort((a, b) => a.sourceRef.localeCompare(b.sourceRef))
              return result
            }

            const mainLinkedSources = buildLinkedSourcesFor(root.artifactId)

            // Flatten ALL linked producer artifacts (recursively) into base nodes.
            // Each linked artifact becomes its own base node in the flow chart with a
            // parentId pointing to its consumer, so an edge is drawn between them.
            type FlatBase = {
              id: string
              label: string
              isResolved: boolean
              sourceRef?: string
              linkedSources: LinkedSource[]
              parentId: string
            }
            const flatBases: FlatBase[] = []
            const seenArtifacts = new Set<string>()
            const walkArtifact = (consumerArtifactId: string, parentNodeId: string) => {
              if (!session) return
              const consumer = session.artifacts[consumerArtifactId]
              if (!consumer) return
              const links = session.dependency_links.filter(
                l => l.consumer_artifact_id === consumerArtifactId
                  && l.resolution === "uploaded_cv"
                  && l.producer_artifact_id
              )
              for (const link of links) {
                const producerId = link.producer_artifact_id as string
                if (seenArtifacts.has(producerId)) continue
                seenArtifacts.add(producerId)
                const producer = session.artifacts[producerId]
                if (!producer) continue
                flatBases.push({
                  id: producerId,
                  label: producer.cv_display_name || link.source_ref_canonical,
                  isResolved: true,
                  sourceRef: link.source_ref_canonical,
                  linkedSources: buildLinkedSourcesFor(producerId),
                  parentId: parentNodeId,
                })
                // Recurse so deeper nested CVs become their own nodes connected to this one
                walkArtifact(producerId, producerId)
              }
            }
            if (root.artifactId) {
              walkArtifact(root.artifactId, root.id)
            }

            const allBases = flatBases
            const handleMapping = (nodeId: string, kind: "Column Mapping" | "Table Mapping") => {
              // Find the artifact this node represents (either root artifact or a linked producer)
              const artifactId = nodeId === root.id || nodeId === "main-view"
                ? root.artifactId
                : (allBases.find(b => b.id === nodeId)?.id ?? nodeId)
              // Find or construct a TreeNode for this artifact
              let node: TreeNode | null = artifactId ? findNode([root], artifactId) : null
              if (!node && nodeId !== "main-view") {
                node = findNode([root], nodeId) || root.children.find(c => c.id === nodeId || c.producerArtifactId === nodeId || c.sourceRefCanonical === nodeId) || null
              }
              if (!node && artifactId) {
                // Synthesize a TreeNode from the session artifact
                const artifact = session?.artifacts[artifactId]
                if (artifact) {
                  node = {
                    id: artifactId,
                    name: artifact.cv_display_name,
                    kind: "calculation_view",
                    nodeType: "artifact",
                    artifactId,
                    ownerArtifactId: artifactId,
                    sqlContent: combinedSql(artifact),
                    columns: (artifact.output_schema || []).map(c => c.column_name),
                    mappings: session?.global_mappings.filter(m => m.artifact_id === artifactId) || [],
                    children: [],
                    parentId: null,
                    resolved: true,
                  }
                }
              }
              if (!node) {
                setError(`Cannot find mapping for node. Refresh the session and try again.`)
                return
              }
              setSelectedNodeId(node.id)
              if (kind === "Column Mapping") openMappingEditor(node)
              else openTableMappingEditor(node)
            }
            const handleRemove = async (nodeId: string) => {
              // Find the artifact id for this node
              const base = allBases.find(b => b.id === nodeId)
              const artifactId = base?.id || (nodeId !== "main-view" ? nodeId : null)
              if (!artifactId) return
              const label = base?.label || nodeId
              if (!window.confirm(`Remove ${label} and its nested links?`)) return
              setLoading(true)
              try {
                const res = await nestedDeleteCv(session.session_id, artifactId)
                if (!res.success) throw new Error(res.error || "Failed to remove CV")
                invalidateResult()
                await refreshSession(session.session_id)
              } catch (caught) {
                setError(caught instanceof Error ? caught.message : "Failed to remove CV")
              } finally {
                setLoading(false)
              }
            }
            const handleResolveBase = (nodeId: string) => {
              // For unresolved bases we don't currently create them in flatBases,
              // so this is a no-op for already-resolved CVs from the toggle flow.
              const child = root.children.find(c => c.id === nodeId || c.producerArtifactId === nodeId)
              if (child) openNested(child)
            }
            // Handle the Nested CV Linkage toggle:
            // - ON: opens NestedDependencyModal to upload/link another CV
            // - OFF: removes the linkage (delegated to removeNode)
            const handleToggleLink = (nodeId: string, sourceRef: string, enabled: boolean) => {
              if (enabled) {
                // Resolve the node from nodeId and open the upload modal
                let node: TreeNode | null = null
                if (nodeId === root.id || nodeId === "main-view") {
                  node = root
                } else {
                  node = findNode([root], nodeId)
                }
                if (!node) {
                  setError(`Cannot find node to link. Refresh the session and try again.`)
                  return
                }
                // For unresolved source nodes, we use the consumerArtifactId (root)
                // For resolved nodes, we use the producer artifact
                const artifactId = node.nodeType === "artifact" ? node.artifactId : node.producerArtifactId || node.ownerArtifactId
                if (!artifactId) {
                  setError(`Cannot link: no artifact found for this node.`)
                  return
                }
                setPendingNestedParent({
                  nodeId,
                  consumerArtifactId: artifactId,
                  sourceRef,
                  nodeName: sourceRef,
                })
                setShowNestedModal(true)
              } else {
                // Unlink: find any existing linked artifact for this source and remove it
                let node: TreeNode | null = null
                if (nodeId === root.id || nodeId === "main-view") {
                  node = root
                } else {
                  node = findNode([root], nodeId)
                }
                if (!node) return
                const consumerId = node.nodeType === "artifact" ? node.artifactId : node.ownerArtifactId
                const link = session.dependency_links.find(
                  l => l.consumer_artifact_id === consumerId
                    && l.source_ref_canonical.toUpperCase() === sourceRef.toUpperCase()
                    && l.producer_artifact_id
                )
                if (link?.producer_artifact_id) {
                  if (window.confirm(`Unlink the nested CV for ${sourceRef}?`)) {
                    nestedDeleteCv(session.session_id, link.producer_artifact_id)
                      .then(response => {
                        if (!response.success) {
                          setError(response.error || "Failed to unlink nested CV")
                          return
                        }
                        invalidateResult()
                        return refreshSession(session.session_id)
                      })
                      .catch(caught => {
                        setError(caught instanceof Error ? caught.message : "Failed to unlink nested CV")
                      })
                  }
                }
              }
            }
            return (
              <FlowBuilder
                mainLabel={root.name || "Calculation Main View name"}
                mainBackendId={root.artifactId}
                mainLinkedSources={mainLinkedSources}
                bases={allBases}
                onMappingClick={handleMapping}
                onRemoveBase={handleRemove}
                onAddBase={openRootUpload}
                onResolveBase={handleResolveBase}
                onToggleLink={handleToggleLink}
              />
            )
          })()}
        </div>
      )}

      {session && tree.length === 0 && <div className="text-center py-12 border-2 border-dashed border-gray-200 rounded-xl"><FileSpreadsheet className="w-12 h-12 mx-auto mb-3 text-gray-300" /><p className="text-gray-500 mb-4">No CVs added yet</p><button onClick={openRootUpload} className="inline-flex items-center gap-1.5 px-4 py-2 text-sm bg-secondary text-primary rounded-lg"><Plus className="w-4 h-4" /> Add First CV or Choose History</button></div>}

      {session && tree.length > 0 && !resultContent && <button onClick={handleGenerate} disabled={generating || loading} className="w-full py-3 bg-secondary text-primary rounded-lg font-semibold flex items-center justify-center gap-2 disabled:opacity-60">{generating ? <><Loader2 className="w-4 h-4 animate-spin" /> {taskMessage || "Generating…"} {taskProgress}%</> : <><GitMerge className="w-4 h-4" /> Generate Flat {session.output_format === "pyspark" ? "PySpark" : "SQL"}</>}</button>}

      {resultContent && (
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          className={cn("mt-6 flex flex-col", isFullScreen && "fixed inset-0 z-50 bg-white p-6")}
        >
          <h2 className="text-xl font-semibold text-primary mb-2">Generated Output</h2>
          <p className="text-gray-600 mb-4 text-sm">Your flattened calculation view is ready. Review the code below and download when satisfied.</p>
          <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2 mb-3">
            <Pencil
              className="h-4 w-4 text-gray-500 hidden sm:block"
              onClick={() => setIsFileNameEditable(!isFileNameEditable)}
            />
            <input
              type="text"
              value={isFileNameEditable
                ? editableFileName.replace(/\.(sql|ipynb)$/i, "")
                : (resultFileName?.replace(/\.(sql|ipynb)$/i, "") ?? editableFileName.replace(/\.(sql|ipynb)$/i, ""))}
              onChange={e => setEditableFileName(`${e.target.value}.${session?.output_format === "pyspark" ? "ipynb" : "sql"}`)}
              readOnly={!isFileNameEditable}
              onBlur={() => {
                if (isFileNameEditable && editableFileName) {
                  setResultFileName(editableFileName)
                }
                setIsFileNameEditable(false)
              }}
              className="flex-grow px-3 py-2 text-sm border border-gray-300 rounded-md bg-white text-gray-800 focus:outline-none focus:ring-2 focus:ring-secondary"
              aria-label="Output file name"
            />
            <button
              onClick={() => setIsFileNameEditable(!isFileNameEditable)}
              className="px-3 py-2 text-sm font-medium text-gray-700 bg-gray-100 rounded-md hover:bg-gray-200 transition-colors"
              title={isFileNameEditable ? "Save file name" : "Rename file"}
            >
              {isFileNameEditable ? <Save className="h-4 w-4" /> : <Pencil className="h-4 w-4" />}
            </button>
            <button
              onClick={() => setIsEditorCollapsed(!isEditorCollapsed)}
              className="ml-0 sm:ml-2 p-2 rounded-md bg-gray-100 hover:bg-gray-200 transition-colors"
              title={isEditorCollapsed ? "Expand editor" : "Collapse editor"}
              aria-label={isEditorCollapsed ? "Expand editor" : "Collapse editor"}
            >
              {isEditorCollapsed ? (
                <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="lucide lucide-chevrons-down"><path d="m7 6 5 5 5-5" /><path d="m7 13 5 5 5-5" /></svg>
              ) : (
                <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="lucide lucide-chevrons-up"><path d="m17 11-5-5-5 5" /><path d="m17 18-5-5-5 5" /></svg>
              )}
            </button>
            <button
              onClick={() => setIsFullScreen(!isFullScreen)}
              className="p-2 rounded-md bg-gray-100 hover:bg-gray-200 transition-colors"
              title={isFullScreen ? "Exit fullscreen" : "Enter fullscreen"}
              aria-label={isFullScreen ? "Exit fullscreen" : "Enter fullscreen"}
            >
              {isFullScreen ? (
                <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="lucide lucide-minimize"><path d="M8 3v3a2 2 0 0 1-2 2H3m18 0h-3a2 2 0 0 1-2-2V3m0 18v-3a2 2 0 0 1 2-2h3m-18 0h3a2 2 0 0 1 2 2v3" /></svg>
              ) : (
                <svg xmlns="http://www.w3.org/2000/svg" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className="lucide lucide-maximize"><path d="M8 3H5a2 2 0 0 0-2 2v3m18 0V5a2 2 0 0 0-2-2h-3m0 18h3a2 2 0 0 0 2-2v-3m-18 0v3a2 2 0 0 0 2 2h3" /></svg>
              )}
            </button>
          </div>
          <div className={cn("w-full mb-4 border rounded-md overflow-hidden", isFullScreen ? "flex-grow overflow-y-auto" : isEditorCollapsed ? "h-20" : "h-[400px]")}>
            {session?.output_format === "pyspark" ? (
              <div className={cn("w-full overflow-y-auto bg-gray-50 dark:bg-gray-800", isFullScreen ? "h-full" : isEditorCollapsed ? "h-20" : "h-[400px]")}>
                {!isEditorCollapsed && (
                  <NotebookRenderer
                    content={resultContent || ""}
                    onChange={setResultContent}
                  />
                )}
              </div>
            ) : (
              <SqlEditor
                value={resultContent || ""}
                onChange={setResultContent}
                editorHeight={isFullScreen ? "100%" : isEditorCollapsed ? "20px" : "340px"}
                isCollapsed={isEditorCollapsed}
              />
            )}
          </div>
          <button
            onClick={() => taskId && nestedDownloadResult(taskId)}
            className="w-full py-3 sm:py-4 rounded-lg font-medium text-sm sm:text-base bg-secondary text-primary hover:bg-secondary/90 transition-colors flex-shrink-0 min-h-[48px] sm:min-h-[44px] flex items-center justify-center"
          >
            <Download className="w-4 h-4 mr-2" />
            {session?.output_format === "pyspark" ? "Download PySpark Notebook (.ipynb)" : "Download SQL File"}
          </button>
        </motion.div>
      )}

      {showNestedModal && pendingNestedParent && (() => {
        // Compute parent's required columns for this source ref so the modal
        // can auto-match them against the nested CV's output columns.
        let parentRequiredColumns: string[] = []
        if (pendingNestedParent.consumerArtifactId && session) {
          const parentArtifact = session.artifacts[pendingNestedParent.consumerArtifactId]
          if (parentArtifact) {
            const sourceUpper = pendingNestedParent.sourceRef.toUpperCase()
            const parentDep = parentArtifact.dependencies.find(
              d => d.source_ref_canonical.toUpperCase() === sourceUpper
            )
            if (parentDep) {
              const raw = parentDep.required_columns_json
              if (Array.isArray(raw)) parentRequiredColumns = raw.map(String)
              else if (typeof raw === "string") {
                try {
                  const arr = JSON.parse(raw || "[]")
                  if (Array.isArray(arr)) parentRequiredColumns = arr.map(String)
                } catch { /* ignore */ }
              }
            }
          }
        }
        return (
          <NestedDependencyModal
            isOpen={showNestedModal}
            onClose={() => setShowNestedModal(false)}
            mode={pendingNestedParent.consumerArtifactId ? "nested" : "root"}
            parentRef={pendingNestedParent.sourceRef}
            parentName={pendingNestedParent.nodeName}
            parentRequiredColumns={parentRequiredColumns}
            onConfirm={handleNestedConfirm}
            onUploadXlsx={async file => {
              if (!session) return { success: false, error: "No session" }
              const response = await nestedAddCvFromXlsx(session.session_id, file, undefined, undefined, true)
              return {
                success: response.success,
                sql_info: response.sql_info || [],
                source_tables: response.source_tables || [],
                output_columns: response.output_columns || [],
                last_chunk_sql: response.last_chunk_sql || "",
                last_chunk_sources: response.last_chunk_sources || [],
                error: response.error,
              }
            }}
            isLoading={loading}
          />
        )
      })()}

      {showMappingEditor && <MappingEditorPopup
        key={mappingNodeId || "mapping-editor"}
        isOpen={showMappingEditor}
        onClose={() => setShowMappingEditor(false)}
        platformName={session?.target_dialect || targetDialect || "bigquery"}
        sqlContent={mappingSql}
        zipContents={{ sqlFiles: {}, mappingFileContent: mappingRows, textFileName: mappingFileName }}
        onSave={rows => saveMappings(rows)}
      />}

      {showTableMappingModal && (() => {
        const node = mappingNodeId ? findNode(tree, mappingNodeId) : null
        return (
          <TableMappingModal
            key={mappingNodeId || "table-mapping-modal"}
            isOpen={showTableMappingModal}
            onClose={() => setShowTableMappingModal(false)}
            platformName={session?.target_dialect || targetDialect || "bigquery"}
            nodeName={node?.name || "Unknown"}
            entries={tableMappingEntries}
            onSave={rows => saveTableMappings(rows)}
          />
        )
      })()}

      {columnMappingContext && (
        <NestedColumnMappingModal
          key={`column-mapping-${columnMappingContext.artifactId}-${columnMappingContext.sourceRef}`}
          isOpen={Boolean(columnMappingContext)}
          onClose={() => setColumnMappingContext(null)}
          parentArtifactName={columnMappingContext.parentName}
          sourceRef={columnMappingContext.sourceRef}
          parentRequiredColumns={columnMappingContext.parentRequiredColumns}
          nestedOutputColumns={columnMappingContext.nestedOutputColumns}
          existingMappings={(session?.global_mappings || []).filter(
            m => m.artifact_id === columnMappingContext.consumerArtifactId
              && (m.source_ref_canonical || "").toUpperCase() === columnMappingContext.sourceRef.toUpperCase()
          )}
          artifactId={columnMappingContext.consumerArtifactId}
          isSaving={columnMappingSaving}
          onSave={saveColumnMappings}
          onSkip={() => setColumnMappingContext(null)}
        />
      )}
    </div>
  )
}
