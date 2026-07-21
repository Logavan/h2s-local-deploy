"use client"

import React, { useState, useEffect, useMemo } from "react"
import ReactMarkdown from "react-markdown"
import remarkGfm from "remark-gfm"
// @ts-ignore - The module is installed but the IDE sometimes fails to resolve its types
import CodeMirror from "@uiw/react-codemirror"
import { Type, Code, Copy, Check } from "lucide-react"
import { python } from "@codemirror/lang-python"

interface Cell {
    cell_type: "markdown" | "code"
    source: string[]
    execution_count?: number | null
    outputs?: any[]
    metadata?: any
}

interface NotebookData {
    nbformat: number
    nbformat_minor: number
    cells: Cell[]
    metadata: any
}

interface NotebookRendererProps {
    content: string
    onChange?: (newContent: string) => void
}

export default function NotebookRenderer({ content, onChange }: NotebookRendererProps) {
    const [notebook, setNotebook] = useState<NotebookData | null>(null)
    const [error, setError] = useState<string | null>(null)

    useEffect(() => {
        try {
            if (!content) {
                setNotebook(null)
                return
            }
            const parsed = JSON.parse(content)
            if (!parsed.cells) {
                throw new Error("Invalid notebook format: Missing cells")
            }
            setNotebook(parsed)
            setError(null)
        } catch (err: any) {
            console.error("Failed to parse notebook JSON:", err)
            setError("Invalid notebook format. Cannot render as notebook.")
        }
    }, [content])

    const handleCellChange = (index: number, newSource: string) => {
        if (!notebook || !onChange) return
        const updatedNotebook = { ...notebook }
        const updatedCells = [...updatedNotebook.cells]

        // Split the new source back into lines as notebook spec expects an array of strings
        const sourceArray = newSource.split('\n').map((line, i, arr) =>
            i === arr.length - 1 ? line : line + '\n'
        )

        updatedCells[index] = { ...updatedCells[index], source: sourceArray }
        updatedNotebook.cells = updatedCells
        setNotebook(updatedNotebook)

        // Trigger the onChange callback with the new JSON string
        onChange(JSON.stringify(updatedNotebook, null, 1))
    }

    if (error) {
        return (
            <div className="p-4 text-red-500 bg-red-50 rounded-md border border-red-200">
                <p className="font-semibold mb-2">Error rendering notebook:</p>
                <p className="text-sm">{error}</p>
                <p className="mt-4 text-sm text-gray-500">Raw JSON content is too malformed to display as a notebook.</p>
            </div>
        )
    }

    if (!notebook || !notebook.cells) {
        return <div className="p-4 text-gray-500 text-center animate-pulse">Loading notebook...</div>
    }

    return (
        <div className="flex flex-col gap-6 bg-white dark:bg-gray-900 mx-auto w-full p-4 sm:p-6 rounded-md border text-left">
            {notebook.cells.map((cell, index) => (
                <NotebookCell
                    key={index}
                    cell={cell}
                    index={index}
                    onChange={(newSource) => handleCellChange(index, newSource)}
                    isEditable={!!onChange}
                />
            ))}
        </div>
    )
}

