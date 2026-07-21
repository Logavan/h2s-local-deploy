"use client"

import { motion, useSpring, useTransform } from "framer-motion"
import { useRef, ReactNode } from "react"

interface GlassmorphicPanelProps {
  children: ReactNode
  className?: string
  intensity?: "sm" | "md" | "lg"
  borderGlow?: boolean
  tilt3d?: boolean
}

export function GlassmorphicPanel({
  children,
  className = "",
  intensity = "md",
  borderGlow = false,
  tilt3d = false,
}: GlassmorphicPanelProps) {
  const ref = useRef<HTMLDivElement>(null)

  const intensityStyles = {
    sm: "bg-white/[0.04] backdrop-blur-xl border-white/[0.06]",
    md: "bg-white/[0.07] backdrop-blur-2xl border-white/[0.1]",
    lg: "bg-white/[0.1] backdrop-blur-3xl border-white/[0.15]",
  }

  const [tiltX, setTiltX] = [useSpring(0), useSpring(0)]
  const springX = useSpring(0, { stiffness: 100, damping: 20 })
  const springY = useSpring(0, { stiffness: 100, damping: 20 })

  const handleMouseMove = (e: React.MouseEvent<HTMLDivElement>) => {
    if (!tilt3d || !ref.current) return
    const rect = ref.current.getBoundingClientRect()
    const centerX = rect.left + rect.width / 2
    const centerY = rect.top + rect.height / 2
    const rotateX = (e.clientY - centerY) / 20
    const rotateY = (centerX - e.clientX) / 20
    springX.set(rotateX)
    springY.set(rotateY)
  }

  const handleMouseLeave = () => {
    springX.set(0)
    springY.set(0)
  }

  return (
    <motion.div
      ref={ref}
      className={`relative overflow-hidden rounded-2xl ${intensityStyles[intensity]} ${className}`}
      onMouseMove={handleMouseMove}
      onMouseLeave={handleMouseLeave}
      style={
        tilt3d
          ? {
              transform: `perspective(1000px) rotateX(${springX}deg) rotateY(${springY}deg)`,
              transition: "transform 0.1s ease-out",
            }
          : undefined
      }
      whileHover={!tilt3d ? {
        borderColor: borderGlow ? "rgba(6, 182, 212, 0.4)" : undefined,
        boxShadow: borderGlow ? "0 0 30px rgba(6, 182, 212, 0.15), inset 0 0 30px rgba(6, 182, 212, 0.05)" : undefined,
      } : undefined}
      transition={{ duration: 0.3 }}
    >
      {/* Inner glow top */}
      <div
        className="absolute top-0 left-0 right-0 h-px"
        style={{
          background: "linear-gradient(90deg, transparent, rgba(6, 182, 212, 0.5), transparent)",
        }}
      />
      {children}
    </motion.div>
  )
}
