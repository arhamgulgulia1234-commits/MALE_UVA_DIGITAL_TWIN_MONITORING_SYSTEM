import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
    "./hooks/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        base: {
          bg: "#0a0e14",
          panel: "#0f1420",
          panel2: "#131a28",
          border: "#1e2734",
        },
        status: {
          go: "#22d3a8",
          caution: "#f5a623",
          nogo: "#ef4a5f",
          cyan: "#3fd0e0",
          amber: "#f5a623",
          red: "#ef4a5f",
          idle: "#5b6b82",
        },
        // Brand gold, sampled from the VAYUDRISHTI emblem — deliberately its own token
        // rather than reusing `status.amber` (a near neighbour in hue). The two must stay
        // visually distinguishable: amber means CAUTION somewhere in the mission-critical
        // color system, and the logo's gold must never be mistaken for that.
        brand: {
          gold: "#dda345",
        },
      },
      fontFamily: {
        mono: ["var(--font-mono)", "ui-monospace", "SFMono-Regular", "monospace"],
        display: ["var(--font-display)", "ui-sans-serif", "system-ui", "sans-serif"],
      },
      boxShadow: {
        glow: "0 0 0 1px rgba(63,208,224,0.15), 0 0 24px -8px rgba(63,208,224,0.35)",
        "glow-amber": "0 0 0 1px rgba(245,166,35,0.2), 0 0 24px -6px rgba(245,166,35,0.45)",
        "glow-red": "0 0 0 1px rgba(239,74,95,0.25), 0 0 28px -6px rgba(239,74,95,0.55)",
        "glow-go": "0 0 0 1px rgba(34,211,168,0.2), 0 0 24px -6px rgba(34,211,168,0.45)",
      },
      keyframes: {
        pulseGlow: {
          "0%, 100%": { opacity: "1" },
          "50%": { opacity: "0.55" },
        },
        scanline: {
          "0%": { backgroundPosition: "0 0" },
          "100%": { backgroundPosition: "0 100%" },
        },
      },
      animation: {
        pulseGlow: "pulseGlow 1.6s ease-in-out infinite",
      },
    },
  },
  plugins: [],
};

export default config;
