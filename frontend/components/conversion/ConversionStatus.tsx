import { AlertCircle, CheckCircle } from 'lucide-react'

interface ConversionStatusProps {
  processingState: string
}

export function ConversionStatus({ processingState }: ConversionStatusProps) {
  return (
    <div className="relative overflow-hidden h-2 w-full bg-secondary/20 rounded-full">
      {processingState !== "success" && processingState !== "error" && (
        <div className="h-full bg-primary animate-progress-indeterminate rounded-full" />
      )}
      {processingState === "success" && (
        <div className="h-full bg-green-600 rounded-full flex items-center justify-center">
          <CheckCircle className="h-4 w-4 text-white" />
        </div>
      )}
      {processingState === "error" && (
        <div className="h-full bg-red-500 rounded-full flex items-center justify-center">
          <AlertCircle className="h-4 w-4 text-white" />
        </div>
      )}
    </div>
  )
}
