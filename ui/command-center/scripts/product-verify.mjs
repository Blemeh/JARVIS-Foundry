import { chromium } from "@playwright/test"
import { spawn } from "node:child_process"
import { mkdirSync, writeFileSync } from "node:fs"
import { join } from "node:path"
import { setTimeout as delay } from "node:timers/promises"
import {
  assertLanePortFree,
  clearLaneRecord,
  registerLaneProcess,
  shutdownLane,
} from "./laneProcess.mjs"

const port = Number(process.env.VERIFY_PORT ?? 4524)
const laneId = `product-verify-${port}`const baseUrl = process.env.VERIFY_BASE ?? `http://localhost:${port}`
const evidenceDir = join(process.cwd(), "..", "..", "evidence", "overnight", "command-center-truth")
const outPath = join(evidenceDir, "PRODUCT_VERIFY_RESULT.json")
const managePreview = process.env.VERIFY_MANAGE_PREVIEW !== "0"

const routes = [
  { path: "/", slug: "overview", heading: "What should JARVIS move next?" },
  { path: "/executive", slug: "executive", heading: "Executive" },
  { path: "/missions", slug: "missions", heading: "Mission Vectors" },
  {
    path: "/missions/M-HYB-014",
    slug: "mission-detail",
    heading: "Hybrid Worker Activation",
    emptyHeading: "Mission not found",
  },
  { path: "/agents", slug: "agents", heading: "Specialist Fleet" },
  {
    path: "/agents/WRK-HERMES",
    slug: "agent-detail",
    heading: "Hermes Orchestrator",
    emptyHeading: "Agent not found",
  },
  { path: "/memory", slug: "memory", heading: "Knowledge Graph" },
  { path: "/memory/n-verdict", slug: "memory-detail", heading: "Knowledge Graph" },
  { path: "/evidence", slug: "evidence", heading: "Evidence Ledger" },
  {
    path: "/evidence/EV-1131-007",
    slug: "evidence-detail",
    heading: "Ledger entry",
    emptyHeading: "Evidence not found",
  },
  { path: "/timeline", slug: "timeline", heading: "Evidence Spine" },
  { path: "/systems", slug: "systems", heading: "Telemetry" },
  {
    path: "/systems/SYS-STOP",
    slug: "system-detail",
    heading: "STOP subsystem",
    emptyHeading: "System not found",
  },
  { path: "/notifications", slug: "notifications", heading: "Notification / Exception Center" },
  { path: "/missing-route", slug: "not-found", heading: "Route not found" },
]

const scenarios = [
  "NORMAL_OPERATION",
  "STOP_ACTIVE",
  "MISSION_BLOCKED",
  "WORKER_CRASH",
  "EMPTY_STATE",
  "STREAM_DISCONNECTED",
  "PROVIDER_DEGRADED",
]

const viewports = [
  { name: "1600x1000", width: 1600, height: 1000 },
  { name: "390x844", width: 390, height: 844 },
]

const forbiddenTelemetry = ["99.8", "$0.38", "Integrity node online"]
const startedAt = new Date().toISOString()
const failures = []
const passes = []

const pushFailure = (entry) => failures.push({ at: new Date().toISOString(), ...entry })

async function waitForPort(url, timeoutMs = 30_000) {
  const deadline = Date.now() + timeoutMs
  while (Date.now() < deadline) {
    try {
      const res = await fetch(url)
      if (res.ok) return
    } catch {
      // retry
    }
    await delay(500)
  }
  throw new Error(`Preview server not ready at ${url}`)
}

let previewProcess
const cleanup = () => {
  shutdownLane(laneId)
}

process.on("SIGINT", () => {
  cleanup()
  process.exit(130)
})
process.on("SIGTERM", () => {
  cleanup()
  process.exit(143)
})

if (managePreview) {
  if (!assertLanePortFree(laneId, port)) {
    console.error(
      `Product verify blocked: port ${port} is held by a foreign process. Set VERIFY_MANAGE_PREVIEW=0 to attach to an existing preview, or free the port.`,
    )
    process.exit(2)
  }
  previewProcess = spawn("npx", ["vite", "preview", "--port", String(port), "--strictPort"], {
    cwd: process.cwd(),
    shell: true,
    stdio: "ignore",
  })
  if (previewProcess.pid) registerLaneProcess(laneId, port, previewProcess.pid)
  previewProcess.on("exit", () => clearLaneRecord(laneId))
  await waitForPort(baseUrl)
}
const browser = await chromium.launch({ channel: "chrome", headless: true })

