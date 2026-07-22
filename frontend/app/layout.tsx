import type React from "react"
import type { Metadata, Viewport } from "next"
import { Syne, Space_Mono } from "next/font/google"
import "../styles/globals.css"
import { ThemeProvider } from "@/components/theme-provider"
import { AuthProvider } from "@/contexts/AuthContext"
import Header from "@/components/Header"

import ScrollProgress from "@/components/ScrollProgress"
import { ToastProvider } from "@/components/ui/toast-provider"
import Script from "next/script"
import { ConnectionManager } from "@/components/ConnectionManager"
import { ClientFloatingSupportButton } from "@/components/ClientFloatingSupportButton"

// Premium typography: Syne for headings and UI, Space Mono for technical accents
const syne = Syne({
  subsets: ["latin"],
  display: "swap",
  preload: true,
  variable: "--font-heading",
  fallback: ["system-ui", "sans-serif"],
  weight: ["400", "500", "600", "700", "800"],
})

const spaceMono = Space_Mono({
  subsets: ["latin"],
  display: "swap",
  preload: false,
  variable: "--font-mono",
  fallback: ["monospace"],
  weight: ["400", "700"],
})

export const metadata: Metadata = {
  title: "HANACV2SQL | AI-Powered HANA Calculation View to SQL Conversion",
  description: "Convert SAP HANA Calculation Views to optimized SQL for BigQuery, Snowflake, Redshift, Databricks, and Microsoft Fabric.",
  icons: {
    icon: [{ url: "/favicon.ico" }],
    shortcut: "/favicon.ico",
  },
}

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
}

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode
}>) {
  return (
    <html lang="en" className="overflow-x-hidden overflow-y-scroll" suppressHydrationWarning>
      <head>
        {/* Add critical CSS inline to prevent layout shifts */}
        <style
          dangerouslySetInnerHTML={{
            __html: `
          :root {
            --font-sans: var(--font-heading), system-ui, sans-serif;
            --font-mono: var(--font-mono), monospace;
          }

          body {
            margin: 0;
            padding: 0;
            font-family: var(--font-sans);
            overflow-x: hidden;
            width: 100%;
            -webkit-font-smoothing: antialiased;
            -moz-osx-font-smoothing: grayscale;
          }

          /* Subtle grid pattern for backgrounds */
          .grid-pattern {
            background-image:
              linear-gradient(to right, rgba(0,0,0,0.03) 1px, transparent 1px),
              linear-gradient(to bottom, rgba(0,0,0,0.03) 1px, transparent 1px);
            background-size: 24px 24px;
          }

          .slider-container {
            width: 100%;
            aspect-ratio: 16/5;
            max-height: 500px;
            position: relative;
          }

          @media (max-width: 768px) {
            .slider-container {
              aspect-ratio: 16/9;
            }
          }

          /* Prevent content layout shifts */
          img {
            max-width: 100%;
            height: auto;
          }

          /* Ensure content doesn't cause horizontal overflow */
          .container {
            width: 100%;
            max-width: 100%;
            overflow-x: hidden;
          }

          /* Geometric decoration animation */
          @keyframes float {
            0%, 100% { transform: translateY(0) rotate(0deg); }
            50% { transform: translateY(-10px) rotate(2deg); }
          }

          @keyframes pulse-glow {
            0%, 100% { opacity: 0.4; }
            50% { opacity: 0.8; }
          }

          .animate-float {
            animation: float 6s ease-in-out infinite;
          }

          .animate-float-delayed {
            animation: float 6s ease-in-out infinite;
            animation-delay: -3s;
          }

          .animate-pulse-glow {
            animation: pulse-glow 3s ease-in-out infinite;
          }
        `,
          }}
        />
      </head>
      <body className={`${syne.variable} ${spaceMono.variable} font-sans overflow-x-hidden`}>
        {/* Script to prevent Flash of Unstyled Content and unregister service workers */}
        <Script id="prevent-fouc-and-sw-unregister" strategy="beforeInteractive">{`
          (function() {
            // Add a class to indicate JS is loaded
            document.documentElement.classList.add('js-loaded');
            
            // Store the viewport dimensions
            const viewportWidth = window.innerWidth;
            document.documentElement.style.setProperty('--viewport-width', \`\${viewportWidth}px\`);
            
            // Prevent layout shifts from font loading
            document.documentElement.classList.add('font-loaded');

            // Unregister any existing service workers to prevent 404 for sw.js
            if ('serviceWorker' in navigator) {
              navigator.serviceWorker.getRegistrations().then(function(registrations) {
                for (let registration of registrations) {
                  registration.unregister().then(function(success) {
                    if (success) {
                      console.log('Service Worker unregistered successfully:', registration.scope);
                    } else {
                      console.warn('Service Worker unregistration failed:', registration.scope);
                    }
                  }).catch(function(error) {
                    console.error('Error unregistering Service Worker:', error);
                  });
                }
              });
              // Also try to clear caches associated with service workers
              caches.keys().then(function(cacheNames) {
                return Promise.all(
                  cacheNames.map(function(cacheName) {
                    console.log('Deleting cache:', cacheName);
                    return caches.delete(cacheName);
                  })
                );
              });
            }
          })();
        `}</Script>

        <ThemeProvider attribute="class" defaultTheme="light" enableSystem>
          <ScrollProgress />
          <AuthProvider>
            <ToastProvider>
              <ConnectionManager />
              <div className="flex flex-col min-h-screen w-full overflow-x-hidden" style={{ position: "relative" }}>
                <Header />
                <main className="flex-grow w-full overflow-x-hidden relative" style={{ isolation: "isolate" }}>{children}</main>
              </div>
              <ClientFloatingSupportButton />
            </ToastProvider>
          </AuthProvider>
        </ThemeProvider>

        {/* Script to ensure images are properly sized */}
        <Script id="image-dimensions" strategy="afterInteractive">{`
          (function() {
            // Set explicit dimensions for all images that don't have them
            const images = document.querySelectorAll('img:not([width]):not([height])');
            images.forEach(img => {
              if (img.naturalWidth && img.naturalHeight) {
                img.width = img.naturalWidth;
                img.height = img.naturalHeight;
              }
            });
          })();
        `}</Script>
      </body>
    </html>
  )
}




/* // This file is part of the HANACV2SQL project. */