"use client"

import { useRouter, usePathname } from "next/navigation"
import { ArrowLeft } from "lucide-react"
import { motion } from "framer-motion"
import Link from "next/link"

interface BackButtonProps {
  href?: string
  label?: string
  className?: string
}

export default function BackButton({ href, label = "Back to Home", className = "" }: BackButtonProps) {
  const router = useRouter()
  const pathname = usePathname()

  // List of main menu pages where the back button should not appear
  const menuPages = [
    "/",
    "/features",
    "/how-it-works",
    "/pricing",
    "/plans-pricing",
    "/help-center",
    "/documentation",
    "/video-tutorials",
  ]

  // Don't render the back button on menu pages
  if (menuPages.includes(pathname)) {
    return null
  }

  const handleClick = () => {
    if (href) {
      // If href is provided, use it
      return
    } else {
      // Otherwise go back in history
      router.back()
    }
  }

  return (
    <motion.div
      className="" // Removed padding-top and margin-top to ensure it's below the header
    >
      <motion.button
        onClick={handleClick}
        className={`inline-flex items-center text-primary hover:text-secondary transition-colors min-h-[44px] min-w-[44px] ${className}`}
        whileHover={{ x: -3 }}
        whileTap={{ scale: 0.97 }}
      >
        <ArrowLeft className="w-5 h-5 mr-2" />
        {href ? (
          <Link href={href} className="font-medium text-sm md:text-base">
            {label}
          </Link>
        ) : (
          <span className="font-medium text-sm md:text-base">{label}</span>
        )}
      </motion.button>
    </motion.div>
  )
}
