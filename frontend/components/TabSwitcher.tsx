"use client"

import { motion } from "framer-motion"
import { Database, FileSpreadsheet, GitMerge } from "lucide-react"

type TabId = "converter" | "mapper" | "nested"

interface TabSwitcherProps {
  activeTab: TabId
  onTabChange: (tab: TabId) => void
}

export default function TabSwitcher({ activeTab, onTabChange }: TabSwitcherProps) {
  const handleTabClick = (tab: TabId) => {
    onTabChange(tab)
  }

  const tabs: { id: TabId; label: string; icon: React.ElementType }[] = [
    { id: "converter", label: "HANA CV Converter", icon: Database },
    { id: "mapper", label: "SQL/PySpark Mapping Engine", icon: FileSpreadsheet },
    { id: "nested", label: "Nested CV Flattener", icon: GitMerge },
  ]

  const activeIndex = tabs.findIndex(t => t.id === activeTab)

  return (
    <div className="tab-switcher-container w-full max-w-4xl mx-auto mb-8">
      {/* Glass container */}
      <div className="relative">
        {/* Background blur and glass effect */}
        <div className="absolute inset-0 bg-white/40 backdrop-blur-xl rounded-2xl border border-gray-200/50 shadow-lg" />

        {/* Tab container */}
        <div className="relative flex bg-gray-50/60 rounded-xl p-1.5 relative z-10">
          {/* Sliding indicator */}
          <motion.div
            className="absolute top-1.5 bottom-1.5 bg-gradient-to-r from-amber-400 to-amber-500 rounded-lg shadow-lg shadow-amber-500/30"
            initial={false}
            animate={{
              left: `${(100 / tabs.length) * activeIndex + 0.25}%`,
              width: `${100 / tabs.length - 0.5}%`,
            }}
            transition={{ type: "spring", stiffness: 400, damping: 35 }}
          />

          {tabs.map((tab) => (
            <button
              key={tab.id}
              onClick={() => handleTabClick(tab.id)}
              className={`relative flex-1 flex items-center justify-center py-3 sm:py-3.5 md:py-4 px-3 sm:px-4 text-center font-semibold transition-all duration-300 text-xs sm:text-sm md:text-base rounded-lg z-10 min-h-[44px] ${
                activeTab === tab.id
                  ? "text-gray-900"
                  : "text-gray-500 hover:text-gray-700"
              }`}
              aria-selected={activeTab === tab.id}
              role="tab"
            >
              {/* Active glow */}
              {activeTab === tab.id && (
                <motion.div
                  className="absolute inset-0 bg-gradient-to-r from-amber-400/20 to-amber-500/20 rounded-lg"
                  initial={{ opacity: 0 }}
                  animate={{ opacity: 1 }}
                  transition={{ duration: 0.3 }}
                />
              )}

              <span className="relative flex items-center gap-2">
                <tab.icon
                  className={`w-4 h-4 md:w-5 md:h-5 transition-all duration-300 ${
                    activeTab === tab.id
                      ? "text-amber-600"
                      : "text-gray-400 group-hover:text-amber-500"
                  }`}
                />
                <span className="hidden sm:inline">{tab.label}</span>
                <span className="sm:hidden">
                  {tab.id === "converter" ? "Converter" : tab.id === "mapper" ? "Mapper" : "Nested"}
                </span>
              </span>

              {/* Active indicator dot */}
              {activeTab === tab.id && (
                <motion.div
                  className="absolute -bottom-1 left-1/2 -translate-x-1/2 w-1 h-1 bg-amber-500 rounded-full"
                  layoutId="activeDot"
                  transition={{ type: "spring", stiffness: 500, damping: 30 }}
                />
              )}
            </button>
          ))}
        </div>

        {/* Decorative elements */}
        <div className="absolute -top-2 -right-2 w-8 h-8 bg-gradient-to-br from-amber-400/20 to-amber-500/10 rounded-full blur-sm" />
        <div className="absolute -bottom-1 -left-1 w-6 h-6 bg-gradient-to-br from-blue-400/10 to-blue-500/5 rounded-full blur-sm" />
      </div>
    </div>
  )
}
