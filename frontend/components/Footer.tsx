"use client"

import type React from "react"
import { useState, useEffect } from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { Github, Linkedin, ArrowRight, CheckCircle, ArrowUpRight, Zap, Shield, Users, Mail } from "lucide-react"
import { subscribeToNewsletter } from "../app/actions/newsletter-actions"
import { useToastContext } from "./ui/toast-provider"
import { motion, useMotionValue, useSpring, AnimatePresence } from "framer-motion"

export default function Footer() {
  const router = useRouter()
  const [email, setEmail] = useState("")
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [subscriptionStatus, setSubscriptionStatus] = useState<"idle" | "success" | "error">("idle")
  const [errorMessage, setErrorMessage] = useState("")
  const [mounted, setMounted] = useState(false)
  const toast = useToastContext()

  // Magnetic effect for logo
  const logoX = useMotionValue(0)
  const logoY = useMotionValue(0)
  const logoXSpring = useSpring(logoX, { stiffness: 150, damping: 15 })
  const logoYSpring = useSpring(logoY, { stiffness: 150, damping: 15 })

  useEffect(() => {
    setMounted(true)
  }, [])

  const handleLogoMouseMove = (e: React.MouseEvent) => {
    const rect = e.currentTarget.getBoundingClientRect()
    const centerX = rect.left + rect.width / 2
    const centerY = rect.top + rect.height / 2
    logoX.set((e.clientX - centerX) * 0.3)
    logoY.set((e.clientY - centerY) * 0.3)
  }

  const handleLogoMouseLeave = () => {
    logoX.set(0)
    logoY.set(0)
  }

  const handleSubscribe = async (e: React.FormEvent) => {
    e.preventDefault()

    if (!email || !email.includes("@")) {
      setErrorMessage("Please enter a valid email address")
      setSubscriptionStatus("error")
      toast.error({
        title: "Invalid Email",
        description: "Please enter a valid email address",
      })
      return
    }

    setIsSubmitting(true)
    setSubscriptionStatus("idle")
    setErrorMessage("")

    try {
      const result = await subscribeToNewsletter(email)

      if (result.success) {
        setSubscriptionStatus("success")
        setEmail("")
        toast.success({
          title: "Subscription Successful",
          description: "Thank you for subscribing to our newsletter!",
        })
      } else {
        setSubscriptionStatus("error")
        setErrorMessage(result.message || "Failed to subscribe. Please try again.")
        toast.error({
          title: "Subscription Failed",
          description: result.message || "Failed to subscribe. Please try again.",
        })
      }
    } catch (error) {
      console.error("Error subscribing to newsletter:", error)
      setSubscriptionStatus("error")
      setErrorMessage("An unexpected error occurred. Please try again.")
      toast.error({
        title: "Error",
        description: "An unexpected error occurred. Please try again.",
      })
    } finally {
      setIsSubmitting(false)
    }
  }

  const handleNavigation = (path: string) => {
    router.push(path)
    setTimeout(() => {
      window.scrollTo({ top: 80, behavior: "smooth" }) // Account for fixed header
    }, 100)
  }

  const handleContactNavigation = () => {
    router.push("/help-center")
    setTimeout(() => {
      const emailSection = document.getElementById("email-support")
      if (emailSection) {
        const headerHeight = 80
        const targetY = emailSection.offsetTop - headerHeight
        window.scrollTo({ top: targetY, behavior: "smooth" })
      }
    }, 300)
  }

  type FooterLink = {
    label: string;
    href?: string;
    action?: () => void;
    icon: React.ComponentType<{ className?: string }> | null;
  }

  const footerLinks: {
    product: FooterLink[];
    company: FooterLink[];
    legal: FooterLink[];
  } = {
    product: [
      { label: "HANA CV Converter", href: "/?tab=converter&scrollToTools=true", icon: Zap },
      { label: "SQL/PySpark Mapping Engine", href: "/?tab=mapper&scrollToTools=true", icon: Shield },
      { label: "Features", href: "/features", icon: null },
      { label: "Pricing", href: "/pricing", icon: null },
    ],
    company: [
      { label: "About Us", href: "/#about-section", icon: null },
      { label: "Contact", href: "#", action: handleContactNavigation, icon: Mail },
      { label: "Help Center", href: "/help-center", icon: null },
      { label: "Blog", href: "/blog", icon: null },
    ],
    legal: [
      { label: "Terms & Conditions", href: "/terms-conditions", icon: null },
      { label: "Privacy Policy", href: "/privacy-policy", icon: null },
      { label: "Security", href: "/features#security", icon: null },
      { label: "Sitemap", href: "/site-map", icon: null },
    ],
  }

  const socialLinks = [
    { name: "YouTube", href: "https://www.youtube.com/@HANACV2SQL", icon: () => (
      <svg className="w-5 h-5" viewBox="0 0 24 24" fill="currentColor">
        <path d="M21.543 6.498c-.232-.86-.914-1.542-1.774-1.774C18.25 4 12 4 12 4s-6.25 0-7.769.724c-.86.232-1.542.914-1.774 1.774C2 8.001 2 12 2 12s0 3.999.724 5.502c.232.86.914 1.542 1.774 1.774C5.75 20 12 20 12 20s6.25 0 7.769-.724c.86-.232 1.542-.914 1.774-1.774C22 15.999 22 12 22 12s0-3.999-.457-5.502zM9.999 15.499V8.501L15.499 12l-5.5 3.499z"/>
      </svg>
    )},
    { name: "LinkedIn", href: "https://www.linkedin.com/company/hanacv2sql", icon: Linkedin },
    { name: "GitHub", href: "https://github.com/hanacv2sql", icon: Github },
  ]

  return (
    <footer className="relative bg-slate-100 border-t border-slate-200">
      {/* Background Elements */}
      <div className="absolute inset-0 pointer-events-none">
        {/* Gradient mesh */}
        <div className="absolute inset-0">
          <div className="absolute top-0 left-1/4 w-48 sm:w-64 md:w-96 lg:w-[500px] h-48 sm:h-64 md:h-96 lg:h-[500px] bg-amber-500/5 rounded-full blur-[150px]" />
          <div className="absolute bottom-0 right-1/4 w-48 sm:w-64 md:w-96 lg:w-[500px] h-48 sm:h-64 md:h-96 lg:h-[500px] bg-cyan-500/5 rounded-full blur-[150px]" />
          <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-64 sm:w-96 md:w-[500px] lg:w-[800px] h-64 sm:h-96 md:h-[500px] lg:h-[800px] bg-slate-200/30 rounded-full blur-[200px]" />
        </div>

        {/* Grid pattern */}
        <div
          className="absolute inset-0 opacity-[0.015]"
          style={{
            backgroundImage: `linear-gradient(rgba(0,0,0,1px), transparent 1px), linear-gradient(90deg, rgba(0,0,0,1px), transparent 1px)`,
            backgroundSize: "64px 64px",
          }}
        />

        {/* Top accent line with glow */}
        <div className="absolute top-0 left-0 right-0 h-px">
          <div className="absolute inset-0 bg-gradient-to-r from-transparent via-amber-500/50 to-transparent" />
          <div className="absolute inset-0 bg-gradient-to-r from-transparent via-amber-400/30 to-transparent blur-sm" />
        </div>
      </div>

      <div className="container mx-auto px-4 relative z-10">
        {/* Main Footer Content */}
        <div className="py-16 md:py-20">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-12 gap-12 lg:gap-8">
            {/* Brand Column */}
            <div className="lg:col-span-4">
              <motion.div
                initial={{ opacity: 0, y: 30 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true }}
                transition={{ duration: 0.7, ease: [0.16, 1, 0.3, 1] }}
              >
                {/* Logo with magnetic effect */}
                <motion.div
                  style={{ x: logoXSpring, y: logoYSpring }}
                  onMouseMove={handleLogoMouseMove}
                  onMouseLeave={handleLogoMouseLeave}
                  className="inline-flex"
                >
                  <Link href="/" className="inline-flex items-center gap-3 group">
                    <div className="relative">
                      <div className="w-11 h-11 bg-gradient-to-br from-amber-400 to-amber-600 rounded-xl flex items-center justify-center shadow-lg shadow-amber-500/30 group-hover:shadow-amber-500/50 transition-shadow duration-300">
                        <Zap className="w-6 h-6 text-white" />
                      </div>
                      {/* Glow ring */}
                      <div className="absolute inset-0 rounded-xl bg-amber-400/30 blur-lg opacity-0 group-hover:opacity-100 transition-opacity duration-300" />
                    </div>
                    <span className="text-2xl font-bold text-slate-900 tracking-tight">HANACV2SQL</span>
                  </Link>
                </motion.div>

                {/* Description */}
                <p className="text-slate-600 mb-8 leading-relaxed max-w-sm mt-6">
                  Convert HANA Calculation Views to optimized SQL. Supports BigQuery, Snowflake, Redshift, Databricks, and Microsoft Fabric.
                </p>

                {/* Social Links */}
                <div className="flex items-center gap-3">
                  {socialLinks.map((social, index) => (
                    <motion.a
                      key={social.name}
                      href={social.href}
                      target="_blank"
                      rel="noopener noreferrer"
                      className="relative p-3 min-w-[44px] min-h-[44px] rounded-xl bg-white border border-slate-200 flex items-center justify-center text-slate-500 hover:text-amber-600 hover:bg-amber-50 hover:border-amber-200 transition-all duration-300 group"
                      whileHover={{ y: -4, scale: 1.08 }}
                      whileTap={{ scale: 0.95 }}
                      initial={{ opacity: 0, y: 20 }}
                      whileInView={{ opacity: 1, y: 0 }}
                      viewport={{ once: true }}
                      transition={{ delay: index * 0.1 }}
                    >
                      <social.icon />
                      {/* Glow effect on hover */}
                      <div className="absolute inset-0 rounded-xl bg-amber-500/0 group-hover:bg-amber-50 transition-colors duration-300" />
                    </motion.a>
                  ))}
                </div>
              </motion.div>
            </div>

            {/* Links Columns */}
            <div className="lg:col-span-8">
              <div className="grid grid-cols-1 sm:grid-cols-3 gap-8 md:gap-12">
                {/* Product Links */}
                <motion.div
                  initial={{ opacity: 0, y: 30 }}
                  whileInView={{ opacity: 1, y: 0 }}
                  viewport={{ once: true }}
                  transition={{ delay: 0.1, duration: 0.7, ease: [0.16, 1, 0.3, 1] }}
                >
                  <h3 className="text-slate-900 font-semibold mb-5 flex items-center gap-3">
                    <span className="w-8 h-px bg-gradient-to-r from-amber-400 to-transparent" />
                    Product
                  </h3>
                  <ul className="space-y-3">
                    {footerLinks.product.map((link) => (
                      <li key={link.label}>
                        {link.action ? (
                          <button
                            onClick={link.action}
                            className="flex items-center gap-2 text-slate-600 hover:text-amber-600 transition-colors group"
                          >
                            {link.icon && <link.icon className="w-4 h-4 opacity-60 flex-shrink-0" />}
                            {link.label}
                          </button>
                        ) : link.href ? (
                          <Link
                            href={link.href}
                            className="flex items-center gap-2 text-slate-600 hover:text-amber-600 transition-colors group"
                          >
                            {link.icon && <link.icon className="w-4 h-4 opacity-60 flex-shrink-0" />}
                            {link.label}
                          </Link>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                </motion.div>

                {/* Company Links */}
                <motion.div
                  initial={{ opacity: 0, y: 30 }}
                  whileInView={{ opacity: 1, y: 0 }}
                  viewport={{ once: true }}
                  transition={{ delay: 0.2, duration: 0.7, ease: [0.16, 1, 0.3, 1] }}
                >
                  <h3 className="text-slate-900 font-semibold mb-5 flex items-center gap-3">
                    <span className="w-8 h-px bg-gradient-to-r from-amber-400 to-transparent" />
                    Company
                  </h3>
                  <ul className="space-y-3">
                    {footerLinks.company.map((link) => (
                      <li key={link.label}>
                        {link.action ? (
                          <button
                            onClick={link.action}
                            className="flex items-center gap-2 text-slate-600 hover:text-amber-600 transition-colors group"
                          >
                            {link.icon && <link.icon className="w-4 h-4 opacity-60 flex-shrink-0" />}
                            {link.label}
                          </button>
                        ) : link.href ? (
                          <Link
                            href={link.href}
                            className="flex items-center gap-2 text-slate-600 hover:text-amber-600 transition-colors group"
                          >
                            {link.icon && <link.icon className="w-4 h-4 opacity-60 flex-shrink-0" />}
                            {link.label}
                          </Link>
                        ) : null}
                      </li>
                    ))}
                  </ul>
                </motion.div>

                {/* Legal Links */}
                <motion.div
                  initial={{ opacity: 0, y: 30 }}
                  whileInView={{ opacity: 1, y: 0 }}
                  viewport={{ once: true }}
                  transition={{ delay: 0.3, duration: 0.7, ease: [0.16, 1, 0.3, 1] }}
                >
                  <h3 className="text-slate-900 font-semibold mb-5 flex items-center gap-3">
                    <span className="w-8 h-px bg-gradient-to-r from-amber-400 to-transparent" />
                    Legal
                  </h3>
                  <ul className="space-y-3">
                    {footerLinks.legal.map((link) => (
                      link.href ? (
                      <li key={link.label}>
                        <Link
                          href={link.href}
                          className="flex items-center gap-2 text-slate-600 hover:text-amber-600 transition-colors group"
                        >
                          {link.icon && <link.icon className="w-4 h-4 opacity-60 flex-shrink-0" />}
                          {link.label}
                        </Link>
                      </li>
                      ) : null
                    ))}
                  </ul>
                </motion.div>
              </div>
            </div>
          </div>

          {/* Newsletter Section */}
          <motion.div
            initial={{ opacity: 0, y: 30 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ delay: 0.4, duration: 0.7, ease: [0.16, 1, 0.3, 1] }}
            className="mt-16 pt-12 border-t border-slate-200"
          >
            <div className="flex flex-col lg:flex-row items-start lg:items-center justify-between gap-8">
              {/* Newsletter Info */}
              <div className="max-w-md">
                <h3 className="text-xl font-bold text-slate-900 mb-2">Stay Updated</h3>
                <p className="text-slate-600 text-sm">
                  Subscribe for the latest updates, features, and migration tips.
                </p>
              </div>

              {/* Newsletter Form */}
              <form onSubmit={handleSubscribe} className="w-full lg:w-auto">
                <div className="flex flex-col sm:flex-row gap-3 w-full">
                  <div className="relative w-full min-w-0">
                    <input
                      type="email"
                      value={email}
                      onChange={(e) => setEmail(e.target.value)}
                      placeholder="Enter your email"
                      className="w-full px-4 sm:px-5 py-3 bg-white border border-slate-200 rounded-xl text-slate-900 placeholder:text-slate-400 transition-all duration-300 focus:outline-none focus:ring-2 focus:ring-amber-500/50 focus:border-amber-500/50 text-sm sm:text-base"
                      aria-label="Email address"
                      disabled={isSubmitting || subscriptionStatus === "success"}
                      required
                    />
                    {/* Focus glow animation */}
                    <motion.div
                      className="absolute inset-0 rounded-xl pointer-events-none opacity-0 transition-opacity duration-200"
                      animate={{ opacity: email ? 0.5 : 0 }}
                      style={{
                        boxShadow: "0 0 25px rgba(245, 158, 11, 0.25)",
                      }}
                    />
                  </div>
                  <motion.button
                    type="submit"
                    disabled={isSubmitting || subscriptionStatus === "success"}
                    className={`
                      px-8 py-3.5 rounded-xl font-semibold transition-all duration-300 flex items-center justify-center gap-2
                      ${
                        subscriptionStatus === "success"
                          ? "bg-green-500/20 text-green-400 border border-green-500/30"
                          : "bg-gradient-to-r from-amber-400 to-amber-500 text-slate-900 hover:shadow-xl hover:shadow-amber-500/30"
                      }
                      disabled:opacity-50 disabled:cursor-not-allowed
                    `}
                    whileHover={{ scale: subscriptionStatus === "success" ? 1 : 1.02 }}
                    whileTap={{ scale: subscriptionStatus === "success" ? 1 : 0.98 }}
                  >
                    {isSubmitting ? (
                      <div className="w-5 h-5 border-2 border-current border-t-transparent rounded-full animate-spin" />
                    ) : subscriptionStatus === "success" ? (
                      <>
                        <CheckCircle className="w-5 h-5" />
                        <span>Subscribed</span>
                      </>
                    ) : (
                      <>
                        <span>Subscribe</span>
                        <ArrowRight className="w-4 h-4" />
                      </>
                    )}
                  </motion.button>
                </div>
                <AnimatePresence>
                  {subscriptionStatus === "error" && (
                    <motion.p
                      className="mt-2 text-sm text-red-400"
                      initial={{ opacity: 0, y: -10 }}
                      animate={{ opacity: 1, y: 0 }}
                      exit={{ opacity: 0, y: -10 }}
                    >
                      {errorMessage}
                    </motion.p>
                  )}
                </AnimatePresence>
              </form>
            </div>
          </motion.div>
        </div>

        {/* Bottom Bar */}
        <div className="py-6 border-t border-slate-200">
          <div className="flex flex-col md:flex-row items-center justify-between gap-4">
            <p className="text-slate-500 text-sm">
              &copy; {new Date().getFullYear()} HANACV2SQL. All rights reserved.
            </p>
            <div className="flex items-center gap-2">
              <span className="text-xs text-slate-500">Built with</span>
              <span className="inline-flex items-center gap-1 text-xs text-slate-500">
                <Zap className="w-3.5 h-3.5 text-amber-500" />
                AI-Powered Conversion
              </span>
            </div>
          </div>
        </div>
      </div>
    </footer>
  )
}
