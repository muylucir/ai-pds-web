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
      // 새 문서가 왔을 때 드로어 손잡이를 **두 번만** 출렁인다(WorkspaceDocPanel).
      // 기본 animate-ping은 무한 반복이라 답하는 동안 시선을 계속 끈다.
      animation: {
        nudge: "ping 1s cubic-bezier(0, 0, 0.2, 1) 2",
      },
    },
  },
  plugins: [require("@tailwindcss/typography")],
};
export default config;
