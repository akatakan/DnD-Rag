import { defineConfig } from "@playwright/test";

// The API serves web/dist at "/", so one server covers both the app and the
// API and the client's origin-based API resolution keeps working. Run
// `npm run build` first. Set E2E_BASE_URL to attach to a running stack.
const BASE_URL = process.env.E2E_BASE_URL || "http://localhost:8000";

export default defineConfig({
  testDir: "./tests",
  timeout: 30_000,
  use: {
    baseURL: BASE_URL,
    // CI images have no Edge; keep msedge as the local default.
    channel: process.env.E2E_CHANNEL || (process.env.CI ? undefined : "msedge"),
    launchOptions: {
      slowMo: Number(process.env.E2E_SLOW_MO || 0),
    },
    viewport: { width: 1440, height: 1000 },
    screenshot: "only-on-failure",
  },
  // The suite needed both servers started by hand, which is why it never ran
  // in CI. Booting the API here makes `npm run test:e2e` self-contained.
  webServer: process.env.E2E_BASE_URL
    ? undefined
    : {
        command: "uv run python run_api.py",
        cwd: "..",
        url: `${BASE_URL}/api/health`,
        reuseExistingServer: true,
        timeout: 120_000,
        env: {
          GAME_DB: "runtime/e2e-playwright.db",
          API_HOST: "127.0.0.1",
        },
      },
});
