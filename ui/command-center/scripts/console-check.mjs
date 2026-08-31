import { chromium } from "@playwright/test"
import { execFileSync, spawn } from "node:child_process"
import net from "node:net"
import { join } from "node:path"
import { setTimeout as delay } from "node:timers/promises"
import {
  assertLanePortFree,
  clearLaneRecord,
  registerLaneProcess,
  shutdownLane,
} from "./laneProcess.mjs"

const routes = [
  "/",
  "/executive",
  "/missions",
  "/missions/M-HYB-014",
  "/agents",
  "/agents/WRK-HERMES",
  "/memory",
  "/evidence",
  "/timeline",
  "/systems",
  "/notifications",
  "/missing-route",
]

async function allocatePort() {
  const server = net.createServer()
  return await new Promise((resolve, reject) => {
    server.once("error", reject)
    server.listen(0, "127.0.0.1", () => {
      const address = server.address()
      const port = typeof address === "object" && address ? address.port : 0
      server.close((error) => {
        if (error) return reject(error)
        if (!port) return reject(new Error("could not allocate an isolated console-check port"))
        resolve(port)
      })
    })
  })
}

const explicitBase = process.env.TRUTH_VISUAL_BASE
const managePreview = process.env.TRUTH_VISUAL_MANAGE_PREVIEW !== "0" && !explicitBase
const port = Number(process.env.TRUTH_VISUAL_PORT) || (managePreview ? await allocatePort() : null)
const baseUrl = explicitBase ?? `http://127.0.0.1:${port}`
const laneId = `console-check-${port ?? "external"}`
const systemRoot = process.env.SystemRoot || process.env.WINDIR || "C:\\Windows"
const pathValue = [join(systemRoot, "System32"), join(systemRoot), process.env.PATH]
  .filter(Boolean)
  .join(process.platform === "win32" ? ";" : ":")
let previewProcess

async function waitForServer(url, timeoutMs = 30_000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const response = await fetch(url)
      if (response.ok) return
    } catch {
      // Vite is still starting.
    }
    await delay(100)
  }
  throw new Error(`Console-check preview did not become ready at ${url}`)
}

function cleanup() {
  if (managePreview) shutdownLane(laneId)
  else if (previewProcess?.pid && previewProcess.exitCode === null) {
    const taskkill = join(systemRoot, "System32", "taskkill.exe")
    try {
      execFileSync(taskkill, ["/PID", String(previewProcess.pid), "/T", "/F"], {
        stdio: "ignore",
        windowsHide: true,
      })
    } catch {
      // The process may already have exited.
    }
  }
}

process.once("SIGINT", () => {
  cleanup()
  process.exit(130)
})
process.once("SIGTERM", () => {
  cleanup()
  process.exit(143)
})

if (managePreview) {
  if (!assertLanePortFree(laneId, port)) {
    console.error(`Console check blocked: port ${port} is held by a foreign process`)
    process.exit(2)
  }
  const viteBin = join(process.cwd(), "node_modules", "vite", "bin", "vite.js")
  previewProcess = spawn(process.execPath, [viteBin, "--host", "127.0.0.1", "--port", String(port), "--strictPort"], {
    cwd: process.cwd(),
    shell: false,
    stdio: "ignore",
    windowsHide: true,
    env: { ...process.env, PATH: pathValue, VITE_JARVIS_MODE: process.env.VITE_JARVIS_MODE ?? "simulated" },
  })
  if (previewProcess.pid) registerLaneProcess(laneId, port, previewProcess.pid)
  previewProcess.on("exit", () => clearLaneRecord(laneId))
}

const errors = []
let browser
try {
  await waitForServer(baseUrl)
  browser = await chromium.launch({ channel: "chrome", headless: true })
  const page = await browser.newPage()

  page.on("pageerror", (err) => errors.push(`pageerror: ${err.message}`))
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(`console.error: ${msg.text()}`)
  })

  for (const path of routes) {
    await page.goto(`${baseUrl}${path}`, { waitUntil: "networkidle" })
    await page.waitForTimeout(100)
  }
} catch (error) {
  errors.push(error instanceof Error ? error.message : String(error))
} finally {
  if (browser) await browser.close().catch(() => undefined)
  cleanup()
}

if (errors.length > 0) {
  console.error("Console check FAILED")
  for (const error of errors) console.error(error)
  process.exit(1)
}

console.log(`Console check PASS (${routes.length} routes, 0 errors; ${managePreview ? `isolated port ${port}` : "explicit external base"})`)
