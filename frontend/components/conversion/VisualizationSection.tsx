"use client"

import dynamic from "next/dynamic";
import { Loader2, Download } from "lucide-react";
import { Button } from "../ui/button";

const DynamicGraphvizViewer = dynamic(() => import("../GraphvizViewer"), { ssr: false });

type ProcessingState = "idle" | "analyzing" | "checking-limits" | "initiating-conversion" | "polling-status" | "success" | "error";

interface VisualizationSectionProps {
  conversionMode?: "single" | "bulk"
  viewXmlFile: File | null;
  processingState: ProcessingState;
  visualizationDotString: string | null;
  renderedSvgContent: string | null;
  onDownloadSvg: () => void;
  showVisualization: boolean;
  onSvgRendered?: (svgContent: string) => void;
}

export function VisualizationSection({
  conversionMode,
  viewXmlFile,
  processingState,
  visualizationDotString,
  renderedSvgContent,
  onDownloadSvg,
  showVisualization,
  onSvgRendered,
}: VisualizationSectionProps) {
  const isProcessing = processingState === "analyzing" ||
    processingState === "checking-limits" ||
    processingState === "initiating-conversion" ||
    processingState === "polling-status";

  // Only show for single file mode
  if (conversionMode !== "single" || !viewXmlFile || processingState === "idle") {
    return null;
  }

  return (
    <div className="mt-8 p-4 bg-white dark:bg-gray-800 shadow-lg rounded-lg">
      <h2 className="text-xl font-semibold text-gray-900 dark:text-gray-100 mb-4">SQL Flow</h2>
      {visualizationDotString && visualizationDotString.trim() !== "" && showVisualization ? (
        <>
          <div className="flex justify-end mb-4">
            <Button onClick={onDownloadSvg} disabled={!renderedSvgContent} className="bg-gray-200 text-gray-800 hover:bg-gray-300">
              <Download className="mr-2 h-4 w-4" />
              Download
            </Button>
          </div>
          <DynamicGraphvizViewer
            key={visualizationDotString}
            dotString={visualizationDotString}
            onSvgRendered={onSvgRendered}
          />
        </>
      ) : isProcessing ? (
        <div className="flex items-center justify-center h-32 text-gray-500">
          <Loader2 className="mr-2 h-5 w-5 animate-spin" />
          Generating visualization...
        </div>
      ) : (
        <div className="text-center text-gray-500 dark:text-gray-400">
          SQL visualization - Not available for this conversion.
        </div>
      )}
    </div>
  );
}
