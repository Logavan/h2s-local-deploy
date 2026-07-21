"use client"

import { motion } from "framer-motion"

interface ShimmerSkeletonProps {
  className?: string
  variant?: "text" | "circular" | "rectangular" | "card"
  width?: string | number
  height?: string | number
}

export function ShimmerSkeleton({
  className = "",
  variant = "rectangular",
  width,
  height,
}: ShimmerSkeletonProps) {
  const variants = {
    text: "rounded-md",
    circular: "rounded-full",
    rectangular: "rounded-xl",
    card: "rounded-2xl",
  }

  return (
    <motion.div
      className={`relative overflow-hidden bg-muted/60 ${variants[variant]} ${className}`}
      style={{ width, height }}
      animate={{
        opacity: [0.5, 0.8, 0.5],
      }}
      transition={{
        duration: 1.5,
        repeat: Infinity,
        ease: "easeInOut",
      }}
    >
      {/* Shimmer overlay */}
      <div
        className="absolute inset-0"
        style={{
          background: "linear-gradient(90deg, transparent 0%, rgba(255,255,255,0.08) 50%, transparent 100%)",
          backgroundSize: "200% 100%",
          animation: "shimmer 2s linear infinite",
        }}
      />
    </motion.div>
  )
}

// Premium multi-line skeleton for content
export function SkeletonContent({ className = "" }: { className?: string }) {
  return (
    <div className={`space-y-4 ${className}`}>
      <ShimmerSkeleton height="1.5rem" width="60%" />
      <ShimmerSkeleton height="1rem" width="100%" />
      <ShimmerSkeleton height="1rem" width="90%" />
      <ShimmerSkeleton height="1rem" width="75%" />
    </div>
  )
}

// Card skeleton
export function SkeletonCard({ className = "" }: { className?: string }) {
  return (
    <div className={`rounded-2xl border border-border/50 bg-card/60 backdrop-blur-xl p-6 space-y-4 ${className}`}>
      <ShimmerSkeleton variant="circular" width={48} height={48} />
      <ShimmerSkeleton height="1.25rem" width="70%" />
      <ShimmerSkeleton height="0.875rem" width="100%" />
      <ShimmerSkeleton height="0.875rem" width="85%" />
      <div className="flex gap-2 pt-2">
        <ShimmerSkeleton height="2rem" width="5rem" />
        <ShimmerSkeleton height="2rem" width="5rem" />
      </div>
    </div>
  )
}
