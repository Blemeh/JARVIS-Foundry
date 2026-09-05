import { test, expect } from "@playwright/test"

const primaryRoutes = [
  { path: "/", nav: "JARVIS", heading: "Hybrid Worker Activation" },
  { path: "/work", nav: "WORK", heading: "Work" },
  { path: "/world", nav: "WORLD", heading: "World" },
  { path: "/capabilities", nav: "CAPABILITIES", heading: "Capabilities" },
  { path: "/you", nav: "YOU", heading: "You" },
  { path: "/activity", nav: "ACTIVITY", heading: "Evidence Spine" },
  { path: "/evidence", nav: "EVIDENCE", heading: "Evidence Ledger" },
  { path: "/settings", nav: "SETTINGS", heading: "Settings" },
]

test.describe("owner workflow navigation", () => {
  for (const route of primaryRoutes) {
    test(`${route.nav} route is reachable with active nav and data source label`, async ({ page }) => {
      await page.goto(route.path)
      await expect(page.getByRole("heading", { name: route.heading, exact: true })).toBeVisible()
      await expect(page.locator(".data-source-pill")).toContainText("SIMULATED")
      const nav = page.getByRole("navigation", { name: "Primary" })
      await expect(nav.getByRole("link", { name: route.nav, exact: true })).toHaveAttribute("aria-current", "page")
    })
  }

  test("trust pill shows trust state not data source mode", async ({ page }) => {
    await page.goto("/")
    const trust = page.locator("button.trust-pill")
    await expect(trust).toContainText(/SIMULATED|DEGRADED|PROVEN|SUSPENDED/)
    await expect(trust).not.toContainText("Data Source")
  })

  test("export ledger control is enabled in simulated mode with integrity label", async ({ page }) => {
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
