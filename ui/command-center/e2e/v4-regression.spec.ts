import { expect, test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

function capturePath(testInfo: { outputPath: (name: string) => string }, name: string): string {
  const root = testInfo.outputPath("captures");
  fs.mkdirSync(root, { recursive: true });
  return path.join(root, name);
}

async function assertNoOverflow(page: import("@playwright/test").Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(1);
}

test("remastered homepage at 1120 shows compact navigation and command dock", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1120, height: 900 });
  await page.goto("/");
  await page.waitForTimeout(500);

  const railWidth = await page.evaluate(() => {
    const el = document.querySelector(".nav-rail-shell") as HTMLElement;
    return el ? el.getBoundingClientRect().width : 0;
  });
  expect(railWidth).toBeCloseTo(78, 2);

  await expect(page.locator(".commandbar")).toBeVisible();
  await expect(page.locator(".rm-focus-field")).toBeVisible();
  await expect(page.locator("[data-jarvis-core-state]")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Hybrid Worker Activation" })).toBeVisible();

  await assertNoOverflow(page);
  await page.screenshot({ path: capturePath(testInfo, "command-deck-1120x900.png"), fullPage: false });
});

test("remastered homepage at 700 shows stacked focus and Nucleus", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 700, height: 900 });
  await page.goto("/");
  await page.waitForTimeout(500);

  await expect(page.locator(".nav-rail-shell")).toBeHidden();
  await expect(page.locator("[data-jarvis-core-state]")).toBeVisible();
  await expect(page.locator(".jarvis-nucleus__state").first()).toContainText("EXECUTING");
  await expect(page.locator(".rm-lifecycle")).toBeVisible();
  await expect(page.locator(".rm-attention")).toBeVisible();

  await assertNoOverflow(page);
  await page.screenshot({ path: capturePath(testInfo, "command-deck-700x900.png"), fullPage: false });
});

test("remastered visual system at desktop 1600", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1600, height: 900 });
  await page.goto("/");
  await page.waitForTimeout(500);

  const topbarBg = await page.evaluate(() => {
    const el = document.querySelector(".topbar") as HTMLElement;
    return el ? getComputedStyle(el).backgroundColor : "";
  });
  expect(topbarBg).not.toBe("");

  await expect(page.locator(".jarvis-os")).toBeVisible();
  await expect(page.locator("[data-jarvis-core-state]")).toBeVisible();
  await expect(page.locator(".rm-lifecycle")).toBeVisible();
  await expect(page.locator(".rm-today")).toBeVisible();
  await expect(page.locator(".commandbar-form")).toBeVisible();

  await assertNoOverflow(page);
  await page.screenshot({ path: capturePath(testInfo, "command-deck-1600x900.png"), fullPage: false });
});

test("remastered IA and legacy forensic route headings are present", async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 900 });

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Hybrid Worker Activation" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "Today" })).toBeVisible();
  await expect(page.getByRole("heading", { name: "What happens next" })).toBeVisible();

  await page.goto("/work");
  await expect(page.getByRole("heading", { name: "Work", exact: true })).toBeVisible();

  await page.goto("/world");
  await expect(page.getByRole("heading", { name: "World", exact: true })).toBeVisible();

  await page.goto("/capabilities");
  await expect(page.getByRole("heading", { name: "Capabilities", exact: true })).toBeVisible();

  await page.goto("/you");
  await expect(page.getByRole("heading", { name: "You", exact: true })).toBeVisible();

  await page.goto("/operations");
  await expect(page.getByRole("heading", { name: "Operations" })).toBeVisible();

  await page.goto("/missions");
  await expect(page.getByRole("heading", { name: "Mission Vectors" })).toBeVisible();

  await page.goto("/agents");
  await expect(page.getByRole("heading", { name: "Specialist Fleet" })).toBeVisible();

  await page.goto("/evidence");
  await expect(page.getByRole("heading", { name: "Evidence Ledger" })).toBeVisible();

  await page.goto("/memory");
  await expect(page.getByRole("heading", { name: "Knowledge Graph" })).toBeVisible();

  await page.goto("/timeline");
  await expect(page.getByRole("heading", { name: "Evidence Spine" })).toBeVisible();

  await page.goto("/systems");
  await expect(page.getByRole("heading", { name: "Telemetry" })).toBeVisible();

  await assertNoOverflow(page);
});
