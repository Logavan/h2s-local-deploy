"use client"
import { useState, useEffect, useCallback, useRef } from "react"
import Link from "next/link"
import { useRouter } from "next/navigation"
import { ArrowRight, Database, Network, Globe, Lock } from "lucide-react"

interface SliderProps {
  activeTab: "converter" | "mapper"
  setActiveTab: (tab: "converter" | "mapper") => void
}

const slides = [
  {
    id: 1,
    headline: "AI Agent for HANACV → SQL",
    subHeadline: "Accelerate Your Data Migration with Intelligent Automation",
    bulletPoints: ["Instant conversion of complex HANACV logic", "Optimized SQL output for maximum efficiency"],
    icon: Database,
    cta: "Start Converting",
    action: "converter",
  },
  {
    id: 2,
    headline: "Precision Analytics",
    subHeadline: "Deep Insights, Node-Level Intelligence",
    bulletPoints: ["Granular analysis for each conversion", "100% accuracy with optimized algorithms"],
    icon: Network,
    cta: "Explore Features",
    ctaLink: "/features",
  },
  {
    id: 3,
    headline: "Multi-Cloud Ready",
    subHeadline: "Convert Once, Deploy Anywhere",
    bulletPoints: ["Support for all major cloud platforms", "Seamless integration with your workflow"],
    icon: Globe,
    cta: "Try SQL Mapping",
    action: "mapper",
  },
  {
    id: 4,
    headline: "Data Privacy First",
    subHeadline: "Your Data, Your Control",
    bulletPoints: [
      "Your HANACV data is processed securely in-memory and never stored.",
      "Industry-standard encryption protects your data throughout the conversion process.",
    ],
    icon: Lock,
    cta: "See How We Protect",
    ctaLink: "/features#security",
  },
]

