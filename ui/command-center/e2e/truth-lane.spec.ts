import { test, expect } from "@playwright/test"

const routes = [
  { path: "/", heading: "What should JARVIS move next?", slug: "overview" },
  { path: "/systems", heading: "Telemetry", slug: "systems" },
  { path: "/missions", heading: "Mission Vectors", slug: "missions" },
  { path: "/timeline", heading: "Evidence Spine", slug: "timeline" },
  { path: "/evidence", heading: "Evidence Ledger", slug: "evidence" },
  { path: "/agents", heading: "Specialist Fleet", slug: "agents" },
  { path: "/notifications", heading: "Notification / Exception Center", slug: "notifications" },
  { path: "/executive", heading: "Executive", slug: "executive" },
]

for (const route of routes) {
  test(`truth lane ${route.slug} has no fabricated cost on overview`, async ({ page }) => {
    if (route.slug !== "overview") return
    await page.goto("/")
    await expect(page.getByRole("heading", { name: route.heading, exact: true })).toBeVisible()
    await expect(page.locator("body")).not.toContainText("$0.38")
    await expect(page.getByLabel(/Measured cost UNKNOWN · SIMULATED/i)).toBeVisible()
  })

  test(`truth lane smoke ${route.slug}`, async ({ page }) => {
    await page.goto(route.path)
    await expect(page.getByRole("heading", { name: route.heading, exact: true })).toBeVisible()
    await expect(page.locator(".data-source-pill")).toContainText("SIMULATED")
  })
}

test("systems resource profile excludes USD cost meters", async ({ page }) => {
  await page.goto("/systems")
  await expect(page.getByText("Foundry Resource Profile")).toBeVisible()
  const body = await page.locator(".systems-resources").innerText()
  expect(body).not.toMatch(/\$/)
})
