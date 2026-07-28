"use client"

import { useCallback, useEffect, useMemo, useRef, useState } from "react"
import dynamic from "next/dynamic"
import Image from "next/image"
import Link from "next/link"
import { motion } from "framer-motion"
import {
  AlertCircle, ArrowRight, Check, CheckCircle, ChevronDown, ChevronRight, ChevronsDown, ChevronsUp,
  Download, FileSpreadsheet, GitMerge, Lightbulb, Loader2, Maximize2, Minimize2, Pencil,
  Plus, RotateCcw, Save, X,
} from "lucide-react"
import { cn } from "@/lib/utils"
import MappingEditorPopup from "@/components/MappingEditorPopup"
import SqlEditor from "@/components/CodeEditor" // Same SqlEditor used by MappingTool
import NotebookRenderer from "@/components/NotebookRenderer"
import { Checkbox } from "@/components/ui/checkbox"
import {
  nestedAddCvFromXlsx, nestedCancelTask, nestedCreateSession, nestedDeleteCv, nestedDeleteSession,
  nestedDownloadResult, nestedGenerate, nestedGetSession, nestedGetTaskStatus,
  nestedUpdateCv, nestedUpdateMappings, nestedValidate,
} from "@/lib/api"
import type {
  CvArtifact, MappingEntry, NestedSession, ObjectKind, OutputFormat, SourceReference,
} from "@/lib/nested-cv-types"
import NestedDependencyModal from "./NestedDependencyModal"
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

/**
 * Build the LinkedSource list for a FlowBuilder node.
 *
 * For the main/standalone artifact, mappings live under its own artifact_id,
 * so we filter `global_mappings` by that id.
 *
 * For a nested CV base, mappings are stored under the PARENT's artifact_id
 * (because the mapping describes how the parent consumes this child source)
 * and are scoped by `source_ref_canonical`. Pass `parentArtifactId` and
 * `parentSourceRef` so we can look them up correctly. The nested CV's own
 * `dependencies` list is still used to enrich the row with `objectKind` and
 * `sourceRefRaw`.
 */
