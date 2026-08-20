import { expect, test } from "@playwright/test";

async function assertNoHorizontalOverflow(page: import("@playwright/test").Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(1);
}

test("mobile nav spans the full width with no sidebar offset", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  const nav = page.locator(".mobile-nav");
  await expect(nav).toBeVisible();
  const box = await nav.boundingBox();
  expect(box!.x).toBe(0);
  expect(box!.width).toBe(390);
  await assertNoHorizontalOverflow(page);
});

test("mobile drawer opens and closes with all sections", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.getByRole("button", { name: "Open all sections" }).click();
  await expect(page.getByRole("dialog", { name: "Navigation" })).toBeVisible();
  const drawer = page.getByRole("dialog", { name: "Navigation" });
  await expect(drawer.getByText("Timeline", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Close navigation" }).click();
  await expect(page.getByRole("dialog", { name: "Navigation" })).toHaveCount(0);
});

test("tablet layout has no sidebar and no horizontal overflow", async ({ page }) => {
  await page.setViewportSize({ width: 834, height: 1112 });
  await page.goto("/missions");
  await expect(page.locator(".sidebar")).toBeHidden();
  await expect(page.locator(".mobile-nav")).toBeVisible();
  await assertNoHorizontalOverflow(page);
});

test("desktop 1280 layout renders without horizontal overflow", async ({ page }) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/");
  await assertNoHorizontalOverflow(page);
  await page.goto("/evidence");
  await assertNoHorizontalOverflow(page);
  await page.goto("/systems");
  await assertNoHorizontalOverflow(page);
  await page.goto("/notifications");
  await assertNoHorizontalOverflow(page);
  await page.goto("/missions/M-VER-031");
  await assertNoHorizontalOverflow(page);
});

test("new detail surfaces have no horizontal overflow on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  for (const path of ["/notifications", "/agents/WRK-HERMES", "/evidence/EV-1142-003", "/systems/SYS-STOP", "/memory/n-verdict", "/missions/M-HYB-014"]) {
    await page.goto(path);
    await assertNoHorizontalOverflow(page);
  }
});
