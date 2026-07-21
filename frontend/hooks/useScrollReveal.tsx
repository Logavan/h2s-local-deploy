"use client"

import { useRef, ReactNode } from "react"
import { motion, useInView } from "framer-motion"

interface ScrollRevealProps {
  children: ReactNode
  className?: string
  direction?: "up" | "down" | "left" | "right" | "scale" | "none"
  delay?: number
  duration?: number
  distance?: number
  once?: boolean
  threshold?: number
}

export function ScrollReveal({
  children,
  className = "",
  direction = "up",
  delay = 0,
  duration = 0.7,
  distance = 40,
  once = true,
  threshold = 0.1,
}: ScrollRevealProps) {
  const ref = useRef<HTMLDivElement>(null)
  const isInView = useInView(ref, {
    once,
    margin: "-50px 0px -50px 0px",
  })

  const directions = {
    up: { y: distance, x: 0 },
    down: { y: -distance, x: 0 },
    left: { x: distance, y: 0 },
    right: { x: -distance, y: 0 },
    scale: { scale: 0.8, y: 0, x: 0 },
    none: { y: 0, x: 0, scale: 1 },
  }

  const initial = {
    opacity: 0,
    ...directions[direction],
  }

  const animate = isInView
    ? {
        opacity: 1,
        y: 0,
        x: 0,
        scale: 1,
      }
    : initial

  return (
    <motion.div
      ref={ref}
      className={className}
      initial={initial}
      animate={animate}
      transition={{
        duration,
        delay,
        ease: [0.16, 1, 0.3, 1], // expo ease-out
      }}
    >
      {children}
    </motion.div>
  )
}

// Staggered container for multiple items
interface StaggerRevealProps {
  children: ReactNode[]
  className?: string
  staggerDelay?: number
  direction?: "up" | "down" | "left" | "right" | "scale"
  delay?: number
}

export function StaggerReveal({
  children,
  className = "",
  staggerDelay = 0.08,
  direction = "up",
  delay = 0,
}: StaggerRevealProps) {
  const ref = useRef<HTMLDivElement>(null)
  const isInView = useInView(ref, { once: true, margin: "-50px 0px -50px 0px" })

  const container = {
    hidden: { opacity: 0 },
    show: {
      opacity: 1,
      transition: {
        staggerChildren: staggerDelay,
        delayChildren: delay,
      },
    },
  }

  const item = {
    hidden: { opacity: 0, y: 30 },
    show: {
      opacity: 1,
      y: 0,
      transition: {
        duration: 0.6,
        ease: [0.16, 1, 0.3, 1],
      },
    },
  }

  return (
    <motion.div
      ref={ref}
      className={className}
      variants={container}
      initial="hidden"
      animate={isInView ? "show" : "hidden"}
    >
      {children.map((child, i) => (
        <motion.div key={i} variants={item}>
          {child}
        </motion.div>
      ))}
    </motion.div>
  )
}
