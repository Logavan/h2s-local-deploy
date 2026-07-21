"use client"

import { useRef, useState, ReactNode } from "react"
import { motion, useSpring } from "framer-motion"

interface MagneticButtonProps {
  children: ReactNode
  className?: string
  strength?: number
  onClick?: () => void
  disabled?: boolean
}

export function MagneticButton({
  children,
  className = "",
  strength = 0.4,
  onClick,
  disabled = false,
}: MagneticButtonProps) {
  const ref = useRef<HTMLDivElement>(null)
  const [isHovered, setIsHovered] = useState(false)

  const springX = useSpring(0, { stiffness: 150, damping: 15, mass: 0.1 })
  const springY = useSpring(0, { stiffness: 150, damping: 15, mass: 0.1 })

  const handleMouseMove = (e: React.MouseEvent<HTMLDivElement>) => {
    if (disabled) return
    const rect = ref.current?.getBoundingClientRect()
    if (!rect) return

    const centerX = rect.left + rect.width / 2
    const centerY = rect.top + rect.height / 2

    const deltaX = (e.clientX - centerX) * strength
    const deltaY = (e.clientY - centerY) * strength

    springX.set(deltaX)
    springY.set(deltaY)
  }

  const handleMouseLeave = () => {
    springX.set(0)
    springY.set(0)
    setIsHovered(false)
  }

  return (
    <motion.div
      ref={ref}
      className={`inline-block cursor-pointer ${disabled ? "cursor-not-allowed opacity-50" : ""} ${className}`}
      style={{ x: springX, y: springY }}
      onMouseMove={handleMouseMove}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={handleMouseLeave}
      onClick={disabled ? undefined : onClick}
      whileTap={disabled ? {} : { scale: 0.95 }}
      animate={disabled ? {} : {
        boxShadow: isHovered
          ? "0 0 30px rgba(6, 182, 212, 0.4), 0 0 60px rgba(6, 182, 212, 0.2)"
          : "0 0 0px rgba(6, 182, 212, 0)",
      }}
      transition={{ boxShadow: { type: "spring", stiffness: 200, damping: 20 } }}
    >
      <motion.div
        className="relative overflow-hidden rounded-xl"
        animate={isHovered ? { scale: 1.02 } : { scale: 1 }}
        transition={{ type: "spring", stiffness: 300, damping: 20 }}
      >
        {children}
      </motion.div>
    </motion.div>
  )
}
