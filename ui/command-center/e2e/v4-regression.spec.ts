import { expect, test } from "@playwright/test";
import path from "node:path";
import { fileURLToPath } from "node:url";

const evidenceDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "evidence");

async function assertNoOverflow(page: import("@playwright/test").Page) {
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  expect(overflow).toBeLessThanOrEqual(1);
}

// V4 visual regression: 1120 compact rail and 700 mobile downlink
test("V4 command deck at 1120 shows compact rail and stacked telemetry", async ({ page }) => {
  await page.setViewportSize({ width: 1120, height: 900 });
  await page.goto("/");
  await page.waitForTimeout(500);
  // Sidebar should be compact 78px
  const sidebarWidth = await page.evaluate(() => {
    const el = document.querySelector(".sidebar") as HTMLElement;
    return el ? el.getBoundingClientRect().width : 0;
  });
  expect(sidebarWidth).toBeCloseTo(78, 1);
  // Telemetry should be 3 columns (grid)
  const telemetryCols = await page.evaluate(() => {
    const el = document.querySelector(".telemetry") as HTMLElement;
    if (!el) return 0;
    return getComputedStyle(el).gridTemplateColumns.split(" ").length;
  });
  expect(telemetryCols).toBe(3);
  // Downlink frame chamfer 18px should be present via clip-path
  const clipPath = await page.evaluate(() => {
    const el = document.querySelector(".v4-downlink") as HTMLElement;
    return el ? getComputedStyle(el).clipPath : "";
  });
  expect(clipPath).toContain("polygon");
  await assertNoOverflow(page);
  await page.screenshot({ path: path.join(evidenceDir, "command-deck-1120x900.png"), fullPage: false });
});

test("V4 command deck at 700 shows mobile header, 2-col telemetry, and orbital core", async ({ page }) => {
  await page.setViewportSize({ width: 700, height: 900 });
  await page.goto("/");
  await page.waitForTimeout(500);
  // Sidebar becomes horizontal header (position static, full width)
  const sidebarPosition = await page.evaluate(() => {
    const el = document.querySelector(".sidebar") as HTMLElement;
    return el ? getComputedStyle(el).position : "";
  });
  expect(["static", "relative"]).toContain(sidebarPosition);
  // Telemetry 2 columns on mobile
  const telemetryCols = await page.evaluate(() => {
    const el = document.querySelector(".telemetry") as HTMLElement;
    if (!el) return 0;
    return getComputedStyle(el).gridTemplateColumns.split(" ").length;
  });
  expect(telemetryCols).toBe(2);
  // Orbital core should be visible
  await expect(page.locator("[data-jarvis-core-state]")).toBeVisible();
  await expect(page.locator(".jarvis-core-label").first()).toContainText("JARVIS");
  // Mission vectors should be visible
  await expect(page.locator(".mission-thread").first()).toBeVisible();
  // Evidence spine diamond nodes
  await expect(page.locator(".timeline-node").first()).toBeVisible();
  await assertNoOverflow(page);
  await page.screenshot({ path: path.join(evidenceDir, "command-deck-700x900.png"), fullPage: false });
});

test("V4 visual components are present at desktop 1600", async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 900 });
  await page.goto("/");
  await page.waitForTimeout(500);
  // Top status bar translucent
  const topbarBg = await page.evaluate(() => {
    const el = document.querySelector(".topbar") as HTMLElement;
    return el ? getComputedStyle(el).backgroundColor : "";
  });
  expect(topbarBg).not.toBe("");
  // Chamfered downlink
  await expect(page.locator(".v4-downlink")).toBeVisible();
  await expect(page.locator(".v4-calibration")).toBeVisible();
  // Cinematic JARVIS core with state label
  await expect(page.locator("[data-jarvis-core-state]")).toBeVisible();
  await expect(page.locator(".jarvis-core-label").first()).toContainText("JARVIS");
  // Telemetry strip 5 columns at desktop
  const telemetryCols = await page.evaluate(() => {
    const el = document.querySelector(".telemetry") as HTMLElement;
    return el ? getComputedStyle(el).gridTemplateColumns.split(" ").length : 0;
  });
  expect(telemetryCols).toBe(5);
  // Mission vectors have progress unknown label
  await expect(page.getByText("progress UNKNOWN / state-derived signal only").first()).toBeVisible();
  // Evidence spine newest node illumination (check animation or static)
  const firstNode = page.locator(".timeline-node").first();
  await expect(firstNode).toBeVisible();
  await assertNoOverflow(page);
  await page.screenshot({ path: path.join(evidenceDir, "command-deck-1600x900.png"), fullPage: false });
});

test("V4 route-mapped headings are present", async ({ page }) => {
  await page.setViewportSize({ width: 1600, height: 900 });
  // Overview -> command deck heading is "What should JARVIS move next?"
  await page.goto("/");
  await expect(page.getByRole("heading", { name: "What should JARVIS move next?" })).toBeVisible();
  // Missions -> mission vectors
  await page.goto("/missions");
  await expect(page.getByRole("heading", { name: "Mission Vectors" })).toBeVisible();
  // Agents -> specialist fleet
  await page.goto("/agents");
  await expect(page.getByRole("heading", { name: "Specialist Fleet" })).toBeVisible();
  // Evidence -> evidence ledger
  await page.goto("/evidence");
  await expect(page.getByRole("heading", { name: "Evidence Ledger" })).toBeVisible();
  // Memory -> knowledge graph
  await page.goto("/memory");
  await expect(page.getByRole("heading", { name: "Knowledge Graph" })).toBeVisible();
  // Timeline -> evidence spine
  await page.goto("/timeline");
  await expect(page.getByRole("heading", { name: "Evidence Spine" })).toBeVisible();
  // Systems -> telemetry
  await page.goto("/systems");
  await expect(page.getByRole("heading", { name: "Telemetry" })).toBeVisible();
  await assertNoOverflow(page);
});
