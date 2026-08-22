import { chromium } from "@playwright/test"
import { writeFileSync, mkdirSync } from "node:fs"
import { join } from "node:path"

const baseUrl = process.env.ENDURANCE_BASE ?? "http://localhost:4523"
const durationMs = Number(process.env.ENDURANCE_DURATION_MS ?? 6 * 60 * 60 * 1000)
const checkpointMs = 15 * 60 * 1000
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
let errors = []

const browser = await chromium.launch({ channel: "chrome", headless: true })
const context = await browser.newContext({ viewport: { width: 1600, height: 1000 } })
const page = await context.newPage()

page.on("pageerror", (err) => errors.push({ at: new Date().toISOString(), type: "pageerror", message: err.message }))
page.on("console", (msg) => {
  if (msg.type() === "error") errors.push({ at: new Date().toISOString(), type: "console.error", message: msg.text() })
})

const runCheckpoint = async (label) => {
  const snapshot = {
    label,
    at: new Date().toISOString(),
    cycles,
    errorCount: errors.length,
    memory: await page.evaluate(() => (performance.memory ? { usedJSHeapSize: performance.memory.usedJSHeapSize } : null)),
  }
  checkpoints.push(snapshot)
  writeFileSync(outPath, `${JSON.stringify(buildReport("running"), null, 2)}\n`)
}

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
  errors,
  pass: status === "complete" && errors.length === 0,
})

mkdirSync(join(outPath, ".."), { recursive: true })
await runCheckpoint("start")

const deadline = startedAt.getTime() + durationMs
let nextCheckpoint = startedAt.getTime() + checkpointMs

while (Date.now() < deadline) {
  for (const scenario of scenarios) {
    for (const path of routes) {
      if (Date.now() >= deadline) break
      try {
        const response = await page.goto(`${baseUrl}${path}?scenario=${scenario}`, { waitUntil: "domcontentloaded", timeout: 30_000 })
        if (!response || !response.ok()) {
          errors.push({ at: new Date().toISOString(), type: "http", message: `${path}?scenario=${scenario} -> ${response?.status() ?? "no response"}` })
          continue
        }
        await page.waitForTimeout(50)
        cycles += 1
      } catch (err) {
        errors.push({ at: new Date().toISOString(), type: "navigation", message: `${path}?scenario=${scenario}: ${err instanceof Error ? err.message : String(err)}` })
      }
    }
    if (Date.now() >= deadline) break
  }

  if (Date.now() >= nextCheckpoint) {
    await runCheckpoint(`t+${Math.round((Date.now() - startedAt.getTime()) / 60_000)}m`)
    nextCheckpoint += checkpointMs
  }
}

await browser.close()
await runCheckpoint("end")

const report = buildReport("complete")
report.pass = errors.length === 0
writeFileSync(outPath, `${JSON.stringify(report, null, 2)}\n`)

console.log(`Endurance ${report.pass ? "PASS" : "FAIL"}: ${cycles} cycles, ${errors.length} errors, ${(report.elapsedMs / 60_000).toFixed(1)} minutes`)
process.exit(report.pass ? 0 : 1)