function buildLinkedSourcesFor(
  session: NestedSession,
  artifactId: string | undefined,
  parentArtifactId?: string,
  parentSourceRef?: string,
): LinkedSource[] {
  if (!artifactId) return []
  const artifact = session.artifacts[artifactId]
  if (!artifact) return []

  const useParentLookup = Boolean(parentArtifactId && parentSourceRef)
  const mappingOwnerId = useParentLookup ? (parentArtifactId as string) : artifactId
  const sourceFilter = parentSourceRef ? parentSourceRef.toUpperCase() : null

  const artifactMappings = sourceFilter
    ? session.global_mappings.filter(
        m => m.artifact_id === mappingOwnerId
          && m.source_ref_canonical.toUpperCase() === sourceFilter,
      )
    : session.global_mappings.filter(m => m.artifact_id === mappingOwnerId)

  // Deduplicate by canonical source, keeping the first mapping per source.
  const seenCanonical = new Map<string, MappingEntry>()
  for (const mapping of artifactMappings) {
    const key = mapping.source_ref_canonical.toUpperCase()
    if (!seenCanonical.has(key)) {
      seenCanonical.set(key, mapping)
    }
  }

  // The dependency metadata for objectKind/raw names comes from the artifact
  // whose output these sources feed into (the nested CV itself).
  const depByCanonical = new Map<string, SourceReference>()
  for (const dep of artifact.dependencies || []) {
    depByCanonical.set(dep.source_ref_canonical.toUpperCase(), dep)
  }

  // Determine which sources are already linked (resolved) via dependency_links.
  const linkedByCanonical = new Map<string, { producer_artifact_id: string; producer: CvArtifact | undefined }>()
  for (const link of session.dependency_links) {
    const linkConsumer = useParentLookup ? parentArtifactId : artifactId
    if (!linkConsumer) continue
    if (link.consumer_artifact_id !== linkConsumer) continue
    if (link.resolution !== "uploaded_cv" || !link.producer_artifact_id) continue
    const key = link.source_ref_canonical.toUpperCase()
    linkedByCanonical.set(key, {
      producer_artifact_id: link.producer_artifact_id,
      producer: session.artifacts[link.producer_artifact_id],
    })
  }

  const result: LinkedSource[] = []
  for (const [key, mapping] of Array.from(seenCanonical.entries())) {
    const dep = depByCanonical.get(key)
    const linked = linkedByCanonical.get(key)
    if (!dep && typeof console !== "undefined") {
      // Mapping exists for a source_ref_canonical that isn't in this
      // artifact's declared dependencies — surface this for diagnostics
      // instead of silently rendering it as a calculation_view.
      console.warn(
        `buildLinkedSourcesFor: source ${mapping.source_ref_canonical} has mappings ` +
        `but no matching dependency on artifact ${artifactId}`,
      )
    }
    result.push({
      sourceRef: mapping.source_ref_canonical,
      sourceRefRaw: dep?.source_ref_raw || mapping.source_ref_canonical,
      objectKind: dep?.object_kind || "calculation_view",
      isLinked: Boolean(linked?.producer_artifact_id),
      linkedArtifactId: linked?.producer_artifact_id ?? undefined,
      linkedArtifactName: linked?.producer?.cv_display_name,
    })
  }

  result.sort((a, b) => a.sourceRef.localeCompare(b.sourceRef))
  return result
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
  // Per-generation cancellation. The ref holds the id of the current poll
  // loop. When we want to cancel, we bump the ref; the in-flight loop checks
  // it on each iteration and bails out cleanly. Distinct from "invalidate
  // result" which also bumps the ref — but here we keep the semantics that
  // ANY bump cancels any active generation.
  const pollGenerationRef = useRef(0)
  const [isEditorCollapsed, setIsEditorCollapsed] = useState(false)
  const [isFullScreen, setIsFullScreen] = useState(false)
  // Single source of truth for the rename UI:
  //   - renameDraft: the in-flight text while the user is editing
  //   - renameEditing: whether the input is currently editable
  // When not editing, the display reads from `resultFileName`.
  const [renameDraft, setRenameDraft] = useState("")
  const [renameEditing, setRenameEditing] = useState(false)

  const tree = useMemo(() => session ? buildTree(session) : [], [session])
  const selectedNode = useMemo(() => findNode(tree, selectedNodeId), [tree, selectedNodeId])

  // Stable callbacks for TreeRow so memoization on its props is meaningful.
  // Each handler is recreated only when its underlying state actually changes.
  const toggleExpand = useCallback((id: string) => {
    setExpandedIds(previous => {
      const next = new Set(previous)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }, [])

  const openNested = useCallback((node: TreeNode) => {
    if (node.nodeType !== "source" || !node.sourceRefCanonical || node.resolved) return
    setPendingNestedParent({
      nodeId: node.id,
      consumerArtifactId: node.ownerArtifactId,
      sourceRef: node.sourceRefCanonical,
      nodeName: node.name,
    })
    setShowNestedModal(true)
  }, [])

  const openMappingEditor = useCallback((node: TreeNode) => {
    let rows = node.mappings.map(mapping => ({
      sourceTable: mapping.source_ref_canonical,
      sourceField: mapping.source_column_raw,
      targetTable: mapping.target_table,
      targetField: mapping.target_column,
    }))
    // Fallback: if join/global mappings are empty, fall back to the
    // artifact's own `mapping_rows` from its uploaded Excel. Without
    // this, nested CVs whose parent didn't declare required columns show
    // "no mappings" even though the artifact itself has mapping info.
    if (!rows.length && node.artifactId && session) {
      const artifact = session.artifacts[node.artifactId]
      const ownRows = (artifact?.mapping_rows || []).map(m => ({
        sourceTable: m.source_ref_canonical,
        sourceField: m.source_column_raw,
        targetTable: m.target_table,
        targetField: m.target_column,
      }))
      if (ownRows.length) rows = ownRows
    }
    if (!rows.length) {
      setError(`No column mappings are available for ${node.name}. Use Adjust Mappings after mappings are saved.`)
      return
    }
    // Clear any prior "no mappings" error from earlier attempts.
    setError("")
    setMappingNodeId(node.id)
    setMappingRows(rows)
    setMappingSql(node.sqlContent)
    // Compose a context-rich fileName so the editor popup shows the
    // source CV, target CV, and (if applicable) the nested-link source
    // this mapping belongs to. e.g.
    //   "SalesCV → NestedCV1 (linked via ORDERS) — columns"
    const parentSource = node.nodeType === "source" ? node.sourceRefCanonical : null
    const headerParts = [node.name]
    if (parentSource) headerParts.push(`linked via ${parentSource}`)
    setMappingFileName(`${headerParts.join(" (")}${parentSource ? ")" : ""} — column mappings`)
    setShowMappingEditor(true)
  }, [])

  // Memoize the main view's linked sources so the FlowBuilder's useEffect
  // (which depends on mainLinkedSources) doesn't re-fire on unrelated parent
  // renders. Recomputed only when the root artifact or the session's mapping/
  // link state changes.
  const mainLinkedSources = useMemo(() => {
    if (!session || tree.length === 0) return []
    const root = tree[0]
    if (!root?.artifactId) return []
    return buildLinkedSourcesFor(session, root.artifactId)
    // tree is itself memoized from session, so [session, tree] is enough.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session, tree])

  // Reset local edit state ONLY when the selected node changes. We deliberately
  // exclude `localColumns`/`descriptions` from the trigger so that updating them
  // (e.g., after saving) doesn't clobber edits the user is still making on the
  // same node. The previously-cached edit values are already in sync with what
  // was persisted, so resetting on save is a no-op visually.
  const lastSelectedIdRef = useRef<string | null>(null)
  useEffect(() => {
    const currentId = selectedNode?.id ?? null
    if (currentId === lastSelectedIdRef.current) return
    lastSelectedIdRef.current = currentId
    if (!selectedNode) return
    setEditName(selectedNode.name)
    setEditColumns(localColumns[selectedNode.id] || selectedNode.columns)
    setEditDescription(descriptions[selectedNode.id] || "")
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedNode])

  useEffect(() => () => { pollGenerationRef.current += 1 }, [])

  // Scroll to the tools section when a new session is created so the user
  // sees the new workspace instead of being stranded mid-page on the
  // "Start Nested CV Session" button. We track the previous session value
  // with a ref so we only scroll on the null → session transition (i.e. when
  // a session is freshly created), not on every re-render.
  const previousSessionRef = useRef<NestedSession | null>(null)
  useEffect(() => {
    const previous = previousSessionRef.current
    previousSessionRef.current = session
    if (!previous && session) {
      // Defer to next frame so the DOM has the new content before we measure.
      requestAnimationFrame(() => {
        const toolsSection = document.getElementById("tools-section")
        if (toolsSection) {
          const headerHeight = 80
          const targetY = toolsSection.offsetTop - headerHeight
          window.scrollTo({ top: targetY, behavior: "instant" })
        } else {
          window.scrollTo({ top: 0, behavior: "instant" })
        }
      })
    }
  }, [session])

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
          // Prefer the freshest session from the upload response so we don't
          // operate on a stale `session` snapshot captured before setSession ran.
          const currentSession = updatedSession ?? session
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

      // Column mappings are already configured and saved inside NestedDependencyModal
      // (the user mapped parent required columns → nested CV output columns before
      // confirming the upload). No need to open another mapping modal here — that was
      // previously causing the same mapping screen to appear twice.
      // The user can still re-edit mappings later via FlowBuilder → Adjust Mappings.
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Failed to add nested CV")
    } finally {
      setLoading(false)
    }
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
    // mappingNodeId can be either a tree node id (for nested TREE source nodes)
    // or a producer artifact id (for nested CV base nodes in the FlowBuilder).
    let treeNode = findNode(tree, mappingNodeId)
    let ownerArtifactId: string | undefined
    let scopedSource: string | null = null
    if (treeNode) {
      ownerArtifactId = treeNode.ownerArtifactId
      scopedSource = treeNode.nodeType === "source" ? treeNode.sourceRefCanonical?.toUpperCase() ?? null : null
    } else if (session.artifacts[mappingNodeId]) {
      // mappingNodeId is the producer artifact ID; mappings live under the parent.
      const producer = session.artifacts[mappingNodeId]
      const link = session.dependency_links.find(l => l.producer_artifact_id === mappingNodeId)
      ownerArtifactId = link?.consumer_artifact_id
      scopedSource = (link?.source_ref_canonical || "").toUpperCase() || null
      // Use the producer's own output_schema as the columns list for the editor context
      if (producer) {
        treeNode = {
          id: mappingNodeId,
          name: producer.cv_display_name,
          kind: "calculation_view",
          nodeType: "artifact",
          artifactId: mappingNodeId,
          ownerArtifactId: ownerArtifactId || mappingNodeId,
          sqlContent: combinedSql(producer),
          columns: (producer.output_schema || []).map(c => c.column_name),
          mappings: [],
          children: [],
          parentId: null,
          resolved: true,
        }
      }
    }
    if (!ownerArtifactId || !treeNode) return
    const node = treeNode
    setLoading(true)
    setError("")
    try {
      // Build a lookup of table-level target renames
      const tableTargetMap = new Map<string, string>()
      for (const entry of entries) {
        tableTargetMap.set(entry.sourceTable, entry.targetTable)
      }

      // Keep mappings that this save does NOT replace:
      //   - mappings on other artifacts: untouched
      //   - mappings on this artifact but for a different source_ref: untouched
      //   - mappings on this artifact AND on the scoped source: REPLACED by the new ones
      // When scopedSource is null (artifact-level edit), nothing is in-scope, so keep
      // everything for this artifact and let the new rows be appended.
      const unrelated = session.global_mappings.filter(mapping => {
        if (mapping.artifact_id !== ownerArtifactId) return true
        if (!scopedSource) return true
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
    // mappingNodeId can be either a tree node id (for nested TREE source nodes)
    // or a producer artifact id (for nested CV base nodes from the FlowBuilder).
    let treeNode = findNode(tree, mappingNodeId)
    let ownerArtifactId: string | undefined
    let scopedSource: string | null = null
    if (treeNode) {
      ownerArtifactId = treeNode.ownerArtifactId
      scopedSource = treeNode.nodeType === "source" ? treeNode.sourceRefCanonical?.toUpperCase() ?? null : null
    } else if (session.artifacts[mappingNodeId]) {
      const link = session.dependency_links.find(l => l.producer_artifact_id === mappingNodeId)
      ownerArtifactId = link?.consumer_artifact_id
      scopedSource = (link?.source_ref_canonical || "").toUpperCase() || null
    }
    if (!ownerArtifactId) return
    setLoading(true)
    setError("")
    try {
      const unrelated = session.global_mappings.filter(mapping => {
        if (mapping.artifact_id !== ownerArtifactId) return true
        if (!scopedSource) return true
        return mapping.source_ref_canonical.toUpperCase() !== scopedSource
      })
      const updated: MappingEntry[] = rows.map(row => ({
        source_ref_canonical: row.sourceTable,
        source_column_raw: row.sourceField,
        target_table: row.targetTable,
        target_column: row.targetField,
        artifact_id: ownerArtifactId,
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
      // Clear detail panel if the removed node (or its artifact) was selected.
      // Tree source-node ids look like `source:<artifactId>:<sourceRef>`, so we
      // also strip-check on artifactId for any selected child.
      if (selectedNodeId) {
        const isRemoved = selectedNodeId === node.id
          || selectedNodeId === artifactId
          || selectedNodeId.startsWith(`source:${artifactId}:`)
        if (isRemoved) setSelectedNodeId(null)
      }
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
    // Bump the cancellation token BEFORE any await so any prior in-flight
    // poll loop (started by an earlier generation) will exit on its next
    // iteration. We capture this id locally and check it on every poll.
    const generation = ++pollGenerationRef.current
    try {
      // Pull the freshest session from the server before validating. If the
      // user has unsaved mapping edits at this point, they will see a
      // validation error rather than silently generating against stale state.
      await refreshSession(session.session_id)
      if (pollGenerationRef.current !== generation) return
      const validation = await nestedValidate(session.session_id)
      // Re-check cancellation after each await — the user may have hit reset
      // or started another generate while validation was in flight.
      if (pollGenerationRef.current !== generation) return
      if (!validation.success || !validation.valid) {
        const messages = validation.errors.map(item => item.message)
        setGenErrors(messages)
        throw new Error(messages[0] || "Resolve validation errors before generating")
      }
      const response = await nestedGenerate(session.session_id)
      if (pollGenerationRef.current !== generation) return
      if (!response.success || !response.task_id) throw new Error(response.error || "No task ID returned")
      setTaskId(response.task_id)
      for (let attempt = 0; attempt < 60 && pollGenerationRef.current === generation; attempt += 1) {
        await new Promise(resolve => setTimeout(resolve, 2000))
        if (pollGenerationRef.current !== generation) return
        const status = await nestedGetTaskStatus(response.task_id)
        if (pollGenerationRef.current !== generation) return
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
      // Don't surface a cancellation triggered by reset/invalidate as an error.
      if (pollGenerationRef.current === generation) {
        setError(caught instanceof Error ? caught.message : "Generation failed")
      }
    } finally {
      if (pollGenerationRef.current === generation) {
        setGenerating(false)
      }
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
    // Wipe any leftover per-node state from the prior session so a fresh
    // session never inherits stale column lists, descriptions, rename edits,
    // mapping editor content, or pending modal payloads.
    setLocalColumns({})
    setDescriptions({})
    setEditName("")
    setEditColumns([])
    setEditDescription("")
    setPendingNestedParent(null)
    setShowNestedModal(false)
    setMappingNodeId(null)
    setMappingRows([])
    setMappingSql("")
    setMappingFileName("")
    setShowMappingEditor(false)
    setTableMappingEntries([])
    setShowTableMappingModal(false)
    setResultContent(null)
    setResultFileName(null)
    setTaskId(null)
    setTaskProgress(0)
    setTaskMessage("")
    setGenErrors([])
    setRenameDraft("")
    setRenameEditing(false)
    setIsEditorCollapsed(false)
    setIsFullScreen(false)
    lastSelectedIdRef.current = null
  }

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

          {/* Screen-reader live regions: announce platform/format selection,
              validation errors, and generation progress so AT users get the
              same feedback as sighted users. */}
          <div className="sr-only" aria-live="polite">
            {targetDialect ? `Selected platform ${DATABASE_PLATFORMS.find(platform => platform.id === targetDialect)?.name}; selected format ${outputFormat}.` : "No target platform selected."}
          </div>
          <div className="sr-only" aria-live="assertive">
            {generating
              ? `Generation in progress: ${taskMessage || "starting"}. ${taskProgress}% complete.`
              : resultContent
                ? "Generation complete. Output is ready to review."
                : ""}
          </div>
          <div className="sr-only" aria-live="assertive">
            {error ? `Error: ${error}` : ""}
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

            // mainLinkedSources is memoized at the top of NestedCVTool — reuse it
            // here rather than recomputing on every render.

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
              mappingCount?: number
              depth?: number
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
                // Count column-mapping rows belonging to this producer's
                // own mapping info (i.e., the rows that this nested CV's
                // mapping info sheet would contribute when displayed).
                const producerMappingCount = (producer.mapping_rows || []).length
                flatBases.push({
                  id: producerId,
                  label: producer.cv_display_name || link.source_ref_canonical,
                  isResolved: true,
                  sourceRef: link.source_ref_canonical,
                  // Nested CV base: mappings are stored under the parent's artifact_id,
                  // scoped by source_ref_canonical. Pass both so the helper can find them.
                  linkedSources: buildLinkedSourcesFor(
                    session,
                    producerId,
                    link.consumer_artifact_id,
                    link.source_ref_canonical,
                  ),
                  parentId: parentNodeId,
                  mappingCount: producerMappingCount,
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
              // Find the artifact this node represents. Two cases:
              //   1. Main view / root: nodeId == root.id or "main-view", use root.artifactId
              //   2. Nested CV base:   nodeId is the producer artifact ID. The mappings
              //      for this nested CV were stored under the PARENT's artifact_id
              //      (because mappings describe how the parent consumes this child source).
              //      We need to find the parent and load mappings from there.
              const isMain = nodeId === root.id || nodeId === "main-view"
              const baseMatch = isMain ? null : allBases.find(b => b.id === nodeId)
              // For a base, the producer artifact ID is nodeId itself (base.id === producerId).
              const artifactId = isMain ? root.artifactId : (baseMatch?.id ?? nodeId)

              // Build a TreeNode that resolves to the producer artifact so the user
              // sees the correct name and output columns. Mappings are NOT stored here
              // because they live under the parent's artifact_id (see below).
              let node: TreeNode | null = artifactId ? findNode([root], artifactId) : null
              if (!node && nodeId !== "main-view") {
                // Compare sourceRefCanonical case-insensitively since the
                // encoded id from sourceNodeId uses encodeURIComponent but the
                // raw sourceRef may differ in case/punctuation.
                const nodeIdLower = nodeId.toLowerCase()
                node = findNode([root], nodeId) || root.children.find(c =>
                  c.id === nodeId
                  || c.producerArtifactId === nodeId
                  || (c.sourceRefCanonical || "").toLowerCase() === nodeIdLower
                ) || null
              }
              if (!node && artifactId) {
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
                    mappings: [],
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

              // Resolve where the actual column mappings are stored.
              // For the main view: under root.artifactId (all mappings belong to the root CV).
              // For a nested CV base: under the PARENT's artifact_id, where source_ref_canonical
              //    matches the source_ref that this nested CV resolves.
              if (isMain) {
                if (artifactId) {
                  node.mappings = session?.global_mappings.filter(m => m.artifact_id === artifactId) || []
                }
              } else if (baseMatch) {
                // Find the parent's artifact ID
                const parentBase = baseMatch.parentId
                  ? allBases.find(b => b.id === baseMatch.parentId)
                  : null
                const parentArtifactId: string | undefined = parentBase?.id || root.artifactId
                if (parentArtifactId) {
                  const parentSourceRef = (baseMatch.sourceRef || "").toUpperCase()
                  node.ownerArtifactId = parentArtifactId
                  const allMappings = session?.global_mappings || []
                  // Join mappings: stored under parent's artifact_id with this source_ref.
                  // These describe how the parent source's columns link to the
                  // nested CV's output columns (the result of toggle→upload→join).
                  const joinMappings = allMappings.filter(m => {
                    if (m.artifact_id !== parentArtifactId) return false
                    return (m.source_ref_canonical || "").toUpperCase() === parentSourceRef
                  })
                  // Own column mappings: the nested CV's OWN mapping info rows
                  // (from the Excel it was uploaded with). These are stored under
                  // the nested CV's own artifact_id. Without this fallback the
                  // editor shows "no mappings" when the parent didn't declare
                  // required columns during upload.
                  const ownMappings = allMappings.filter(m => m.artifact_id === baseMatch.id)
                  node.mappings = [...joinMappings, ...ownMappings]
                }
              } else {
                // Fallback: search by artifact_id (defensive — should not normally happen)
                if (artifactId) {
                  node.mappings = session?.global_mappings.filter(m => m.artifact_id === artifactId) || []
                }
              }

              if (node.mappings.length === 0 && node.name && artifactId) {
                const producer = session?.artifacts[artifactId]
                if (producer) {
                  // 0 mappings is OK for a brand-new producer; surface a friendly message
                  // only when the producer has dependencies that SHOULD have mappings.
                  const hasDeps = (producer.dependencies || []).length > 0
                  if (hasDeps) {
                    setError(`No column mappings found for ${node.name}. Re-upload the nested CV and confirm the column mapping step.`)
                  }
                }
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
                // Clear selection if we just removed the artifact being viewed.
                if (selectedNodeId === nodeId
                  || selectedNodeId === artifactId
                  || (selectedNodeId?.startsWith(`source:${artifactId}:`) ?? false)) {
                  setSelectedNodeId(null)
                }
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
            const handleToggleLink = async (nodeId: string, sourceRef: string, enabled: boolean) => {
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
                // Compose a breadcrumb-style display name so the user can see
                // they're linking a child of <parent> when the toggle fires
                // on an already-resolved node (e.g. "SalesCV ← <sourceRef>").
                const displayName = node.nodeType === "artifact"
                  ? `${node.name} ← ${sourceRef}`
                  : sourceRef
                setPendingNestedParent({
                  nodeId,
                  consumerArtifactId: artifactId,
                  sourceRef,
                  nodeName: displayName,
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
                if (!link?.producer_artifact_id) return
                if (!window.confirm(`Unlink the nested CV for ${sourceRef}?`)) return
                setLoading(true)
                try {
                  const response = await nestedDeleteCv(session.session_id, link.producer_artifact_id)
                  if (!response.success) throw new Error(response.error || "Failed to unlink nested CV")
                  invalidateResult()
                  await refreshSession(session.session_id)
                } catch (caught) {
                  setError(caught instanceof Error ? caught.message : "Failed to unlink nested CV")
                } finally {
                  setLoading(false)
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

      {session && tree.length > 0 && !resultContent && (
        <div className="flex flex-col sm:flex-row gap-2">
          <button
            onClick={handleGenerate}
            disabled={generating || loading}
            className="flex-1 py-3 bg-secondary text-primary rounded-lg font-semibold flex items-center justify-center gap-2 disabled:opacity-60"
          >
            {generating ? <><Loader2 className="w-4 h-4 animate-spin" /> {taskMessage || "Generating…"} {taskProgress}%</> : <><GitMerge className="w-4 h-4" /> Generate Flat {session.output_format === "pyspark" ? "PySpark" : "SQL"}</>}
          </button>
          {generating && taskId && (
            <button
              onClick={async () => {
                // Bump the local poll ref so the in-flight loop bails out
                // immediately; the server-side cancel flag stops the worker.
                pollGenerationRef.current += 1
                setGenerating(false)
                setTaskMessage("Cancelling…")
                try {
                  await nestedCancelTask(taskId)
                } catch { /* non-fatal */ }
                setTaskId(null)
                setTaskProgress(0)
                setTaskMessage("")
              }}
              className="px-4 py-3 bg-white border border-gray-300 text-gray-700 rounded-lg font-medium flex items-center justify-center gap-2 hover:bg-gray-50"
              title="Cancel generation"
            >
              <X className="w-4 h-4" /> Cancel
            </button>
          )}
        </div>
      )}

      {resultContent && (
        <motion.div
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          // Fullscreen z-index sits above the modals (z-70/80/100) so the
          // code editor remains visible even when a mapping modal is opened
          // over it. We also opt-out of fullscreen if any modal is open so
          // the user can interact with the modal normally.
          className={cn(
            "mt-6 flex flex-col",
            isFullScreen && !showNestedModal && !showMappingEditor && !showTableMappingModal
              && "fixed inset-0 z-[60] bg-white p-6",
          )}
        >
          <h2 className="text-xl font-semibold text-primary mb-2">Generated Output</h2>
          <p className="text-gray-600 mb-4 text-sm">Your flattened calculation view is ready. Review the code below and download when satisfied.</p>
          <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-2 mb-3">
            <input
              type="text"
              value={renameEditing
                ? renameDraft.replace(/\.(sql|ipynb)$/i, "")
                : (resultFileName?.replace(/\.(sql|ipynb)$/i, "") ?? "")}
              onChange={e => setRenameDraft(`${e.target.value}.${session?.output_format === "pyspark" ? "ipynb" : "sql"}`)}
              readOnly={!renameEditing}
              onFocus={() => {
                // When the user enters edit mode, seed the draft from the
                // current saved filename so they see the value they're editing.
                if (!renameEditing && resultFileName) {
                  setRenameDraft(resultFileName)
                }
                setRenameEditing(true)
              }}
              onBlur={() => {
                if (renameEditing && renameDraft) {
                  setResultFileName(renameDraft)
                }
                setRenameEditing(false)
              }}
              className="flex-grow px-3 py-2 text-sm border border-gray-300 rounded-md bg-white text-gray-800 focus:outline-none focus:ring-2 focus:ring-secondary"
              aria-label="Output file name"
            />
            <button
              onClick={() => {
                if (!renameEditing && resultFileName) setRenameDraft(resultFileName)
                setRenameEditing(!renameEditing)
              }}
              className="px-3 py-2 text-sm font-medium text-gray-700 bg-gray-100 rounded-md hover:bg-gray-200 transition-colors"
              title={renameEditing ? "Save file name" : "Rename file"}
            >
              {renameEditing ? <Save className="h-4 w-4" /> : <Pencil className="h-4 w-4" />}
            </button>
            <button
              onClick={() => setIsEditorCollapsed(!isEditorCollapsed)}
              className="ml-0 sm:ml-2 p-2 rounded-md bg-gray-100 hover:bg-gray-200 transition-colors"
              title={isEditorCollapsed ? "Expand editor" : "Collapse editor"}
              aria-label={isEditorCollapsed ? "Expand editor" : "Collapse editor"}
            >
              {isEditorCollapsed ? <ChevronsDown className="h-5 w-5" /> : <ChevronsUp className="h-5 w-5" />}
            </button>
            <button
              onClick={() => setIsFullScreen(!isFullScreen)}
              className="p-2 rounded-md bg-gray-100 hover:bg-gray-200 transition-colors"
              title={isFullScreen ? "Exit fullscreen" : "Enter fullscreen"}
              aria-label={isFullScreen ? "Exit fullscreen" : "Enter fullscreen"}
            >
              {isFullScreen ? <Minimize2 className="h-5 w-5" /> : <Maximize2 className="h-5 w-5" />}
            </button>
          </div>
          <div className={cn("w-full mb-4 border rounded-md overflow-hidden", isFullScreen ? "flex-grow overflow-y-auto" : isEditorCollapsed ? "h-20" : "h-[400px]")}>
            {session?.output_format === "pyspark" ? (
              <div className={cn(
                "w-full overflow-y-auto bg-gray-50 dark:bg-gray-800",
                isFullScreen ? "h-full" : isEditorCollapsed ? "h-20" : "h-[400px]",
              )}>
                {/* Render NotebookRenderer always but hide its content via the
                    wrapper height when collapsed, matching SqlEditor's
                    behavior. This avoids the visual flicker and content loss
                    of unmounting the renderer on every collapse toggle. */}
                <div className={isEditorCollapsed ? "hidden" : "block"}>
                  <NotebookRenderer
                    content={resultContent || ""}
                    onChange={setResultContent}
                  />
                </div>
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
            onClick={() => taskId && nestedDownloadResult(taskId, resultFileName ?? undefined)}
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
        //
        // Primary source: the parent dependency's required_columns_json. This is
        // populated server-side from the workbook's SourceTable_mapping_fields
        // column (a stringified dict) and is the most explicit declaration.
        //
        // Fallback: derive from the parent's own mapping_rows where
        // source_ref_canonical matches. Many workbooks (e.g. the ones without
        // SourceTable_mapping_fields populated for a given source) still have
        // rows in the mapping info sheet that effectively declare which columns
        // the parent uses from each source — so we treat them as the
        // de-facto required-columns list when the explicit one is empty.
        // Deduped, case-insensitive, original casing preserved.
        let parentRequiredColumns: string[] = []
        if (pendingNestedParent.consumerArtifactId && session) {
          const parentArtifact = session.artifacts[pendingNestedParent.consumerArtifactId]
          if (parentArtifact) {
            const sourceUpper = pendingNestedParent.sourceRef.toUpperCase()

            // 1) Try the explicit dependency declaration
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

            // 2) Fallback: derive from mapping_rows (sourceField where
            //    source_ref_canonical matches this source ref). Only kicks in
            //    when the explicit list was empty OR the dependency wasn't
            //    found at all — i.e. a workbook whose SourceTable_mapping_fields
            //    didn't declare columns for this source, but whose mapping info
            //    sheet does reference it.
            if (parentRequiredColumns.length === 0) {
              const seen = new Set<string>()
              const derived: string[] = []
              for (const row of parentArtifact.mapping_rows || []) {
                const rowSourceUpper = (row.source_ref_canonical || "").toUpperCase()
                if (rowSourceUpper !== sourceUpper) continue
                const col = (row.source_column_raw || "").trim()
                if (!col) continue
                const key = col.toUpperCase()
                if (seen.has(key)) continue
                seen.add(key)
                derived.push(col)
              }
              parentRequiredColumns = derived
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
    </div>
  )
}
