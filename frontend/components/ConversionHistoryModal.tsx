"use client"

import { useState, useEffect } from "react"
import { Dialog, DialogContent, DialogHeader, DialogTitle, DialogDescription } from "@/components/ui/dialog"
import { Button } from "@/components/ui/button"
import { Loader2, FileSpreadsheet, Search } from "lucide-react"
import { Input } from "@/components/ui/input"
import * as apiModule from "@/lib/api"
import type { PreviousConversion } from "@/lib/api"

interface ConversionHistoryModalProps {
  isOpen: boolean
  onClose: () => void
  onSelectFile: (file: File) => void
}

export default function ConversionHistoryModal({ isOpen, onClose, onSelectFile }: ConversionHistoryModalProps) {
  const [conversions, setConversions] = useState<PreviousConversion[]>([])
  const [loading, setLoading] = useState(true)
  const [downloadingId, setDownloadingId] = useState<string | null>(null)
  const [searchTerm, setSearchTerm] = useState("")

  useEffect(() => {
    if (isOpen) {
      fetchConversions()
    }
  }, [isOpen])

  const fetchConversions = async () => {
    try {
      setLoading(true)
      const result = await apiModule.listPreviousConversations()
      if (result.success) {
        setConversions(result.conversions)
      } else {
        console.error("Failed to load conversions:", result.error)
        setConversions([])
      }
    } catch (error) {
      console.error("Error fetching conversions:", error)
    } finally {
      setLoading(false)
    }
  }

  const handleSelect = async (conversion: PreviousConversion) => {
    try {
      setDownloadingId(conversion.task_id)

      const result = await apiModule.downloadPreviousMapping(conversion.task_id)

      if (result.type === "success") {
        const fileName = `${conversion.file_name}.xlsx`
        const file = new File([result.file], fileName, {
          type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        })
        onSelectFile(file)
        onClose()
      } else {
        alert("Failed to download file. Please try again.")
      }
    } catch (error) {
      console.error("Error downloading file:", error)
      alert("Failed to download file. Please try again.")
    } finally {
      setDownloadingId(null)
    }
  }

  const filteredConversions = conversions.filter((c) =>
    c.file_name.toLowerCase().includes(searchTerm.toLowerCase())
  )

  const formatDate = (isoString: string) => {
    try {
      return new Date(isoString).toLocaleString()
    } catch {
      return isoString
    }
  }

  return (
    <Dialog open={isOpen} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-3xl max-h-[80vh] flex flex-col bg-white">
        <DialogHeader>
          <DialogTitle>Select from Conversion History</DialogTitle>
          <DialogDescription>
            Choose a mapping file from your previous conversions to load into the engine.
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-col sm:flex-row items-stretch gap-2 py-4">
          <div className="relative flex-1">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-gray-400" />
            <Input
              placeholder="Search by file name..."
              value={searchTerm}
              onChange={(e) => setSearchTerm(e.target.value)}
              className="pl-9 w-full"
            />
          </div>
        </div>

        <div className="flex-1 overflow-y-auto min-h-[300px]">
          {loading ? (
            <div className="flex flex-col items-center justify-center h-40">
              <Loader2 className="h-8 w-8 animate-spin text-primary mb-2" />
              <p className="text-gray-500">Loading history...</p>
            </div>
          ) : filteredConversions.length === 0 ? (
            <div className="flex flex-col items-center justify-center h-40 text-gray-500">
              <FileSpreadsheet className="h-10 w-10 mb-2 opacity-20" />
              <p>No mapping files found.</p>
            </div>
          ) : (
            <div className="border rounded-md overflow-x-auto">
              <table className="w-full text-sm min-w-[400px]">
                <thead className="bg-gray-50 border-b sticky top-0">
                  <tr>
                    <th className="text-left p-2 sm:p-3 font-medium text-gray-600">Name</th>
                    <th className="text-left p-2 sm:p-3 font-medium text-gray-600 hidden sm:table-cell">Date</th>
                    <th className="text-right p-2 sm:p-3 font-medium text-gray-600">Action</th>
                  </tr>
                </thead>
                <tbody className="divide-y">
                  {filteredConversions.map((conversion) => (
                    <tr key={conversion.task_id} className="hover:bg-gray-50 transition-colors">
                      <td className="p-2 sm:p-3">
                        <div className="font-medium text-gray-900 truncate max-w-[150px] sm:max-w-none">
                          {conversion.file_name}
                        </div>
                        <div className="text-xs text-gray-500 sm:hidden">
                          {formatDate(conversion.modified_at)}
                        </div>
                      </td>
                      <td className="p-2 sm:p-3 hidden sm:table-cell">
                        <span className="text-gray-600">{formatDate(conversion.modified_at)}</span>
                      </td>
                      <td className="p-2 sm:p-3 text-right">
                        <Button
                          size="sm"
                          onClick={() => handleSelect(conversion)}
                          disabled={!!downloadingId}
                          className="bg-secondary hover:bg-secondary/90 text-secondary-foreground font-semibold border-none min-h-[36px]"
                        >
                          {downloadingId === conversion.task_id ? (
                            <Loader2 className="h-4 w-4 animate-spin mr-1" />
                          ) : null}
                          Select
                        </Button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
