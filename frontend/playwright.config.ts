import { defineConfig, devices } from "@playwright/test";

// E2E for the dual-surface workspace. Setup (once):
//   pnpm add -D @playwright/test
//   pnpm exec playwright install chromium
// Run:
//   pnpm exec playwright test            # boots `next dev` automatically
//
// Preconditions for the @backend-tagged cases: the FastAPI backend reachable at
// BACKEND_ORIGIN (default http://localhost:8000) with a populated ../store/ so
// /compare returns rows. Those cases skip themselves if /compare is unreachable,
// so the FE-only cases still run anywhere.
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: "list",
  use: {
    baseURL: "http://localhost:3000",
    trace: "on-first-retry",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
  webServer: {
    command: "pnpm dev",
    url: "http://localhost:3000",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
