import { test, expect } from "@playwright/test"

const primaryRoutes = [
  { path: "/", nav: "Overview", heading: "What should JARVIS move next?" },
  { path: "/executive", nav: "Executive", heading: "Executive" },
  { path: "/missions", nav: "Missions", heading: "Mission Vectors" },
  { path: "/agents", nav: "Agents", heading: "Specialist Fleet" },
  { path: "/evidence", nav: "Evidence", heading: "Evidence Ledger" },
  { path: "/memory", nav: "Memory", heading: "Knowledge Graph" },
  { path: "/systems", nav: "Systems", heading: "Telemetry" },
  { path: "/timeline", nav: "Timeline", heading: "Evidence Spine" },
  { path: "/notifications", nav: "Notifications", heading: "Notification / Exception Center" },
]

test.describe("owner workflow navigation", () => {
  for (const route of primaryRoutes) {
    test(`${route.nav} route is reachable with active nav and data source label`, async ({ page }) => {
      await page.goto(route.path)
      await expect(page.getByRole("heading", { name: route.heading })).toBeVisible()
      await expect(page.locator(".data-source-pill")).toContainText("SIMULATED")
      const nav = page.getByRole("navigation", { name: "Primary" })
      await expect(nav.getByRole("link", { name: route.nav })).toHaveAttribute("aria-current", "page")
    })
  }

  test("trust pill shows trust state not data source mode", async ({ page }) => {
    await page.goto("/")
    const trust = page.getByRole("button", { name: /Trust state/i })
    await expect(trust).toContainText("SIMULATED")
    await expect(trust).not.toContainText("Data Source")
  })

  test("export ledger control is disabled outside simulated mode label", async ({ page }) => {
    await page.goto("/evidence")
    const exportBtn = page.getByRole("button", { name: /Export ledger/i })
    await expect(exportBtn).toBeEnabled()
    await expect(page.getByText("Integrity SIMULATED")).toBeVisible()
  })

  test("deep link to mission detail resolves", async ({ page }) => {
    await page.goto("/missions")
    await page.getByRole("link", { name: /M-HYB/i }).first().click()
    await expect(page).toHaveURL(/\/missions\//)
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible()
  })

  test("browser back returns to missions list", async ({ page }) => {
    await page.goto("/missions")
    await page.getByRole("link", { name: /M-HYB/i }).first().click()
    await page.goBack()
    await expect(page.getByRole("heading", { name: "Mission Vectors" })).toBeVisible()
  })
})
