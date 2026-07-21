"use client";

import { useRef, ChangeEvent, MouseEvent } from "react";
import { motion } from "framer-motion";
import { UploadCloud, X } from "lucide-react";
import Lottie from "lottie-react";
import uploadAnimation from "./uploading.json"; // Lottie file path

interface FileUploadAreaProps {
  xlsxFile: File | null;
  setXlsxFile: (file: File | null) => void;
}

export default function FileUploadArea({ xlsxFile, setXlsxFile }: FileUploadAreaProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);

  const handleFileChange = (e: ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (file) setXlsxFile(file);
  };

  const removeFile = (e: MouseEvent<SVGSVGElement>) => {
    e.stopPropagation(); // Prevent event from bubbling up to the parent div
    setXlsxFile(null);
  };

  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.95 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ duration: 0.3 }}
      className="border-2 border-dashed rounded-xl p-6 relative mb-8 cursor-pointer hover:shadow-xl hover:bg-secondary/10 transition-all duration-300"
      onClick={() => fileInputRef.current?.click()}
    >
      <input
        type="file"
        onChange={handleFileChange}
        className="hidden"
        ref={fileInputRef}
        accept=".xlsx,.xls"
      />

      {!xlsxFile ? (
        <div className="flex flex-col items-center justify-center h-full min-h-[220px]">
          <UploadCloud className="w-12 h-12 text-gray-400 mb-4 animate-bounce" />
          <span className="text-gray-700 dark:text-gray-300 font-medium text-lg mb-2">
            Upload an Excel File
          </span>
          <span className="text-sm text-gray-500">
            Click or drag and drop to upload
          </span>
          <span className="text-xs text-gray-400 mt-2">Supported formats: .xlsx</span>
        </div>
      ) : (
        <div className="flex items-center justify-between px-4 py-2 bg-white dark:bg-gray-800 rounded-lg shadow-inner">
          <div className="flex items-center gap-3">
            <Lottie animationData={uploadAnimation} loop autoplay style={{ height: 50 }} />
            <div>
              <p className="text-sm font-medium text-gray-800 dark:text-gray-100">
                {xlsxFile.name}
              </p>
              <p className="text-xs text-gray-500">Ready to process</p>
            </div>
          </div>
          <X
            className="text-red-500 cursor-pointer hover:scale-110 transition-transform min-w-[44px] min-h-[44px] p-2 flex items-center justify-center rounded-lg hover:bg-red-50"
            onClick={removeFile}
          />
        </div>
      )}
    </motion.div>
  );
}
