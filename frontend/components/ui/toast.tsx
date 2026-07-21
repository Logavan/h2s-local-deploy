"use client"

import { useEffect, useState } from "react"
import { X } from "lucide-react"
import { cn } from "@/lib/utils"

export type ToastVariant = "default" | "success" | "error" | "warning" | "info"

export interface ToastProps {
  id?: string
  title?: string
  description: string
  variant?: ToastVariant
  duration?: number
  onDismiss?: (id: string) => void
}

export function Toast({ id, title, description, variant = "default", duration = 5000, onDismiss }: ToastProps) {
  const [isVisible, setIsVisible] = useState(true)

  const handleDismiss = () => {
    setIsVisible(false)
    if (onDismiss && id !== undefined) {
      setTimeout(() => onDismiss(id), 300) // Allow animation to complete
    }
  }

  useEffect(() => {
    const timer = setTimeout(() => {
      handleDismiss()
    }, duration)

    return () => clearTimeout(timer)
  }, [id, duration])

  const variantStyles = {
    default: "bg-gray-800",
    success: "bg-green-600",
    error: "bg-red-600",
    warning: "bg-yellow-600",
    info: "bg-blue-600",
  }

  return (
    <div
      className={cn(
        "flex items-start gap-3 rounded-md p-4 shadow-lg text-white max-w-md transform transition-all duration-300",
        variantStyles[variant],
        isVisible ? "translate-y-0 opacity-100" : "translate-y-2 opacity-0",
      )}
      role="alert"
      aria-live={variant === "error" ? "assertive" : "polite"}
    >
      <div className="flex-grow">
        {title && <h4 className="font-semibold mb-1">{title}</h4>}
        <div className="text-sm">{description}</div>
      </div>
      <button
        onClick={handleDismiss}
        className="p-1 rounded-full hover:bg-white hover:bg-opacity-20 transition-colors self-start"
        aria-label="Dismiss notification"
      >
        <X size={16} />
      </button>
    </div>
  )
}

export function ToastContainer({
  toasts,
  onDismiss,
}: {
  toasts: ToastProps[]
  onDismiss: (id: string) => void
}) {
  if (toasts.length === 0) return null

  return (
    <div className="fixed bottom-0 right-0 z-50 p-4 space-y-3 max-h-screen overflow-hidden pointer-events-none">
      <div className="flex flex-col items-end gap-3 pointer-events-auto">
        {toasts.map((toast) => (
          <Toast key={toast.id} {...toast} onDismiss={onDismiss} />
        ))}
      </div>
    </div>
  )
}
