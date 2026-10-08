import { render, screen, waitFor, within } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { describe, expect, it } from "vitest"
import { Overview, OverviewContent } from "../Overview"
import { CommandCenterProvider } from "../../context/CommandCenterContext"
import type {
  AutonomyProjection,
  Mission,
  OwnerAttentionRecord,
  OwnerControlSurface,
} from "../../lib/contracts"
import type { OverviewData } from "../../lib/overviewLoader"
import userEvent from "@testing-library/user-event"

function renderOverview() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <CommandCenterProvider>
        <Overview />
      </CommandCenterProvider>
    </MemoryRouter>,
  )
}

describe("Overview truth lane", () => {
  it("shows UNKNOWN confidence when executive prediction is absent", async () => {
    renderOverview()
    await waitFor(() => expect(screen.getByText(/Confidence UNKNOWN/)).toBeInTheDocument())
  })

  it("opens the current work in focus mode", async () => {
    renderOverview()
    await waitFor(() => expect(screen.getByRole("heading", { name: "Hybrid Worker Activation" })).toBeInTheDocument())
    await userEvent.click(screen.getByRole("button", { name: /Focus mode/i }))
    expect(screen.getByRole("dialog", { name: /Focus mode/i })).toBeInTheDocument()
    expect(screen.getByRole("region", { name: "Next transition" })).toBeInTheDocument()
    expect(screen.getByText("Mission and workers")).toBeInTheDocument()
    expect(screen.getByText("Verification")).toBeInTheDocument()
    expect(screen.getAllByText("SIMULATED").length).toBeGreaterThan(0)
  })

  it("renders today, completion, discovery, and next-action depth", async () => {
    renderOverview()
    await waitFor(() => expect(screen.getByRole("heading", { name: "Today" })).toBeInTheDocument())
    expect(screen.getByRole("heading", { name: "Recently completed" })).toBeInTheDocument()
    expect(screen.getByRole("heading", { name: "Discovered" })).toBeInTheDocument()
    expect(screen.getByRole("heading", { name: "What happens next" })).toBeInTheDocument()
  })
})

export const HOOWEN_DIRECTIVE = "owner-delegation-643042f8f84795e7de8b3d79a4b7142c"
export const HOOWEN_LABEL = "Hoowen — Active strategic goal"
export const HOOWEN_EXACT_TEXT = "Turn Hoowen into a commercially credible workforce-management product."

function hoowenMission(init: {
  id: string
  status: Mission["status"]
  startedAt: string
  owner: string
  title: string
  objective: string
  nextAction: string
}): Mission {
  return {
    id: init.id,
    code: init.id,
    title: init.title,
    description: init.title,
    status: init.status,
    priority: "NORMAL",
    agentsActive: 0,
    completion: init.status === "COMPLETE" ? 100 : 0,
    nextAction: init.nextAction,
    stage: init.status,
    startedAt: init.startedAt,
    objective: init.objective,
    owner: init.owner,
  }
}

