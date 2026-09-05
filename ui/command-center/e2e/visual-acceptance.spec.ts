import { expect, test } from "@playwright/test";

test("Focus Mode becomes a full-viewport operational field", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await page.getByRole("button", { name: "Focus mode" }).click();

  const dialog = page.getByRole("dialog", { name: "Focus mode" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("region", { name: "Next transition" })).toBeVisible();
  await expect(dialog.getByText("Mission and workers")).toBeVisible();
  await expect(dialog.getByText("Blocker / owner boundary")).toBeVisible();

  const bounds = await dialog.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    return {
      width: rect.width,
      height: rect.height,
      viewportWidth: document.documentElement.clientWidth,
      viewportHeight: document.documentElement.clientHeight,
    };
  });
  expect(bounds.width).toBeGreaterThanOrEqual(bounds.viewportWidth - 1);
  expect(bounds.height).toBeGreaterThanOrEqual(bounds.viewportHeight - 1);

  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
});

test("delegation modes remain explicit on desktop and mobile", async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.goto("/");
  await page.getByRole("button", { name: "Handle" }).click();
  await expect(page.getByRole("button", { name: "Handle" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByRole("combobox", { name: /Direct JARVIS/ })).toHaveValue("");

  await page.setViewportSize({ width: 390, height: 844 });
  await page.reload();
  const mode = page.getByRole("combobox", { name: "Delegation mode" });
  await expect(mode).toBeVisible();
  await mode.selectOption("Watch");
  await expect(page.getByRole("combobox", { name: /Direct JARVIS/ })).toHaveValue("");
});

test("supported runtime scenarios change the Nucleus structure", async ({ page }) => {
  await page.goto("/?scenario=WORKER_CRASH");
  await expect(page.locator('[data-jarvis-core-state="recovering"] .jarvis-nucleus__recovery-frame')).toHaveCSS("opacity", "0.78");

  await page.goto("/?scenario=MISSION_BLOCKED");
  const blockedStructure = await page
    .locator('[data-jarvis-core-state="blocked"] .jarvis-nucleus__unresolved')
    .evaluate((element) => {
      const style = getComputedStyle(element);
      return {
        opacity: Number(style.opacity),
        animationName: style.animationName,
      };
    });
  expect(blockedStructure.opacity).toBeGreaterThan(0.2);
  expect(blockedStructure.animationName).toBe("rm-alert");

  await page.goto("/?scenario=STOP_ACTIVE");
  await expect(page.locator('[data-jarvis-core-state="stopped"] .jarvis-nucleus__halt-frame')).toHaveCSS("opacity", "0.88");
  await expect(page.locator('[data-jarvis-core-state="stopped"] .jarvis-nucleus__particles')).toHaveCSS("display", "none");
  await expect(page.locator(".rm-lifecycle .is-suspended")).toContainText("Suspended here");
  await expect(page.getByText(/Suspended at Verify/i)).toBeVisible();

  await page.getByRole("button", { name: "Focus mode" }).click();
  await expect(page.getByRole("dialog", { name: "Focus mode" }).getByText("Suspended at", { exact: true })).toBeVisible();
  await expect(page.locator(".rm-focus-mode-lifecycle .is-suspended")).toContainText("Suspended here");
});

test("mobile Work hierarchy is a compact shared rail without overflow", async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto("/work");

  const geometry = await page.locator(".rm-hierarchy-map").evaluate((element) => ({
    clientWidth: element.clientWidth,
    scrollWidth: element.scrollWidth,
    height: element.getBoundingClientRect().height,
  }));
  expect(geometry.scrollWidth).toBeLessThanOrEqual(geometry.clientWidth + 1);
  expect(geometry.height).toBeLessThan(230);
});
