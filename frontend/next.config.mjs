import { fileURLToPath } from 'url';
import path from 'path';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  reactStrictMode: true,

  // ---------------------------------------------------------------------------
  // Production hardening — minimize what's shipped to the browser.
  // ---------------------------------------------------------------------------
  productionBrowserSourceMaps: false,   // do NOT ship source maps
  poweredByHeader: false,               // remove X-Powered-By: Next.js
  generateBuildId: async () => process.env.H2S_BUILD_ID || `build-${Date.now()}`,

  serverExternalPackages: [],
  transpilePackages: ['lucide-react'],

  typescript: { ignoreBuildErrors: true },

  experimental: {
    serverActions: { bodySizeLimit: '2mb' },
    optimizePackageImports: ['lucide-react', 'recharts', 'framer-motion', 'd3', 'date-fns'],
  },

  // ---------------------------------------------------------------------------
  // Security headers — defense in depth against XSS, clickjacking, MIME sniffing
  // ---------------------------------------------------------------------------
  async headers() {
    const csp = [
      "default-src 'self'",
      // Next.js inline styles require 'unsafe-inline' on most setups; keep tight
      "style-src 'self' 'unsafe-inline'",
      // No inline JS — Next.js bundles are external scripts after build
      "script-src 'self'",
      "img-src 'self' data: blob:",
      "connect-src 'self' " + (process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8080'),
      "font-src 'self' data:",
      "frame-ancestors 'none'",
      "base-uri 'self'",
      "form-action 'self'",
    ].join('; ');

    return [
      {
        // Apply tight CSP to the HTML responses only
        source: '/:path*\\.html',
        headers: [
          { key: 'Content-Security-Policy', value: csp },
        ],
      },
      {
        // Looser CSP for the API rewrites + static assets (Next needs eval in dev)
        source: '/:path((?!_next/static|_next/image|favicon).)*',
        headers: [
          { key: 'X-Frame-Options', value: 'DENY' },
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'X-XSS-Protection', value: '1; mode=block' },
          { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
          { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=()' },
          { key: 'Strict-Transport-Security', value: 'max-age=31536000; includeSubDomains' },
        ],
      },
    ];
  },

  images: {
    unoptimized: true,
    remotePatterns: [
      { protocol: 'https', hostname: 'lh3.googleusercontent.com' },
      { protocol: 'https', hostname: 'avatars.githubusercontent.com' },
      { hostname: 'localhost' },
      { hostname: 'blob.v0.dev' },
    ],
  },

  env: {
    NEXT_PUBLIC_API_BASE_URL: process.env.NEXT_PUBLIC_API_BASE_URL,
  },

  async rewrites() {
    // Next.js server-side rewrite for /api/* - only used by SSR/edge code
    // (browser fetch() calls bypass this entirely and use lib/config.ts).
    //
    // Priority:
    //   1. BACKEND_UPSTREAM_URL - Docker-network URL for SSR
    //      (e.g. http://backend:8080 in compose)
    //   2. NEXT_PUBLIC_API_BASE_URL - same-origin deployments
    //   3. localhost:8080 - last-resort dev default
    const backendUpstream =
      process.env.BACKEND_UPSTREAM_URL ||
      process.env.NEXT_PUBLIC_API_BASE_URL ||
      'http://localhost:8080';
    return [
      {
        source: '/api/:path*',
        destination: `${backendUpstream}/api/:path*`,
      },
    ];
  },

  // ---------------------------------------------------------------------------
  // Turbopack — Next.js 16 default bundler.
  // Browser-only Node polyfills (fs / path / child_process) are auto-stubbed,
  // SWC handles minification natively, and source maps are produced by default
  // in dev. The previous webpack block (Terser drop_console + bundle analyzer)
  // was removed because Turbopack does not expose those hooks. Re-add via
  // `turbopack.rules` / `turbopack.minify` only if a concrete need appears.
  // ---------------------------------------------------------------------------
  turbopack: {},
};

export default nextConfig;