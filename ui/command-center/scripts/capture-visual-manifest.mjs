import { chromium } from "@playwright/test"
import { mkdirSync, writeFileSync } from "node:fs"
import { join } from "node:path"

const baseUrl = process.env.TRUTH_VISUAL_BASE ?? "http://localhost:4523"
const outDir = join(process.cwd(), "..", "..", "evidence", "overnight", "command-center-truth", "screenshots")
const manifestPath = join(process.cwd(), "..", "..", "evidence", "overnight", "command-center-truth", "VISUAL_MANIFEST.json")

const routes = [
  { path: "/", slug: "overview" },
  { path: "/executive", slug: "executive" },
  { path: "/missions", slug: "missions" },
  { path: "/missions/M-HYB-014", slug: "mission-detail" },
  { path: "/agents", slug: "agents" },
  { path: "/agents/WRK-HERMES", slug: "agent-detail" },
  { path: "/memory", slug: "memory" },
  { path: "/memory/n-verdict", slug: "memory-detail" },
  { path: "/evidence", slug: "evidence" },
  { path: "/evidence/EV-1131-007", slug: "evidence-detail" },
  { path: "/timeline", slug: "timeline" },
  { path: "/systems", slug: "systems" },
  { path: "/systems/SYS-STOP", slug: "system-detail" },
  { path: "/notifications", slug: "notifications" },
  { path: "/missing-route", slug: "not-found" },
]

const scenarios = [
  "NORMAL_OPERATION",
  "STOP_ACTIVE",
  "MISSION_BLOCKED",
  "WORKER_CRASH",
  "EMPTY_STATE",
]

const viewports = [
  { name: "1600x1000", width: 1600, height: 1000 },
  { name: "834x1112", width: 834, height: 1112 },
  { name: "390x844", width: 390, height: 844 },
]

mkdirSync(outDir, { recursive: true })

const cells = []

const browser = await chromium.launch({ channel: "chrome", headless: true })

for (const viewport of viewports) {
  const context = await browser.newContext({ viewport: { width: viewport.width, height: viewport.height } })
  const page = await context.newPage()

  for (const route of routes) {
    for (const scenario of scenarios) {
      const file = `${route.slug}__${scenario}__${viewport.name}.png`
      const url = `${baseUrl}${route.path}?scenario=${scenario}`
      await page.goto(url, { waitUntil: "networkidle" })
      await page.waitForTimeout(150)

      const metrics = await page.evaluate(() => {
        const doc = document.documentElement
        const body = document.body
        const horizontalOverflow = doc.scrollWidth > doc.clientWidth + 1 || body.scrollWidth > window.innerWidth + 1
        return {
          horizontalOverflow,
          scrollWidth: doc.scrollWidth,
          clientWidth: doc.clientWidth,
        }
      })

      await page.screenshot({ path: join(outDir, file), fullPage: false })

      const autoPass = !metrics.horizontalOverflow
      cells.push({
        route: route.slug,
        path: route.path,
        scenario,
        viewport: viewport.name,
        screenshot: `screenshots/${file}`,
        automated: {
          horizontalOverflow: metrics.horizontalOverflow,
          scrollWidth: metrics.scrollWidth,
          clientWidth: metrics.clientWidth,
        },
        inspection: autoPass ? "PASS" : "FAIL",
        notes: autoPass ? "Viewport fits without horizontal scroll." : "Document horizontal overflow detected.",
      })
    }
  }

  await context.close()
}

await browser.close()

const summary = {
  capturedAt: new Date().toISOString(),
  baseUrl,
  viewports: viewports.map((v) => v.name),
  routes: routes.map((r) => r.slug),
  scenarios,
  totalCells: cells.length,
  passCount: cells.filter((c) => c.inspection === "PASS").length,
  failCount: cells.filter((c) => c.inspection === "FAIL").length,
  cells,
}

writeFileSync(manifestPath, `${JSON.stringify(summary, null, 2)}\n`)
console.log(`Captured ${cells.length} screenshots. PASS ${summary.passCount} FAIL ${summary.failCount}`)
console.log(`Manifest: ${manifestPath}`)
