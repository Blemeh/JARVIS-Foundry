import { expect, test, type APIRequestContext, type Page } from "@playwright/test"
import { mkdirSync } from "node:fs"
import { join } from "node:path"

type SnapshotMission = {
  mission_id?: string
  objective?: string
  state?: string
}

type SnapshotTimelineEvent = {
  sequence?: number
  event_id?: string
  event_type?: string
  summary?: string
}

type CapabilityRow = {
  spec?: Record<string, unknown>
  metadata?: Record<string, unknown> | null
  readiness?: Record<string, unknown>
}

type Snapshot = {
  generated_at?: string
  source_of_truth?: { ledger_head_sequence?: number }
  health?: { degraded?: boolean }
  stop?: { active?: boolean }
  missions?: SnapshotMission[]
  workers?: Array<Record<string, unknown>>
  verification?: Array<Record<string, unknown>>
  evidence?: Array<Record<string, unknown>>
  memory?: Array<Record<string, unknown>>
  timeline?: SnapshotTimelineEvent[]
  briefing_packet?: Record<string, unknown>
  work_hierarchy?: {
    schema?: string
    status?: string
    goals?: Array<Record<string, unknown>>
    projects?: Array<Record<string, unknown>>
    objectives?: Array<Record<string, unknown>>
    campaigns?: Array<Record<string, unknown>>
    missions?: Array<Record<string, unknown>>
    actions?: Array<Record<string, unknown>>
  }
  decision_queue?: {
    open_count?: number
    open?: Array<Record<string, unknown>>
  }
  capability_fabric?: {
    capabilities?: CapabilityRow[]
  }
  effect_truth?: {
    attempts?: Array<Record<string, unknown>>
    lineage?: Array<Record<string, unknown>>
    actions?: Array<Record<string, unknown>>
  }
  owner_context?: {
    current_count?: number
    sections?: Record<string, Array<Record<string, unknown>>>
  }
}

type AppendResponse = {
  status?: string
  event_ids?: string[]
  ledger_head?: number
}

const apiBase = process.env.VITE_JARVIS_API_BASE
if (!apiBase) throw new Error("VITE_JARVIS_API_BASE must be provided by the live runner")
const controlBase = process.env.JARVIS_FIXTURE_CONTROL_BASE
if (!controlBase) throw new Error("JARVIS_FIXTURE_CONTROL_BASE must be provided by the live runner")

test.describe.configure({ mode: "serial" })

function capturePath(name: string): string | undefined {
  const dir = process.env.JARVIS_CAPTURE_DIR
  if (!dir) return undefined
  mkdirSync(dir, { recursive: true })
  return join(dir, name)
}

async function fetchSnapshot(request: APIRequestContext): Promise<Snapshot> {
  const response = await request.get(`${apiBase}/command-center`)
  expect(response.ok()).toBeTruthy()
  return await response.json() as Snapshot
}

async function controlState(request: APIRequestContext): Promise<Record<string, unknown>> {
  const response = await request.get(`${controlBase}/state`)
  expect(response.ok()).toBeTruthy()
  return await response.json() as Record<string, unknown>
}

function capabilityId(row: CapabilityRow): string {
  return String(row.spec?.capability_id ?? "")
}

function sourceLabel(snapshot: Snapshot): "LIVE" | "DEGRADED" {
  return snapshot.health?.degraded ? "DEGRADED" : "LIVE"
}

function ownerContextText(snapshot: Snapshot): string[] {
  return Object.values(snapshot.owner_context?.sections ?? {})
    .flat()
    .map((row) => String(row.text ?? ""))
}

