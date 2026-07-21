"use client"

import type React from "react"
import { useEffect, useRef } from "react"

const AnimatedLogo: React.FC = () => {
  const canvasRef = useRef<HTMLCanvasElement>(null)

  useEffect(() => {
    const canvas = canvasRef.current
    if (!canvas) return

    const ctx = canvas.getContext("2d")
    if (!ctx) return

    // Account for device pixel ratio for crisp rendering on high-DPI displays
    const dpr = window.devicePixelRatio || 1
    const isMobile = window.innerWidth < 640 // sm breakpoint
    const baseWidth = 240
    const baseHeight = isMobile ? 60 : 56
    canvas.width = baseWidth * dpr
    canvas.height = baseHeight * dpr
    canvas.style.width = `${baseWidth}px`
    canvas.style.height = `${baseHeight}px`
    ctx.scale(dpr, dpr)

    const drawStar = (x: number, y: number, size: number, opacity: number) => {
      ctx.save()
      ctx.translate(x, y)
      ctx.rotate(Math.PI / 4)
      ctx.beginPath()
      for (let i = 0; i < 5; i++) {
        ctx.lineTo(0, size)
        ctx.translate(0, size)
        ctx.rotate((Math.PI * 2) / 5)
        ctx.lineTo(0, -size)
        ctx.translate(0, -size)
        ctx.rotate(-((Math.PI * 6) / 5))
      }
      ctx.closePath()
      ctx.fillStyle = `rgba(246, 177, 0, ${opacity})` // Using theme secondary color
      ctx.fill()
      ctx.restore()
    }

    const stars = Array.from({ length: 5 }, () => ({
      x: Math.random() * baseWidth,
      y: Math.random() * baseHeight,
      size: Math.random() * 3 + 1.5, // Slightly larger stars
      speed: Math.random() * 0.5 + 0.1,
      opacity: Math.random() * 0.4 + 0.6, // Higher opacity for visibility
    }))

    let glowIntensity = 0
    let glowIncreasing = true

    const animate = () => {
      ctx.clearRect(0, 0, baseWidth, baseHeight)

      // Scale font sizes for mobile
      const fontSize = isMobile ? 26 : 28 // Larger font for mobile readability
      const sqlFontSize = isMobile ? 30 : 32 // Larger SQL font for mobile
      const productTextSize = isMobile ? 11 : 10 // Larger product text for mobile
      const yPosition = isMobile ? 28 : 28 // Adjusted vertical position
      const spacing = isMobile ? 10 : 12 // Adjusted spacing between HANACV and 2
      const sqlSpacing = isMobile ? 4 : 6 // Reduced spacing before SQL on mobile

      // Draw static HANACV text
      ctx.font = `900 ${fontSize}px Arial` // Extra bold for better visibility
      ctx.textAlign = "left"
      ctx.textBaseline = "middle"
      ctx.fillStyle = "#374151" // Darker gray for better contrast on mobile
      ctx.fillText("HANACV", 10, yPosition)

      // Measure HANACV text width
      const hanacvWidth = ctx.measureText("HANACV").width

      // Draw static 2
      ctx.font = `900 ${fontSize}px Arial` // Extra bold
      ctx.fillStyle = "#374151" // Darker gray
      ctx.fillText("2", hanacvWidth + spacing, yPosition)

      // Measure 2 text width
      const twoWidth = ctx.measureText("2").width

      // Draw animated SQL text
      ctx.font = `900 ${sqlFontSize}px Arial` // Extra bold for mobile visibility

      // Glow effect
      if (glowIncreasing) {
        glowIntensity += 0.02
        if (glowIntensity >= 1) glowIncreasing = false
      } else {
        glowIntensity -= 0.02
        if (glowIntensity <= 0) glowIncreasing = true
      }

      const sqlGradient = ctx.createLinearGradient(
        hanacvWidth + twoWidth + spacing + sqlSpacing,
        yPosition,
        baseWidth,
        yPosition,
      )
      // Brighter gradient for mobile visibility
      sqlGradient.addColorStop(0, `rgba(246, 177, 0, ${0.9 + glowIntensity * 0.1})`)
      sqlGradient.addColorStop(0.5, `rgba(255, 190, 0, ${0.95 + glowIntensity * 0.05})`)
      sqlGradient.addColorStop(1, `rgba(246, 177, 0, ${0.9 + glowIntensity * 0.1})`)

      ctx.shadowColor = "rgba(246, 177, 0, 0.7)"
      ctx.shadowBlur = isMobile ? 10 : 8 // Stronger glow on mobile
      ctx.fillStyle = sqlGradient

      // Animate SQL text with correct spacing
      const sqlLetters = "SQL"
      const sqlWidth = ctx.measureText(sqlLetters).width
      const letterS = ctx.measureText("S").width
      const letterQ = ctx.measureText("Q").width
      const letterL = ctx.measureText("L").width
      const totalLetterWidth = letterS + letterQ + letterL
      const letterSpacing = (sqlWidth - totalLetterWidth) / 2

      let sqlX = hanacvWidth + twoWidth + spacing + sqlSpacing

      const animationAmount = isMobile ? 1.5 : 2 // Adjusted animation movement
      ctx.fillText("S", sqlX, yPosition + Math.sin(Date.now() / 500) * animationAmount)
      sqlX += letterS + letterSpacing
      ctx.fillText("Q", sqlX, yPosition + Math.sin(Date.now() / 500 + 1) * animationAmount)
      sqlX += letterQ + letterSpacing
      ctx.fillText("L", sqlX, yPosition + Math.sin(Date.now() / 500 + 2) * animationAmount)

      // Reset shadow for stars
      ctx.shadowColor = "transparent"
      ctx.shadowBlur = 0

      // Animate stars
      stars.forEach((star) => {
        star.y -= star.speed
        if (star.y < 0) {
          star.y = baseHeight
          star.x = Math.random() * baseWidth
        }
        drawStar(star.x, star.y, star.size, star.opacity)
      })

      // Calculate total logo width for centering the product text
      const totalLogoWidth = hanacvWidth + spacing + twoWidth + sqlSpacing + sqlWidth
      const startX = 10 // Starting X position of HANACV

      // Add the "A LuViRa Product" text centered under the logo
      ctx.font = `300 ${productTextSize}px Arial` // Light weight for subtle text
      ctx.fillStyle = "#F6B100" // Keep light amber color
      ctx.textAlign = "center"
      const productText = "A CodesKit Product"
      ctx.fillText(productText, startX + totalLogoWidth / 2, yPosition + (isMobile ? 22 : 24))

      requestAnimationFrame(animate)
    }

    animate()

    // Handle window resize
    const handleResize = () => {
      const dpr = window.devicePixelRatio || 1
      const isMobile = window.innerWidth < 640
      const baseWidth = 240
      const baseHeight = isMobile ? 60 : 56
      canvas.width = baseWidth * dpr
      canvas.height = baseHeight * dpr
      canvas.style.width = `${baseWidth}px`
      canvas.style.height = `${baseHeight}px`
      ctx.scale(dpr, dpr)
    }

    window.addEventListener("resize", handleResize)

    return () => {
      window.removeEventListener("resize", handleResize)
    }
  }, [])

  return <canvas ref={canvasRef} className="h-10 sm:h-12 md:h-12 w-auto" /> // Consistent with Header container
}

export default AnimatedLogo
