"use client"

interface SimpleTabsProps {
  activeTab: string
  onTabChange: (tab: string) => void
}

export default function SimpleTabs({ activeTab, onTabChange }: SimpleTabsProps) {
  // Function to handle tab click with console logging
  const handleTabClick = (tab: string) => {
    console.log(`Tab clicked: ${tab}`)
    onTabChange(tab)
  }

  return (
    <div className="w-full max-w-4xl mx-auto mb-8 rounded-lg overflow-hidden shadow-md">
      <div className="flex flex-col md:flex-row">
        <button
          onClick={() => handleTabClick("converter")}
          className={`flex-1 w-full md:w-auto py-4 px-6 text-center font-medium transition-all duration-200 ${
            activeTab === "converter" ? "bg-white text-primary" : "bg-gray-100 text-gray-600 hover:bg-gray-200"
          }`}
          data-testid="converter-tab"
        >
          HANA CV Converter
        </button>
        <button
          onClick={() => handleTabClick("mapper")}
          className={`flex-1 w-full md:w-auto py-4 px-6 text-center font-medium transition-all duration-200 ${
            activeTab === "mapper" ? "bg-white text-primary" : "bg-gray-100 text-gray-600 hover:bg-gray-200"
          }`}
          data-testid="mapper-tab"
        >
          SQL/PySpark Mapping Engine
        </button>
      </div>
    </div>
  )
}