try {
  for (const viewport of viewports) {
    const context = await browser.newContext({ viewport: { width: viewport.width, height: viewport.height } })
    const page = await context.newPage()
    const consoleErrors = []
    page.on("pageerror", (err) => consoleErrors.push(`pageerror: ${err.message}`))
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(`console.error: ${msg.text()}`)
    })

    for (const scenario of scenarios) {
      for (const route of routes) {
        const label = `${route.slug}@${scenario}@${viewport.name}`
        try {
          const response = await page.goto(`${baseUrl}${route.path}?scenario=${scenario}`, {
            waitUntil: "domcontentloaded",
            timeout: 20_000,
          })
          if (!response?.ok()) {
            pushFailure({ label, kind: "http", detail: `${response?.status() ?? "no response"}` })
            continue
          }
          await page.locator("main#main-content").waitFor({ state: "visible", timeout: 10_000 })
          if (route.slug === "not-found") {
            await page.getByText("Route not found").waitFor({ state: "visible", timeout: 10_000 })
          } else {
            const expectedHeading =
              scenario === "EMPTY_STATE" && route.emptyHeading ? route.emptyHeading : route.heading
            const headingLocator =
              route.emptyHeading && scenario === "EMPTY_STATE"
                ? page.getByText(expectedHeading)
                : page.getByRole("heading", { name: expectedHeading })
            await headingLocator.waitFor({ state: "visible", timeout: 10_000 })
            const pill = page.locator(".data-source-pill")
            const pillText = await pill.innerText()
            if (!pillText.includes("SIMULATED")) {
              pushFailure({ label, kind: "source-pill", detail: `expected SIMULATED, got ${pillText}` })
              continue
            }
          }
          const body = await page.locator("body").innerText()
          for (const forbidden of forbiddenTelemetry) {
            if (body.includes(forbidden)) {
              pushFailure({ label, kind: "fabricated-telemetry", detail: forbidden })
            }
          }
          if (scenario === "STREAM_DISCONNECTED") {
            await page.getByRole("status", { name: /Stream disconnected/i }).waitFor({ state: "visible", timeout: 5_000 })
          }
          if (consoleErrors.length > 0) {
            pushFailure({ label, kind: "console", detail: consoleErrors.join(" | ") })
            consoleErrors.length = 0
            continue
          }
          passes.push(label)
        } catch (err) {
          pushFailure({
            label,
            kind: "exception",
            detail: err instanceof Error ? err.message : String(err),
          })
        }
      }
    }

    const navSequence = ["/missions", "/agents", "/evidence", "/systems", "/"]
    for (const path of navSequence) {
      await page.goto(`${baseUrl}${path}`)
      await page.locator("main#main-content").waitFor({ state: "visible" })
    }
    await page.goBack()
    await page.goForward()
    await page.goto(`${baseUrl}/systems`)
    await page.reload()
    await page.getByRole("heading", { name: "Telemetry" }).waitFor({ state: "visible" })
    if (consoleErrors.length > 0) {
      pushFailure({ label: `navigation@${viewport.name}`, kind: "console", detail: consoleErrors.join(" | ") })
    } else {
      passes.push(`navigation@${viewport.name}`)
    }

    await context.close()
  }
} finally {
  await browser.close()
  cleanup()
}
const result = {
  status: failures.length === 0 ? "PASS" : "FAIL",
  baseUrl,
  port,
  startedAt,
  endedAt: new Date().toISOString(),
  routeCount: routes.length,
  scenarioCount: scenarios.length,
  viewportCount: viewports.length,
  passCount: passes.length,
  failureCount: failures.length,
  failures,
  passes: passes.slice(0, 20),
  pass: failures.length === 0,
}

mkdirSync(evidenceDir, { recursive: true })
writeFileSync(outPath, `${JSON.stringify(result, null, 2)}\n`)

console.log(
  `Product verify ${result.status}: ${result.passCount} checks, ${result.failureCount} failures → ${outPath}`,
)
process.exit(result.pass ? 0 : 1)
