import { fileURLToPath } from 'url';
import path from 'path';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

/** @type {import('next').AppRunnerEnvType, import('next').NextConfig} */
const isProd = process.env.NODE_ENV === 'production';

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
    return [
      {
        source: '/api/:path*',
        destination: `${process.env.NEXT_PUBLIC_API_BASE_URL || 'http://localhost:8080'}/api/:path*`,
      },
    ];
  },

  // ---------------------------------------------------------------------------
  // Webpack — strip debug code in production
  // ---------------------------------------------------------------------------
  webpack: (config, { isServer, dev }) => {
    if (!isServer) {
      config.resolve.fallback = {
        ...config.resolve.fallback,
        fs: false,
        path: false,
        child_process: false,
      };
    }

    if (isProd) {
      // Strip source maps from output
      config.devtool = false;

      // Use Terser to remove console calls + debug statements in production
      const terserPlugin = config.optimization?.minimizer?.find?.(
        (minimizer) => minimizer.constructor.name === 'TerserPlugin'
      );
      if (terserPlugin) {
        terserPlugin.options.terserOptions = {
          ...terserPlugin.options.terserOptions,
          compress: {
            ...terserPlugin.options.terserOptions.compress,
            drop_console: true,
            drop_debugger: true,
            passes: 2,
          },
          mangle: { toplevel: true },
          format: { comments: false },
        };
      }
    } else {
      // Faster dev rebuilds — use eval source maps
      config.devtool = 'eval-cheap-module-source-map';
    }

    return config;
  },

  // ---------------------------------------------------------------------------
  // Bundle analyzer — run `ANALYZE=true npm run build` to dump report.html
  // ---------------------------------------------------------------------------
  ...(process.env.ANALYZE === 'true' && {
    webpack: (config) => {
      // Lazy-load to avoid bundling in production images
      const { BundleAnalyzerPlugin } = require('webpack-bundle-analyzer');
      config.plugins.push(
        new BundleAnalyzerPlugin({
          analyzerMode: 'static',
          reportFilename: '../bundle-report.html',
          openAnalyzer: false,
        })
      );
      return config;
    },
  }),
};

export default nextConfig;