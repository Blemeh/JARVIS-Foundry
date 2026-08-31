import { defineConfig } from "@playwright/test";
// The config is loaded in the coordinator and in every Playwright worker.
// Port allocation therefore belongs in scripts/run-playwright.mjs, which
// exports one port to all processes.  A deterministic fallback fails closed
// if it is occupied rather than attaching to an unrelated server.
const port = Number(process.env.JARVIS_PLAYWRIGHT_PORT || 4174);
const useManagedWebServer = process.env.JARVIS_PLAYWRIGHT_USE_WEBSERVER === "1";

export default defineConfig({
  testDir: "./e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: true,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    channel: "chrome",
    headless: true,
    viewport: { width: 1600, height: 1280 },
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
  },
  ...(useManagedWebServer
    ? {
        webServer: {
          command: `${JSON.stringify(process.execPath)} node_modules/vite/bin/vite.js --host 127.0.0.1 --port ${port} --strictPort`,
          url: `http://127.0.0.1:${port}`,
          reuseExistingServer: false,
          timeout: 30_000,
          env: {
            ...process.env,
            VITE_JARVIS_MODE: process.env.VITE_JARVIS_MODE ?? "simulated",
          },
        },
      }
    : {}),
});
