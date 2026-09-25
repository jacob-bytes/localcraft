import { defineConfig, devices } from "@playwright/test";

/**
 * E2E harness for the CONTRACT §8 acceptance list (docs/04 §1 lists Playwright).
 * It always runs against `npm run dev`, i.e. with `VITE_ENABLE_MOCKS=true`, so
 * the whole M1 vertical slice can be exercised without the backend.
 */
export default defineConfig({
  testDir: "./e2e",
  timeout: 45_000,
  expect: { timeout: 10_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  outputDir: "e2e/artifacts/test-output",
  use: {
    baseURL: "http://127.0.0.1:5173",
    trace: "off",
    screenshot: "only-on-failure",
    viewport: { width: 1440, height: 900 },
    locale: "zh-CN",
  },
  projects: [
    {
      name: "chromium",
      // Use the locally installed Google Chrome: the sandbox cannot write to
      // ~/Library/Caches/ms-playwright, so the bundled browser cannot be
      // downloaded here. `npx playwright install chromium` is the CI equivalent.
      use: { ...devices["Desktop Chrome"], channel: "chrome" },
    },
  ],
  webServer: {
    command: "npm run dev",
    url: "http://127.0.0.1:5173",
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
