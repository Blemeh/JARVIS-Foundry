import { test, expect } from "@playwright/test"
import AxeBuilder from "@axe-core/playwright"

const routes = [
  { path: "/", name: "Overview" },
  { path: "/missions", name: "Missions" },
  { path: "/agents", name: "Agents" },
  { path: "/evidence", name: "Evidence" },
  { path: "/systems", name: "Systems" },
  { path: "/timeline", name: "Timeline" },
  { path: "/notifications", name: "Notifications" },
  { path: "/executive", name: "Executive" },
  { path: "/memory", name: "Memory" },
]

for (const route of routes) {
  test(`accessibility scan ${route.name}`, async ({ page }) => {
    await page.goto(route.path)
    await page.locator("main#main-content").waitFor({ state: "visible" })
    await page.waitForLoadState("networkidle")

    const results = await new AxeBuilder({ page })
      .withTags(["wcag2a", "wcag2aa", "best-practice"])
      .analyze()

    expect(results.violations, formatViolations(results.violations)).toEqual([])
  })
}

test("keyboard skip link focuses main content", async ({ page }) => {
  await page.goto("/")
  await page.keyboard.press("Tab")
  const skip = page.getByRole("link", { name: "Skip to content" })
  await expect(skip).toBeFocused()
  await page.keyboard.press("Enter")
  await expect(page.locator("#main-content")).toBeFocused()
})

test("primary navigation exposes aria-current on active route", async ({ page }) => {
  await page.goto("/missions")
  await expect(page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "Missions" })).toHaveAttribute("aria-current", "page")
})

const formatViolations = (violations: { id: string; impact?: string; nodes: { target: string[] }[] }[]) =>
  violations
    .map((v) => `${v.id} (${v.impact}): ${v.nodes.map((n) => n.target.join(" ")).join("; ")}`)
    .join("\n")