function durableSignature(snapshot: Snapshot): Record<string, unknown> {
  const pairs = (
    rows: Array<Record<string, unknown>> | undefined,
    id: string,
    state: string,
  ) => (rows ?? [])
    .map((row) => `${String(row[id] ?? "")}:${String(row[state] ?? "")}`)
    .sort()
  const ownerIds = Object.values(snapshot.owner_context?.sections ?? {})
    .flat()
    .map((row) => String(row.context_id ?? row.directive_id ?? row.source_event_id ?? ""))
    .filter(Boolean)
    .sort()
  return {
    ledgerHead: snapshot.source_of_truth?.ledger_head_sequence,
    stop: snapshot.stop?.active,
    missions: (snapshot.missions ?? [])
      .map((row) => `${row.mission_id ?? ""}:${row.state ?? ""}`)
      .sort(),
    objectives: pairs(snapshot.work_hierarchy?.objectives, "objective_id", "status"),
    campaigns: pairs(snapshot.work_hierarchy?.campaigns, "campaign_id", "status"),
    verification: pairs(snapshot.verification, "verdict_id", "status"),
    evidence: (snapshot.evidence ?? []).map((row) => String(row.bundle_id ?? "")).sort(),
    memory: (snapshot.memory ?? []).map((row) => String(row.memory_id ?? "")).sort(),
    ownerIds,
    attempts: pairs(snapshot.effect_truth?.attempts, "attempt_id", "outcome"),
  }
}

async function expectCanonicalSource(page: Page, label: "LIVE" | "DEGRADED"): Promise<void> {
  await expect(page.locator(".data-source-pill")).toContainText(`DATA SOURCE: ${label}`)
  await expect(page.locator(".topbar-disconnect")).toHaveCount(0)
}

async function appendMarkers(
  request: APIRequestContext,
  label: string,
  count = 1,
): Promise<AppendResponse> {
  const response = await request.post(`${controlBase}/append`, { data: { label, count } })
  expect(response.ok()).toBeTruthy()
  const payload = await response.json() as AppendResponse
  expect(payload.status).toBe("APPENDED")
  expect(payload.event_ids).toHaveLength(count)
  return payload
}

async function exerciseStopAndResume(page: Page, expectedSource: "LIVE" | "DEGRADED"): Promise<void> {
  await page.goto("/")
  await expectCanonicalSource(page, expectedSource)
  await page.getByRole("button", { name: "Emergency STOP", exact: true }).first().click()
  const dialog = page.getByRole("dialog", { name: "Emergency STOP" })
  await expect(dialog).toBeVisible()
  await dialog.getByRole("button", { name: "Activate STOP" }).click()
  await expect(page.getByText("LIVE STOP - CANONICAL DIRECTIVE", { exact: true }).first()).toBeVisible()
  await expect(page.locator("[data-jarvis-core-state]")).toHaveAttribute("data-jarvis-core-state", "stopped")
  await expect.poll(async () => Boolean((await controlState(page.request)).stop_active)).toBe(true)

  await dialog.getByRole("button", { name: "Resume" }).click()
  await expect.poll(async () => Boolean((await controlState(page.request)).stop_active)).toBe(false)
  await expect(page.getByText("LIVE STOP - CANONICAL DIRECTIVE", { exact: true })).toHaveCount(0)
  await page.keyboard.press("Escape")
  await expect(dialog).toBeHidden()
  await expectCanonicalSource(page, expectedSource)
}

