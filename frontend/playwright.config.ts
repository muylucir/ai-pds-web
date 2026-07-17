import { defineConfig } from "@playwright/test";

// INTEGRATION ONLY — requires a live FastAPI backend (Phase 1 + API Completion
// merged & running) reachable at NEXT_PUBLIC_API_BASE_URL, plus `npm run dev`.
// Never run by the unit CI job; run explicitly with `npm run test:e2e`.
export default defineConfig({
  testDir: "./e2e",
  use: { baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000" },
  webServer: {
    command: "npm run dev",
    url: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
