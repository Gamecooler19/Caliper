import { defineConfig, devices } from "@playwright/test";

/**
 * Every test in this suite mocks /api/v1/classify at the browser's
 * network layer (page.route) rather than hitting a real backend --
 * see tests/fixtures.ts. This keeps the suite fast and deterministic,
 * and matches the brief's "mocked API response" requirement; it is a
 * UI/contract test suite, not an end-to-end integration test against a
 * live Caliper backend.
 */
export default defineConfig({
  testDir: "./tests",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? "github" : "list",
  use: {
    baseURL: "http://localhost:4173",
    trace: "on-first-retry",
  },
  webServer: {
    command: "npm run build && npm run preview -- --port 4173",
    url: "http://localhost:4173",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
  projects: [
    {
      name: "chromium",
      use: { ...devices["Desktop Chrome"] },
    },
    {
      name: "mobile-chromium",
      use: { ...devices["Pixel 7"] },
    },
  ],
});
