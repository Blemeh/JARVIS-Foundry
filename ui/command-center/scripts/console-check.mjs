import { chromium } from "@playwright/test"

const baseUrl = process.env.TRUTH_VISUAL_BASE ?? "http://localhost:4523"
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

const browser = await chromium.launch({ channel: "chrome", headless: true })
const page = await browser.newPage()
const errors = []

page.on("pageerror", (err) => errors.push(`pageerror: ${err.message}`))
page.on("console", (msg) => {
  if (msg.type() === "error") errors.push(`console.error: ${msg.text()}`)
})

for (const path of routes) {
  await page.goto(`${baseUrl}${path}`, { waitUntil: "networkidle" })
  await page.waitForTimeout(100)
}

await browser.close()

if (errors.length > 0) {
  console.error("Console check FAILED")
  for (const e of errors) console.error(e)
  process.exit(1)
}

console.log(`Console check PASS (${routes.length} routes, 0 errors)`)
