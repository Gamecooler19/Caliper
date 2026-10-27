import type { Config } from "tailwindcss";

// Token-based color system, defined once here before any component code
// (per the Module 5 brief). Every semantic color is a CSS custom property
// with both a light and a dark value (see src/styles/tokens.css) -- this
// file only maps those variables into Tailwind's palette and utility
// classes; it never hardcodes a single-mode hex value itself.
//
// Anchors:
//   - graphite neutral #14181D: dark-mode base surface / light-mode
//     heading+text color
//   - teal #14B8A6: primary action + risk-readout emphasis (sparingly)
//   - red #DC2626: the error state only
//   - green #16A34A: explicit no-discrepancy confirmation
//   - amber #D97706: consistency-flags review callout (never confused
//     with the red danger state)
const config: Config = {
  darkMode: "class",
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        bg: {
          DEFAULT: "rgb(var(--color-bg) / <alpha-value>)",
          subtle: "rgb(var(--color-bg-subtle) / <alpha-value>)",
        },
        surface: {
          DEFAULT: "rgb(var(--color-surface) / <alpha-value>)",
          raised: "rgb(var(--color-surface-raised) / <alpha-value>)",
        },
        border: {
          DEFAULT: "rgb(var(--color-border) / <alpha-value>)",
          strong: "rgb(var(--color-border-strong) / <alpha-value>)",
        },
        text: {
          DEFAULT: "rgb(var(--color-text) / <alpha-value>)",
          muted: "rgb(var(--color-text-muted) / <alpha-value>)",
          onAccent: "rgb(var(--color-text-on-accent) / <alpha-value>)",
        },
        accent: {
          DEFAULT: "rgb(var(--color-accent) / <alpha-value>)",
          hover: "rgb(var(--color-accent-hover) / <alpha-value>)",
          subtle: "rgb(var(--color-accent-subtle) / <alpha-value>)",
        },
        danger: {
          DEFAULT: "rgb(var(--color-danger) / <alpha-value>)",
          subtle: "rgb(var(--color-danger-subtle) / <alpha-value>)",
          border: "rgb(var(--color-danger-border) / <alpha-value>)",
        },
        success: {
          DEFAULT: "rgb(var(--color-success) / <alpha-value>)",
          subtle: "rgb(var(--color-success-subtle) / <alpha-value>)",
          border: "rgb(var(--color-success-border) / <alpha-value>)",
        },
        amber: {
          DEFAULT: "rgb(var(--color-amber) / <alpha-value>)",
          subtle: "rgb(var(--color-amber-subtle) / <alpha-value>)",
          border: "rgb(var(--color-amber-border) / <alpha-value>)",
        },
      },
      fontFamily: {
        // Body/prose text split, established before any component code.
        sans: [
          "Inter",
          "IBM Plex Sans",
          "system-ui",
          "-apple-system",
          "sans-serif",
        ],
        // Risk-class letter + any instrument-readout numeric element.
        mono: [
          "JetBrains Mono",
          "IBM Plex Mono",
          "ui-monospace",
          "SFMono-Regular",
          "monospace",
        ],
      },
      keyframes: {
        "fade-in": {
          "0%": { opacity: "0" },
          "100%": { opacity: "1" },
        },
      },
      animation: {
        // Used only for the two earned transitions: entering the result
        // state, and the segmentation overlay's own fade-in.
        "fade-in": "fade-in 320ms ease-out",
      },
    },
  },
  plugins: [],
};

export default config;
