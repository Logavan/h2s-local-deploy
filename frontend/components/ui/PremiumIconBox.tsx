"use client"

import { useEffect, useRef, ReactNode } from "react"
import { motion } from "framer-motion"

interface BlobProps {
  className?: string
  color?: string
  size?: string
  duration?: number
  delay?: number
}

export function AnimatedBlob({
  className = "",
  color = "rgba(6, 182, 212, 0.2)",
  size = "400px",
  duration = 8,
  delay = 0,
}: BlobProps) {
  return (
    <div
      className={`absolute rounded-full blur-3xl ${className}`}
      style={{
        width: size,
        height: size,
        background: color,
        animation: `morphBlob ${duration}s ease-in-out infinite`,
        animationDelay: `${delay}s`,
      }}
    />
  )
}

// Floating orb for ambient decoration
interface FloatingOrbProps {
  className?: string
  size?: number
  color?: string
  duration?: number
  delay?: number
}

export function FloatingOrb({
  className = "",
  size = 8,
  color = "#06b6d4",
  duration = 4,
  delay = 0,
}: FloatingOrbProps) {
  return (
    <motion.div
      className={`absolute rounded-full ${className}`}
      style={{
        width: size,
        height: size,
        background: color,
        boxShadow: `0 0 ${size * 2}px ${color}, 0 0 ${size * 4}px ${color}40`,
      }}
      animate={{
        y: [0, -20, 0],
        x: [0, 10, 0],
        opacity: [0.4, 0.8, 0.4],
        scale: [1, 1.2, 1],
      }}
      transition={{
        duration,
        delay,
        repeat: Infinity,
        ease: "easeInOut",
      }}
    />
  )
}

// Glowing ring decoration
interface GlowingRingProps {
  className?: string
  size?: number
  color?: string
  duration?: number
}

export function GlowingRing({
  className = "",
  size = 200,
  color = "#06b6d4",
  duration = 3,
}: GlowingRingProps) {
  return (
    <motion.div
      className={`absolute rounded-full border ${className}`}
      style={{
        width: size,
        height: size,
        borderColor: color,
        opacity: 0.15,
      }}
      animate={{
        scale: [1, 1.1, 1],
        opacity: [0.1, 0.2, 0.1],
      }}
      transition={{
        duration,
        repeat: Infinity,
        ease: "easeInOut",
      }}
    />
  )
}

// Scan line effect
export function ScanLine({ className = "" }: { className?: string }) {
  return (
    <div className={`absolute inset-0 overflow-hidden pointer-events-none ${className}`}>
      <motion.div
        className="absolute left-0 right-0 h-px"
        style={{
          background: "linear-gradient(90deg, transparent, rgba(6, 182, 212, 0.4), transparent)",
          top: "0%",
        }}
        animate={{
          top: ["0%", "100%"],
          opacity: [0, 1, 0],
        }}
        transition={{
          duration: 3,
          repeat: Infinity,
          ease: "linear",
          repeatDelay: 2,
        }}
      />
    </div>
  )
}

// Premium icon container with glow
interface IconBoxProps {
  children: ReactNode
  className?: string
  color?: "cyan" | "gold" | "mixed"
  glowIntensity?: "sm" | "md" | "lg"
}

export function PremiumIconBox({
  children,
  className = "",
  color = "cyan",
  glowIntensity = "md",
}: IconBoxProps) {
  const colors = {
    cyan: { bg: "bg-cyan-500/10", border: "border-cyan-500/30", glow: "shadow-glow-sm", icon: "text-cyan-400" },
    gold: { bg: "bg-amber-500/10", border: "border-amber-500/30", glow: "shadow-glow-sm-gold", icon: "text-amber-400" },
    mixed: { bg: "bg-gradient-to-br from-cyan-500/10 to-amber-500/10", border: "border-cyan-500/20", glow: "", icon: "text-cyan-400" },
  }

  const glowSizes = {
    sm: "0 0 15px rgba(6, 182, 212, 0.2)",
    md: "0 0 25px rgba(6, 182, 212, 0.3)",
    lg: "0 0 40px rgba(6, 182, 212, 0.4)",
  }

  const c = colors[color]

  return (
    <motion.div
      className={`inline-flex items-center justify-center rounded-2xl p-4 ${c.bg} border ${c.border} ${c.glow} ${className}`}
      whileHover={{
        scale: 1.05,
        boxShadow: glowSizes[glowIntensity],
      }}
      transition={{ type: "spring", stiffness: 300, damping: 20 }}
    >
      <div className={c.icon}>{children}</div>
    </motion.div>
  )
}

// Code block with syntax highlight styling
interface CodeBlockProps {
  children: ReactNode
  className?: string
}

export function CodeBlock({ children, className = "" }: CodeBlockProps) {
  return (
    <div
      className={`relative rounded-xl border border-border/50 bg-card/80 backdrop-blur-xl overflow-hidden ${className}`}
    >
      {/* Top bar with dots */}
      <div className="flex items-center gap-2 px-4 py-2 border-b border-border/50 bg-muted/30">
        <div className="w-3 h-3 rounded-full bg-red-500/60" />
        <div className="w-3 h-3 rounded-full bg-amber-500/60" />
        <div className="w-3 h-3 rounded-full bg-green-500/60" />
        <span className="ml-2 text-xs text-muted-foreground font-mono">sql</span>
      </div>
      <div className="p-4 font-mono text-sm overflow-x-auto">{children}</div>
    </div>
  )
}
