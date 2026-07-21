/** @type {import('tailwindcss').Config} */
module.exports = {
  darkMode: ["class"],
  content: [
    "./pages/**/*.{js,ts,jsx,tsx,mdx}",
    "./components/**/*.{js,ts,jsx,tsx,mdx}",
    "./app/**/*.{js,ts,jsx,tsx,mdx}",
    "*.{js,ts,jsx,tsx,mdx}",
  ],
  theme: {
    extend: {
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        secondary: {
          DEFAULT: "hsl(var(--secondary))",
          foreground: "hsl(var(--secondary-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        popover: {
          DEFAULT: "hsl(var(--popover))",
          foreground: "hsl(var(--popover-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        // Premium accent colors
        cyan: {
          400: "#22d3ee",
          500: "#06b6d4",
          600: "#0891b2",
        },
        gold: {
          400: "#fbbf24",
          500: "#f59e0b",
        },
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 2px)",
        sm: "calc(var(--radius) - 4px)",
        xl: "1rem",
        "2xl": "1.5rem",
        "3xl": "2rem",
      },
      fontFamily: {
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
        display: ["var(--font-display)", "system-ui", "sans-serif"],
        mono: ["var(--font-mono)", "monospace"],
      },
      animation: {
        // Premium spring-physics animations
        "progress-indeterminate": "progress-indeterminate 1.5s ease-in-out infinite",
        "gradient-x": "gradient-x 15s ease infinite",
        bounce: "bounce 1s infinite",
        pulse: "pulse 2s cubic-bezier(0.4, 0, 0.6, 1) infinite",
        float: "float 3s ease-in-out infinite",
        "float-delayed": "float-delayed 3.5s ease-in-out infinite",
        "float-slow": "float-slow 4s ease-in-out infinite",
        "float-delayed-slow": "float-delayed-slow 4.5s ease-in-out infinite",
        "file-upload-entry": "fadeIn 0.5s ease-out, scaleIn 0.5s ease-out",
        "file-upload-pulse": "pulseBorder 2s infinite ease-in-out",

        // Premium animations
        "spin-slow": "spin 20s linear infinite",
        "spin-slower": "spin 30s linear infinite",
        "spin-slow-reverse": "spin 25s linear infinite reverse",
        shimmer: "shimmer 2s linear infinite",
        "fade-in-up": "fadeInUp 0.6s cubic-bezier(0.16, 1, 0.3, 1) forwards",
        "fade-in-scale": "fadeInScale 0.5s cubic-bezier(0.16, 1, 0.3, 1) forwards",
        "slide-in-right": "slideInRight 0.6s cubic-bezier(0.16, 1, 0.3, 1) forwards",
        "slide-in-left": "slideInLeft 0.6s cubic-bezier(0.16, 1, 0.3, 1) forwards",
        "glow-pulse": "glowPulse 2s ease-in-out infinite",
        "float-3d": "float3d 6s ease-in-out infinite",
        "rotate-ring": "rotateRing 8s linear infinite",
        "pulse-ring": "pulseRing 2s ease-out infinite",
        "particle-drift": "particleDrift 20s linear infinite",
        "counter-tick": "counterTick 0.1s ease-out forwards",
        "gradient-shift": "gradientShift 8s ease infinite",
        "morph-blob": "morphBlob 8s ease-in-out infinite",
        "draw-line": "drawLine 1.5s cubic-bezier(0.16, 1, 0.3, 1) forwards",
        "reveal-text": "revealText 0.8s cubic-bezier(0.16, 1, 0.3, 1) forwards",
        "stagger-in": "staggerIn 0.5s cubic-bezier(0.16, 1, 0.3, 1) forwards",
        "breathing": "breathing 4s ease-in-out infinite",
        "scan": "scan 3s linear infinite",
      },
      keyframes: {
        // Base fade/scale
        fadeIn: {
          from: { opacity: "0", transform: "translateY(10px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        pulseBorder: {
          "0%": { "border-color": "#d1d5db" },
          "50%": { "border-color": "#a7a7a7" },
          "100%": { "border-color": "#d1d5db" },
        },
        scaleIn: {
          from: { transform: "scale(0.95)", opacity: "0.7" },
          to: { transform: "scale(1)", opacity: "1" },
        },
        // Premium keyframes
        shimmer: {
          "0%": { backgroundPosition: "-200% 0" },
          "100%": { backgroundPosition: "200% 0" },
        },
        fadeInUp: {
          from: { opacity: "0", transform: "translateY(30px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        fadeInScale: {
          from: { opacity: "0", transform: "scale(0.92)" },
          to: { opacity: "1", transform: "scale(1)" },
        },
        slideInRight: {
          from: { opacity: "0", transform: "translateX(40px)" },
          to: { opacity: "1", transform: "translateX(0)" },
        },
        slideInLeft: {
          from: { opacity: "0", transform: "translateX(-40px)" },
          to: { opacity: "1", transform: "translateX(0)" },
        },
        glowPulse: {
          "0%, 100%": { opacity: "0.4", transform: "scale(1)" },
          "50%": { opacity: "0.8", transform: "scale(1.05)" },
        },
        float3d: {
          "0%, 100%": { transform: "translateY(0) rotateX(0deg) rotateY(0deg)" },
          "25%": { transform: "translateY(-15px) rotateX(2deg) rotateY(-2deg)" },
          "50%": { transform: "translateY(-8px) rotateX(-1deg) rotateY(1deg)" },
          "75%": { transform: "translateY(-12px) rotateX(1deg) rotateY(-1deg)" },
        },
        rotateRing: {
          from: { transform: "rotate(0deg)" },
          to: { transform: "rotate(360deg)" },
        },
        pulseRing: {
          "0%": { transform: "scale(1)", opacity: "1" },
          "100%": { transform: "scale(2)", opacity: "0" },
        },
        particleDrift: {
          from: { transform: "translate(0, 0)" },
          to: { transform: "translate(100px, -50px)" },
        },
        gradientShift: {
          "0%, 100%": { backgroundPosition: "0% 50%" },
          "50%": { backgroundPosition: "100% 50%" },
        },
        morphBlob: {
          "0%, 100%": { borderRadius: "60% 40% 30% 70% / 60% 30% 70% 40%" },
          "25%": { borderRadius: "30% 60% 70% 40% / 50% 60% 30% 60%" },
          "50%": { borderRadius: "50% 60% 30% 60% / 30% 60% 70% 40%" },
          "75%": { borderRadius: "60% 40% 60% 30% / 70% 30% 50% 60%" },
        },
        drawLine: {
          from: { strokeDashoffset: "1000" },
          to: { strokeDashoffset: "0" },
        },
        revealText: {
          from: { clipPath: "inset(0 100% 0 0)" },
          to: { clipPath: "inset(0 0 0 0)" },
        },
        staggerIn: {
          from: { opacity: "0", transform: "translateY(20px) scale(0.95)" },
          to: { opacity: "1", transform: "translateY(0) scale(1)" },
        },
        breathing: {
          "0%, 100%": { transform: "scale(1)", opacity: "0.6" },
          "50%": { transform: "scale(1.05)", opacity: "0.9" },
        },
        scan: {
          from: { transform: "translateY(-100%)" },
          to: { transform: "translateY(100vh)" },
        },
        progressIndeterminate: {
          "0%": { left: "-40%" },
          "100%": { left: "100%" },
        },
        gradientX: {
          "0%, 100%": { backgroundSize: "200% 200%" },
          "50%": { backgroundSize: "400% 400%" },
        },
      },
      backdropBlur: {
        xs: "2px",
      },
      boxShadow: {
        "glow-sm": "0 0 10px rgba(6, 182, 212, 0.3)",
        glow: "0 0 20px rgba(6, 182, 212, 0.4)",
        "glow-lg": "0 0 40px rgba(6, 182, 212, 0.5)",
        "glow-xl": "0 0 60px rgba(6, 182, 212, 0.6)",
        "glow-gold": "0 0 30px rgba(245, 158, 11, 0.4)",
        "glow-sm-gold": "0 0 10px rgba(245, 158, 11, 0.3)",
        "inner-glow": "inset 0 0 20px rgba(6, 182, 212, 0.1)",
        "premium": "0 25px 50px -12px rgba(0, 0, 0, 0.5), 0 0 0 1px rgba(255,255,255,0.05)",
        "premium-sm": "0 10px 40px -10px rgba(0, 0, 0, 0.4), 0 0 0 1px rgba(255,255,255,0.05)",
        "card-hover": "0 30px 60px -15px rgba(0, 0, 0, 0.4), 0 0 0 1px rgba(6, 182, 212, 0.15)",
      },
      transitionTimingFunction: {
        "spring": "cubic-bezier(0.16, 1, 0.3, 1)",
        "spring-bounce": "cubic-bezier(0.68, -0.55, 0.265, 1.55)",
        "ease-out-expo": "cubic-bezier(0.16, 1, 0.3, 1)",
      },
      backgroundImage: {
        "gradient-radial": "radial-gradient(var(--tw-gradient-stops))",
        "gradient-conic": "conic-gradient(from 180deg at 50% 50%, var(--tw-gradient-stops))",
        "mesh-gradient": "linear-gradient(135deg, var(--tw-gradient-from) 0%, var(--tw-gradient-via) 50%, var(--tw-gradient-to) 100%)",
        "shimmer-gradient": "linear-gradient(90deg, transparent 0%, rgba(255,255,255,0.08) 50%, transparent 100%)",
      },
    },
  },
  plugins: [
    require("tailwindcss-animate"),
    require("@tailwindcss/typography"),
  ],
}
