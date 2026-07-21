"\"use client"

import { useState, useCallback } from "react"
import type { ToastProps } from "@/components/ui/toast"

export type ToastType = "default" | "success" | "error" | "warning" | "info"

export function useToast() {
  const [toasts, setToasts] = useState<(ToastProps & { id: string })[]>([])

  const toast = useCallback((props: ToastProps) => {
    const id = Math.random().toString(36).substring(2, 9)
    const newToast = { ...props, id, variant: props.variant || "default" }

    setToasts((prev) => [...prev, newToast])

    return id
  }, [])

  const dismissToast = useCallback((id: string) => {
    setToasts((prev) => prev.filter((toast) => toast.id !== id))
  }, [])

  // Helper methods for different toast types
  const success = useCallback((props: Omit<ToastProps, "variant" | "id" | "onDismiss">) => toast({ ...props, variant: "success" }), [toast])

  const error = useCallback((props: Omit<ToastProps, "variant" | "id" | "onDismiss">) => toast({ ...props, variant: "error" }), [toast])

  const warning = useCallback((props: Omit<ToastProps, "variant" | "id" | "onDismiss">) => toast({ ...props, variant: "warning" }), [toast])

  const info = useCallback((props: Omit<ToastProps, "variant" | "id" | "onDismiss">) => toast({ ...props, variant: "info" }), [toast])

  return {
    toast,
    toasts,
    dismissToast,
    success,
    error,
    warning,
    info,
  }
}
