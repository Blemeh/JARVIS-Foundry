import { expect, test, type Page } from "@playwright/test";

const sections = [
  { path: "/", name: "Overview", heading: "What should JARVIS move next?" },
  { path: "/missions", name: "Missions", heading: "Mission Vectors" },
  { path: "/missions/M-HYB-014", name: "Mission Detail", heading: "Hybrid Worker Activation" },
  { path: "/agents", name: "Agents", heading: "Specialist Fleet" },
  { path: "/memory", name: "Memory", heading: "Knowledge Graph" },
  { path: "/evidence", name: "Evidence", heading: "Evidence Ledger" },
  { path: "/timeline", name: "Timeline", heading: "Evidence Spine" },
  { path: "/systems", name: "Systems", heading: "Telemetry" },
  { path: "/notifications", name: "Notifications", heading: "Notification / Exception Center" },
  { path: "/agents/WRK-HERMES", name: "Agent Detail", heading: "Hermes Orchestrator" },
  { path: "/evidence/EV-1131-007", name: "Evidence Detail", heading: "Ledger entry" },
  { path: "/systems/SYS-STOP", name: "System Detail", heading: "STOP subsystem" },
  { path: "/memory/n-verdict", name: "Memory Detail", heading: "Knowledge Graph" },
] as const;

async function collectErrors(page: Page) {
  const errors: string[] = [];
  page.on("pageerror", (err) => errors.push(`pageerror: ${err.message}`));
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(`console.error: ${msg.text()}`);
  });
  return errors;
}

for (const section of sections) {
  test(`${section.name} renders without console errors`, async ({ page }) => {
    const errors = await collectErrors(page);
    await page.goto(section.path);
    await expect(page.locator("main#main-content")).toBeVisible();
    await expect(page.getByRole("heading", { name: section.heading, exact: true })).toBeVisible();
    const text = await page.evaluate(() => document.body.innerText.length);
    expect(text).toBeGreaterThan(600);
    expect(errors).toEqual([]);
  });
}

test("Mission Detail is not blank and shows pipeline + verdicts", async ({ page }) => {
  const errors = await collectErrors(page);
  await page.goto("/missions/M-HYB-014");
  await expect(page.getByText("Execution Pipeline")).toBeVisible();
  await expect(page.getByText("Agent Telemetry Log")).toBeVisible();
  await expect(page.getByText("Verifier Verdicts")).toBeVisible();
  await expect(page.getByText("Re-evaluate Gate")).toBeVisible();
  await expect(page.getByText("Force Advance")).toHaveCount(0);
  const bodyText = await page.evaluate(() => document.body.innerText);
  expect(bodyText).not.toContain("99.8");
  expect(errors).toEqual([]);
});

test("trust is explainable and the hard-coded 99.8% is gone", async ({ page }) => {
  const errors = await collectErrors(page);
  await page.goto("/");
  const bodyText = await page.evaluate(() => document.body.innerText);
  expect(bodyText).not.toContain("99.8");
  expect(bodyText).toContain("DATA SOURCE: SIMULATED");
  await page.getByRole("button", { name: "Trust state SIMULATED. Open trust explanation." }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByRole("dialog").getByLabel(/Trust score not available/i)).toBeVisible();
  await expect(page.getByRole("dialog").getByText("Evidence coverage").first()).toBeVisible();
  expect(errors).toEqual([]);
});

test("the clock shows live local time", async ({ page }) => {
  await page.goto("/");
  const time = await page.locator(".topbar-clock-time").textContent();
  expect(time).toMatch(/^\d{2}:\d{2}:\d{2}$/);
});

test("Emergency STOP simulates without touching the network or real runtime", async ({ page }) => {
  const requests: string[] = [];
  const errors = await collectErrors(page);
  await page.goto("/");
  const pageOrigin = new URL(page.url()).origin;
  page.on("request", (req) => {
    if (!req.url().startsWith(pageOrigin)) requests.push(req.url());
  });

  await page.getByRole("button", { name: /Emergency STOP/i }).first().click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByText(/not connected to the JARVIS Supervisor/i)).toBeVisible();
  await page.getByRole("button", { name: "Simulate STOP" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.getByText("SIMULATED STOP - UI CANDIDATE")).toBeVisible();
  await expect(dialog.getByText("NO PRODUCTION SIDE EFFECT")).toBeVisible();
  await expect(page.getByText(/STOP simulation active/i).first()).toBeVisible();
  expect(requests).toEqual([]);
  expect(errors).toEqual([]);
});

test("missions paginate and controls are labelled", async ({ page }) => {
  await page.goto("/missions");
  await expect(page.getByText(/Showing 1-6 of 12 missions/i)).toBeVisible();
  await page.getByRole("button", { name: "Next page" }).click();
  await expect(page.getByText(/Showing 7-12 of 12 missions/i)).toBeVisible();
  const labelled = page.locator('a[aria-describedby^="tt-open-"]').first();
  await expect(labelled).toHaveAttribute("aria-describedby", /tt-open-/);
});

test("memory graph has a legend with semantic categories", async ({ page }) => {
  await page.goto("/memory");
  await expect(page.getByText("Semantic categories")).toBeVisible();
  const legend = page.locator(".graph-legend");
  await expect(legend.getByText("Mission", { exact: true })).toBeVisible();
  await expect(legend.getByText("Evidence", { exact: true })).toBeVisible();
  await expect(legend.getByText("Observation", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "Select M-HYB-014" }).click();
  await expect(page.getByText("Hybrid Worker Activation is executing through gate V-2.")).toBeVisible();
});

test("systems screen is complete and inspectable", async ({ page }) => {
  await page.goto("/systems");
  await expect(page.getByRole("heading", { name: "Telemetry" })).toBeVisible();
  await expect(page.getByText("STOP subsystem")).toBeVisible();
  await expect(page.getByText("Hermes Gateway")).toBeVisible();
  await page.getByRole("button", { name: "Inspect JARVIS Supervisor" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(page.getByText("Deterministic control core")).toBeVisible();
});
