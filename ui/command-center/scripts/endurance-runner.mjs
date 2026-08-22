import { chromium } from "@playwright/test"
import { writeFileSync, mkdirSync } from "node:fs"
import { join } from "node:path"

const baseUrl = process.env.ENDURANCE_BASE ?? "http://localhost:4523"
const durationMs = Number(process.env.ENDURANCE_DURATION_MS ?? 6 * 60 * 60 * 1000)
const checkpointMs = Number(process.env.ENDURANCE_CHECKPOINT_MS ?? 15 * 60 * 1000)
const recycleEvery = Number(process.env.ENDURANCE_RECYCLE_EVERY ?? 40)
const requestDelayMs = Number(process.env.ENDURANCE_REQUEST_DELAY_MS ?? 250)
const maxErrors = Number(process.env.ENDURANCE_MAX_ERRORS ?? 200)
const outPath = join(process.cwd(), "..", "..", "evidence", "overnight", "command-center-truth", "ENDURANCE_RESULT.json")

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
]

const scenarios = ["NORMAL_OPERATION", "STOP_ACTIVE", "MISSION_BLOCKED", "WORKER_CRASH", "EMPTY_STATE"]

const startedAt = new Date()
const checkpoints = []
let cycles = 0
const errors = []
const pushError = (entry) => {
  if (errors.length < maxErrors) errors.push(entry)
}
let browser
let lastHeartbeatAt = startedAt.toISOString()

const buildReport = (status) => ({
  status,
  baseUrl,
  requestedDurationMs: durationMs,
  requestedDurationHours: durationMs / 3_600_000,
  checkpointIntervalMs: checkpointMs,
  startedAt: startedAt.toISOString(),
  endedAt: status === "running" ? null : new Date().toISOString(),
  elapsedMs: Date.now() - startedAt.getTime(),
  cycles,
  routes,
  scenarios,
  checkpoints,
  errors: errors.slice(-200),
  lastHeartbeatAt,
  pass: status === "complete" && errors.length === 0,
  note:
    durationMs < 6 * 60 * 60 * 1000
      ? "Session used reduced ENDURANCE_DURATION_MS; full 6h gate requires unattended rerun."
      : undefined,
})

const persist = (status) => {
  mkdirSync(join(outPath, ".."), { recursive: true })
  writeFileSync(outPath, `${JSON.stringify(buildReport(status), null, 2)}\n`)
}

const heartbeat = setInterval(() => {
  lastHeartbeatAt = new Date().toISOString()
  persist("running")
}, 30_000)

const runCheckpoint = async (label, page) => {
  const snapshot = {
    label,
    at: new Date().toISOString(),
    cycles,
    errorCount: errors.length,
    memory: page
      ? await page
          .evaluate(() => (performance.memory ? { usedJSHeapSize: performance.memory.usedJSHeapSize } : null))
          .catch(() => null)
      : null,
  }
  checkpoints.push(snapshot)
  persist("running")
}

const attachGuards = (page) => {
  page.on("pageerror", (err) => pushError({ at: new Date().toISOString(), type: "pageerror", message: err.message }))
  page.on("console", (msg) => {
    if (msg.type() === "error") pushError({ at: new Date().toISOString(), type: "console.error", message: msg.text() })
  })
}

const createSession = async () => {
  if (!browser) browser = await chromium.launch({ headless: true })
  const context = await browser.newContext({ viewport: { width: 1600, height: 1000 } })
  const page = await context.newPage()
  attachGuards(page)
  return { context, page }
}

persist("running")
let session = await createSession()
await runCheckpoint("start", session.page)

const deadline = startedAt.getTime() + durationMs
let nextCheckpoint = startedAt.getTime() + checkpointMs
let sinceRecycle = 0

try {
  while (Date.now() < deadline) {
    for (const scenario of scenarios) {
      for (const path of routes) {
        if (Date.now() >= deadline) break

        if (sinceRecycle >= recycleEvery) {
          await session.context.close().catch(() => undefined)
          session = await createSession()
          sinceRecycle = 0
        }

        const url = `${baseUrl}${path}?scenario=${scenario}`
        try {
          const response = await session.page.goto(url, {
            waitUntil: "commit",
            timeout: 20_000,
          })
          if (!response || !response.ok()) {
            pushError({
              at: new Date().toISOString(),
              type: "http",
              message: `${url} -> ${response?.status() ?? "no response"}`,
            })
          } else {
            cycles += 1
            sinceRecycle += 1
          }
          await session.page.waitForTimeout(requestDelayMs)
          if (cycles % 25 === 0) persist("running")
        } catch (err) {
          pushError({
            at: new Date().toISOString(),
            type: "navigation",
            message: `${url}: ${err instanceof Error ? err.message : String(err)}`,
          })
          await session.context.close().catch(() => undefined)
          session = await createSession()
          sinceRecycle = 0
        }
      }
      if (Date.now() >= deadline) break
    }

    if (Date.now() >= nextCheckpoint) {
      await runCheckpoint(`t+${Math.round((Date.now() - startedAt.getTime()) / 60_000)}m`, session.page)
      nextCheckpoint += checkpointMs
    }
  }
} finally {
  clearInterval(heartbeat)
  await session.context.close().catch(() => undefined)
  if (browser) await browser.close().catch(() => undefined)
  await runCheckpoint("end", null)
  persist("complete")
}

const report = buildReport("complete")
console.log(
  `Endurance ${report.pass ? "PASS" : "FAIL"}: ${cycles} cycles, ${errors.length} errors, ${(report.elapsedMs / 60_000).toFixed(1)} minutes`,
)
process.exit(report.pass ? 0 : 1)
