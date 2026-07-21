"use client"

import { useEffect, useRef, useState } from "react"
import { motion, useSpring, useTransform } from "framer-motion"
import { useInView } from "framer-motion"

interface AnimatedCounterProps {
  value: number
  duration?: number
  prefix?: string
  suffix?: string
  className?: string
  decimals?: number
}

export function AnimatedCounter({
  value,
  duration = 2,
  prefix = "",
  suffix = "",
  className = "",
  decimals = 0,
}: AnimatedCounterProps) {
  const ref = useRef<HTMLSpanElement>(null)
  const isInView = useInView(ref, { once: true, margin: "-50px" })
  const [displayValue, setDisplayValue] = useState(0)

  const spring = useSpring(0, {
    stiffness: 50,
    damping: 20,
    restDelta: 0.001,
  })

  const display = useTransform(spring, (latest) =>
    decimals > 0 ? latest.toFixed(decimals) : Math.round(latest).toLocaleString()
  )

  useEffect(() => {
    if (isInView) {
      spring.set(value)
    }
  }, [isInView, spring, value])

  useEffect(() => {
    return display.on("change", (v) => {
      setDisplayValue(v as unknown as number)
    })
  }, [display])

  return (
    <motion.span ref={ref} className={`inline-block ${className}`}>
      {prefix}
      {displayValue.toLocaleString(undefined, {
        minimumFractionDigits: decimals,
        maximumFractionDigits: decimals,
      })}
      {suffix}
    </motion.span>
  )
}