export default function Slider({ setActiveTab }: SliderProps) {
  const [current, setCurrent] = useState(0)
  const [isPaused, setIsPaused] = useState(false)
  const [touchStart, setTouchStart] = useState<number | null>(null)
  const [touchEnd, setTouchEnd] = useState<number | null>(null)
  const intervalRef = useRef<NodeJS.Timeout | null>(null)
  const sliderRef = useRef<HTMLDivElement>(null)
  const router = useRouter()
  const minSwipeDistance = 50

  // Touch handlers for mobile swipe
  const onTouchStart = (e: React.TouchEvent) => {
    setTouchEnd(null)
    setTouchStart(e.targetTouches[0].clientX)
  }

  const onTouchMove = (e: React.TouchEvent) => {
    setTouchEnd(e.targetTouches[0].clientX)
  }

  const onTouchEnd = () => {
    if (!touchStart || !touchEnd) return
    const distance = touchStart - touchEnd
    const isLeftSwipe = distance > minSwipeDistance
    const isRightSwipe = distance < -minSwipeDistance
    if (isLeftSwipe) nextSlide()
    if (isRightSwipe) prevSlide()
  }

  // Pause auto-advance when slider is not in view
  useEffect(() => {
    const observer = new IntersectionObserver(
      ([entry]) => {
        setIsPaused(!entry.isIntersecting)
      },
      { threshold: 0.1 }
    )

    if (sliderRef.current) {
      observer.observe(sliderRef.current)
    }

    return () => observer.disconnect()
  }, [])

  const nextSlide = useCallback(() => {
    setCurrent((prev) => (prev + 1) % slides.length)
  }, [])

  const prevSlide = useCallback(() => {
    setCurrent((prev) => (prev - 1 + slides.length) % slides.length)
  }, [])

  useEffect(() => {
    if (isPaused) {
      if (intervalRef.current) {
        clearInterval(intervalRef.current)
        intervalRef.current = null
      }
      return
    }

    intervalRef.current = setInterval(nextSlide, 5000)
    return () => {
      if (intervalRef.current) clearInterval(intervalRef.current)
    }
  }, [nextSlide, isPaused])

  const handleSlideAction = (slide: typeof slides[0]) => {
    if (slide.action === "converter") {
      setActiveTab("converter")
      router.replace("/")
    } else if (slide.action === "mapper") {
      setActiveTab("mapper")
      router.replace("/?tab=mapper")
    }
  }

  const CurrentIcon = slides[current].icon

  return (
    <div ref={sliderRef} className="w-full px-3 py-4 sm:px-4 sm:py-6 overflow-hidden">
      {/* Main Slider Container */}
      <div
        className="relative w-full rounded-2xl overflow-hidden bg-white border border-slate-200 shadow-lg touch-pan-y"
        onTouchStart={onTouchStart}
        onTouchMove={onTouchMove}
        onTouchEnd={onTouchEnd}
      >
        {/* Light gradient background */}
        <div className="absolute inset-0 bg-gradient-to-br from-white via-slate-50 to-amber-50" />

        {/* Decorative circles */}
        <div className="absolute top-0 right-0 w-32 h-32 sm:w-48 sm:h-48 md:w-64 md:h-64 bg-amber-400/10 rounded-full blur-3xl" />
        <div className="absolute bottom-0 left-0 w-24 h-24 sm:w-36 sm:h-36 md:w-48 md:h-48 bg-cyan-400/10 rounded-full blur-3xl" />

        {/* Mobile: stacked vertical layout | Tablet+: side by side */}
        <div className="relative min-h-[340px] sm:min-h-[360px] md:min-h-[380px] flex items-center">
          <div className="grid grid-cols-1 md:grid-cols-5 items-center w-full px-4 sm:px-8 md:px-16 py-5 sm:py-8 gap-5 sm:gap-6">
            {/* Text Content - full width on mobile, 2 columns on md+ */}
            <div className="col-span-1 md:col-span-3 flex flex-col order-2 md:order-1">
              {/* Badge */}
              <div className="hidden md:inline-flex items-center gap-2 px-3 py-1 bg-amber-100 rounded-full mb-3 sm:mb-4 w-fit">
                <CurrentIcon className="w-3 h-3 sm:w-4 sm:h-4 text-amber-600" />
                <span className="text-xs sm:text-sm font-medium text-amber-700">HANACV2SQL</span>
              </div>

              {/* Headline */}
              <h1 className="text-xl sm:text-2xl md:text-3xl lg:text-4xl font-bold text-slate-900 mb-2 sm:mb-3 leading-tight">
                {slides[current].headline}
              </h1>

              {/* Subheadline */}
              <p className="text-sm sm:text-base md:text-lg text-amber-600 font-medium mb-3 sm:mb-5">
                {slides[current].subHeadline}
              </p>

              {/* Bullet Points */}
              <ul className="space-y-1.5 sm:space-y-2 mb-4 sm:mb-6">
                {slides[current].bulletPoints.map((point, index) => (
                  <li key={index} className="flex items-start gap-2 sm:gap-3 text-slate-600 text-xs sm:text-sm md:text-base">
                    <span className="w-1.5 h-1.5 sm:w-2 sm:h-2 mt-1.5 sm:mt-1 rounded-full bg-gradient-to-r from-amber-400 to-amber-500 flex-shrink-0" />
                    <span className="leading-relaxed">{point}</span>
                  </li>
                ))}
              </ul>

              {/* CTA Button */}
              <div className="flex-shrink-0">
                {slides[current].action ? (
                  <button
                    onClick={() => handleSlideAction(slides[current])}
                    className="inline-flex items-center gap-2 px-4 sm:px-6 py-2.5 sm:py-3 min-h-[44px] bg-gradient-to-r from-amber-400 to-amber-500 text-slate-900 font-semibold rounded-lg hover:shadow-lg hover:shadow-amber-500/30 transition-all text-sm sm:text-base"
                  >
                    {slides[current].cta}
                    <ArrowRight className="w-4 h-4" />
                  </button>
                ) : (
                  <Link
                    href={slides[current].ctaLink || "#"}
                    className="inline-flex items-center gap-2 px-4 sm:px-6 py-2.5 sm:py-3 min-h-[44px] bg-gradient-to-r from-amber-400 to-amber-500 text-slate-900 font-semibold rounded-lg hover:shadow-lg hover:shadow-amber-500/30 transition-all text-sm sm:text-base"
                  >
                    {slides[current].cta}
                    <ArrowRight className="w-4 h-4" />
                  </Link>
                )}
              </div>
            </div>

            {/* Large Icon - full width on mobile, 2 columns on md+ */}
            <div className="col-span-1 md:col-span-2 flex justify-center order-1 md:order-2">
              <div className="relative">
                {/* Large glow behind icon */}
                <div className="absolute inset-0 bg-gradient-to-br from-amber-400/30 to-cyan-400/30 rounded-3xl blur-3xl transform scale-110" />
                {/* Icon Container */}
                <div className="relative w-28 h-28 sm:w-40 sm:h-40 md:w-56 md:h-56 rounded-2xl sm:rounded-3xl bg-gradient-to-br from-amber-50 to-cyan-50 border-2 border-amber-200/50 flex items-center justify-center shadow-inner">
                  <CurrentIcon className="w-14 h-14 sm:w-20 sm:h-20 md:w-32 md:h-32 text-amber-500" />
                </div>
              </div>
            </div>
          </div>
        </div>

        {/* Slide Indicators - bottom right corner */}
        <div className="absolute bottom-5 right-6 sm:bottom-8 sm:right-12 z-10">
          <div className="flex gap-2.5 items-center">
            {slides.map((_, index) => (
              <button
                key={index}
                onClick={() => setCurrent(index)}
                className={`h-1.5 rounded-full transition-all duration-300 ${
                  current === index
                    ? "w-8 bg-gradient-to-r from-amber-400 to-amber-600 shadow-[0_0_10px_rgba(251,191,36,0.5)]"
                    : "w-1.5 bg-slate-300 hover:bg-slate-400"
                }`}
                aria-label={`Go to slide ${index + 1}`}
              />
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