export function buildHoowenOverviewData(): OverviewData {
  const historicalBlocked = hoowenMission({
    id: "mission-4646c7023b05",
    status: "BLOCKED",
    startedAt: "2026-08-19T12:00:00Z",
    owner: "portfolio:pw-dupkey-9f31:CI_FAILURE",
    title: "Overnight CI verification sweep",
    objective: "Historical CI verification failure.",
    nextAction: "Fix the CI pipeline.",
  })
  const unrelatedCompleted = hoowenMission({
    id: "mission-unrelated-01",
    status: "COMPLETE",
    startedAt: "2026-10-06T12:00:00Z",
    owner: "portfolio:some-other-thing",
    title: "Unrelated portfolio tidy-up",
    objective: "Unrelated portfolio work completed.",
    nextAction: "No follow-up recorded.",
  })
  const followup2 = hoowenMission({
    id: "mission-62250d335ab6",
    status: "COMPLETE",
    startedAt: "2026-10-06T15:34:00Z",
    owner: `directive:${HOOWEN_DIRECTIVE}:follow-up-2`,
    title: "Hoowen follow-up 2",
    objective: "Hoowen follow-up 2 complete with its outcome summary.",
    nextAction: "Start follow-up 3.",
  })
  const followup3 = hoowenMission({
    id: "mission-fe81cb0d1e37",
    status: "BLOCKED",
    startedAt: "2026-10-06T15:50:00Z",
    owner: `directive:${HOOWEN_DIRECTIVE}:follow-up-3`,
    title: "Hoowen follow-up 3",
    objective: "Hoowen follow-up 3 blocked after builder failure.",
    nextAction: "Repair the builder failure and re-run verification.",
  })
  // Open record linked to the HISTORICAL mission only: no linked action exists
  // for Hoowen work, so NEEDS YOU must stay "Nothing needed from you."
  const decoyRecord: OwnerAttentionRecord = {
    queue_id: "q-historical-1",
    status: "OPEN",
    classification: "DECISION_REQUIRED",
    summary: "Approve the CI pipeline fix.",
    consequence: "The old CI sweep stays red until you approve.",
    mission_id: historicalBlocked.id,
    opened_at: "2026-10-06T16:00:00Z",
  }
  const ownerSurface: OwnerControlSurface = {
    source: "SIMULATED",
    runtimeState: "RUNNING",
    stateReason: "test",
    stateSource: "test",
    paused: false,
    stopped: false,
    resumeAvailable: false,
    resumableDirectiveIds: [],
    goals: [
      {
        directive_id: HOOWEN_DIRECTIVE,
        active: 1,
        command_type: "DELEGATE",
        updated_at: "2026-10-06T15:50:00Z",
        label: HOOWEN_LABEL,
        exact_text: HOOWEN_EXACT_TEXT,
      },
    ],
    constraints: [],
    vetoes: [],
    reprioritizations: [],
    redirects: [],
    directiveHistory: [],
    briefings: [],
    resultTraceability: { status: "TEST", results: [], count: 0 },
    briefingPacket: null,
    briefingL10n: null,
    decisionQueue: {
      open: [decoyRecord],
      history: [],
      open_count: 1,
      history_count: 0,
      counts_by_classification: {},
    },
  }
  const autonomy: AutonomyProjection = {
    schema: "v11.command-center-autonomy.v1",
    status: "AVAILABLE",
    reason: null,
    generated_at: "2026-10-06T16:00:00Z",
    executive: {
      state: "WAIT",
      cycle_id: null,
      stage: null,
      last_observation_cycle: null,
      next_wake: "2026-10-06T17:00:00Z",
      wait_reason: "Waiting on the next supervised window.",
      active_wip: { value: null, status: "UNAVAILABLE" },
    },
    funnel: {},
    candidates: [],
    sources: [],
    timeline: [],
    current_work: null,
    limits: {},
  }
  return {
    missions: [historicalBlocked, unrelatedCompleted, followup2, followup3],
    overview: {
      version: "test",
      schema: null,
      build: "test",
      releaseState: "TEST",
      uptime: "1h",
      resourceUsage: [],
    },
    trust: {
      state: "SIMULATED",
      score: null,
      gateStatus: "GATED",
      evidenceCoverage: 0,
      unresolvedFindings: 0,
      health: "UNKNOWN",
      lastVerifiedAt: "2026-10-06T15:00:00Z",
      factors: [],
    },
    workers: [],
    evidence: [],
    timeline: [],
    executive: null,
    ownerAttention: [decoyRecord],
    obsidianVault: [],
    ownerSurface,
    operationalIntelligence: null,
    autonomy,
    worldIntelligence: null,
    capabilityFabric: null,
  }
}

function renderHoowenHome(data: OverviewData = buildHoowenOverviewData()) {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <CommandCenterProvider>
        <OverviewContent {...data} />
      </CommandCenterProvider>
    </MemoryRouter>,
  )
}

describe("Overview owner-goal prioritization", () => {
  it("shows the active Hoowen goal card with truthful blocked progress and next", async () => {
    renderHoowenHome()
    // NOW is the active goal short label while no executing mission exists.
    await screen.findByRole("heading", { name: HOOWEN_LABEL })
    // Hoowen card is present even though the current child is BLOCKED.
    expect(screen.getByText(HOOWEN_LABEL)).toBeInTheDocument()
    // WHY carries the serving goal exact text plus the executive wait reason.
    expect(screen.getByText(new RegExp(HOOWEN_EXACT_TEXT.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")))).toBeInTheDocument()
    // PROGRESS names the current blocked child and says Blocked truthfully.
    expect(screen.getByText(/Hoowen follow-up 3 blocked after builder failure\./)).toBeInTheDocument()
    expect(screen.getAllByText(/Blocked/).length).toBeGreaterThan(0)
    // NEXT comes from the current child's exact next_action.
    expect(
      screen.getAllByText("Repair the builder failure and re-run verification.").length,
    ).toBeGreaterThan(0)
    // No raw mission id leaks into the primary heading copy.
    const heading = await screen.findByRole("heading", { name: HOOWEN_LABEL })
    expect(heading.textContent).not.toMatch(/mission-[0-9a-f]{4,}/)
  })

  it("sources LATEST from the newest completed Hoowen child and NEEDS YOU from linked records only", async () => {
    renderHoowenHome()
    await screen.findByRole("heading", { name: HOOWEN_LABEL })
    const glance = screen.getByLabelText("At a glance")
    // LATEST is Hoowen follow-up 2, linked to its mission — never the
    // first-global COMPLETE row ("Unrelated portfolio tidy-up").
    const latestLink = within(glance).getByRole("link", { name: /Hoowen follow-up 2/ })
    expect(latestLink).toHaveAttribute("href", "/missions/mission-62250d335ab6")
    expect(within(glance).queryByText("Unrelated portfolio tidy-up")).not.toBeInTheDocument()
    // NEEDS YOU is the literal sentence: the only open record belongs to the
    // historical mission, and the historical BLOCKED title must never surface.
    expect(within(glance).getByText("Nothing needed from you.")).toBeInTheDocument()
    expect(within(glance).queryByText("Overnight CI verification sweep")).not.toBeInTheDocument()
    expect(within(glance).queryByText("Approve the CI pipeline fix.")).not.toBeInTheDocument()
  })
})
