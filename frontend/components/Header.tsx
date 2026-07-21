"use client"

import React, { memo } from "react"
import { useState, useEffect, useRef } from "react"
import { Menu, X, PlayCircle, Layers, CreditCard, HelpCircle, ChevronDown, User, LogOut, History } from "lucide-react"
import Link from "next/link"
import { useRouter, usePathname } from "next/navigation"
import { motion, AnimatePresence, useMotionValue, useSpring } from "framer-motion"
import AnimatedLogo from "./AnimatedLogo"
import { useAuth } from "@/contexts/AuthContext"

const Header = memo(function Header() {
  const [isMenuOpen, setIsMenuOpen] = useState(false)
  const [hoveredItem, setHoveredItem] = useState<string | null>(null)
  const [isAccountDropdownOpen, setIsAccountDropdownOpen] = useState(false)
  const [isTransitioning, setIsTransitioning] = useState(false)
  const [scrolled, setScrolled] = useState(false)
  const accountBtnRef = useRef<HTMLButtonElement>(null)
  const dropdownRef = useRef<HTMLDivElement>(null)

  // Magnetic button effect state
  const magneticX = useMotionValue(0)
  const magneticY = useMotionValue(0)
  const springX = useSpring(magneticX, { stiffness: 300, damping: 30 })
  const springY = useSpring(magneticY, { stiffness: 300, damping: 30 })

  const { isLoggedIn, userName, userEmail, credits, signOut, loading } = useAuth()
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

  // Handle click outside to close dropdown
  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (
        isAccountDropdownOpen &&
        accountBtnRef.current &&
        dropdownRef.current && 
        !accountBtnRef.current.contains(event.target as Node) &&
        !dropdownRef.current.contains(event.target as Node)
      ) {
        setIsAccountDropdownOpen(false)
      }
    }

    document.addEventListener("mousedown", handleClickOutside)
    return () => {
      document.removeEventListener("mousedown", handleClickOutside)
    }
  }, [isAccountDropdownOpen])

  const menuItems = [
    {
      name: "How It Works",
      href: "/how-to-use",
      icon: PlayCircle,
      description: "Learn about our conversion process",
    },
    {
      name: "Features",
      href: "/features",
      icon: Layers,
      description: "Explore our powerful tool",
    },
    {
      name: "Pricing",
      href: "/pricing",
      icon: CreditCard,
      description: "Simple, transparent pricing",
    },
    {
      name: "Help Center",
      href: "/help-center",
      icon: HelpCircle,
      description: "Get support and resources",
    },
  ]

  const router = useRouter()
  const handleLogout = async () => {
    setIsTransitioning(true)
    setIsAccountDropdownOpen(false)

    try {
      await signOut()
      router.push("/")
    } catch (error) {
      console.error("Logout error:", error)
      setIsTransitioning(false)
      router.push("/")
    }
  }

  const toggleAccountDropdown = () => {
    console.log("Toggle dropdown clicked, current state:", !isAccountDropdownOpen)
    setIsAccountDropdownOpen(!isAccountDropdownOpen)
  }

  if (loading) {
    return (
      <header className="fixed top-0 left-0 right-0 z-50 transition-all duration-300">
        <div className="bg-white/80 backdrop-blur-xl border-b border-slate-200/50">
          <div className="container mx-auto px-4 md:px-6 max-w-[1400px]">
            <div className="flex items-center justify-between h-16">
              <Link href="/" className="flex items-center">
                <AnimatedLogo />
              </Link>
              <div className="flex items-center space-x-4">
                <div className="w-20 h-8 bg-slate-200 animate-pulse rounded-lg" />
                <div className="w-24 h-8 bg-slate-200 animate-pulse rounded-lg" />
              </div>
            </div>
          </div>
        </div>
      </header>
    )
  }

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
                            className="fixed left-1/2 -translate-x-1/2 top-[68px] w-72 p-4 bg-white rounded-2xl shadow-xl border border-slate-100 z-[9998]"
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

              {/* Account Section or Login/Signup Buttons */}
              <div className="flex items-center space-x-3 h-full">
                <div className="relative flex items-center h-full">
                  {isLoggedIn ? (
                    <>
                      <button
                        ref={accountBtnRef}
                        onClick={toggleAccountDropdown}
                        className={`
                          inline-flex h-10 items-center justify-center rounded-xl px-4
                          transition-all duration-300
                          ${isAccountDropdownOpen
                            ? "bg-amber-50 text-amber-700 border-2 border-amber-200"
                            : "bg-white text-slate-700 border-2 border-slate-200 hover:border-amber-300 hover:bg-amber-50"
                          }
                        `}
                        type="button"
                      >
                        <User className={`h-4 w-4 mr-2 ${isAccountDropdownOpen ? "text-amber-600" : "text-slate-500"}`} />
                        <span className="font-semibold text-sm">Account</span>
                        <ChevronDown
                          className={`ml-2 h-4 w-4 transition-transform duration-200 ${isAccountDropdownOpen ? "rotate-180 text-amber-600" : "text-slate-400"}`}
                        />
                      </button>

                      {/* Account Dropdown */}
                      {isAccountDropdownOpen && (
                        <div
                          ref={dropdownRef}
                          className="fixed right-4 md:right-8 lg:right-16 top-16 mt-2 w-[280px] sm:w-80 bg-white rounded-2xl shadow-2xl border border-slate-200 z-[999999] overflow-hidden"
                          style={{ minHeight: "100px" }}
                          onClick={(e) => e.stopPropagation()}
                        >
                          {/* User Info */}
                          <div className="p-5 bg-gradient-to-r from-amber-50 to-orange-50 border-b border-amber-100">
                            <div className="flex items-center gap-3">
                              <div className="w-12 h-12 bg-gradient-to-br from-amber-400 to-amber-600 rounded-xl flex items-center justify-center shadow-lg shadow-amber-500/20">
                                <User className="h-6 w-6 text-white" />
                              </div>
                              <div className="flex-1 min-w-0">
                                <h3 className="font-bold text-slate-900 truncate">{userName || "User"}</h3>
                                <div className="text-sm text-slate-500 truncate">
                                  {userEmail || ""}
                                </div>
                              </div>
                            </div>
                          </div>

                          {/* Credits */}
                          <div className="p-5 border-b border-slate-100">
                            <div className="flex items-center justify-between">
                              <div className="flex items-center gap-2">
                                <CreditCard className="h-4 w-4 text-amber-600" />
                                <span className="text-sm font-medium text-slate-700">Available Credits</span>
                              </div>
                              <span className="text-2xl font-bold text-amber-600">{credits}</span>
                            </div>
                          </div>

                          {/* Menu Items */}
                          <div className="p-2">
                            <Link
                              href="/account?tab=conversions"
                              className="flex items-center gap-3 px-4 py-3 rounded-xl text-slate-700 hover:bg-slate-50 hover:text-amber-700 transition-colors"
                              onClick={() => setIsAccountDropdownOpen(false)}
                            >
                              <History className="h-5 w-5 text-slate-400" />
                              <div className="flex flex-col">
                                <span className="font-medium">Conversion History</span>
                                <span className="text-xs text-slate-500">View your past conversions</span>
                              </div>
                            </Link>
                            <Link
                              href="/account"
                              className="flex items-center gap-3 px-4 py-3 rounded-xl text-slate-700 hover:bg-slate-50 hover:text-amber-700 transition-colors"
                              onClick={() => setIsAccountDropdownOpen(false)}
                            >
                              <User className="h-5 w-5 text-slate-400" />
                              <div className="flex flex-col">
                                <span className="font-medium">Account Settings</span>
                                <span className="text-xs text-slate-500">View your profile</span>
                              </div>
                            </Link>
                          </div>

                          {/* Logout */}
                          <div className="p-3 border-t border-slate-100">
                            <button
                              onClick={handleLogout}
                              className="w-full flex items-center justify-center gap-2 px-4 py-3 text-sm font-medium text-red-600 hover:bg-red-50 rounded-xl transition-colors"
                            >
                              <LogOut className="h-4 w-4" />
                              Sign Out
                            </button>
                          </div>
                        </div>
                      )}
                    </>
                  ) : (
                    <div className="flex items-center space-x-3">
                      <Link
                        href="/login"
                        className="inline-flex h-10 items-center justify-center rounded-xl px-5 border-2 border-slate-200 bg-white text-slate-700 font-medium text-sm transition-all duration-300 hover:border-slate-300 hover:bg-slate-50 hover:-translate-y-0.5"
                      >
                        Log in
                      </Link>
                      <Link
                        href="/signup"
                        className="inline-flex h-10 items-center justify-center rounded-xl px-5 bg-gradient-to-r from-amber-400 to-amber-600 text-slate-900 font-bold text-sm transition-all duration-300 hover:shadow-lg hover:shadow-amber-500/30 hover:-translate-y-0.5"
                      >
                        Get started
                      </Link>
                    </div>
                  )}
                </div>
              </div>
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
                  {isLoggedIn ? (
                    <div className="space-y-3">
                      <div className="flex items-center w-full p-4 rounded-xl bg-amber-50">
                        <User className="h-6 w-6 text-amber-600 mr-3" />
                        <div>
                          <h3 className="text-base font-semibold text-slate-900">{userName || "User"}</h3>
                          <div className="text-sm text-slate-500 truncate">{userEmail || ""}</div>
                        </div>
                      </div>
                      <div className="flex items-center w-full p-4 rounded-xl bg-amber-50">
                        <CreditCard className="h-6 w-6 text-amber-600 mr-3" />
                        <span className="text-lg font-bold text-amber-600">{credits} Credits</span>
                      </div>
                      <Link
                        href="/account?tab=conversions"
                        className="flex items-center w-full p-4 min-h-[48px] rounded-xl text-slate-700 hover:bg-slate-50"
                        onClick={() => setIsMenuOpen(false)}
                      >
                        <History className="h-6 w-6 mr-3 text-slate-400" />
                        <span className="text-base font-medium">Conversion History</span>
                      </Link>
                      <Link
                        href="/account"
                        className="flex items-center w-full p-4 min-h-[48px] rounded-xl text-slate-700 hover:bg-slate-50"
                        onClick={() => setIsMenuOpen(false)}
                      >
                        <User className="h-6 w-6 mr-3 text-slate-400" />
                        <span className="text-base font-medium">Account Settings</span>
                      </Link>
                      <button
                        onClick={handleLogout}
                        className="w-full flex items-center p-4 min-h-[48px] rounded-xl text-red-600 hover:bg-red-50"
                      >
                        <LogOut className="h-6 w-6 mr-3" />
                        <span className="text-base font-medium">Sign Out</span>
                      </button>
                    </div>
                  ) : (
                    <div className="space-y-3">
                      <Link
                        href="/login"
                        className="flex items-center justify-center w-full p-4 min-h-[48px] rounded-xl border-2 border-slate-200 text-slate-700 text-base font-medium"
                        onClick={() => setIsMenuOpen(false)}
                      >
                        Log in
                      </Link>
                      <Link
                        href="/signup"
                        className="flex items-center justify-center w-full p-4 min-h-[48px] rounded-xl bg-gradient-to-r from-amber-400 to-amber-600 text-slate-900 text-base font-bold"
                        onClick={() => setIsMenuOpen(false)}
                      >
                        Get started for free
                      </Link>
                    </div>
                  )}
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
