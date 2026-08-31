import { test } from "@playwright/test";
import fs from "node:fs";
import path from "node:path";

function capturePath(testInfo: { outputPath: (name: string) => string }, name: string): string {
  const root = process.env.JARVIS_CAPTURE_DIR
    ? path.resolve(process.env.JARVIS_CAPTURE_DIR)
    : testInfo.outputPath("captures");
  fs.mkdirSync(root, { recursive: true });
  return path.join(root, name);
}

const captures = [
  { path: "/", name: "overview" },
  { path: "/missions", name: "missions" },
  { path: "/missions/M-HYB-014", name: "mission-detail" },
  { path: "/agents", name: "agents" },
  { path: "/memory", name: "memory" },
  { path: "/evidence", name: "evidence" },
  { path: "/timeline", name: "timeline" },
  { path: "/systems", name: "systems" },
  { path: "/notifications", name: "notifications" },
  { path: "/agents/WRK-HERMES", name: "agent-detail" },
  { path: "/evidence/EV-1131-007", name: "evidence-detail" },
  { path: "/systems/SYS-STOP", name: "system-detail" },
  { path: "/memory/n-verdict", name: "memory-detail" },
] as const;

const cohesionCaptures = [
  { path: "/timeline?mission=M-HYB-014", name: "timeline-filtered" },
  { path: "/evidence?mission=M-HYB-014", name: "evidence-filtered" },
  { path: "/missions/M-VER-031?scenario=VERIFICATION_FAILURE", name: "mission-verification-failure" },
] as const;

test("capture all eight sections at 1600x1280", async ({ page }, testInfo) => {
  test.setTimeout(120_000)
  await page.setViewportSize({ width: 1600, height: 1280 })
  for (const c of captures) {
    await page.goto(c.path);
    await page.waitForTimeout(450);
    await page.screenshot({ path: capturePath(testInfo, `${c.name}-1600x1280.png`), fullPage: false });
  }
});

test("capture responsive states", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1280, height: 800 });
  await page.goto("/");
  await page.screenshot({ path: capturePath(testInfo, "overview-1280x800.png") });

  await page.setViewportSize({ width: 834, height: 1112 });
  await page.goto("/missions");
  await page.waitForTimeout(400);
  await page.screenshot({ path: capturePath(testInfo, "missions-tablet-834x1112.png") });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.waitForTimeout(400);
  await page.screenshot({ path: capturePath(testInfo, "overview-mobile-390x844.png") });

  await page.getByRole("button", { name: "Open all sections" }).click();
  await page.waitForTimeout(300);
  await page.screenshot({ path: capturePath(testInfo, "mobile-drawer-390x844.png") });
});

test("capture cohesion surfaces", async ({ page }, testInfo) => {
  await page.setViewportSize({ width: 1600, height: 1280 });
  for (const c of cohesionCaptures) {
    await page.goto(c.path);
    await page.waitForTimeout(450);
    await page.screenshot({ path: capturePath(testInfo, `${c.name}-1600x1280.png`), fullPage: false });
  }

  await page.goto("/");
  const input = page.getByRole("combobox");
  await input.fill("EV-1142");
  await page.waitForTimeout(350);
  await page.screenshot({ path: capturePath(testInfo, "global-search-1600x1280.png"), fullPage: false });
  await input.press("Escape");

  await page.getByRole("button", { name: /Emergency STOP/i }).first().click();
  await page.getByRole("button", { name: "Simulate STOP" }).click();
  await page.waitForTimeout(500);
  await page.keyboard.press("Escape");
  await page.getByRole("navigation", { name: "Primary" }).getByRole("link", { name: "JARVIS", exact: true }).click();
  await page.waitForTimeout(400);
  await page.screenshot({ path: capturePath(testInfo, "overview-stop-1600x1280.png"), fullPage: false });

  await page.getByRole("button", { name: /STOP simulation active/i }).first().click();
  await page.getByRole("button", { name: "Resume" }).click();
  await page.getByRole("button", { name: "Cancel" }).click();
  await page.getByRole("button", { name: /Notifications, \d+ unread/ }).first().click();
  await page.waitForTimeout(350);
  await page.screenshot({ path: capturePath(testInfo, "notifications-popover-1600x1280.png"), fullPage: false });
});
