import type { Config } from "tailwindcss";

// Palette + font are lifted directly from files/ui/01–03 so ported components
// render pixel-faithfully. Violet is the primary; slate is the neutral. We rely
// on Tailwind's default violet/slate/emerald/amber/rose/sky scales (the mockups
// use them unmodified) and only pin the font family here.
const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: ["var(--font-noto-sans-kr)", "sans-serif"],
      },
    },
  },
  plugins: [],
};
export default config;
