"use client"

import React, { memo } from "react"
import { useState, useEffect } from "react"
import { Menu, X, PlayCircle, ChevronDown } from "lucide-react"
import Link from "next/link"
import { usePathname } from "next/navigation"
import { motion, AnimatePresence, useMotionValue, useSpring } from "framer-motion"
import AnimatedLogo from "./AnimatedLogo"

const Header = memo(function Header() {
  const [isMenuOpen, setIsMenuOpen] = useState(false)
  const [hoveredItem, setHoveredItem] = useState<string | null>(null)
  const [scrolled, setScrolled] = useState(false)

  // Magnetic button effect state
  const magneticX = useMotionValue(0)
  const magneticY = useMotionValue(0)
  const springX = useSpring(magneticX, { stiffness: 300, damping: 30 })
  const springY = useSpring(magneticY, { stiffness: 300, damping: 30 })

  const pathname = usePathname()

  // Close mobile menu on route change
  useEffect(() => {
    setIsMenuOpen(false)
  }, [pathname])

  // Handle scroll effect
  useEffect(() => {
    const handleScroll = () => {
      setScrolled(window.scrollY > 20)
    }
    window.addEventListener("scroll", handleScroll)
    return () => window.removeEventListener("scroll", handleScroll)
  }, [])

  const menuItems = [
    {
      name: "How It Works",
      href: "/how-to-use",
      icon: PlayCircle,
      description: "Learn about our conversion process",
    },
  ]

  return (
    <>
      <header
        className={`
          fixed top-0 left-0 right-0 z-[9999] transition-all duration-500
          ${scrolled
            ? "bg-white/95 backdrop-blur-xl shadow-lg shadow-slate-200/30 border-b border-slate-200/30"
            : "bg-white/80 backdrop-blur-md border-b border-transparent"
          }
        `}
      >
        <div className="container mx-auto px-4 md:px-6 max-w-[1400px]">
          <div className="flex items-center justify-between h-16 sm:h-16">
            {/* Logo */}
            <div className="flex items-center">
              <Link href="/" className="flex items-center relative">
                <div className="h-10 sm:h-12 md:h-12 w-auto flex items-center justify-center">
                  <AnimatedLogo />
                </div>
              </Link>
            </div>

            {/* Mobile menu button */}
            <div className="flex items-center md:hidden">
              <motion.button
                onClick={() => setIsMenuOpen(!isMenuOpen)}
                className="p-2 min-h-[44px] min-w-[44px] rounded-xl hover:bg-slate-100 transition-colors inline-flex items-center gap-2"
                aria-label={isMenuOpen ? "Close menu" : "Open menu"}
                aria-expanded={isMenuOpen}
                whileTap={{ scale: 0.95 }}
              >
                {isMenuOpen ? (
                  <>
                    <X className="h-6 w-6 md:h-5 md:w-5 text-slate-700" />
                    <span className="text-slate-700 text-base md:text-sm font-medium">Close</span>
                  </>
                ) : (
                  <>
                    <Menu className="h-6 w-6 md:h-5 md:w-5 text-slate-700" />
                    <span className="text-slate-700 text-base md:text-sm font-medium">Menu</span>
                  </>
                )}
              </motion.button>
            </div>

            {/* Desktop Navigation */}
            <div className="hidden md:flex items-center justify-end flex-1 h-full">
              <nav className="flex items-center mr-6 lg:mr-8 h-full">
                {menuItems.map((item) => {
                  const Icon = item.icon
                  return (
                    <div
                      key={item.name}
                      className="relative h-full flex items-center"
                      onMouseEnter={() => setHoveredItem(item.name)}
                      onMouseLeave={() => setHoveredItem(null)}
                    >
                      <Link
                        href={item.href}
                        className={`
                          flex items-center px-3 lg:px-4 py-2 rounded-xl group transition-all duration-300
                          ${hoveredItem === item.name
                            ? "text-amber-700 bg-amber-50"
                            : "text-slate-600 hover:text-slate-900 hover:bg-slate-50"
                          }
                        `}
                      >
                        <Icon
                          className={`
                            w-4 h-4 mr-2 transition-colors
                            ${hoveredItem === item.name ? "text-amber-600" : "text-slate-400 group-hover:text-slate-600"}
                          `}
                        />
                        <span className="text-sm font-medium">{item.name}</span>
                        <motion.div
                          animate={{ rotate: hoveredItem === item.name ? 180 : 0 }}
                          transition={{ duration: 0.2 }}
                        >
                          <ChevronDown
                            className={`
                              w-4 h-4 ml-1 transition-colors
                              ${hoveredItem === item.name ? "text-amber-600" : "text-slate-400"}
                            `}
                          />
                        </motion.div>
                      </Link>

                      {/* Dropdown Preview */}
                      <AnimatePresence>
                        {hoveredItem === item.name && (
                          <motion.div
                            initial={{ opacity: 0, y: -8, scale: 0.96 }}
                            animate={{ opacity: 1, y: 0, scale: 1 }}
                            exit={{ opacity: 0, y: -8, scale: 0.96 }}
                            transition={{ type: "spring", stiffness: 400, damping: 25 }}
                            className="absolute left-0 top-full mt-2 w-72 p-4 bg-white rounded-2xl shadow-xl border border-slate-100 z-[9998]"
                          >
                            <div className="flex items-start gap-3 p-3 rounded-xl hover:bg-slate-50 transition-colors">
                              <div className={`
                                w-10 h-10 rounded-xl flex items-center justify-center
                                ${hoveredItem === item.name ? "bg-amber-100" : "bg-slate-100"}
                              `}>
                                <Icon className={`w-5 h-5 ${hoveredItem === item.name ? "text-amber-600" : "text-slate-600"}`} />
                              </div>
                              <div>
                                <div className="font-semibold text-slate-900">{item.name}</div>
                                <div className="text-sm text-slate-500 mt-0.5">{item.description}</div>
                              </div>
                            </div>
                          </motion.div>
                        )}
                      </AnimatePresence>
                    </div>
                  )
                })}
              </nav>
            </div>
          </div>
        </div>

        {/* Mobile Menu Backdrop */}
        <AnimatePresence>
          {isMenuOpen && (
            <>
              <motion.div
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.2 }}
                className="fixed inset-0 top-16 bg-gray-900/30 backdrop-blur-md z-40 md:hidden"
                onClick={() => setIsMenuOpen(false)}
              />
              <motion.div
                initial={{ opacity: 0, y: -20 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0, y: -20 }}
                transition={{ type: "spring", stiffness: 400, damping: 30 }}
                className="fixed top-16 left-0 right-0 z-50 md:hidden bg-white/95 backdrop-blur-xl border-t border-slate-100 shadow-xl overflow-x-hidden"
              >
              <nav className="container mx-auto px-4 py-6 space-y-2 overflow-x-hidden">
                {menuItems.map((item) => {
                  const Icon = item.icon
                  return (
                    <Link
                      key={item.name}
                      href={item.href}
                      className="flex items-center w-full p-4 min-h-[48px] rounded-xl text-slate-700 hover:bg-slate-50 hover:text-amber-700 transition-colors"
                      onClick={() => setIsMenuOpen(false)}
                    >
                      <Icon className="w-6 h-6 mr-3 text-slate-400" />
                      <span className="text-lg font-medium">{item.name}</span>
                    </Link>
                  )
                })}

                <div className="pt-4 border-t border-slate-100 mt-4 overflow-x-hidden">
                  {/* Enterprise edition — no login/account needed */}
                </div>
              </nav>
              </motion.div>
            </>
          )}
        </AnimatePresence>
      </header>

      {/* Spacer for fixed header */}
      <div className="h-16" />

      {/* Hide scrollbar styles */}
      <style jsx>{`
        .scrollbar-hide::-webkit-scrollbar {
          display: none;
        }
        .scrollbar-hide {
          -ms-overflow-style: none;
          scrollbar-width: none;
        }
      `}</style>
    </>
  )
})

export default Header
