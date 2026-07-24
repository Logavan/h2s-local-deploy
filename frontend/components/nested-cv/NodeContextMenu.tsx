"use client"

import { MoreVertical, Pencil, Plus, Trash2 } from "lucide-react"
import {
  ContextMenu,
  ContextMenuContent,
  ContextMenuItem,
  ContextMenuSeparator,
  ContextMenuTrigger,
} from "@/components/ui/context-menu"
import { cn } from "@/lib/utils"

interface NodeContextMenuProps {
  children: React.ReactNode
  canAdjustMappings: boolean
  canResolveNested: boolean
  canRemove: boolean
  onAdjustMappings: () => void
  onResolveNested: () => void
  onRemove: () => void
}

export default function NodeContextMenu({
  children,
  canAdjustMappings,
  canResolveNested,
  canRemove,
  onAdjustMappings,
  onResolveNested,
  onRemove,
}: NodeContextMenuProps) {
  return (
    <ContextMenu>
      <ContextMenuTrigger asChild>{children}</ContextMenuTrigger>
      <ContextMenuContent className="z-[100] w-60">
        <ContextMenuItem disabled={!canAdjustMappings} onSelect={onAdjustMappings}>
          <Pencil className="mr-2 h-4 w-4" />
          Adjust Column &amp; Table Mapping
        </ContextMenuItem>
        <ContextMenuItem disabled={!canResolveNested} onSelect={onResolveNested}>
          <Plus className="mr-2 h-4 w-4" />
          Nested Calculation View
        </ContextMenuItem>
        <ContextMenuSeparator />
        <ContextMenuItem
          disabled={!canRemove}
          onSelect={onRemove}
          className={cn(canRemove && "text-red-600 focus:bg-red-50 focus:text-red-700")}
        >
          <Trash2 className="mr-2 h-4 w-4" />
          Remove
        </ContextMenuItem>
      </ContextMenuContent>
    </ContextMenu>
  )
}

export function NodeMenuHint() {
  return <MoreVertical className="h-3.5 w-3.5" aria-hidden="true" />
}