test("renders the complete source-backed product truth across Command Center surfaces", async ({ page }) => {
  const snapshot = await fetchSnapshot(page.request)
  const hierarchy = snapshot.work_hierarchy
  expect(hierarchy?.schema).toBe("v13.command-center-work.v1")
  expect(hierarchy?.status).toBe("AVAILABLE")

  const objective = hierarchy?.objectives?.find((row) => row.status === "ACTIVE")
  expect(objective?.objective_id).toBeTruthy()
  const objectiveId = String(objective!.objective_id)
  const objectiveLabel = String(objective!.label)
  const campaign = hierarchy?.campaigns?.find((row) =>
    row.objective_id === objectiveId
    && Array.isArray(row.wait_kinds)
    && row.wait_kinds.includes("PROVIDER"))
  expect(campaign?.campaign_id).toBeTruthy()
  const campaignId = String(campaign!.campaign_id)
  const missionIds = Array.isArray(objective!.mission_ids)
    ? objective!.mission_ids.map(String)
    : []
  expect(missionIds.length).toBeGreaterThanOrEqual(3)

  const completedMission = snapshot.missions?.find((row) =>
    missionIds.includes(String(row.mission_id))
    && ["COMPLETE", "COMPLETED"].includes(String(row.state).toUpperCase()))
  const activeMission = snapshot.missions?.find((row) =>
    missionIds.includes(String(row.mission_id))
    && ["ACTION_SELECTION", "READY", "EXECUTING", "VERIFYING"].includes(String(row.state).toUpperCase()))
  const blockedMission = snapshot.missions?.find((row) =>
    missionIds.includes(String(row.mission_id))
    && String(row.state).toUpperCase() === "BLOCKED")
  expect(completedMission?.mission_id).toBeTruthy()
  expect(activeMission?.mission_id).toBeTruthy()
  expect(blockedMission?.mission_id).toBeTruthy()
  const completedMissionId = String(completedMission!.mission_id)
  const activeMissionId = String(activeMission!.mission_id)
  const blockedMissionId = String(blockedMission!.mission_id)

  const activeWorker = snapshot.workers?.find((row) =>
    row.mission_id === activeMissionId && row.status === "RUNNING")
  expect(activeWorker?.run_id).toBeTruthy()
  const verdicts = snapshot.verification
    ?.filter((row) => row.mission_id === completedMissionId)
    .map((row) => row.status) ?? []
  expect(verdicts).toEqual(expect.arrayContaining(["FAIL", "PASS"]))
  expect(snapshot.workers?.some((row) =>
    row.mission_id === completedMissionId
    && row.role === "repair"
    && row.status === "COMPLETED")).toBe(true)
  expect(snapshot.evidence?.some((row) => row.mission_id === completedMissionId)).toBe(true)
  expect(snapshot.memory?.some((row) => row.mission_id === completedMissionId)).toBe(true)
  expect(snapshot.briefing_packet).toBeTruthy()
  expect(snapshot.decision_queue?.open_count ?? 0).toBeGreaterThan(0)

  const probation = snapshot.capability_fabric?.capabilities?.find((row) =>
    row.readiness?.trust_state === "PROBATION")
  const mcp = snapshot.capability_fabric?.capabilities?.find((row) =>
    String(row.metadata?.provenance && (row.metadata.provenance as Record<string, unknown>).source) === "mcp.discovery")
  expect(capabilityId(probation ?? {})).toBeTruthy()
  expect(probation?.readiness?.state).toBe("READY")
  expect(capabilityId(mcp ?? {})).toBeTruthy()
  expect(mcp?.readiness?.state).toBe("READY")
  expect(snapshot.timeline?.filter((row) => row.event_type === "mcp.server.connected")).toHaveLength(2)
  expect(snapshot.timeline?.some((row) => row.event_type === "mcp.server.disconnected")).toBe(true)
  expect(snapshot.timeline?.some((row) => row.event_type === "provider.recovered")).toBe(true)
  expect(snapshot.timeline?.some((row) => row.event_type === "worker.recovered")).toBe(true)

  const providerAttempt = snapshot.effect_truth?.attempts?.find((row) =>
    row.mission_id === completedMissionId && row.outcome === "COMPLETED")
  expect(providerAttempt?.attempt_id).toBeTruthy()
  expect(providerAttempt?.provider_ref).toBeTruthy()
  expect(snapshot.effect_truth?.lineage?.some((row) =>
    row.mission_id === completedMissionId && row.outcome === "COMPLETED")).toBe(true)
  expect(snapshot.owner_context?.current_count ?? 0).toBeGreaterThanOrEqual(4)
  const contextText = ownerContextText(snapshot)
  expect(contextText).toEqual(expect.arrayContaining([
    "JARVIS Command Center live product integration",
    "Prefer canonical readback over builder claims.",
    "Show evidence-backed milestones instead of fake percentage progress.",
    "Disposable local proof state is permitted; live production mutation is not.",
  ]))

  const expectedSource = sourceLabel(snapshot)
  await page.goto("/")
  await expect(page.getByRole("main").getByRole("heading", { level: 1 }).first()).toHaveText(objectiveLabel)
  await expectCanonicalSource(page, expectedSource)
  await expect(page.locator("[data-jarvis-core-state]")).toHaveAttribute("data-jarvis-core-state", "needs_owner")
  await expect(page.getByRole("region", { name: "Today" })).toBeVisible()
  await expect(page.getByRole("region", { name: "Recently completed" }).locator(`a[href="/missions/${completedMissionId}"]`)).toBeVisible()
  await expect(page.getByRole("region", { name: "Persistent JARVIS command surface" })).toBeVisible()
  await page.getByRole("button", { name: /Focus mode/i }).click()
  const focusDialog = page.getByRole("dialog", { name: "Focus mode" })
  await expect(focusDialog).toContainText("Progress evidence")
  await expect(focusDialog).not.toContainText("-1%")
  await page.keyboard.press("Escape")
  const overviewCapture = capturePath("live-overview-1600x1280.png")
  if (overviewCapture) await page.screenshot({ path: overviewCapture, fullPage: false })

  await page.goto("/work?status=all")
  await expect(page.getByRole("heading", { name: "Work", exact: true })).toBeVisible()
  await expect(page.getByText(objectiveLabel, { exact: true }).first()).toBeVisible()
  await expect(page.getByText(campaignId, { exact: true }).first()).toBeVisible()
  await expect(page.getByText(/waiting: PROVIDER/i)).toBeVisible()
  for (const missionId of [completedMissionId, activeMissionId, blockedMissionId]) {
    await expect(page.locator(`a[href="/missions/${missionId}"]`).first()).toBeVisible()
  }
  await expect(page.getByText(/action.*worker.*evidence.*verdict/i).first()).toBeVisible()
  await page.setViewportSize({ width: 390, height: 844 })
  const mobileWorkGeometry = await page.locator(".rm-hierarchy-map").evaluate((element) => ({
    height: element.getBoundingClientRect().height,
    pageScrollWidth: document.documentElement.scrollWidth,
    pageClientWidth: document.documentElement.clientWidth,
  }))
  expect(mobileWorkGeometry.pageScrollWidth).toBeLessThanOrEqual(mobileWorkGeometry.pageClientWidth + 1)
  expect(mobileWorkGeometry.height).toBeLessThan(230)
  const mobileWorkCapture = capturePath("live-work-390x844.png")
  if (mobileWorkCapture) await page.screenshot({ path: mobileWorkCapture, fullPage: false })
  await page.setViewportSize({ width: 1600, height: 1280 })

  await page.goto("/world")
  await expect(page.getByRole("heading", { name: "World", exact: true })).toBeVisible()
  await expect(page.getByRole("heading", { name: "Awareness field" })).toBeVisible()
  await expect(page.getByText("World is advisory context.", { exact: false })).toBeVisible()
  await page.locator(".rm-world-signal").first().click()
  const signalDialog = page.getByRole("dialog", { name: "Signal evidence" })
  await expect(signalDialog).toContainText("Evidence references")
  await expect(signalDialog.getByRole("link", { name: "Open source event" })).toHaveAttribute("href", /^\/activity\?event=/)
  await page.keyboard.press("Escape")

  const probationId = capabilityId(probation!)
  const mcpId = capabilityId(mcp!)
  await page.goto("/capabilities")
  await expect(page.getByRole("heading", { name: "Capabilities", exact: true })).toBeVisible()
  await expect(page.getByText("Gmail remains deferred by owner", { exact: true })).toBeVisible()
  const probationButton = page.getByRole("button", { name: new RegExp(probationId) })
  await expect(probationButton).toContainText("PROBATION")
  await probationButton.click()
  let capabilityDialog = page.getByRole("dialog", { name: "Capability record" })
  await expect(capabilityDialog).toContainText("Observed readiness")
  await expect(capabilityDialog).toContainText("READY")
  await expect(capabilityDialog).toContainText("Catalog trust")
  await expect(capabilityDialog).toContainText("PROBATION")
  await expect(capabilityDialog).toContainText("self_expansion")
  await expect(capabilityDialog).toContainText("VERIFIED")
  await page.keyboard.press("Escape")
  const mcpButton = page.getByRole("button", { name: new RegExp(mcpId) })
  await expect(mcpButton).toContainText("READY")
  await mcpButton.click()
  capabilityDialog = page.getByRole("dialog", { name: "Capability record" })
  await expect(capabilityDialog).toContainText("mcp.discovery")
  await expect(capabilityDialog).toContainText("ESTABLISHED")
  await page.keyboard.press("Escape")

  await page.goto("/you")
  await expect(page.getByRole("heading", { name: "You", exact: true })).toBeVisible()
  await expect(page.getByRole("heading", { name: "Owner authority remains human" })).toBeVisible()
  for (const text of contextText.slice(-4)) {
    await expect(page.getByText(text, { exact: true }).first()).toBeVisible()
  }
  await expect(page.getByRole("heading", { name: "Waiting on you" })).toBeVisible()

  await page.goto(`/missions/${encodeURIComponent(completedMissionId)}`)
  const pipeline = page.locator(".md-pipeline")
  await expect(pipeline.getByRole("heading", { name: "Execution Pipeline" })).toBeVisible()
  for (const label of ["Owner goal", "Plan", "Mission", "Worker", "Action / artifact", "Evidence", "Verifier", "Repair / retry", "Memory", "Owner briefing"]) {
    await expect(pipeline.getByText(label, { exact: true })).toBeVisible()
  }
  await expect(pipeline.locator(".md-stage").filter({ hasText: "Repair / retry" })).toContainText("COMPLETE")
  await expect(page.getByText("FAILED", { exact: true })).toBeVisible()
  await expect(page.getByText("COMPLETE", { exact: true }).first()).toBeVisible()
  const attemptId = String(providerAttempt!.attempt_id)
  await expect(page.locator(`a[href="/records/provider_attempt/${encodeURIComponent(attemptId)}"]`)).toBeVisible()
  await page.evaluate(() => window.scrollTo(0, 0))
  const missionCapture = capturePath("live-mission-detail-1600x1280.png")
  if (missionCapture) await page.screenshot({ path: missionCapture, fullPage: false })

  await page.goto(`/records/provider_attempt/${encodeURIComponent(attemptId)}`)
  await expect(page.getByRole("heading", { name: "provider attempt record" })).toBeVisible()
  await expect(page.getByText("READ-ONLY PROJECTION", { exact: true })).toBeVisible()
  await expect(page.locator(".forensic-json")).toContainText(String(providerAttempt!.provider_ref))

  await page.goto("/executive")
  await expect(page.getByRole("heading", { name: "Autonomous Executive", exact: true })).toBeVisible()
  await expect(page.getByRole("region", { name: "Executive overview" })).toBeVisible()
  await page.goto("/evolution")
  await expect(page.getByText("Canonical evolution projection", { exact: true })).toBeVisible()
})

