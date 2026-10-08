import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import {
  MissionDetail,
  MissionDetailContent,
  compactMissionTracker,
  historicalBannerForMission,
  shouldShowGateButton,
  shouldShowHaltButton,
  type MissionDetailContentProps,
} from "../MissionDetail";
import { CommandCenterProvider } from "../../context/CommandCenterContext";
import type {
  AutonomyLifecycleRecord,
  EvidenceArtifact,
  Mission,
  MissionRun,
  OwnerAttentionRecord,
  OwnerControlSurface,
  RelatedEntities,
} from "../../lib/contracts";

function renderRoute(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <CommandCenterProvider>
        <MissionDetail />
      </CommandCenterProvider>
    </MemoryRouter>,
  );
}

describe("Mission Detail", () => {
  it("renders a full non-blank mission surface", async () => {
    const { container } = renderRoute("/missions/M-HYB-014");

    await waitFor(() => expect(screen.getByText("Progress")).toBeInTheDocument());
    expect(screen.getByText("Agent Telemetry Log")).toBeInTheDocument();
    expect(screen.getByText("Verifier Verdicts")).toBeInTheDocument();
    expect(screen.getByText("Active Data Streams")).toBeInTheDocument();

    // Compact tracker stages present (the wide md-stages pipeline is gone).
    for (const stage of ["Goal", "Plan", "Build", "Verify", "Outcome"]) {
      expect(screen.getAllByText(stage).length).toBeGreaterThan(0);
    }
    expect(container.querySelector(".md-stages")).toBeNull();

    // Controls use safe semantics - no "Force Advance"
    expect(screen.getByText("Re-evaluate Gate")).toBeInTheDocument();
    expect(screen.getByText("Halt Execution")).toBeInTheDocument();
    expect(container.textContent).not.toContain("Force Advance");
    expect(container.textContent!.length).toBeGreaterThan(800);
  });

  it("replaces Force Advance with a conditional re-evaluation request", async () => {
    const user = (await import("@testing-library/user-event")).default;
    renderRoute("/missions/M-HYB-014");
    await waitFor(() => expect(screen.getByText("Re-evaluate Gate")).toBeInTheDocument());
    await user.click(screen.getByText("Re-evaluate Gate"));
    expect(screen.getByText(/Advancement stays conditional/i)).toBeInTheDocument();
    await user.click(screen.getByText("Request advance"));
    expect(screen.getByText(/RE-EVALUATION REQUESTED - OUTCOME DEPENDS ON RUNTIME EVIDENCE/i)).toBeInTheDocument();
  });

  it("shows a simulated halt flow without runtime claims", async () => {
    const user = (await import("@testing-library/user-event")).default;
    renderRoute("/missions/M-HYB-014");
    await waitFor(() => expect(screen.getByText("Halt Execution")).toBeInTheDocument());
    await user.click(screen.getByText("Halt Execution"));
    expect(screen.getByText(/Simulated halt request/i)).toBeInTheDocument();
    await user.click(screen.getByText("Request halt"));
    expect(screen.getByText(/HALT REQUEST SIMULATED - NO RUNTIME EFFECT/i)).toBeInTheDocument();
  });
});

export const HOOWEN_DIRECTIVE = "owner-delegation-643042f8f84795e7de8b3d79a4b7142c";
export const HOOWEN_LABEL = "Hoowen — Active strategic goal";
export const HOOWEN_EXACT_TEXT = "Turn Hoowen into a commercially credible workforce-management product.";

const HISTORICAL_ID = "mission-4646c7023b05";

function lifecycleRecord(
  key: string,
  label: string,
  state: AutonomyLifecycleRecord["state"],
  detail: string,
): AutonomyLifecycleRecord {
  return { key, label, state, detail, refs: [], eventIds: [] };
}

