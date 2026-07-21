"use client"

import { motion, useScroll, useSpring, useTransform } from "framer-motion"
import { useEffect, useState } from "react"

export default function ScrollProgress() {
  const { scrollYProgress } = useScroll()
  const scaleX = useSpring(scrollYProgress, { stiffness: 100, damping: 30, restDelta: 0.001 })
  const [isVisible, setIsVisible] = useState(false)

  // Show only after scrolling past header
  const opacity = useTransform(scrollYProgress, [0, 0.02], [0, 1])

  useEffect(() => {
    const handleScroll = () => {
      setIsVisible(window.scrollY > 64) // After header height
    }
    window.addEventListener("scroll", handleScroll, { passive: true })
    return () => window.removeEventListener("scroll", handleScroll)
  }, [])

  return (
    <motion.div
      className="fixed left-0 right-0 z-[9998] pointer-events-none"
      style={{
        top: "64px", // Below the header (h-16 = 64px)
        height: "12px", // Taller to accommodate glow
        opacity,
      }}
    >
      {/* Glow backdrop - multi-color */}
      <div
        className="absolute inset-0 w-full"
        style={{
          background: "linear-gradient(90deg, rgba(6, 182, 212, 0.2) 0%, rgba(16, 185, 129, 0.2) 50%, rgba(245, 158, 11, 0.2) 100%)",
          filter: "blur(6px)",
          height: "12px",
          top: "-4px",
        }}
      />

      {/* Progress bar */}
      <motion.div
        className="h-full origin-left rounded-full"
        style={{
          scaleX,
          height: "5px",
          background: "linear-gradient(90deg, #06b6d4 0%, #10b981 40%, #f59e0b 100%)",
          boxShadow: "0 0 10px rgba(6, 182, 212, 0.7), 0 0 20px rgba(16, 185, 129, 0.5), 0 0 30px rgba(245, 158, 11, 0.4)",
        }}
      />

      {/* Shimmer overlay */}
      <motion.div
        className="absolute inset-0 rounded-full"
        style={{
          height: "5px",
          top: 0,
          left: 0,
          right: 0,
          background: "linear-gradient(90deg, transparent 0%, rgba(255,255,255,0.5) 50%, transparent 100%)",
        }}
        animate={{
          x: ["-200%", "200%"],
        }}
        transition={{
          duration: 1.8,
          repeat: Infinity,
          repeatDelay: 4,
          ease: "easeInOut",
        }}
      />
    </motion.div>
  )
}
