import { chromium } from "@playwright/test"
import { mkdirSync } from "node:fs"
import { join } from "node:path"

const baseUrl = process.env.TRUTH_VISUAL_BASE ?? "http://localhost:4523"
const outDir = join(process.cwd(), "..", "..", "evidence", "overnight", "command-center-truth", "screenshots")
const routes = [
  { path: "/", slug: "overview" },
  { path: "/systems", slug: "systems" },
  { path: "/missions", slug: "missions" },
  { path: "/timeline", slug: "timeline" },
  { path: "/evidence", slug: "evidence" },
  { path: "/agents", slug: "agents" },
  { path: "/notifications", slug: "notifications" },
  { path: "/executive", slug: "executive" },
]
const viewports = [
  { name: "1600x1000", width: 1600, height: 1000 },
  { name: "834x1112", width: 834, height: 1112 },
  { name: "390x844", width: 390, height: 844 },
]

mkdirSync(outDir, { recursive: true })

const browser = await chromium.launch({ channel: "chrome", headless: true })
for (const viewport of viewports) {
  const context = await browser.newContext({ viewport: { width: viewport.width, height: viewport.height } })
  const page = await context.newPage()
  for (const route of routes) {
    await page.goto(`${baseUrl}${route.path}`, { waitUntil: "networkidle" })
    await page.screenshot({ path: join(outDir, `${route.slug}-${viewport.name}.png`), fullPage: true })
  }
  await context.close()
}
await browser.close()
console.log(`Captured ${routes.length * viewports.length} screenshots to ${outDir}`)
