import { expect, test } from "@playwright/test";

async function assertNoHorizontalOverflow(page: import("@playwright/test").Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(1);
}

test("compact nav rail at 1120 has 78px rendered width and no overflow", async ({ page }) => {
  await page.setViewportSize({ width: 1120, height: 900 });
  await page.goto("/");
  const railWidth = await page.evaluate(() => {
    const el = document.querySelector(".nav-rail-shell") as HTMLElement;
    return el ? el.getBoundingClientRect().width : 0;
  });
  expect(railWidth).toBeCloseTo(78, 2);
  await assertNoHorizontalOverflow(page);
});

test("mobile drawer opens and closes with all sections", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  // V4 mobile nav is fixed at bottom; use force to avoid commandbar intercept
  await page.getByRole("button", { name: "Open all sections" }).click({ force: true });
  await expect(page.getByRole("dialog", { name: "Navigation" })).toBeVisible();
  const drawer = page.getByRole("dialog", { name: "Navigation" });
  await expect(drawer.getByText("EVIDENCE", { exact: true })).toBeVisible();
  await expect(drawer.getByText("SETTINGS", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Close navigation" }).click();
  await expect(page.getByRole("dialog", { name: "Navigation" })).toHaveCount(0);
});

test("tablet layout at 834 has nav rail and no horizontal overflow", async ({ page }) => {
  await page.setViewportSize({ width: 834, height: 1112 });
  await page.goto("/missions");
  const railWidth = await page.evaluate(() => {
    const el = document.querySelector(".nav-rail-shell") as HTMLElement;
    return el ? el.getBoundingClientRect().width : 0;
  });
  expect(railWidth).toBeCloseTo(78, 2);
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

test("mobile 700 homepage has core and no overflow", async ({ page }) => {
  await page.setViewportSize({ width: 700, height: 900 });
  await page.goto("/");
  await assertNoHorizontalOverflow(page);
  await expect(page.locator(".jarvis-os")).toBeVisible();
  await expect(page.locator("[data-jarvis-core-state]")).toBeVisible();
});

test("new detail surfaces have no horizontal overflow on mobile", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  for (const path of ["/notifications", "/agents/WRK-HERMES", "/evidence/EV-1142-003", "/systems/SYS-STOP", "/memory/n-verdict", "/missions/M-HYB-014"]) {
    await page.goto(path);
    await assertNoHorizontalOverflow(page);
  }
});

test("small phone 375 keeps focus, command, and bottom navigation operable", async ({ page }) => {
  await page.setViewportSize({ width: 375, height: 812 });
  await page.goto("/");
  await assertNoHorizontalOverflow(page);
  await expect(page.getByRole("heading", { name: "Hybrid Worker Activation" })).toBeVisible();
  await expect(page.getByRole("combobox", { name: /Direct JARVIS/ })).toBeVisible();
  const stopTarget = await page.locator(".mobile-nav-stop .stop-control").evaluate((element) => {
    const bounds = element.getBoundingClientRect();
    return { width: bounds.width, height: bounds.height };
  });
  expect(stopTarget.width).toBeGreaterThanOrEqual(44);
  expect(stopTarget.height).toBeGreaterThanOrEqual(44);
});

test("mobile owner attention remains fully actionable above the command dock", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await expect(page.locator(".rm-attention")).toBeVisible();
  const geometry = await page.evaluate(() => {
    const attention = document.querySelector(".rm-attention")?.getBoundingClientRect();
    const dock = document.querySelector(".commandbar")?.getBoundingClientRect();
    return attention && dock ? { attentionBottom: attention.bottom, dockTop: dock.top } : null;
  });
  expect(geometry).not.toBeNull();
  expect(geometry!.attentionBottom).toBeLessThanOrEqual(geometry!.dockTop);
  await expect(page.locator(".rm-attention").getByRole("link")).toBeVisible();
});

test("phone landscape keeps the focus surface free of horizontal overflow", async ({ page }) => {
  await page.setViewportSize({ width: 844, height: 390 });
  await page.goto("/");
  await assertNoHorizontalOverflow(page);
  await expect(page.locator(".rm-focus-field")).toBeVisible();
  await expect(page.locator(".commandbar")).toBeVisible();
});

test("reduced-motion preference collapses ongoing Nucleus motion", async ({ page }) => {
  await page.emulateMedia({ reducedMotion: "reduce" });
  await page.goto("/");
  const animation = await page.locator(".jarvis-nucleus__orbit--outer").evaluate((element) => {
    const style = getComputedStyle(element);
    return { duration: style.animationDuration, iterations: style.animationIterationCount };
  });
  expect(Number.parseFloat(animation.duration)).toBeLessThanOrEqual(0.001);
  expect(animation.iterations).toBe("1");
});
