"use client"

import * as React from "react"
import { Search } from "lucide-react"
import { motion, AnimatePresence } from "framer-motion"
import { cn } from "@/lib/utils"

interface AnimatedSearchInputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  onScroll?: () => void
}

export const AnimatedSearchInput = React.forwardRef<HTMLInputElement, AnimatedSearchInputProps>(
  ({ className, onScroll, ...props }, ref) => {
    const [isFocused, setIsFocused] = React.useState(false)
    const [showHighlight, setShowHighlight] = React.useState(false)
    const inputRef = React.useRef<HTMLInputElement>(null)

    // Handle scroll to show highlight effect
    React.useEffect(() => {
      let lastScrollY = window.scrollY

      const handleScroll = () => {
        const currentScrollY = window.scrollY
        // Show highlight when scrolling
        if (Math.abs(currentScrollY - lastScrollY) > 5) {
          setShowHighlight(true)
          // Hide highlight after animation
          setTimeout(() => setShowHighlight(false), 1000)
        }
        lastScrollY = currentScrollY
        if (onScroll) onScroll()
      }

      window.addEventListener("scroll", handleScroll, { passive: true })
      return () => window.removeEventListener("scroll", handleScroll)
    }, [onScroll])

    const handleFocus = () => setIsFocused(true)
    const handleBlur = () => setIsFocused(false)

    return (
      <div className="relative w-full">
        {/* Search Input */}
        <div className="relative">
          <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground transition-colors duration-300" />
          <input
            ref={inputRef}
            type={props.type || "text"}
            className={cn(
              "flex h-11 min-h-[44px] w-full rounded-lg border border-slate-200 bg-white pl-10 pr-4 py-3 text-sm text-slate-900 placeholder:text-slate-400 transition-all duration-300",
              "focus:outline-none focus:border-transparent focus:ring-0",
              isFocused && "bg-slate-50",
              className
            )}
            onFocus={handleFocus}
            onBlur={handleBlur}
            {...props}
          />
        </div>

        {/* Animated Highlight Line - Below Input */}
        <div className="relative h-1 mt-1 overflow-hidden">
          {/* Background line */}
          <div className="absolute inset-0 bg-slate-100 rounded-full" />

          {/* Animated highlight line */}
          <motion.div
            className="absolute inset-0 rounded-full"
            initial={{ scaleX: 0, opacity: 0 }}
            animate={{
              scaleX: isFocused || showHighlight ? 1 : 0,
              opacity: isFocused || showHighlight ? 1 : 0,
            }}
            transition={{
              scaleX: { type: "spring", stiffness: 300, damping: 25 },
              opacity: { duration: 0.2 },
            }}
            style={{
              background: "linear-gradient(90deg, #06b6d4 0%, #22d3ee 40%, #f59e0b 70%, #fbbf24 100%)",
              boxShadow: "0 0 12px rgba(6, 182, 212, 0.6), 0 0 24px rgba(6, 182, 212, 0.3)",
              transformOrigin: "left center",
            }}
          />

          {/* Glow effect when focused */}
          <AnimatePresence>
            {(isFocused || showHighlight) && (
              <motion.div
                className="absolute inset-0 rounded-full blur-sm"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.3 }}
                style={{
                  background: "linear-gradient(90deg, #06b6d4 0%, #22d3ee 50%, #f59e0b 100%)",
                  filter: "blur(4px)",
                }}
              />
            )}
          </AnimatePresence>
        </div>

        {/* Decorative dots on ends */}
        <AnimatePresence>
          {(isFocused || showHighlight) && (
            <>
              <motion.div
                className="absolute left-0 -bottom-0.5 w-1.5 h-1.5 rounded-full"
                initial={{ scale: 0, opacity: 0 }}
                animate={{ scale: 1, opacity: 1 }}
                exit={{ scale: 0, opacity: 0 }}
                transition={{ delay: 0.1, duration: 0.2 }}
                style={{
                  background: "linear-gradient(135deg, #06b6d4, #22d3ee)",
                  boxShadow: "0 0 6px rgba(6, 182, 212, 0.8)",
                }}
              />
              <motion.div
                className="absolute right-0 -bottom-0.5 w-1.5 h-1.5 rounded-full"
                initial={{ scale: 0, opacity: 0 }}
                animate={{ scale: 1, opacity: 1 }}
                exit={{ scale: 0, opacity: 0 }}
                transition={{ delay: 0.1, duration: 0.2 }}
                style={{
                  background: "linear-gradient(135deg, #f59e0b, #fbbf24)",
                  boxShadow: "0 0 6px rgba(245, 158, 11, 0.8)",
                }}
              />
            </>
          )}
        </AnimatePresence>
      </div>
    )
  }
)

AnimatedSearchInput.displayName = "AnimatedSearchInput"

export default AnimatedSearchInput
