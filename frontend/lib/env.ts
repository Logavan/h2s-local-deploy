// Environment variables with fallbacks
import { config } from "./config"

export const API_URL = config.api.baseUrl
export const AUTH_DOMAIN = process.env.NEXT_PUBLIC_AUTH_DOMAIN || "your-auth-domain.com" // Placeholder, replace with actual auth domain if needed
