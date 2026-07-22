"use client"
import { useState, useRef, useEffect } from "react"
import { motion, AnimatePresence } from "framer-motion"
import ConversionTool from "@/components/ConversionTool"
import MappingTool from "@/components/MappingTool"
import TabSwitcher from "@/components/TabSwitcher"
import { useAuth } from "@/contexts/AuthContext"
import { useSearchParams } from "next/navigation"
import { HorizontalReviewsCarousel } from "@/components/HorizontalReviewsCarousel"
import ComparisonChart from "@/components/ComparisonChart"
import Features from "@/components/Features"
import Benefits from "@/components/Benefits"
import CallToAction from "@/components/CallToAction"
import FAQSection from "@/components/FAQSection"
import Integrations from "@/components/Integrations"
import AboutUsSection from "@/components/AboutUsSection"
import UseCases from "@/components/UseCases"
import ContactSection from "@/components/ContactSection"
import WorkflowSteps from "@/components/WorkflowSteps"

// Simple section wrapper without scroll animations
function SimpleSection({ children, className = "", id }: { children: React.ReactNode; className?: string; id?: string }) {
  return (
    <section id={id} className={`relative ${className}`}>
      {children}
    </section>
  )
}

// Section divider
function SectionDivider() {
  return (
    <div className="relative py-8">
      <div className="absolute left-1/2 top-1/2 -translate-x-1/2 -translate-y-1/2 w-px h-12" style={{ background: "linear-gradient(to bottom, transparent, rgba(6,182,212,0.2), transparent)" }} />
    </div>
  )
}

export default function HomeClient() {
    const [activeTab, setActiveTab] = useState<string>("converter")
    const { loading } = useAuth()
    const searchParams = useSearchParams()
    const scrollTimeoutRef = useRef<NodeJS.Timeout | null>(null)
    const isScrollingRef = useRef<boolean>(false)

    useEffect(() => {
        if (loading) return

        const tab = searchParams?.get("tab")

        if (tab === "mapper") {
            setActiveTab("mapper")
        } else {
            setActiveTab("converter")
        }
    }, [searchParams, loading])

    const scrollToTools = () => {
        const toolsSection = document.getElementById("tools-section")
        if (!toolsSection) return

        const headerHeight = 80
        const targetY = toolsSection.offsetTop - headerHeight

        window.scrollTo({ top: targetY, behavior: "instant" })
    }

    // Cleanup on unmount
    useEffect(() => {
        return () => {
            if (scrollTimeoutRef.current) {
                clearTimeout(scrollTimeoutRef.current)
            }
        }
    }, [])

    const handleTabChange = (tab: string) => {
        if (tab === activeTab) {
            // Same tab clicked - just scroll to top
            scrollToTools()
            return
        }

        setActiveTab(tab)
        scrollToTools()
    }

    if (loading) {
        return (
            <div className="min-h-screen flex flex-col items-center justify-center bg-gradient-to-br from-slate-50 to-slate-100">
                <div className="w-16 h-16 border-4 border-cyan-400/30 border-t-cyan-400 rounded-full animate-spin" />
                <p className="mt-6 text-slate-600 font-medium">Loading HANACV2SQL...</p>
            </div>
        )
    }

    return (
        <>
            <main className="w-full overflow-x-hidden">
                    {/* Tools Section */}
                    <div id="tools-section" className="container mx-auto px-4 py-8" style={{ scrollMarginTop: '80px' }}>
                        <TabSwitcher activeTab={activeTab as "converter" | "mapper"} onTabChange={handleTabChange} />
                        <AnimatePresence mode="wait">
                            <motion.div
                                key={activeTab}
                                initial={{ opacity: 0, y: 10 }}
                                animate={{ opacity: 1, y: 0 }}
                                exit={{ opacity: 0, y: -10 }}
                                transition={{ duration: 0.2 }}
                            >
                                {activeTab === "converter" ? <ConversionTool /> : <MappingTool />}
                            </motion.div>
                        </AnimatePresence>
                    </div>

                    {/* Workflow Steps */}
                    <SimpleSection>
                        <WorkflowSteps />
                    </SimpleSection>

                    <SectionDivider />

                    {/* Comparison Chart */}
                    <SimpleSection>
                        <ComparisonChart />
                    </SimpleSection>

                    <SectionDivider />

                    {/* Integrations */}
                    <SimpleSection>
                        <Integrations />
                    </SimpleSection>

                    <SectionDivider />

                    {/* Features */}
                    <SimpleSection>
                        <Features />
                    </SimpleSection>

                    {/* Benefits */}
                    <SimpleSection>
                        <Benefits />
                    </SimpleSection>

                    {/* Call to Action */}
                    <SimpleSection>
                        <CallToAction />
                    </SimpleSection>

                    <SectionDivider />

                    {/* FAQ */}
                    <SimpleSection>
                        <FAQSection />
                    </SimpleSection>

                    <SectionDivider />

                    {/* Reviews */}
                    <SimpleSection>
                        <HorizontalReviewsCarousel />
                    </SimpleSection>

                    <SectionDivider />

                    {/* About */}
                    <SimpleSection id="about-section">
                        <AboutUsSection />
                    </SimpleSection>

                    <SectionDivider />

                    {/* Use Cases */}
                    <SimpleSection>
                        <UseCases />
                    </SimpleSection>

                    <SectionDivider />

                    {/* Contact */}
                    <SimpleSection>
                        <ContactSection />
                    </SimpleSection>
                </main>
        </>
    )
}
