import { defineConfig } from "@playwright/test";
import net from "node:net";

function freePort(): number {
  const server = net.createServer();
  server.listen(0, "127.0.0.1");
  const address = server.address();
  const port = typeof address === "object" && address ? address.port : 0;
  server.close();
  if (!port) throw new Error("could not allocate an isolated Playwright port");
  return port;
}

const port = Number(process.env.JARVIS_PLAYWRIGHT_PORT || freePort());

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
  webServer: {
    command: `npm run preview -- --host 127.0.0.1 --port ${port} --strictPort`,
    url: `http://127.0.0.1:${port}`,
    reuseExistingServer: false,
    timeout: 30_000,
  },
});
