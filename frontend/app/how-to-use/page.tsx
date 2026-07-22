"use client"

import { FileUp, Download, Database, Code2, CheckCircle, ArrowRight, HelpCircle, FileArchive, FileSpreadsheet } from "lucide-react"
import { useEffect } from "react"
import Link from "next/link"
import Image from "next/image"
import { motion } from "framer-motion"

const faqs = [
  {
    q: "What file formats are supported?",
    a: "Upload single .xml or .txt files, or a .zip containing multiple .xml/.txt files for bulk conversion.",
  },
  {
    q: "How do I create a ZIP file for bulk conversion?",
    a: "Select all your HANA XML files in File Explorer (Windows) or Finder (Mac), right-click, and choose 'Send to > Compressed (zipped) folder' or 'Compress'. Rename the ZIP to something meaningful, then upload it — the tool extracts and processes all .xml/.txt files inside automatically. Subfolders are also supported.",
  },
  {
    q: "Which platforms and output formats are supported?",
    a: "Generated SQL and PySpark code is compatible with BigQuery, Snowflake, Databricks, Amazon Redshift, and Microsoft Fabric. Choose SQL for traditional data warehouses or PySpark for Databricks and Spark-based environments.",
  },
  {
    q: "What does the mapping file do?",
    a: "The encrypted Excel mapping file lets you map HANA table/column names to your target system's schema before generating final SQL or PySpark code. Use it in the SQL/PySpark Mapping Engine to customize names for your specific platform.",
  },
  {
    q: "Can I generate PySpark instead of SQL?",
    a: "Yes. The SQL/PySpark Mapping Engine lets you choose SQL or PySpark as the output format. Select PySpark when targeting Databricks or other Spark-based platforms for optimized performance.",
  },
  {
    q: "Where are my conversions stored?",
    a: "All conversions are saved in your Account > Conversions page. You can re-download SQL/PySpark files and mapping sheets anytime.",
  },
  {
    q: "Are there any limitations to the conversion?",
    a: "Yes. Input parameters, Hierarchies, Currency Conversion, and UOM (Unit of Measurement) Translation require manual handling as these features vary significantly across platforms. These must be adapted separately in your target system after conversion.",
  },
  {
    q: "Does the tool convert only Calculation Views, or also Procedures and Table Functions?",
    a: "The tool is focused on Graphical Calculation Views, which are the most complex and time-consuming to migrate. Procedures and Table Functions are already script-based and closely align with standard SQL, making them straightforward to port or rewrite manually without needing a dedicated conversion tool.",
  },
]

