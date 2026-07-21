import { fileURLToPath } from 'url';
import path from 'path';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

/** @type {import('next').NextConfig} */
const nextConfig = {
  output: 'standalone',
  turbopack: {},
  reactStrictMode: true,
  serverExternalPackages: ['@supabase/supabase-js'],
  async headers() {
    return [
      {
        source: '/:path*',
        headers: [
          { key: 'X-Frame-Options', value: 'DENY' },
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'X-XSS-Protection', value: '1; mode=block' },
          { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
          { key: 'Permissions-Policy', value: 'camera=(), microphone=(), geolocation=()' },
        ],
      },
    ]
  },
  experimental: {
    serverActions: {
      bodySizeLimit: '2mb',
    },
    optimizePackageImports: ['lucide-react', 'recharts', 'framer-motion', 'd3', 'date-fns'],
  },
  poweredByHeader: false,
  typescript: {
    ignoreBuildErrors: true,
  },
  transpilePackages: ['lucide-react'],
  images: {
    unoptimized: true,
    remotePatterns: [
      {
        protocol: 'https',
        hostname: 'lh3.googleusercontent.com',
      },
      {
        protocol: 'https',
        hostname: 'avatars.githubusercontent.com',
      },
      {
        protocol: 'https',
        hostname: 'cdn.sanity.io',
      },
      {
        protocol: 'https',
        hostname: 'images.unsplash.com',
      },
      {
        protocol: 'https',
        hostname: 'plus.unsplash.com',
      },
      {
        protocol: 'https',
        hostname: 'v0.dev',
      },
      {
        hostname: 'localhost',
      },
      {
        hostname: 'blob.v0.dev',
      },
    ],
  },
  // Enable environment variables to be available in the browser
  // NOTE: Only NEXT_PUBLIC_ prefixed vars are safe for the browser
  env: {
    NEXT_PUBLIC_SUPABASE_URL: process.env.NEXT_PUBLIC_SUPABASE_URL,
    NEXT_PUBLIC_SUPABASE_ANON_KEY: process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY,
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
  webpack: (config, { isServer }) => {
    // This is for the backend Python integration, ensure it's still needed and correct
    if (!isServer) {
      config.resolve.fallback = {
        ...config.resolve.fallback,
        fs: false,
        path: false,
        child_process: false,
      };

    }

    // Suppress all console logs in production (strip console.log, console.warn, console.error)
    if (process.env.NODE_ENV === 'production') {
      config.plugins.push({
        apply: (compiler) => {
          compiler.options.optimization = {
            ...compiler.options.optimization,
            minimize: true,
          };
        },
      });

      // Use Terser to remove console calls in production
      const terserPlugin = config.optimization.minimizer.find(
        (minimizer) => minimizer.constructor.name === 'TerserPlugin'
      );
      if (terserPlugin) {
        terserPlugin.options.terserOptions = {
          ...terserPlugin.options.terserOptions,
          compress: {
            ...terserPlugin.options.terserOptions.compress,
            drop_console: true,
            drop_debugger: true,
          },
        };
      }
    }

    return config;
  },
};

// Remove any devServer HTTPS configuration if it was here
// if (process.env.NODE_ENV === 'development') { ... }

export default nextConfig
