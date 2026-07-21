/**
 * GA4 Analytics Event Tracking Utility
 *
 * Sends custom events to Google Analytics 4 for tracking user actions
 * that matter for conversion optimization.
 *
 * Usage:
 *   import { trackEvent } from "@/lib/analytics"
 *   trackEvent("file_upload", { file_type: "xml", file_size: 1024 })
 */

// Extend the Window interface for gtag
declare global {
    interface Window {
        gtag?: (...args: unknown[]) => void
    }
}

/**
 * Track a custom GA4 event.
 * Safe to call even if GA hasn't loaded yet — it will silently no-op.
 */
export function trackEvent(
    eventName: string,
    eventParams?: Record<string, string | number | boolean>
) {
    if (typeof window !== "undefined" && window.gtag) {
        window.gtag("event", eventName, eventParams)
    }
}

// ─── Pre-defined events for key conversion funnel steps ───

/** User uploaded a HANA CV XML file */
export function trackFileUpload(fileSize?: number) {
    trackEvent("file_upload", {
        file_type: "xml",
        ...(fileSize && { file_size_kb: Math.round(fileSize / 1024) }),
    })
}

/** User completed a conversion (SQL generated) */
export function trackConversionComplete(target: string, nodeCount?: number) {
    trackEvent("conversion_complete", {
        target_platform: target,
        ...(nodeCount && { node_count: nodeCount }),
    })
}

/** User clicked signup */
export function trackSignupClick(source: string) {
    trackEvent("signup_click", { source })
}

/** User clicked to purchase credits */
export function trackCreditPurchaseClick(source: string) {
    trackEvent("credit_purchase_click", { source })
}

/** User downloaded a converted file */
export function trackFileDownload(fileType: string) {
    trackEvent("file_download", { file_type: fileType })
}

/** User visited the pricing page */
export function trackPricingView() {
    trackEvent("pricing_view")
}

/** User started using the tool (first interaction with converter/mapper) */
export function trackToolInteraction(tool: "converter" | "mapper") {
    trackEvent("tool_interaction", { tool_type: tool })
}
