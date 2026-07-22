"use client"

import React, { createContext, useContext, useCallback, useMemo, ReactNode } from "react"

// Enterprise context - no auth, unlimited usage
interface EnterpriseContextType {
  isEnterprise: true
  isAuthenticated: boolean
  isLoggedIn: boolean
  user: null
  signOut: () => void
  session: null
  userName: null
  userEmail: null
  loading: boolean
}

const EnterpriseContext = createContext<EnterpriseContextType | null>(null)

export function EnterpriseProvider({ children }: { children: ReactNode }) {
  const value = useMemo<EnterpriseContextType>(
    () => ({
      isEnterprise: true as const,
      isAuthenticated: true,
      isLoggedIn: true,
      user: null,
      signOut: () => {},
      session: null,
      userName: null,
      userEmail: null,
      loading: false,
    }),
    []
  )

  return (
    <EnterpriseContext.Provider value={value}>
      {children}
    </EnterpriseContext.Provider>
  )
}

export function useEnterprise(): EnterpriseContextType {
  const context = useContext(EnterpriseContext)
  if (!context) {
    // Return default values if not in provider (for SSR)
    return {
      isEnterprise: true as const,
      isAuthenticated: true,
      isLoggedIn: true,
      user: null,
      signOut: () => {},
      session: null,
      userName: null,
      userEmail: null,
      loading: false,
    }
  }
  return context
}

// Alias for backwards compatibility with code using useAuth
export const useAuth = useEnterprise

export default EnterpriseContext
