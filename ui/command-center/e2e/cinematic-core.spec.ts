import { test, expect } from "@playwright/test";

test("cinematic JARVIS core renders on overview with state label", async ({ page }) => {
  await page.goto("/");
  const core = page.locator("[data-jarvis-core-state]").first();
  await expect(core).toBeVisible();
  await expect(page.locator(".jarvis-core-label").first()).toContainText("JARVIS");
  await expect(core).toHaveAttribute("data-jarvis-core-state", /^(HEALTHY_IDLE|ACTIVE_MISSION|VERIFYING|DEGRADED|STOPPED|DISCONNECTED)$/);
});

test("deep-linked mission detail keeps cinematic shell and STOP control", async ({ page }) => {
  await page.goto("/missions/M-HYB-014");
  await expect(page.getByRole("heading", { name: "Hybrid Worker Activation" })).toBeVisible();
  await expect(page.getByRole("button", { name: /Emergency STOP/i }).first()).toBeVisible();
});

test("STOP scenario shifts core to stopped state", async ({ page }) => {
  await page.goto("/?scenario=STOP_ACTIVE");
  await expect(page.locator('[data-jarvis-core-state="STOPPED"]')).toBeVisible();
});

test("stream disconnected scenario shifts core state", async ({ page }) => {
  await page.goto("/?scenario=STREAM_DISCONNECTED");
  await expect(page.locator('[data-jarvis-core-state="DISCONNECTED"]')).toBeVisible();
});

test("responsive nav reaches key routes", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/");
  await page.locator(".mobile-nav").getByRole("link", { name: "Missions", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Mission Vectors" })).toBeVisible();
  await page.getByRole("button", { name: "Open all sections" }).click({ force: true });
  await page.getByRole("dialog", { name: "Navigation" }).getByRole("link", { name: "Evidence", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Evidence Ledger" })).toBeVisible();
});

test("no donor Gemini or voice APIs in runtime bundle markers", async ({ page }) => {
  await page.goto("/");
  const html = await page.content();
  expect(html).not.toMatch(/gemini/i);
  expect(html).not.toMatch(/SpeechRecognition|speechSynthesis/i);
});