function NotebookCell({
    cell,
    index,
    onChange,
    isEditable
}: {
    cell: Cell;
    index: number;
    onChange: (s: string) => void;
    isEditable: boolean;
}) {
    const sourceText = useMemo(() => {
        return Array.isArray(cell.source) ? cell.source.join("") : (cell.source || "")
    }, [cell.source])

    const [copied, setCopied] = useState(false)

    const copyToClipboard = () => {
        navigator.clipboard.writeText(sourceText)
        setCopied(true)
        setTimeout(() => setCopied(false), 2000)
    }

    if (cell.cell_type === "markdown") {
        return (
            <div className="group relative flex border border-transparent hover:border-gray-100 dark:hover:border-gray-800 rounded-md transition-colors px-2 py-3 -mx-2">
                <div className="w-10 sm:w-16 flex-shrink-0 flex justify-end pr-2 pt-1">
                    <Type className="w-4 h-4 text-gray-300 dark:text-gray-600 opacity-0 group-hover:opacity-100 transition-opacity" />
                </div>

                <div className="flex-grow min-w-0">
                    <div
                        className="prose prose-sm sm:prose-base dark:prose-invert max-w-none 
                       prose-headings:mt-4 prose-headings:mb-2 prose-headings:font-semibold 
                       prose-h1:text-2xl prose-h2:text-xl prose-h3:text-lg
                       prose-p:my-2 prose-a:text-blue-600 dark:prose-a:text-blue-400
                       prose-code:px-1.5 prose-code:py-0.5 prose-code:bg-gray-100 dark:prose-code:bg-gray-800 prose-code:rounded prose-code:text-gray-800 dark:prose-code:text-gray-200 prose-code:font-mono prose-code:text-sm
                       prose-pre:bg-gray-50 dark:prose-pre:bg-gray-800 prose-pre:p-3 prose-pre:rounded-md prose-pre:border prose-pre:border-gray-200 dark:prose-pre:border-gray-700
                       prose-blockquote:border-l-4 prose-blockquote:border-gray-300 dark:prose-blockquote:border-gray-600 prose-blockquote:pl-4 prose-blockquote:italic
                       prose-table:w-auto prose-table:min-w-[50%] prose-table:border-collapse
                       prose-th:border prose-th:border-gray-300 dark:prose-th:border-gray-700 prose-th:bg-gray-50 dark:prose-th:bg-gray-800 prose-th:px-4 prose-th:py-2
                       prose-td:border prose-td:border-gray-300 dark:prose-td:border-gray-700 prose-td:px-4 prose-td:py-2"
                    >
                        <ReactMarkdown remarkPlugins={[remarkGfm]}>
                            {sourceText}
                        </ReactMarkdown>
                    </div>
                </div>
            </div>
        )
    }

    if (cell.cell_type === "code") {
        // Determine execution count (1-based from index but typically provided by python)
        const execCount = cell.execution_count || (index + 1)

        return (
            <div className="group relative flex flex-col sm:flex-row mb-2">
                {/* Execution indicator (In [1]:) */}
                <div className="sm:w-16 flex-shrink-0 sm:flex sm:justify-end sm:pr-3 pt-2 text-xs font-mono text-gray-500 hidden sm:block">
                    <span className="text-blue-600 dark:text-blue-400">In [{execCount}]:</span>
                </div>

                {/* Code editor container */}
                <div className="flex-grow min-w-0 rounded-md border border-gray-200 dark:border-gray-700 bg-white dark:bg-gray-800/50 overflow-hidden relative">

                    {/* Header/toolbar for code cell */}
                    <div className="flex justify-between items-center px-3 py-1.5 bg-gray-50 dark:bg-gray-800 border-b border-gray-200 dark:border-gray-700">
                        <div className="flex items-center gap-1.5 text-xs text-gray-500 font-medium">
                            <Code className="w-3.5 h-3.5" />
                            Python
                        </div>

                        <button
                            onClick={copyToClipboard}
                            className="p-1 rounded-md text-gray-500 hover:text-gray-700 hover:bg-gray-200 dark:hover:bg-gray-700 transition-colors"
                            title="Copy snippet"
                        >
                            {copied ? <Check className="w-3.5 h-3.5 text-green-600" /> : <Copy className="w-3.5 h-3.5" />}
                        </button>
                    </div>

                    <div className="notebook-syntax-wrapper text-sm">
                        <CodeMirror
                            value={sourceText}
                            theme="light"
                            extensions={[python()]}
                            basicSetup={{
                                lineNumbers: true,
                                foldGutter: true,
                                highlightActiveLine: isEditable,
                                highlightActiveLineGutter: isEditable,
                                dropCursor: isEditable,
                                allowMultipleSelections: isEditable,
                                indentOnInput: isEditable,
                            }}
                            editable={isEditable}
                            onChange={(value: string) => {
                                if (isEditable) onChange(value)
                            }}
                            className="font-mono"
                        />
                    </div>
                </div>
            </div>
        )
    }

    return null
}
