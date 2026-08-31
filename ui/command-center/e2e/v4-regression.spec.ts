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

test("GOD MODE homepage at 1120 shows nav rail and telemetry bar", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1120, height: 900 });
  await page.goto("/");
  await page.waitForTimeout(500);

  const railWidth = await page.evaluate(() => {
    const el = document.querySelector(".nav-rail-shell") as HTMLElement;
    return el ? el.getBoundingClientRect().width : 0;
  });
  expect(railWidth).toBeCloseTo(64, 2);

  await expect(page.locator(".telemetry-bar")).toBeVisible();
  await expect(page.locator(".jarvis-os-hero")).toBeVisible();
  await expect(page.locator("[data-jarvis-core-state]")).toBeVisible();
  await expect(page.getByRole("heading", { name: "Command JARVIS" })).toBeVisible();

  await assertNoOverflow(page);
  await page.screenshot({ path: capturePath(testInfo, "command-deck-1120x900.png"), fullPage: false });
});

test("GOD MODE homepage at 700 shows stacked layout and core", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 700, height: 900 });
  await page.goto("/");
  await page.waitForTimeout(500);

  await expect(page.locator(".nav-rail-shell")).toBeHidden();
  await expect(page.locator("[data-jarvis-core-state]")).toBeVisible();
  await expect(page.locator(".jarvis-core-label").first()).toContainText("JARVIS");
  await expect(page.locator(".op-thread").first()).toBeVisible();
  await expect(page.locator(".intel-stream-item").first()).toBeVisible();

  await assertNoOverflow(page);
  await page.screenshot({ path: capturePath(testInfo, "command-deck-700x900.png"), fullPage: false });
});

test("GOD MODE visual components at desktop 1600", async ({ page }, testInfo) => {
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
  await expect(page.locator(".intelligence-rail")).toBeVisible();
  await expect(page.locator(".telemetry-bar-item").first()).toBeVisible();
  await expect(page.locator(".owner-command-form")).toBeVisible();

  await assertNoOverflow(page);
  await page.screenshot({ path: capturePath(testInfo, "command-deck-1600x900.png"), fullPage: false });
});

test("GOD MODE route headings are present", async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 900 });

  await page.goto("/");
  await expect(page.getByRole("heading", { name: "Command JARVIS" })).toBeVisible();
  await expect(page.getByText("Active operations")).toBeVisible();
  await expect(page.getByText("Intelligence stream")).toBeVisible();

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
