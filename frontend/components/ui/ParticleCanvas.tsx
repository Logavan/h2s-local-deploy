"use client"

import { useEffect, useRef, useCallback } from "react"

interface Particle {
  x: number
  y: number
  vx: number
  vy: number
  radius: number
  alpha: number
  color: string
}

interface ParticleCanvasProps {
  className?: string
  particleCount?: number
  connectionDistance?: number
  particleColor?: string
  speed?: number
}

export function ParticleCanvas({
  className = "",
  particleCount = 60,
  connectionDistance = 120,
  particleColor = "#06b6d4",
  speed = 0.3,
}: ParticleCanvasProps) {
  const canvasRef = useRef<HTMLCanvasElement>(null)
  const animationRef = useRef<number>(0)
  const particlesRef = useRef<Particle[]>([])
  const mouseRef = useRef({ x: -1000, y: -1000 })

  const initParticles = useCallback((width: number, height: number) => {
    const particles: Particle[] = []
    const colors = ["#06b6d4", "#22d3ee", "#0891b2", "#67e8f9", "#f59e0b"]

    for (let i = 0; i < particleCount; i++) {
      const radius = Math.random() * 2 + 0.5
      particles.push({
        x: Math.random() * width,
        y: Math.random() * height,
        vx: (Math.random() - 0.5) * speed,
        vy: (Math.random() - 0.5) * speed,
        radius,
        alpha: Math.random() * 0.5 + 0.2,
        color: colors[Math.floor(Math.random() * colors.length)],
      })
    }
    particlesRef.current = particles
  }, [particleCount, speed])

  const draw = useCallback((ctx: CanvasRenderingContext2D, width: number, height: number) => {
    ctx.clearRect(0, 0, width, height)

    const particles = particlesRef.current
    const mouse = mouseRef.current

    // Update and draw particles
    particles.forEach((p) => {
      // Mouse influence
      const dx = mouse.x - p.x
      const dy = mouse.y - p.y
      const dist = Math.sqrt(dx * dx + dy * dy)
      if (dist < 150) {
        const force = (150 - dist) / 150
        p.vx += dx * force * 0.0003
        p.vy += dy * force * 0.0003
      }

      // Velocity damping
      p.vx *= 0.99
      p.vy *= 0.99

      // Update position
      p.x += p.vx
      p.y += p.vy

      // Wrap around edges
      if (p.x < 0) p.x = width
      if (p.x > width) p.x = 0
      if (p.y < 0) p.y = height
      if (p.y > height) p.y = 0

      // Draw particle with glow
      const gradient = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, p.radius * 3)
      gradient.addColorStop(0, p.color + Math.round(p.alpha * 255).toString(16).padStart(2, "0"))
      gradient.addColorStop(1, "transparent")

      ctx.beginPath()
      ctx.arc(p.x, p.y, p.radius * 3, 0, Math.PI * 2)
      ctx.fillStyle = gradient
      ctx.fill()

      ctx.beginPath()
      ctx.arc(p.x, p.y, p.radius, 0, Math.PI * 2)
      ctx.fillStyle = p.color
      ctx.globalAlpha = p.alpha
      ctx.fill()
      ctx.globalAlpha = 1
    })

    // Draw connections
    for (let i = 0; i < particles.length; i++) {
      for (let j = i + 1; j < particles.length; j++) {
        const dx = particles[i].x - particles[j].x
        const dy = particles[i].y - particles[j].y
        const dist = Math.sqrt(dx * dx + dy * dy)

        if (dist < connectionDistance) {
          const opacity = (1 - dist / connectionDistance) * 0.3
          ctx.beginPath()
          ctx.moveTo(particles[i].x, particles[i].y)
          ctx.lineTo(particles[j].x, particles[j].y)
          ctx.strokeStyle = `rgba(6, 182, 212, ${opacity})`
          ctx.lineWidth = 0.5
          ctx.stroke()
        }
      }
    }

    // Mouse connection
    particles.forEach((p) => {
      const dx = mouse.x - p.x
      const dy = mouse.y - p.y
      const dist = Math.sqrt(dx * dx + dy * dy)

      if (dist < connectionDistance * 1.5) {
        const opacity = (1 - dist / (connectionDistance * 1.5)) * 0.4
        ctx.beginPath()
        ctx.moveTo(p.x, p.y)
        ctx.lineTo(mouse.x, mouse.y)
        ctx.strokeStyle = `rgba(6, 182, 212, ${opacity})`
        ctx.lineWidth = 0.8
        ctx.stroke()
      }
    })
  }, [connectionDistance])

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const ctx = canvas.getContext("2d")
    if (!ctx) return

    const resize = () => {
      canvas.width = canvas.offsetWidth
      canvas.height = canvas.offsetHeight
      initParticles(canvas.width, canvas.height)
    }

    resize()
    window.addEventListener("resize", resize)

    const handleMouseMove = (e: MouseEvent) => {
      const rect = canvas.getBoundingClientRect()
      mouseRef.current = {
        x: e.clientX - rect.left,
        y: e.clientY - rect.top,
      }
    }

    const handleMouseLeave = () => {
      mouseRef.current = { x: -1000, y: -1000 }
    }

    canvas.addEventListener("mousemove", handleMouseMove)
    canvas.addEventListener("mouseleave", handleMouseLeave)

    const animate = () => {
      if (ctx && canvas) {
        draw(ctx, canvas.width, canvas.height)
      }
      animationRef.current = requestAnimationFrame(animate)
    }

    animate()

    return () => {
      window.removeEventListener("resize", resize)
      canvas.removeEventListener("mousemove", handleMouseMove)
      canvas.removeEventListener("mouseleave", handleMouseLeave)
      cancelAnimationFrame(animationRef.current)
    }
  }, [draw, initParticles])

  return (
    <canvas
      ref={canvasRef}
      className={`absolute inset-0 w-full h-full ${className}`}
      style={{ pointerEvents: "none" }}
    />
  )
}
