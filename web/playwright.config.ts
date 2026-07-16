import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./tests",
  timeout: 30_000,
  use: {
    baseURL: "http://localhost:5173",
    channel: "msedge",
    viewport: { width: 1440, height: 1000 },
    screenshot: "only-on-failure",
  },
});
