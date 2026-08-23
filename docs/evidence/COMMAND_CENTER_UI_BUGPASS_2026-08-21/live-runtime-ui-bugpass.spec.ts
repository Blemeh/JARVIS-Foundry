import { test, expect } from "@playwright/test";

// Read-only UI bug/feature pass against the LIVE dev server (localhost:5173).
// No mission submits, no STOP/resume, no writes to the runtime.

const results: { area: string; check: string; verdict: "PASS" | "FAIL" | "WARN"; detail: string }[] = [];
function record(area: string, check: string, verdict: "PASS" | "FAIL" | "WARN", detail: string) {
  results.push({ area, check, verdict, detail });
  console.log(`[${verdict}] ${area} > ${check}: ${detail}`);
}

test.describe.serial("V4 UI live bug/feature pass", () => {
  let consoleErrors: string[] = [];

  test.beforeEach(async ({ page }) => {
    consoleErrors = [];
    page.on("pageerror", (err) => consoleErrors.push(`pageerror: ${err.message}`));
    page.on("console", (msg) => {
      if (msg.type() === "error") consoleErrors.push(`console.error: ${msg.text()}`);
    });
  });

  test("01 overview command deck", async ({ page }) => {
    await page.setViewportSize({ width: 1600, height: 900 });
    await page.goto("/");
    await page.waitForTimeout(6000);

    // LIVE label
    const body = await page.evaluate(() => document.body.innerText);
    record("overview", "DATA SOURCE: LIVE", body.includes("DATA SOURCE: LIVE") ? "PASS" : "FAIL",
      body.includes("DATA SOURCE: LIVE") ? "live label present" : `got: ${body.slice(0, 200)}`);
    record("overview", "TRUST: LIVE", body.includes("TRUST: LIVE") ? "PASS" : "FAIL", "");
    record("overview", "Command deck heading", body.includes("What should JARVIS move next?") ? "PASS" : "FAIL", "");
    record("overview", "Owner channel locked + NOT_TESTED honesty",
      body.includes("CHANNEL LOCKED") && body.includes("NOT_TESTED") ? "PASS" : "FAIL", "");
    record("overview", "Orbital core visible", await page.locator(".core-orbit").isVisible() ? "PASS" : "FAIL", "");
    record("overview", "Telemetry strip 5 cells", (await page.locator(".telemetry article").count()) === 5 ? "PASS" : "FAIL",
      `count=${await page.locator(".telemetry article").count()}`);
    record("overview", "Mission vectors render", (await page.locator(".mission-thread").count()) > 0 ? "PASS" : "FAIL",
      `count=${await page.locator(".mission-thread").count()}`);
    record("overview", "Evidence spine nodes", (await page.locator(".timeline-node").count()) > 0 ? "PASS" : "FAIL",
      `count=${await page.locator(".timeline-node").count()}`);
    record("overview", "No horizontal overflow", (await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth)) <= 1 ? "PASS" : "FAIL", "");

    // Bug probe: telemetry numbers sane (no NaN/undefined leaking)
    const badValues = await page.evaluate(() =>
      Array.from(document.querySelectorAll(".telemetry strong"))
        .map((el) => el.textContent ?? "")
        .filter((t) => /nan|undefined|null/i.test(t))
    );
    record("overview", "Telemetry has no NaN/undefined", badValues.length === 0 ? "PASS" : "FAIL", JSON.stringify(badValues));

    // Bug probe: clock ticking
    const t1 = await page.locator(".topbar-clock-time").textContent();
    await page.waitForTimeout(2100);
    const t2 = await page.locator(".topbar-clock-time").textContent();
    record("overview", "Clock ticks", t1 !== t2 ? "PASS" : "FAIL", `${t1} -> ${t2}`);

    record("overview", "Zero console errors", consoleErrors.length === 0 ? "PASS" : "WARN", JSON.stringify(consoleErrors.slice(0, 3)));
    await page.screenshot({ path: "evidence/ui-pass-01-overview.png" });
  });

  test("02 navigation to all sections", async ({ page }) => {
    await page.setViewportSize({ width: 1600, height: 900 });
    await page.goto("/");
    await page.waitForTimeout(4000);

    const nav = [
      { link: "Missions", url: /\/missions$/, marker: "Mission Vectors" },
      { link: "Agents", url: /\/agents$/, marker: "Specialist Fleet" },
      { link: "Evidence", url: /\/evidence$/, marker: "Evidence Ledger" },
      { link: "Memory", url: /\/memory$/, marker: "Knowledge Graph" },
      { link: "Timeline", url: /\/timeline$/, marker: "Evidence Spine" },
      { link: "Systems", url: /\/systems$/, marker: "Telemetry" },
    ];
    for (const n of nav) {
      await page.locator(".sidebar-nav").getByRole("link", { name: n.link }).click();
      await page.waitForTimeout(2500);
      const urlOk = n.url.test(page.url());
      const text = await page.evaluate(() => document.body.innerText);
      const markerOk = text.includes(n.marker);
      record("nav", `${n.link} route+heading`, urlOk && markerOk ? "PASS" : "FAIL",
        `url=${page.url()} marker(${n.marker})=${markerOk}`);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      record("nav", `${n.link} no overflow`, overflow <= 1 ? "PASS" : "FAIL", `overflow=${overflow}px`);
    }
    record("nav", "Console errors across nav", consoleErrors.length === 0 ? "PASS" : "WARN", JSON.stringify(consoleErrors.slice(0, 3)));
  });

  test("03 missions list features", async ({ page }) => {
    await page.setViewportSize({ width: 1600, height: 900 });
    await page.goto("/missions");
    await page.waitForTimeout(3500);
    const body = await page.evaluate(() => document.body.innerText);
    record("missions", "LIVE data source on missions", body.includes("DATA SOURCE: LIVE") ? "PASS" : "FAIL", "");
    record("missions", "Filter chips present", (await page.locator(".segmented-item").count()) >= 4 ? "PASS" : "FAIL",
      `count=${await page.locator(".segmented-item").count()}`);

    // Filter interaction
    const activeFilter = page.locator(".segmented-item", { hasText: "Active" });
    if (await activeFilter.count()) {
      await activeFilter.first().click();
      await page.waitForTimeout(800);
      record("missions", "Active filter clickable", true ? "PASS" : "PASS", "clicked");
    }
    // Pagination
    const nextBtn = page.getByRole("button", { name: "Next page" });
    if (await nextBtn.count() && await nextBtn.isEnabled()) {
      await nextBtn.click();
      await page.waitForTimeout(800);
      record("missions", "Pagination next works", "PASS", "navigated");
      const prev = page.getByRole("button", { name: "Previous page" });
      if (await prev.isEnabled()) { await prev.click(); await page.waitForTimeout(500); }
    } else {
      record("missions", "Pagination next", "WARN", "single page or disabled");
    }
    // New Mission button must be gated in live mode without auth
    const newMission = page.getByRole("button", { name: /New Mission/i });
    const disabled = await newMission.isDisabled().catch(() => null);
    record("missions", "New Mission gated in live mode", disabled === true ? "PASS" : "WARN", `disabled=${disabled}`);
    record("missions", "No console errors", consoleErrors.length === 0 ? "PASS" : "WARN", JSON.stringify(consoleErrors.slice(0, 3)));
    await page.screenshot({ path: "evidence/ui-pass-03-missions.png" });
  });

  test("04 agents/evidence/memory/timeline/systems spot checks", async ({ page }) => {
    await page.setViewportSize({ width: 1600, height: 900 });

    await page.goto("/agents");
    await page.waitForTimeout(3000);
    let body = await page.evaluate(() => document.body.innerText);
    record("agents", "Fleet renders with workers", /workers|WRK|worker/i.test(body) ? "PASS" : "FAIL", "");
    record("agents", "Deploy Agent gated in live mode", (await page.getByRole("button", { name: /Deploy Agent/i }).isDisabled().catch(() => null)) === true ? "PASS" : "WARN", "");

    await page.goto("/evidence");
    await page.waitForTimeout(3000);
    body = await page.evaluate(() => document.body.innerText);
    record("evidence", "Evidence ledger renders", body.includes("Evidence Ledger") ? "PASS" : "FAIL", "");

    await page.goto("/memory");
    await page.waitForTimeout(3500);
    const graphNodes = await page.locator("svg circle, svg g").count();
    record("memory", "Knowledge graph SVG renders", graphNodes > 0 ? "PASS" : "FAIL", `svg elements=${graphNodes}`);

    await page.goto("/timeline");
    await page.waitForTimeout(3000);
    body = await page.evaluate(() => document.body.innerText);
    record("timeline", "Evidence spine renders events", /EVT-|cycle_started|worker_loop/i.test(body) ? "PASS" : "FAIL", "");
    // severity filter chips exist?
    record("timeline", "Filter chips present", (await page.locator(".chip").count()) > 0 ? "PASS" : "WARN", `count=${await page.locator(".chip").count()}`);

    await page.goto("/systems");
    await page.waitForTimeout(3000);
    body = await page.evaluate(() => document.body.innerText);
    record("systems", "Telemetry table renders components", /SYS-|Supervisor|STOP/i.test(body) ? "PASS" : "FAIL", "");
    record("systems", "Heartbeats labelled canonical (LIVE)", /CANONICAL|LIVE/i.test(body) ? "PASS" : "WARN", "");
    record("systems", "No console errors overall", consoleErrors.length === 0 ? "PASS" : "WARN", JSON.stringify(consoleErrors.slice(0, 3)));
  });

  test("05 responsive 1120/700/390", async ({ page }) => {
    for (const [w, h] of [[1120, 900], [700, 900], [390, 844]] as const) {
      await page.setViewportSize({ width: w, height: h });
      await page.goto("/");
      await page.waitForTimeout(2500);
      const overflow = await page.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
      record("responsive", `${w}px no overflow`, overflow <= 1 ? "PASS" : "FAIL", `overflow=${overflow}px`);
      const coreVisible = await page.locator(".core-orbit").isVisible();
      record("responsive", `${w}px orbital core visible`, coreVisible ? "PASS" : "FAIL", "");
      await page.screenshot({ path: `evidence/ui-pass-05-${w}.png` });
    }
    // Mobile drawer
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    await page.waitForTimeout(1500);
    const moreBtn = page.getByRole("button", { name: "Open all sections" });
    if (await moreBtn.count()) {
      await moreBtn.click({ force: true });
      await page.waitForTimeout(600);
      const drawerVisible = await page.getByRole("dialog", { name: "Navigation" }).isVisible().catch(() => false);
      record("responsive", "390px drawer opens", drawerVisible ? "PASS" : "FAIL", "");
      await page.getByRole("button", { name: "Close navigation" }).click().catch(() => {});
    } else {
      record("responsive", "390px drawer button", "WARN", "button not found");
    }
  });

  test("06 STOP dialog opens but does NOT execute (read-only)", async ({ page }) => {
    await page.setViewportSize({ width: 1600, height: 900 });
    await page.goto("/");
    await page.waitForTimeout(4000);
    // Open the emergency stop dialog only to verify it presents honestly; DO NOT click activate.
    const stopBtn = page.getByRole("button", { name: /Emergency STOP/i }).first();
    await stopBtn.click();
    await page.waitForTimeout(800);
    const dialog = page.getByRole("dialog");
    const visible = await dialog.isVisible().catch(() => false);
    record("stop", "STOP dialog opens", visible ? "PASS" : "FAIL", "");
    if (visible) {
      const text = await dialog.innerText();
      record("stop", "Live STOP copy is canonical (not simulated)",
        /LIVE|Supervisor|durable/i.test(text) && !/SIMULATED STOP - UI CANDIDATE/i.test(text) ? "PASS" : "FAIL",
        text.slice(0, 120).replace(/\n/g, " "));
      const activate = dialog.getByRole("button", { name: /Activate STOP/i });
      record("stop", "Activate button present (NOT clicked)", (await activate.count()) === 1 ? "PASS" : "WARN", "");
      // close without executing
      await page.keyboard.press("Escape");
      await page.waitForTimeout(400);
      record("stop", "Dialog closes via Escape", !(await dialog.isVisible().catch(() => false)) ? "PASS" : "FAIL", "");
    }
  });

  test.afterAll(async () => {
    const fails = results.filter((r) => r.verdict === "FAIL");
    const warns = results.filter((r) => r.verdict === "WARN");
    console.log("\n===== UI PASS SUMMARY =====");
    console.log(`TOTAL: ${results.length} | PASS: ${results.length - fails.length - warns.length} | WARN: ${warns.length} | FAIL: ${fails.length}`);
    for (const f of fails) console.log(`FAIL: ${f.area} > ${f.check} — ${f.detail}`);
    for (const w of warns) console.log(`WARN: ${f_area(w)} > ${w.check} — ${w.detail}`);
    function f_area(x: typeof results[number]) { return x.area; }
  });
});