function historicalMission(): Mission & { created_from?: string } {
  return {
    id: HISTORICAL_ID,
    code: HISTORICAL_ID,
    title: "Overnight CI verification sweep",
    description: "Overnight CI verification sweep. Historical CI verification failure.",
    status: "BLOCKED",
    priority: "NORMAL",
    agentsActive: 0,
    completion: 0,
    nextAction: "Fix the CI pipeline.",
    stage: "Verify",
    startedAt: "2026-08-19T12:00:00Z",
    objective: "Historical CI verification failure. The sweep never joined the Hoowen lineage.",
    owner: "portfolio:pw-dupkey-9f31:CI_FAILURE",
    created_from: "portfolio:pw-dupkey-9f31:CI_FAILURE",
  };
}

function historicalRun(): MissionRun {
  return {
    missionId: HISTORICAL_ID,
    pipeline: [],
    lifecycle: [
      lifecycleRecord("owner_goal", "Owner goal", "UNKNOWN", "No owner goal is recorded for this mission."),
      lifecycleRecord("plan", "Plan", "PENDING", "No plan admission event is recorded."),
      lifecycleRecord("mission", "Mission", "FAILED", "BLOCKED · next Fix the CI pipeline."),
      lifecycleRecord("worker", "Worker", "PENDING", "No worker run is recorded."),
      lifecycleRecord("action_artifact", "Action / artifact", "PENDING", "No action or artifact record is available."),
      lifecycleRecord("evidence", "Evidence", "PENDING", "No evidence bundle is recorded."),
      lifecycleRecord("verifier", "Verifier", "FAILED", "FAIL · CI verification rejected the sweep bundle."),
      lifecycleRecord("repair_retry", "Repair / retry", "PENDING", "Verifier failed; no repair event is recorded yet."),
      lifecycleRecord("memory", "Memory", "UNKNOWN", "No mission-linked memory write is recorded."),
      lifecycleRecord("owner_briefing", "Owner briefing", "UNKNOWN", "No briefing packet projection is available."),
    ],
    telemetry: [],
    streams: [],
    verdicts: [
      {
        id: "VF-CI-001",
        title: "CI verification sweep",
        state: "FAILED",
        detail: "CI verification rejected the sweep bundle.",
      },
    ],
    stats: { elapsed: "00:01:00", computeCost: "—", agentsActive: 0, evidenceArtifacts: 0 },
  };
}

function hoowenOwnerSurface(open: OwnerAttentionRecord[] = []): OwnerControlSurface {
  return {
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
      open,
      history: [],
      open_count: open.length,
      history_count: 0,
      counts_by_classification: {},
    },
  };
}

function emptyRelated(): RelatedEntities {
  return { missions: [], agents: [], evidence: [], systems: [], memory: [], timeline: [], notifications: [] };
}

function historicalProps(
  overrides: Partial<MissionDetailContentProps> = {},
): MissionDetailContentProps {
  return {
    mission: historicalMission(),
    run: historicalRun(),
    workers: [],
    evidence: [],
    lineage: [],
    steps: [],
    related: emptyRelated(),
    ownerSurface: hoowenOwnerSurface(),
    sourceMode: "SIMULATED",
    mutationsEnabled: true,
    stopActive: false,
    ...overrides,
  };
}

function renderContent(props: MissionDetailContentProps) {
  return render(
    <MemoryRouter initialEntries={[`/missions/${props.mission.id}`]}>
      <MissionDetailContent {...props} />
    </MemoryRouter>,
  );
}

