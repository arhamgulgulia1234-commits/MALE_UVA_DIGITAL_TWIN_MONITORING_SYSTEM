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
        // Desaturated from the original neon HUD values (go #22d3a8, amber/caution
        // #f5a623, red/nogo #ef4a5f, cyan #3fd0e0) for the control-room restyle — same
        // hues and semantic meaning, contrast checked to stay >=4.5:1 on base.bg/panel.
        status: {
          go: "#37af92",
          caution: "#d59834",
          nogo: "#da6978",
          cyan: "#4ab9c6",
          amber: "#d59834",
          red: "#da6978",
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
      // Crisp 1px status-colored rings, no blur halo — was a 24-28px glow blur layered
      // under each ring; control-room panels get a flat border instead.
      boxShadow: {
        glow: "0 0 0 1px rgba(74,185,198,0.4)",
        "glow-amber": "0 0 0 1px rgba(213,152,52,0.45)",
        "glow-red": "0 0 0 1px rgba(218,105,120,0.45)",
        "glow-go": "0 0 0 1px rgba(55,175,146,0.4)",
      },
      keyframes: {
        scanline: {
          "0%": { backgroundPosition: "0 0" },
          "100%": { backgroundPosition: "0 100%" },
        },
      },
    },
  },
  plugins: [],
};

export default config;
