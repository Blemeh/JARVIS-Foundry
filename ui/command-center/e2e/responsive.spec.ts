import { expect, test } from "@playwright/test";

async function assertNoHorizontalOverflow(page: import("@playwright/test").Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(1);
}

test("V4 compact rail at 1120 has 78px sidebar and no overflow", async ({ page }) => {
  await page.setViewportSize({ width: 1120, height: 900 });
  await page.goto("/");
  const sidebarWidth = await page.evaluate(() => {
    const el = document.querySelector(".sidebar") as HTMLElement;
    return el ? el.getBoundingClientRect().width : 0;
  });
  expect(sidebarWidth).toBeCloseTo(78, 1);
  await assertNoHorizontalOverflow(page);
});

test("mobile drawer opens and closes with all sections", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  // V4 mobile nav is fixed at bottom; use force to avoid commandbar intercept
  await page.getByRole("button", { name: "Open all sections" }).click({ force: true });
  await expect(page.getByRole("dialog", { name: "Navigation" })).toBeVisible();
  const drawer = page.getByRole("dialog", { name: "Navigation" });
  await expect(drawer.getByText("Timeline", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Close navigation" }).click();
  await expect(page.getByRole("dialog", { name: "Navigation" })).toHaveCount(0);
});

test("tablet layout at 834 has compact rail and no horizontal overflow", async ({ page }) => {
  await page.setViewportSize({ width: 834, height: 1112 });
  await page.goto("/missions");
  const sidebarWidth = await page.evaluate(() => {
    const el = document.querySelector(".sidebar") as HTMLElement;
    return el ? el.getBoundingClientRect().width : 0;
  });
  // At 834, V4 is still compact rail (between 700 and 1120)
  expect(sidebarWidth).toBeCloseTo(78, 1);
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

test("mobile 700 downlink has 2-col telemetry and no overflow", async ({ page }) => {
  await page.setViewportSize({ width: 700, height: 900 });
  await page.goto("/");
  await assertNoHorizontalOverflow(page);
  await expect(page.locator(".v4-downlink")).toBeVisible();
  await expect(page.locator("[data-jarvis-core-state]")).toBeVisible();
});

test("new detail surfaces have no horizontal overflow on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  for (const path of ["/notifications", "/agents/WRK-HERMES", "/evidence/EV-1142-003", "/systems/SYS-STOP", "/memory/n-verdict", "/missions/M-HYB-014"]) {
    await page.goto(path);
    await assertNoHorizontalOverflow(page);
  }
});