export default function HowToUsePage() {
  useEffect(() => {
    if (window.location.hash) {
      const id = window.location.hash.substring(1)
      const element = document.getElementById(id)
      if (element) {
        setTimeout(() => {
          const headerHeight = 80
          const targetY = element.offsetTop - headerHeight
          window.scrollTo({ top: targetY, behavior: "smooth" })
          element.classList.add("highlight-section")
          setTimeout(() => {
            element.classList.remove("highlight-section")
          }, 2000)
        }, 100)
      }
    }
  }, [])

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="container mx-auto px-3 sm:px-4 py-6 sm:py-8 mt-16">
        <div className="max-w-4xl mx-auto">
          <h1 className="text-2xl sm:text-3xl md:text-4xl font-bold text-slate-900 mb-4 sm:mb-6 md:mb-8">
            How to Use Our Tools
          </h1>

          {/* HANA CV Converter Section */}
          <section
            id="hana-cv-to-sql-converter"
            className="bg-white rounded-lg shadow-lg p-4 sm:p-6 mb-4 sm:mb-6 md:mb-8 scroll-mt-24 transition-all duration-300"
          >
            <h2 className="text-xl sm:text-2xl font-semibold text-slate-900 mb-4 sm:mb-6 flex items-center">
              <Code2 className="w-5 h-5 sm:w-6 sm:h-6 mr-2 text-cyan-600" />
              HANA CV Converter
            </h2>

            <div className="space-y-4 sm:space-y-6">
              <div className="flex items-start space-x-3 sm:space-x-4">
                <div className="w-6 h-6 sm:w-8 sm:h-8 rounded-full bg-cyan-600/10 flex items-center justify-center flex-shrink-0">
                  <FileUp className="w-3.5 h-3.5 sm:w-5 sm:h-5 text-cyan-600" />
                </div>
                <div>
                  <h3 className="font-medium text-lg text-slate-900 mb-1 sm:mb-2">
                    Step 1: Get XML File from HANA Studio
                  </h3>
                  <p className="text-slate-600 text-base">
                    Export your HANA Calculation View as an XML file from SAP HANA Studio. Save the file with a .xml or .txt extension.
                  </p>
                  <div className="mt-4">
                    {/* TODO: Add MP4 video demo */}
                    <Image
                      src="/how_to_use/xml_extract_image.png"
                      alt="HANA Studio XML Export"
                      width={600}
                      height={300}
                      className="rounded-lg shadow-md"
                    />
                  </div>
                </div>
              </div>

              <div className="flex items-start space-x-3 sm:space-x-4">
                <div className="w-6 h-6 sm:w-8 sm:h-8 rounded-full bg-cyan-600/10 flex items-center justify-center flex-shrink-0">
                  <FileUp className="w-3.5 h-3.5 sm:w-5 sm:h-5 text-cyan-600" />
                </div>
                <div>
                  <h3 className="font-medium text-lg text-slate-900 mb-1 sm:mb-2">Step 2: Upload XML Files</h3>
                  <p className="text-slate-600 text-base">
                    Upload your View XML file using the file upload option. The tool accepts .xml and .txt file formats. For <strong>bulk conversion</strong>, upload a ZIP file and our AI Agent will extract and process all files in one go.
                  </p>

                  {/* ZIP Creation Instructions */}
                  <div className="mt-4 p-4 bg-blue-50 border border-blue-200 rounded-lg">
                    <h4 className="font-semibold text-slate-900 mb-3 flex items-center">
                      <FileArchive className="w-4 h-4 mr-2" />
                      How to Create a ZIP for Bulk Conversion
                    </h4>
                    <ol className="list-decimal list-inside space-y-2 text-slate-700 text-sm">
                      <li>Open the folder on your computer where your HANA XML files are saved.</li>
                      <li>Select all the .xml files you want to convert (hold <kbd className="px-1.5 py-0.5 bg-gray-200 rounded text-xs font-mono">Ctrl</kbd> / <kbd className="px-1.5 py-0.5 bg-gray-200 rounded text-xs font-mono">Cmd</kbd> + click to select multiple files).</li>
                      <li>Right-click any selected file and choose <strong>"Send to"</strong> → <strong>"Compressed (zipped) folder"</strong> (Windows) or <strong>"Compress"</strong> (Mac).</li>
                      <li>Rename the ZIP file to something meaningful (e.g., <code className="px-1 py-0.5 bg-gray-100 rounded text-xs">hana_views_batch1.zip</code>).</li>
                      <li>Upload the ZIP file — the tool will automatically extract and process all .xml and .txt files inside.</li>
                    </ol>
                    <div className="mt-3 flex flex-wrap gap-3 text-xs text-slate-600">
                      <span className="flex items-center">
                        <CheckCircle className="w-3 h-3 mr-1 text-green-500" />
                        Only .xml and .txt files are processed
                      </span>
                      <span className="flex items-center">
                        <CheckCircle className="w-3 h-3 mr-1 text-green-500" />
                        Subfolders inside ZIP are supported
                      </span>
                      <span className="flex items-center">
                        <CheckCircle className="w-3 h-3 mr-1 text-green-500" />
                        No file size limit per file
                      </span>
                    </div>
                  </div>
                  <div className="mt-4 grid grid-cols-1 sm:grid-cols-2 gap-4">
                    <div>
                      <p className="text-sm text-slate-500 mb-2 font-medium">Single file upload</p>
                      <Image
                        src="/how_to_use/single_file.png"
                        alt="Single file upload interface"
                        width={600}
                        height={300}
                        className="rounded-lg shadow-md"
                      />
                    </div>
                    <div>
                      <p className="text-sm text-slate-500 mb-2 font-medium">ZIP bulk upload</p>
                      <Image
                        src="/how_to_use/bulk_file.png"
                        alt="ZIP file contents for bulk upload"
                        width={600}
                        height={300}
                        className="rounded-lg shadow-md"
                      />
                    </div>
                  </div>
                </div>
              </div>

              <div className="flex items-start space-x-3 sm:space-x-4">
                <div className="w-6 h-6 sm:w-8 sm:h-8 rounded-full bg-cyan-600/10 flex items-center justify-center flex-shrink-0">
                  <FileArchive className="w-3.5 h-3.5 sm:w-5 sm:h-5 text-cyan-600" />
                </div>
                <div>
                  <h3 className="font-medium text-lg text-slate-900 mb-1 sm:mb-2">Step 3: Process Files</h3>
                  <p className="text-slate-600 text-base">
                    Click the Process button to start conversion. The tool validates your XML files, displays node count, then converts HANA Calculation View logic into standard SQL + metadata mapping. For bulk uploads (ZIP), all files are processed simultaneously with individual tracking.
                  </p>
                </div>
              </div>

            </div>

            {/* Try it now button */}
            <div className="mt-6">
              <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }} className="inline-block">
                <Link
                  href="/?tab=converter&scrollToTools=true"
                  className="inline-flex items-center px-4 py-2 bg-gradient-to-r from-amber-400 to-amber-500 text-slate-900 rounded-lg hover:from-amber-500 hover:to-amber-600 transition-colors"
                >
                  <span className="font-medium">Try HANA CV Converter</span>
                  <ArrowRight className="w-5 h-5 ml-2" />
                </Link>
              </motion.div>
            </div>

            {/* Workflow connection note */}
            <div className="mt-6 p-4 bg-amber-50 border border-amber-200 rounded-lg">
              <p className="text-sm text-amber-800">
                <strong>Tip:</strong> The metadata file generated by the HANA CV Converter is used as input in the SQL/PySpark Mapping Engine to customize table and column names for your target platform. Download it from your Account — Conversions page after conversion completes.
              </p>
            </div>
          </section>

          {/* SQL/PySpark Mapping Engine Section */}
          <section
            id="sql-mapping-engine"
            className="bg-white rounded-lg shadow-lg p-6 mb-4 sm:mb-6 md:mb-8 scroll-mt-24 transition-all duration-300"
          >
            <h2 className="text-xl sm:text-2xl font-semibold text-slate-900 mb-4 sm:mb-6 flex items-center">
              <Database className="w-5 h-5 sm:w-6 sm:h-6 mr-2 text-cyan-600" />
              SQL/PySpark Mapping Engine
            </h2>
            <p className="text-slate-600 text-base mb-6">
              Use the mapping metadata extracted from your HANA XML during conversion to customize table and column names for your target platform, then generate optimized SQL or PySpark code.
            </p>

            <div className="space-y-6">
              <div className="flex items-start space-x-4">
                <div className="w-8 h-8 rounded-full bg-cyan-600/10 flex items-center justify-center flex-shrink-0">
                  <Database className="w-5 h-5 text-cyan-600" />
                </div>
                <div>
                  <h3 className="font-medium text-lg text-slate-900 mb-2">Step 1: Choose Target Platform</h3>
                  <p className="text-slate-600 text-base">
                    Select your target data processing system — BigQuery, Snowflake, Databricks, Redshift, or Microsoft Fabric — to ensure the generated SQL or PySpark is optimized for your platform's dialect.
                  </p>
                  <div className="mt-4">
                    <Image
                      src="/how_to_use/select_platform.png"
                      alt="Select target platform"
                      width={600}
                      height={300}
                      className="rounded-lg shadow-md"
                    />
                  </div>
                </div>
              </div>

              <div className="flex items-start space-x-4">
                <div className="w-8 h-8 rounded-full bg-cyan-600/10 flex items-center justify-center flex-shrink-0">
                  <FileSpreadsheet className="w-5 h-5 text-cyan-600" />
                </div>
                <div>
                  <h3 className="font-medium text-lg text-slate-900 mb-2">Step 2: Select Mapping Metadata</h3>
                  <p className="text-slate-600 text-base">
                    Select mapping metadata extracted from your HANA XML during conversion. You can either upload the metadata file manually, or select it directly from your conversion history on the{" "}
                    <b>
                      <Link href="/account?tab=conversions" className="text-blue-600 hover:underline">
                        Account — Conversions
                      </Link>
                    </b>{" "}
                    page.
                  </p>
                  <div className="mt-4">
                    <Image
                      src="/how_to_use/select_mapping_metadata.png"
                      alt="Select mapping metadata file"
                      width={600}
                      height={300}
                      className="rounded-lg shadow-md"
                    />
                  </div>
                </div>
              </div>

              <div className="flex items-start space-x-4">
                <div className="w-8 h-8 rounded-full bg-cyan-600/10 flex items-center justify-center flex-shrink-0">
                  <Database className="w-5 h-5 text-cyan-600" />
                </div>
                <div>
                  <h3 className="font-medium text-lg text-slate-900 mb-2">Step 3: Edit Mapping Metadata</h3>
                  <p className="text-slate-600 text-base">
                    Update the table and column names in the mapping sheet to match your target system's schema. You can rename HANA source names to your preferred target names before generating the final SQL or PySpark code.
                  </p>
                  <div className="mt-4">
                    <Image
                      src="/how_to_use/edit_mapping_metadata.png"
                      alt="Edit mapping metadata"
                      width={600}
                      height={300}
                      className="rounded-lg shadow-md"
                    />
                  </div>
                </div>
              </div>

              <div className="flex items-start space-x-4">
                <div className="w-8 h-8 rounded-full bg-cyan-600/10 flex items-center justify-center flex-shrink-0">
                  <CheckCircle className="w-5 h-5 text-cyan-600" />
                </div>
                <div>
                  <h3 className="font-medium text-lg text-slate-900 mb-2">Step 4: Choose Output Format & Download</h3>
                  <p className="text-slate-600 text-base">
                    Select your preferred output format — SQL or PySpark — and download the generated code. The output is tuned to your chosen target platform's dialect.
                  </p>
                  <div className="mt-4">
                    <Image
                      src="/how_to_use/choose_output_format.png"
                      alt="Choose output format and download"
                      width={600}
                      height={300}
                      className="rounded-lg shadow-md"
                    />
                  </div>
                </div>
              </div>
            </div>

            {/* Try it now button */}
            <div className="mt-6">
              <motion.div whileHover={{ scale: 1.02 }} whileTap={{ scale: 0.98 }} className="inline-block">
                <Link
                  href="/?tab=mapper&scrollToTools=true"
                  className="inline-flex items-center px-4 py-2 bg-cyan-600 text-white rounded-lg hover:bg-cyan-600/90 transition-colors"
                >
                  <span className="font-medium">Try SQL/PySpark Mapping Engine</span>
                  <ArrowRight className="w-5 h-5 ml-2" />
                </Link>
              </motion.div>
            </div>
          </section>

          {/* FAQ Section */}
          <section className="bg-white rounded-lg shadow-lg p-6 mb-4 sm:mb-6 md:mb-8">
            <h2 className="text-xl sm:text-2xl font-semibold text-slate-900 mb-6 flex items-center">
              <HelpCircle className="w-5 h-5 sm:w-6 sm:h-6 mr-2 text-cyan-600" />
              Frequently Asked Questions
            </h2>
            <div className="space-y-4">
              {faqs.map((faq, i) => (
                <div key={i} className="border-b border-gray-100 last:border-0 pb-4 last:pb-0">
                  <h3 className="font-medium text-slate-900 mb-1">{faq.q}</h3>
                  <p className="text-slate-600 text-base">{faq.a}</p>
                </div>
              ))}
            </div>
          </section>

          {/* Pro Tips */}
          <div className="p-4 bg-cyan-600/10 rounded-lg">
            <h3 className="text-lg font-medium text-slate-900 mb-2">Pro Tips</h3>
            <ul className="list-disc list-inside text-slate-600 text-base space-y-2">
              <li>Always validate your XML files before uploading to ensure proper formatting.</li>
              <li>Name your XML files descriptively before zipping — file names appear in your conversion history for easy identification.</li>
              <li>Minor adjustments to the generated SQL may still be needed for edge cases.</li>
              <li>Download and review the mapping Excel sheet to understand table/column lineage before making changes.</li>
            </ul>
          </div>
        </div>
      </div>

      <style jsx global>{`
        .highlight-section {
          box-shadow: 0 0 0 4px rgba(59, 130, 246, 0.5);
          animation: pulse 2s ease-in-out;
        }
        @keyframes pulse {
          0% { box-shadow: 0 0 0 0 rgba(59, 130, 246, 0.7); }
          70% { box-shadow: 0 0 0 10px rgba(59, 130, 246, 0); }
          100% { box-shadow: 0 0 0 0 rgba(59, 130, 246, 0); }
        }
      `}</style>
    </div>
  )
}
