"use client"

// Re-export EnterpriseContext as AuthContext for backwards compatibility
export { EnterpriseProvider as AuthProvider, useAuth, useEnterprise } from "./EnterpriseContext"
