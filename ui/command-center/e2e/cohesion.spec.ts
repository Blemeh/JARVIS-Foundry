import { expect, test, type Page } from "@playwright/test";

async function collectErrors(page: Page) {
  const errors: string[] = [];
  page.on("pageerror", (err) => errors.push(`pageerror: ${err.message}`));
  page.on("console", (msg) => {
    if (msg.type() === "error") errors.push(`console.error: ${msg.text()}`);
  });
  return errors;
}

test("JOURNEY A + event consistency: verification failure through repair to pass", async ({ page }) => {
  const errors = await collectErrors(page);
  const navLink = (name: string) => ({
    click: async () => {
      const section = name === "Overview" ? "JARVIS" : name === "Notifications" || name === "Timeline" || name === "Systems" ? "SYSTEMS" : "OPERATIONS";
      if (name === "Overview" || name === "Systems") {
        await page.getByRole("link", { name: section, exact: true }).click();
        return;
      }
      const item = page.locator(".nav-rail-item").filter({ has: page.getByRole("link", { name: section, exact: true }) }).first();
      await item.hover();
      await item.getByRole("menuitem", { name, exact: true }).click();
    },
  });
  await page.goto("/notifications");
  await expect(page.getByRole("heading", { name: "Notification / Exception Center" })).toBeVisible();

  // verification.failed
  await page.getByRole("button", { name: /Simulate next event \(failed\)/ }).click();
  const failedItem = page.getByText("Verification failed", { exact: true });
  await expect(failedItem).toBeVisible();
  await expect(page.getByText(/Finding VF-2047 failed on mission M-VER-031/)).toBeVisible();
  await page.getByRole("link", { name: "M-VER-031" }).first().click();
  await expect(page).toHaveURL(/\/missions\/M-VER-031$/);
  await expect(page.getByRole("heading", { name: "Verification Cycle - Sol Audit" })).toBeVisible();
  await expect(page.getByText("VF-2047").first()).toBeVisible();
  await expect(page.getByText("FAILED", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("FAILED - repair pending")).toBeVisible();

  // repair.created
  await navLink("Notifications").click();
  await page.getByRole("button", { name: /Simulate next event \(repair\)/ }).click();
  await expect(page.getByText("Repair run created")).toBeVisible();
  await page.getByRole("link", { name: "WRK-OPENHANDS" }).first().click();
  await expect(page).toHaveURL(/\/agents\/WRK-OPENHANDS$/);
  await expect(page.getByText(/Repair run A-104 for mission M-VER-031/)).toBeVisible();

  // timeline reflects the failure and the repair
  await navLink("Timeline").click();
  await expect(page.getByText("verification.failed", { exact: true })).toBeVisible();
  await expect(page.getByText("repair.created", { exact: true })).toBeVisible();

  // worker.recovered then verification.passed reconcile everything
  await navLink("Notifications").click();
  await page.getByRole("button", { name: /Simulate next event \(recovery\)/ }).click();
  await expect(page.getByText("Worker recovered", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: /Simulate next event \(pass\)/ }).click();
  const failedAfter = page.locator(".notify-item").filter({ hasText: "Verification failed" }).first();
  await expect(failedAfter).toBeVisible();
  await expect(failedAfter.getByText("RESOLVED")).toBeVisible();
  await navLink("Timeline").click();
  await expect(page.getByText("verification.passed", { exact: true })).toBeVisible();
  await navLink("Missions").click();
  await page.locator(".mission-card").filter({ hasText: "Verification Cycle - Sol Audit" }).click();
  await expect(page).toHaveURL(/\/missions\/M-VER-031$/);
  await expect(page.getByText("PASSED", { exact: true }).first()).toBeVisible();
  expect(errors).toEqual([]);
});

test("JOURNEY B: worker crash propagates and recovery reconciles", async ({ page }) => {
  const errors = await collectErrors(page);
  const navLink = (name: string) => ({
    click: async () => {
      const section = name === "Overview" ? "JARVIS" : name === "Notifications" || name === "Timeline" || name === "Systems" ? "SYSTEMS" : "OPERATIONS";
      if (name === "Overview" || name === "Systems") {
        await page.getByRole("link", { name: section, exact: true }).click();
        return;
      }
      const item = page.locator(".nav-rail-item").filter({ has: page.getByRole("link", { name: section, exact: true }) }).first();
      await item.hover();
      await item.getByRole("menuitem", { name, exact: true }).click();
    },
  });
  await page.goto("/?scenario=WORKER_CRASH");
  await page.getByRole("button", { name: /Notifications, \d+ unread/ }).click();
  await expect(page.getByText("Worker crashed", { exact: true })).toBeVisible();
  await page.getByRole("link", { name: "WRK-OPENHANDS" }).first().click();
  await expect(page).toHaveURL(/\/agents\/WRK-OPENHANDS$/);
  await expect(page.getByRole("heading", { name: "OpenHands Worker", exact: true })).toBeVisible();
  await expect(page.getByText("FAILED", { exact: true }).first()).toBeVisible();
  await expect(page.getByText(/CRASHED - lease replayed/)).toBeVisible();

  await navLink("Missions").click();
  await page.getByRole("heading", { name: "Mission Vectors" }).waitFor({ state: "visible" });
  await page.locator(".mission-card").filter({ hasText: "Worker Crash Recovery" }).click();
  await expect(page).toHaveURL(/\/missions\/M-CRS-008$/);
  await expect(page.getByText("INTERRUPTED - crash")).toBeVisible();

  await page.locator(".md-hub-link").filter({ hasText: "Systems" }).click();
  await page.getByRole("button", { name: "Inspect OpenHands worker" }).click();
  await page.getByRole("link", { name: "Open system detail" }).click();
  await expect(page).toHaveURL(/\/systems\/SYS-OPENHANDS-WORKER$/);
  await expect(page.getByText("DEGRADED", { exact: true }).first()).toBeVisible();
  await expect(page.getByText("worker.crash", { exact: true })).toBeVisible();

  await navLink("Timeline").click();
  await expect(page.getByText("worker.crash", { exact: true })).toBeVisible();

  // Recovery scenario reconciles the same surfaces
  await page.goto("/?scenario=RECOVERY");
  await navLink("Agents").click();
  await page.locator(".agent-card").filter({ hasText: "OpenHands Worker" }).locator("a.agent-card-name-link").click();
  await expect(page).toHaveURL(/\/agents\/WRK-OPENHANDS$/);
  await expect(page.getByText("ACTIVE", { exact: true }).first()).toBeVisible();
  await navLink("Missions").click();
  await page.getByRole("heading", { name: "Mission Vectors" }).waitFor({ state: "visible" });
  await page.locator(".mission-card").filter({ hasText: "Worker Crash Recovery" }).click();
  await page.locator(".md-hub-link").filter({ hasText: "Systems" }).click();
  await page.getByRole("button", { name: "Inspect OpenHands worker" }).click();
  await page.getByRole("link", { name: "Open system detail" }).click();
  await expect(page.getByText("HEALTHY", { exact: true }).first()).toBeVisible();
  await navLink("Timeline").click();
  await expect(page.getByText("worker.recovered", { exact: true })).toBeVisible();
  expect(errors).toEqual([]);
});

test("JOURNEY C: evidence provenance is traceable end to end", async ({ page }) => {
  const errors = await collectErrors(page);
  await page.goto("/evidence/EV-1131-007");
  await expect(page.getByRole("heading", { name: "Ledger entry" })).toBeVisible();
  for (const step of ["Observation", "Evidence", "Claim", "Decision", "Outcome"]) {
    await expect(page.getByText(step, { exact: true }).first()).toBeVisible();
  }
  await expect(page.getByText(/Expected a7ffc9/)).toBeVisible();
  await page.getByRole("link", { name: "M-GAT-022" }).first().click();
  await expect(page.getByRole("heading", { name: "Evidence Gate Review - Ledger 1142" })).toBeVisible();
  await page.goto("/evidence/EV-1131-007");
  await page.getByRole("link", { name: "VF-2038" }).first().click();
  await expect(page.getByRole("heading", { name: "Evidence Gate Review - Ledger 1142" })).toBeVisible();
  await page.goto("/evidence/EV-1131-007");
  await expect(page.getByText("Position in the verification lineage")).toBeVisible();
  await expect(page.getByText("Finding", { exact: true }).first()).toBeVisible();
  await page.getByRole("link", { name: "TL-0912" }).click();
  await expect(page).toHaveURL(/\/timeline\?event=TL-0912/);
  await expect(page.getByText("Ledger 1142 sealed").first()).toBeVisible();
  expect(errors).toEqual([]);
});

test("JOURNEY D: simulated STOP propagates everywhere and resume restores", async ({ page }) => {
  const errors = await collectErrors(page);
  const navLink = (name: string) => ({
    click: async () => {
      const section = name === "Overview" ? "JARVIS" : name === "Notifications" || name === "Timeline" || name === "Systems" ? "SYSTEMS" : "OPERATIONS";
      if (name === "Overview" || name === "Systems") {
        await page.getByRole("link", { name: section, exact: true }).click();
        return;
      }
      const item = page.locator(".nav-rail-item").filter({ has: page.getByRole("link", { name: section, exact: true }) }).first();
      await item.hover();
      await item.getByRole("menuitem", { name, exact: true }).click();
    },
  });
  await page.goto("/");
  await page.getByRole("button", { name: /Emergency STOP/i }).first().click();
  await page.getByRole("button", { name: "Simulate STOP" }).click();
  await expect(page.getByText("SIMULATED STOP - UI CANDIDATE").first()).toBeVisible();
  await expect(page.getByText("Suspended", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Execute" })).toBeDisabled();
  await page.keyboard.press("Escape");
  await page.getByRole("button", { name: /Notifications, \d+ unread/ }).click();
  await expect(page.getByRole("button", { name: /Simulate/ })).toBeDisabled();
  await page.keyboard.press("Escape");

  await navLink("Systems").click();
  await page.getByRole("button", { name: "Inspect STOP subsystem" }).click();
  await page.getByRole("link", { name: "Open system detail" }).click();
  await expect(page).toHaveURL(/\/systems\/SYS-STOP$/);
  await expect(page.getByText("ACTIVE", { exact: true }).first()).toBeVisible();
  await expect(page.getByText(/SIMULATED STOP - UI CANDIDATE/).first()).toBeVisible();

  await navLink("Timeline").click();
  await expect(page.getByText("emergency_stop.requested", { exact: true })).toBeVisible();
  await expect(page.getByText("emergency_stop.frozen", { exact: true })).toBeVisible();

  await navLink("Notifications").click();
  await expect(page.getByText("STOP activated (simulated)")).toBeVisible();

  // resume
  await navLink("Overview").click();
  await page.getByRole("button", { name: /STOP simulation active/i }).first().click();
  await page.getByRole("button", { name: "Resume" }).click();
  await expect(page.getByText("SIMULATED STOP - UI CANDIDATE")).toHaveCount(0);
  await page.getByRole("button", { name: "Cancel" }).click();
  await navLink("Timeline").click();
  await expect(page.getByText("resume.reorientation", { exact: true })).toBeVisible();
  await expect(page.getByText("resume.reconciliation", { exact: true })).toBeVisible();
  await navLink("Systems").click();
  await page.getByRole("button", { name: "Inspect STOP subsystem" }).click();
  await page.getByRole("link", { name: "Open system detail" }).click();
  await expect(page.getByText("ARMED", { exact: true }).first()).toBeVisible();
  expect(errors).toEqual([]);
});

test("JOURNEY E: global search navigates to the correct deep links", async ({ page }) => {
  const errors = await collectErrors(page);
  await page.goto("/");
  await page.keyboard.press("/");
  const input = page.getByRole("combobox");
  await expect(input).toBeFocused();

  await input.fill("show mission M-HYB-014");
  await expect(page.getByText("Open mission M-HYB-014")).toBeVisible();
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/missions\/M-HYB-014$/);
  await expect(page.getByRole("heading", { name: "Hybrid Worker Activation" })).toBeVisible();

  await page.goto("/");
  await input.fill("EV-1131-007");
  await page.getByRole("option", { name: /EV-1131-007/ }).first().evaluate((el) => (el as HTMLElement).click());
  await expect(page).toHaveURL(/\/evidence\/EV-1131-007$/);

  await page.goto("/");
  await input.fill("show degraded systems");
  await page.keyboard.press("Enter");
  await expect(page).toHaveURL(/\/systems\?health=degraded/);
  await expect(page.getByText("Health DEGRADED")).toBeVisible();
  await expect(page.getByRole("button", { name: "Inspect Provider routing" })).toBeVisible();
  expect(errors).toEqual([]);
});

test("JOURNEY F: mission context filters persist across surfaces and back", async ({ page }) => {
  const errors = await collectErrors(page);
  await page.goto("/missions/M-HYB-014");
  await page.locator(".md-hub-link").filter({ hasText: "Timeline" }).click();
  await expect(page).toHaveURL(/\/timeline\?mission=M-HYB-014/);
  await expect(page.getByText("Mission M-HYB-014").first()).toBeVisible();
  await expect(page.getByText("Verification gate V-2 prepared")).toBeVisible();
  await expect(page.getByText("Provider degradation detected")).toHaveCount(0);

  await page.goBack();
  await expect(page).toHaveURL(/\/missions\/M-HYB-014$/);
  await expect(page.getByRole("heading", { name: "Hybrid Worker Activation" })).toBeVisible();

  await page.locator(".md-hub-link").filter({ hasText: "Evidence" }).click();
  await expect(page).toHaveURL(/\/evidence\?mission=M-HYB-014/);
  await expect(page.getByText("EV-1142-003").first()).toBeVisible();
  await expect(page.getByText("EV-1142-004").first()).toBeVisible();
  expect(errors).toEqual([]);
});

test("deep links survive reload for every entity surface", async ({ page }) => {
  const cases = [
    { path: "/agents/WRK-HERMES", text: "Hermes Orchestrator" },
    { path: "/evidence/EV-1142-003", text: "Handoff payload snapshot" },
    { path: "/systems/SYS-ROUTING", text: "Provider routing" },
    { path: "/memory/n-verdict", text: "VF-2041" },
    { path: "/timeline?mission=M-PRV-011", text: "Provider degradation detected" },
    { path: "/notifications", text: "Notification / Exception Center" },
  ];
  for (const c of cases) {
    await page.goto(c.path);
    await expect(page.getByText(c.text).first()).toBeVisible();
    await page.reload();
    await expect(page.getByText(c.text).first()).toBeVisible();
  }
});

test("unknown entity ids produce recovery states, not crashes", async ({ page }) => {
  const cases = [
    { path: "/missions/M-NOPE", text: "Mission not found" },
    { path: "/agents/NOBODY", text: "Agent not found" },
    { path: "/evidence/NOBODY", text: "Evidence not found" },
    { path: "/systems/NOBODY", text: "System not found" },
    { path: "/memory/nope", text: "Memory node not found" },
  ];
  for (const c of cases) {
    await page.goto(c.path);
    await expect(page.getByText(c.text)).toBeVisible();
  }
});

test("memory mission filter shows an honest empty state when no node exists", async ({ page }) => {
  await page.goto("/memory?mission=M-SUP-002");
  await expect(page.getByText("No node for M-SUP-002")).toBeVisible();
  await expect(page.getByText("Hybrid Worker Activation is executing through gate V-2.")).toHaveCount(0);
  await page.getByRole("button", { name: "Select Provider Degradation" }).click();
  await expect(page.getByText("Provider latency elevated to 1860ms")).toBeVisible();
});

test("keyboard-first navigation works without text-input hijack", async ({ page }) => {
  await page.goto("/");
  await page.keyboard.press("/");
  await expect(page.getByRole("combobox")).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("combobox")).not.toBeFocused();
  await page.keyboard.press("g");
  await page.keyboard.press("m");
  await expect(page).toHaveURL(/\/missions$/);
  await expect(page.getByRole("heading", { name: "Mission Vectors" })).toBeVisible();
});
