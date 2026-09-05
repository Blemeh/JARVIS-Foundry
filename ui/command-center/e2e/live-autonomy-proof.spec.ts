import { expect, test } from "@playwright/test"
import { mkdirSync } from "node:fs"
import { join } from "node:path"

type Snapshot = {
  missions?: Array<{ mission_id?: string; state?: string }>
  verification?: Array<{ mission_id?: string; status?: string }>
  evidence?: Array<{ mission_id?: string; bundle_id?: string }>
  memory?: Array<{ mission_id?: string; memory_id?: string }>
  briefing_packet?: Record<string, unknown>
  source_of_truth?: Record<string, unknown>
}

const apiBase = process.env.VITE_JARVIS_API_BASE
if (!apiBase) throw new Error("VITE_JARVIS_API_BASE must be provided by the live runner")

function capturePath(name: string): string | undefined {
  const dir = process.env.JARVIS_CAPTURE_DIR
  if (!dir) return undefined
  mkdirSync(dir, { recursive: true })
  return join(dir, name)
}

test("renders the source-backed autonomous loop across Command Center surfaces", async ({ page }) => {
  const response = await page.request.get(`${apiBase}/command-center`)
  expect(response.ok()).toBeTruthy()
  const snapshot = (await response.json()) as Snapshot
  const completedMission = snapshot.missions?.find((mission) => mission.state === "COMPLETE" || mission.state === "COMPLETED")
  expect(completedMission?.mission_id).toBeTruthy()
  const missionId = completedMission!.mission_id!
  const missionVerdicts = snapshot.verification?.filter((item) => item.mission_id === missionId).map((item) => item.status) ?? []
  expect(missionVerdicts).toEqual(expect.arrayContaining(["FAIL", "PASS"]))
  expect(snapshot.evidence?.some((item) => item.mission_id === missionId)).toBe(true)
  expect(snapshot.memory?.some((item) => item.mission_id === missionId)).toBe(true)
  expect(snapshot.briefing_packet).toBeTruthy()
  expect(snapshot.source_of_truth).toBeTruthy()

  await page.goto("/")
  await expect(page.getByRole("main").getByRole("heading", { level: 1 }).first()).toBeVisible()
  await expect(page.locator(".data-source-pill")).toContainText("LIVE")
  await expect(page.locator("[data-jarvis-core-state]")).toBeVisible()
  await expect(page.getByRole("region", { name: "Today" })).toBeVisible()
  await expect(page.getByRole("region", { name: "Recently completed" }).locator(`a[href="/missions/${missionId}"]`)).toBeVisible()
  await expect(page.getByRole("region", { name: "Discovered" })).toBeVisible()
  await expect(page.getByRole("region", { name: "What happens next" })).toBeVisible()
  await expect(page.getByRole("region", { name: "Persistent JARVIS command surface" })).toBeVisible()
  const overviewText = await page.locator("body").innerText()
  expect(overviewText).not.toContain("Complete roadmap V4 without touching live state")
  expect(overviewText).not.toContain("VERIFIER_RUN_1142")
  expect(overviewText).not.toContain("8F9A.22C")
  const overviewCapture = capturePath("live-overview-1600x1280.png")
  if (overviewCapture) await page.screenshot({ path: overviewCapture, fullPage: false })

  await page.goto("/work?status=completed")
  await expect(page.getByRole("heading", { name: "Work", exact: true })).toBeVisible()
  await expect(page.locator(`a[href="/missions/${missionId}"]`)).toBeVisible()

  await page.goto("/world")
  await expect(page.getByRole("heading", { name: "World", exact: true })).toBeVisible()
  await expect(page.getByRole("heading", { name: "Awareness field" })).toBeVisible()

  await page.goto("/capabilities")
  await expect(page.getByRole("heading", { name: "Capabilities", exact: true })).toBeVisible()
  await expect(page.getByText("Gmail remains deferred by owner", { exact: true })).toBeVisible()
  await expect(page.getByText("DEFERRED_BY_OWNER", { exact: true })).toBeVisible()

  await page.goto("/you")
  await expect(page.getByRole("heading", { name: "You", exact: true })).toBeVisible()
  await expect(page.getByRole("heading", { name: "Owner authority remains human" })).toBeVisible()

  await page.goto(`/missions/${encodeURIComponent(missionId)}`)
  const pipeline = page.locator(".md-pipeline")
  await expect(pipeline.getByRole("heading", { name: "Execution Pipeline" })).toBeVisible()
  for (const label of ["Owner goal", "Plan", "Mission", "Worker", "Action / artifact", "Evidence", "Verifier", "Repair / retry", "Memory", "Owner briefing"]) {
    await expect(pipeline.getByText(label, { exact: true })).toBeVisible()
  }
  await expect(pipeline.locator(".md-stage").filter({ hasText: "Repair / retry" })).toContainText("COMPLETE")
  await expect(page.getByText("FAILED", { exact: true })).toBeVisible()
  await expect(page.getByText("COMPLETE", { exact: true }).first()).toBeVisible()
  const missionCapture = capturePath("live-mission-detail-1600x1280.png")
  if (missionCapture) await page.screenshot({ path: missionCapture, fullPage: false })

  await page.goto("/executive")
  await expect(page.getByRole("heading", { name: "Autonomous Executive", exact: true })).toBeVisible()
  await expect(page.getByRole("region", { name: "Executive overview" })).toBeVisible()
  await expect(page.getByRole("region", { name: "Autonomous activity" })).toBeVisible()
  await expect(page.getByText(/^Owner action:/).first()).toBeVisible()
  const executiveCapture = capturePath("live-executive-1600x1280.png")
  if (executiveCapture) await page.screenshot({ path: executiveCapture, fullPage: false })

  await page.goto("/evolution")
  await expect(page.getByText("Canonical evolution projection", { exact: true })).toBeVisible()
})

test("keeps missing backend state unavailable instead of falling back to fixtures", async ({ page }) => {
  await page.route("**/api/command-center", async (route) => {
    await route.fulfill({ status: 503, contentType: "application/json", body: JSON.stringify({ error: "disposable backend offline" }) })
  })
  await page.goto("/evidence")
  await expect(page.getByText(/Evidence unavailable|unavailable|disconnected/i).first()).toBeVisible()
  const body = await page.locator("body").innerText()
  expect(body).not.toContain("EV-1131-007")
  expect(body).not.toContain("VERIFIER_RUN_1142")
})