describe("Mission Detail primary order (Task 4)", () => {
  it("renders title/status/serves/summary/current-result/next/owner-action in order", () => {
    const { container } = renderContent(historicalProps());

    const primary = container.querySelector(".md-primary");
    expect(primary).not.toBeNull();
    const order = ["md-primary-title", "md-primary-status", "md-primary-serves", "md-primary-summary", "md-primary-current", "md-primary-next", "md-primary-action"].map(
      (cls) => {
        const el = primary!.querySelector(`.${cls}`);
        expect(el).not.toBeNull();
        return el!;
      },
    );
    for (let i = 1; i < order.length; i += 1) {
      expect(order[i - 1].compareDocumentPosition(order[i]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    }

    // Title carries the objective in sentence case.
    expect(within(primary as HTMLElement).getByRole("heading", { level: 1 }).textContent).toContain(
      "Historical CI verification failure.",
    );
    // Status pill + historical badge.
    expect(screen.getByText("BLOCKED")).toBeInTheDocument();
    expect(screen.getByText("Historical")).toBeInTheDocument();
    // Serves: unlinked historical mission reports UNKNOWN with no borrowed label.
    expect(screen.getByText("UNKNOWN")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: /Hoowen/ })).toBeNull();
    // Summary keeps the verbatim leading sentences plus a plain state line.
    const summaryEl = primary!.querySelector(".md-primary-summary") as HTMLElement;
    expect(within(summaryEl).getByText(/Historical CI verification failure\./)).toBeInTheDocument();
    expect(screen.getByText(/Status: Blocked\./)).toBeInTheDocument();
    // Current/result names builder and verifier states without raw IDs in primary.
    expect(screen.getByText(/Builder .* · Verifier .*/)).toBeInTheDocument();
    expect(within(primary as HTMLElement).queryByText(/VF-CI-001/)).toBeNull();
    // Next repeats the exact next action.
    expect(screen.getByText("Fix the CI pipeline.")).toBeInTheDocument();
    // No linked owner record: the literal no-action sentence.
    expect(screen.getByText("Nothing needed from you.")).toBeInTheDocument();
    expect(container.textContent).not.toContain("Force Advance");
  });

  it("links serves to goal-filtered Work and surfaces a linked owner action", () => {
    const mission: Mission = {
      ...historicalMission(),
      id: "mission-fe81cb0d1e37",
      code: "mission-fe81cb0d1e37",
      title: "Hoowen follow-up 3",
      status: "BLOCKED",
      nextAction: "Repair the builder failure and re-run verification.",
      objective: "Hoowen follow-up 3 blocked after builder failure. The verifier passed before the builder fell over.",
      owner: `directive:${HOOWEN_DIRECTIVE}:follow-up-3`,
      created_from: `directive:${HOOWEN_DIRECTIVE}:follow-up-3`,
    } as Mission;
    const openRecord: OwnerAttentionRecord = {
      queue_id: "q-hoowen-3",
      status: "OPEN",
      classification: "DECISION_REQUIRED",
      summary: "Approve the Hoowen builder retry.",
      consequence: "The Hoowen follow-up stays held until you approve.",
      mission_id: mission.id,
      opened_at: "2026-10-06T16:00:00Z",
    };
    renderContent(historicalProps({ mission, ownerSurface: hoowenOwnerSurface([openRecord]) }));

    const servesLink = screen.getByRole("link", { name: HOOWEN_LABEL });
    expect(servesLink.getAttribute("href")).toContain("/work");
    expect(screen.getByText("Approve the Hoowen builder retry.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Review the decision/i })).toHaveAttribute("href", "/owner");
    expect(screen.queryByText("Nothing needed from you.")).toBeNull();
  });

  it("reports the literal fallback when no next action is recorded", () => {
    const mission = { ...historicalMission(), nextAction: "" };
    renderContent(historicalProps({ mission }));
    expect(screen.getByText("No next action recorded")).toBeInTheDocument();
  });
});

describe("Mission Detail compact tracker (Task 4)", () => {
  it("maps lifecycle records onto Goal/Plan/Build/Verify/Outcome", () => {
    const stages = compactMissionTracker(historicalRun().lifecycle, "BLOCKED");
    expect(stages.map((stage) => stage.label)).toEqual(["Goal", "Plan", "Build", "Verify", "Outcome"]);
    expect(stages.find((stage) => stage.label === "Verify")?.state).toBe("FAILED");
  });

  it("renders the five-step tracker and keeps evidence behind disclosure", () => {
    const { container } = renderContent(historicalProps());
    const tracker = container.querySelector(".md-compact-tracker");
    expect(tracker).not.toBeNull();
    for (const stage of ["Goal", "Plan", "Build", "Verify", "Outcome"]) {
      expect(within(tracker as HTMLElement).getByText(stage)).toBeInTheDocument();
    }
    // The wide prose pipeline is gone from primary.
    expect(container.querySelector(".md-stages")).toBeNull();

    const evidenceHeading = screen.getByText("Evidence Bundle");
    expect(evidenceHeading.closest("details")).not.toBeNull();
    const telemetryHeading = screen.getByText("Agent Telemetry Log");
    expect(telemetryHeading.closest("details")).not.toBeNull();
  });
});

describe("Mission Detail historical truth (Task 4)", () => {
  it("shows the full historical banner for mission-4646c7023b05 when unlinked from Hoowen work", () => {
    renderContent(historicalProps());
    expect(
      screen.getByText("Historical mission · Blocked on CI verification · Not affecting current Hoowen work"),
    ).toBeInTheDocument();
    expect(
      historicalBannerForMission(historicalMission(), hoowenOwnerSurface(), { workers: [], evidence: [], related: emptyRelated() }),
    ).toBe("Historical mission · Blocked on CI verification · Not affecting current Hoowen work");
  });

  it("omits the Hoowen clause when linkage cannot be computed from loaded data", () => {
    renderContent(historicalProps({ ownerSurface: null }));
    expect(screen.getByText("Historical")).toBeInTheDocument();
    expect(screen.queryByText(/Not affecting current Hoowen work/)).toBeNull();
    expect(
      historicalBannerForMission(historicalMission(), null, { workers: [], evidence: [], related: emptyRelated() }),
    ).toBe("Historical mission · Blocked on CI verification");
  });

  it("hides HALT for the terminal historical mission and hides the gate button in LIVE", () => {
    renderContent(historicalProps({ sourceMode: "LIVE", mutationsEnabled: true }));
    expect(screen.getByText(/Historical mission · Blocked on CI verification/)).toBeInTheDocument();
    expect(screen.queryByText("Halt Execution")).toBeNull();
    expect(screen.queryByText("Re-evaluate Gate")).toBeNull();
    expect(shouldShowHaltButton("BLOCKED", true)).toBe(false);
    expect(shouldShowGateButton("LIVE")).toBe(false);
    expect(shouldShowGateButton("SIMULATED")).toBe(true);
  });

  it("shows HALT without the gate button for an executing mission in LIVE", () => {
    const mission = { ...historicalMission(), id: "mission-live-01", status: "ACTIVE" as const, owner: `directive:${HOOWEN_DIRECTIVE}:live-run` };
    renderContent(historicalProps({ mission, sourceMode: "LIVE", mutationsEnabled: true }));
    expect(screen.getByText("Halt Execution")).toBeInTheDocument();
    expect(screen.queryByText("Re-evaluate Gate")).toBeNull();
    expect(shouldShowHaltButton("ACTIVE", true)).toBe(true);
    expect(shouldShowHaltButton("ACTIVE", false)).toBe(false);
    expect(shouldShowHaltButton("IN_PROGRESS", true)).toBe(true);
    expect(shouldShowHaltButton("INTERVENTION", true)).toBe(true);
  });

  it("keeps evidence IDs out of primary sentences", () => {
    const artifact: EvidenceArtifact = {
      id: "EV-CI-001",
      kind: "artifact",
      typeLabel: "CI log",
      source: "test",
      sourceUri: "test://EV-CI-001",
      hash: "hash-ci-001",
      verdict: "FAILED",
      capturedAt: "2026-08-19T12:00:00Z",
      missionRef: HISTORICAL_ID,
      note: "CI log bundle.",
    };
    const { container } = renderContent(historicalProps({ evidence: [artifact] }));
    const primary = container.querySelector(".md-primary");
    expect(primary!.textContent).not.toContain("EV-CI-001");
    expect(primary!.textContent).not.toContain("hash-ci-001");
    // IDs stay available in secondary tech-id elements and links.
    expect(container.querySelectorAll(".tech-id").length).toBeGreaterThan(0);
  });
});
