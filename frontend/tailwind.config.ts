import type { Config } from "tailwindcss";

const config: Config = {
  content: [
    "./app/**/*.{ts,tsx}",
    "./components/**/*.{ts,tsx}",
    "./lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      fontFamily: {
        mono: ["var(--font-mono)", "ui-monospace", "monospace"],
        sans: ["var(--font-sans)", "system-ui", "sans-serif"],
      },
      colors: {
        orange: {
          400: "#ffb347",
          500: "#ff9500",
          600: "#e08500",
        },
      },
      backgroundColor: {
        surface: "#0a0c0f",
      },
    },
  },
  plugins: [],
};

export default config;