test("reconciles duplicate, out-of-order, stale, disconnected, refreshed, restarted, and STOP truth", async ({ page }) => {
  test.setTimeout(90_000)
  const initial = await fetchSnapshot(page.request)
  const expectedSource = sourceLabel(initial)
  await page.goto("/activity")
  await expectCanonicalSource(page, expectedSource)
  await expect(page.getByRole("heading", { name: "Evidence Spine", exact: true })).toBeVisible()

  let networkDown = false
  let reorderPending = false
  let reorderDelivered = false
  let staleProjection: Snapshot | null = null

  await page.route("**/api/timeline?**", async (route) => {
    if (networkDown) {
      await route.abort("connectionfailed")
      return
    }
    let response
    try {
      response = await route.fetch()
    } catch {
      await route.abort("connectionfailed").catch(() => undefined)
      return
    }
    const payload = await response.json() as {
      events?: SnapshotTimelineEvent[]
      [key: string]: unknown
    }
    if (reorderPending && (payload.events?.length ?? 0) >= 2) {
      reorderPending = false
      reorderDelivered = true
      const reversed = [...payload.events!].reverse()
      await route.fulfill({
        response,
        json: { ...payload, events: [...reversed, reversed[0]] },
      })
      return
    }
    await route.fulfill({ response })
  })
  await page.route("**/api/command-center", async (route) => {
    if (networkDown) {
      await route.abort("connectionfailed")
      return
    }
    if (staleProjection) {
      await route.fulfill({
        status: 200,
        contentType: "application/json",
        body: JSON.stringify(staleProjection),
      })
      return
    }
    await route.continue()
  })

  reorderPending = true
  const markerBatch = await appendMarkers(page.request, "ledger reorder proof", 2)
  await expect.poll(() => reorderDelivered).toBe(true)
  await page.getByRole("textbox", { name: "Search timeline events" }).fill("ledger reorder proof")
  await expect(page.getByText("ledger reorder proof 1", { exact: true })).toHaveCount(1)
  await expect(page.getByText("ledger reorder proof 2", { exact: true })).toHaveCount(1)
  expect(new Set(markerBatch.event_ids).size).toBe(2)
  await expectCanonicalSource(page, expectedSource)

  networkDown = true
  await expect(page.locator(".data-source-pill")).toContainText("DATA SOURCE: DISCONNECTED", { timeout: 10_000 })
  await expect(page.locator(".topbar-disconnect")).toBeVisible()
  networkDown = false
  await page.getByRole("button", { name: "Reconnect", exact: true }).click()
  await expectCanonicalSource(page, expectedSource)

  const staleCandidate = await fetchSnapshot(page.request)
  await appendMarkers(page.request, "accepted newer truth", 1)
  await page.getByRole("textbox", { name: "Search timeline events" }).fill("accepted newer truth")
  await expect(page.getByText("accepted newer truth 1", { exact: true })).toHaveCount(1)
  const acceptedNewer = await fetchSnapshot(page.request)
  expect(acceptedNewer.source_of_truth?.ledger_head_sequence)
    .toBeGreaterThan(staleCandidate.source_of_truth?.ledger_head_sequence ?? -1)

  staleProjection = staleCandidate
  await expect(page.locator(".data-source-pill")).toContainText("DATA SOURCE: DISCONNECTED", { timeout: 10_000 })
  await expect(page.locator(".data-source-pill")).toHaveAttribute("title", /stale snapshot sequence/)
  staleProjection = null
  await page.getByRole("button", { name: "Reconnect", exact: true }).click()
  await expectCanonicalSource(page, expectedSource)

  await exerciseStopAndResume(page, expectedSource)

  const beforeRestart = await fetchSnapshot(page.request)
  const restartPromise = page.request.post(`${controlBase}/restart`, { data: { delay_ms: 2000 } })
  await expect(page.locator(".data-source-pill")).toContainText("DATA SOURCE: DISCONNECTED", { timeout: 10_000 })
  const restartResponse = await restartPromise
  expect(restartResponse.ok()).toBeTruthy()
  expect(await restartResponse.json()).toMatchObject({ status: "RESTARTED", restart_count: 1 })
  const reconnect = page.getByRole("button", { name: "Reconnect", exact: true })
  if (await reconnect.isVisible().catch(() => false)) await reconnect.click()
  await expectCanonicalSource(page, expectedSource)
  await expect.poll(async () => Number((await controlState(page.request)).restart_count)).toBe(1)

  const beforeRefresh = await fetchSnapshot(page.request)
  expect(beforeRefresh.source_of_truth?.ledger_head_sequence)
    .toBe(beforeRestart.source_of_truth?.ledger_head_sequence)
  const signature = durableSignature(beforeRefresh)
  await page.goto("/activity")
  await page.getByRole("textbox", { name: "Search timeline events" }).fill("ledger reorder proof")
  await expect(page.getByText("ledger reorder proof 1", { exact: true })).toHaveCount(1)
  await page.reload()
  await expectCanonicalSource(page, expectedSource)
  await page.getByRole("textbox", { name: "Search timeline events" }).fill("ledger reorder proof")
  await expect(page.getByText("ledger reorder proof 1", { exact: true })).toHaveCount(1)
  expect(durableSignature(await fetchSnapshot(page.request))).toEqual(signature)

  // A second control cycle proves the browser's pre-restart HttpOnly session
  // is rejected and transparently re-established against the replacement API.
  await exerciseStopAndResume(page, expectedSource)
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
