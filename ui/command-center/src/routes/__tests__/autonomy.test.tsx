import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import type { AutonomyCandidate, AutonomyProjection } from "../../lib/contracts";
import { CommandCenterProvider } from "../../context/CommandCenterContext";
import { AutonomyView } from "../Autonomy";

function renderAutonomy(ui: React.ReactNode) {
  return render(<MemoryRouter><CommandCenterProvider>{ui}</CommandCenterProvider></MemoryRouter>);
}

const work = (state: string) => ({
  candidate: { candidate_id: "candidate-1", title: "Reduce retry latency", state: "ELIGIBLE", event_id: "event-judged" },
  objective: { status: "UNAVAILABLE", objective_id: null },
  mission: { status: "UNAVAILABLE", mission_id: null },
  worktree: { status: "UNAVAILABLE", path: null },
  current_step: { status: "UNAVAILABLE", step_id: null },
  worker: { status: "UNAVAILABLE", run_id: null },
  verifier: { status: state === "PRELIMINARY_PASS" ? "PASS" : "UNAVAILABLE", verdict_id: state === "PRELIMINARY_PASS" ? "verdict-1" : null },
  final_outcome: {
    state, mission_state: "UNAVAILABLE",
    verification: {
      status: state === "PRELIMINARY_PASS" ? "PASS" : "UNAVAILABLE",
      verdict_id: state === "PRELIMINARY_PASS" ? "verdict-1" : null,
      acceptance: state === "ACCEPTED" ? "ACCEPTED" : state === "PRELIMINARY_PASS" ? "PENDING" : "NOT_APPLICABLE",
      acceptance_event_id: state === "ACCEPTED" ? "accept-1" : null,
      reason: null,
    },
    repair_attempts: 0, actual: null, measurement_status: "UNAVAILABLE",
    learned_calibration: null, next_action: null, candidate_id: "candidate-1",
  },
});

const candidate = (overrides: Partial<AutonomyCandidate> = {}): AutonomyCandidate => ({
  candidate_id: "candidate-1",
  title: "Reduce retry latency",
  expected_outcome: "Reduce duplicate retries by 30%",
  project: "JARVIS",
  sources: [{ kind: "project", identity: "JARVIS", ref: "PROJECT_STATE.md", authority: "DATA_ONLY", event_id: "event-source" }],
  score: 0.873,
  rank: 1,
  confidence: 0.81,
  research: { status: "COMPLETED", research_id: "research-1", source_ref: "evidence://1", budget_used: 2, completed_at: "2026-09-02T10:03:00Z", event_id: "event-research" },
  state: "ELIGIBLE",
  rejection: null,
  judgment: {
    decision_id: "decision-1", policy_version: "a2-v1", do_nothing_won: false,
    reason_code: "SURVIVED", reason: "Stored factors cleared the eligibility boundary.",
    evidence: {
      owner_relevance: [{ factor: "goal_fit", value: 0.9, kind: "benefit", contribution: 0.18 }],
      expected_value: [{ factor: "expected_value", value: 0.8, kind: "benefit", contribution: 0.16 }],
      confidence: [{ factor: "confidence", value: 0.81, kind: "benefit", contribution: 0.08 }],
      effort: [{ factor: "effort", value: 0.2, kind: "penalty", contribution: -0.02 }],
      capability_readiness: [{ factor: "fabric_capability_readiness", value: 1, kind: "gate", contribution: 0.1 }],
      opportunity_cost: [], duplicate_rejection_history: [], all_factors: {},
    },
    event_id: "event-judged",
  },
  age_seconds: 600, expires_at: "2026-09-03T10:00:00Z", ttl_seconds: 85_800, stale: false,
  current_work: work("UNAVAILABLE"),
  ...overrides,
});

const projection = (overrides: Partial<AutonomyProjection> = {}): AutonomyProjection => ({
  schema: "v11.command-center-autonomy.v1", status: "AVAILABLE", reason: null,
  generated_at: "2026-09-02T10:10:00Z",
  executive: {
    state: "WAIT", supervisor_state: "RUNNING", trust_state: "SUSPENDED",
    cycle_id: "cycle-1", stage: "COMPLETE", last_observation_cycle: "2026-09-02T10:05:00Z",
    next_wake: "2026-09-02T10:20:00Z", wait_reason: "No additional eligible work survived policy.",
    owner_action: {
      value: null, status: "AVAILABLE", items: [], count: 0,
      open_count: 0, scan_truncated: false, items_truncated: false,
      scan_limit: 500,
    },
    active_wip: { value: null, status: "UNAVAILABLE" },
  },
  funnel: {
    observed: { value: 8, status: "AVAILABLE" }, generated: { value: 5, status: "AVAILABLE" },
    deduped: { value: 3, status: "AVAILABLE" }, researched: { value: 2, status: "AVAILABLE" },
    rejected: { value: 1, status: "AVAILABLE" }, eligible: { value: 1, status: "AVAILABLE" },
    executing: { value: null, status: "UNAVAILABLE" }, verified_outcome: { value: null, status: "UNAVAILABLE" },
  },
  candidates: [candidate()], candidate_filter: "all",
  sources: [
    { source_kind: "project", source_identity: "JARVIS", state: "READY", readiness: "READY", last_checked: "2026-09-02T10:00:00Z", last_meaningful_change: "2026-09-02T10:00:00Z", next_due: "2026-09-02T10:20:00Z", last_error: null, observations: 8, candidates: 5, event_id: "source-1" },
    { source_kind: "gmail", source_identity: "owner-inbox", state: "BLOCKED", readiness: "DEFERRED", last_checked: "2026-09-02T10:00:00Z", last_meaningful_change: null, next_due: "2026-09-02T11:00:00Z", last_error: "Provider unavailable; deferred by policy", observations: 0, candidates: 0, event_id: "source-2" },
  ],
  timeline: [
    { sequence: 1, event_id: "obs-1", event_type: "world.observation_recorded", occurred_at: "2026-09-02T10:00:00Z", stage: "OBSERVED", summary: "Project changed", candidate_id: null, mission_id: null, objective_id: null, verdict_id: null, decision: null },
    { sequence: 2, event_id: "judged-1", event_type: "discovery.candidate_judged", occurred_at: "2026-09-02T10:04:00Z", stage: "ELIGIBLE", summary: "Survived policy", candidate_id: "candidate-1", mission_id: null, objective_id: null, verdict_id: null, decision: "ELIGIBLE" },
  ],
  current_work: work("UNAVAILABLE"),
  limits: { candidate_limit: 50, timeline_limit: 100 },
  ...overrides,
});

describe("V11 Autonomy Command Center", () => {
  it("shows truthful funnel, WAIT reason, source readiness, and candidate detail", () => {
    renderAutonomy(<AutonomyView projection={projection()} filter="all" onFilter={() => undefined} />);
    expect(screen.getByText("No additional eligible work survived policy.")).toBeInTheDocument();
    const funnel = screen.getByLabelText("Candidate funnel");
    expect(within(funnel).getByText("8")).toBeInTheDocument();
    expect(within(funnel).getByText("5")).toBeInTheDocument();
    expect(screen.getByText("Reduce duplicate retries by 30%")).toBeInTheDocument();
    expect(screen.getByText("goal fit")).toBeInTheDocument();
    expect(screen.getAllByText("READY").length).toBeGreaterThan(0);
  });

  it("renders Gmail as deferred with its durable block reason", () => {
    renderAutonomy(<AutonomyView projection={projection()} filter="all" onFilter={() => undefined} />);
    expect(screen.getByText("gmail")).toBeInTheDocument();
    expect(screen.getByText("DEFERRED")).toBeInTheDocument();
    expect(screen.getByText("Provider unavailable; deferred by policy")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "event source-2" })).toHaveAttribute("href", "/timeline?event=source-2");
  });

  it("shows the Executive recommendation and options for open owner escalations", () => {
    const ownerAction = {
      escalation_id: "escalation:retry-budget",
      objective_id: "objective-retry",
      decision: "retry budget exhausted",
      why: "The execute step exhausted its bounded retry budget.",
      recommendation: "Suspend until owner direction or a materially different capability is available.",
      options: ["change strategy", "grant capability", "suspend objective"],
      delay_consequence: "Downstream work remains blocked.",
      created_at: "2026-09-25T06:20:00Z",
      event_id: "escalation-event-1",
    };
    const current = projection();
    renderAutonomy(<AutonomyView
      projection={projection({
        executive: {
          ...current.executive,
          owner_action: {
          value: ownerAction, status: "AVAILABLE", items: [ownerAction],
            count: 1, open_count: 1, scan_truncated: false,
            items_truncated: false, scan_limit: 500,
          },
        },
      })}
      filter="all"
      onFilter={() => undefined}
    />);
    const panel = screen.getByRole("region", { name: "Owner direction" });
    expect(within(panel).getByText("The execute step exhausted its bounded retry budget.")).toBeInTheDocument();
    expect(within(panel).getByText(ownerAction.recommendation)).toBeInTheDocument();
    expect(within(panel).getByText("grant capability")).toBeInTheDocument();
    expect(within(panel).getByText(/If deferred: Downstream work remains blocked\./)).toBeInTheDocument();
    expect(within(panel).getByRole("link", { name: "View escalation record" }))
      .toHaveAttribute("href", "/timeline?event=escalation-event-1");
    expect(within(panel).queryByText(/no durable owner requirement is recorded/i)).not.toBeInTheDocument();
  });

  it("keeps every candidate provenance source inspectable", () => {
    const multiSource = candidate({ sources: [
      { kind: "project", identity: "JARVIS", ref: "PROJECT_STATE.md", authority: "DATA_ONLY", event_id: "candidate-source-1" },
      { kind: "web", identity: "release-notes", ref: "https://example.test/release", authority: "DATA_ONLY", event_id: "candidate-source-2" },
    ] });
    renderAutonomy(<AutonomyView projection={projection({ candidates: [multiSource] })} filter="all" onFilter={() => undefined} />);
    expect(screen.getByText(/PROJECT_STATE\.md/)).toBeInTheDocument();
    expect(screen.getByText(/example\.test\/release/)).toBeInTheDocument();
    expect(screen.getAllByRole("link", { name: "source event" })).toHaveLength(2);
  });

  it("shows rejection reasons and forwards read-only filter selection", async () => {
    const onFilter = vi.fn();
    const rejected = candidate({
      candidate_id: "candidate-2", title: "Duplicate alert route", state: "REJECTED",
      rejection: { reason_code: "STANDING_REJECTION", reason: "Owner rejected this duplicate pattern twice.", event_id: "reject-2" },
    });
    renderAutonomy(<AutonomyView projection={projection({ candidates: [rejected] })} filter="all" onFilter={onFilter} />);
    expect(screen.getByText("Owner rejected this duplicate pattern twice.")).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "rejected" }));
    expect(onFilter).toHaveBeenCalledWith("rejected");
  });

  it("keeps absent A3 fields explicit and distinguishes preliminary PASS from acceptance", () => {
    const preliminary = work("PRELIMINARY_PASS");
    renderAutonomy(<AutonomyView projection={projection({ current_work: preliminary })} filter="all" onFilter={() => undefined} />);
    expect(screen.getAllByText("Unavailable").length).toBeGreaterThan(0);
    expect(screen.getByText(/no durable AcceptanceGate receipt exists/i)).toBeInTheDocument();
    expect(screen.queryByText("accept-1")).not.toBeInTheDocument();
  });

  it("shows durable accepted outcome without the preliminary warning", () => {
    renderAutonomy(<AutonomyView projection={projection({ current_work: work("ACCEPTED") })} filter="all" onFilter={() => undefined} />);
    expect(screen.getAllByText("ACCEPTED").length).toBeGreaterThan(0);
    expect(screen.queryByText(/no durable AcceptanceGate receipt exists/i)).not.toBeInTheDocument();
  });

  it("opens the owner-facing candidate brief with research, readiness, context, and plan", async () => {
    const enriched = candidate();
    enriched.research = {
      ...enriched.research,
      dossier: {
        status: "COMPLETED", contract: "jarvis.v11.research-dossier.v1",
        human_summary: "Evidence supports the bounded retry change.",
        supporting_evidence: [{ statement: "Release notes identify the retry regression." }],
        contradicting_evidence: [{ statement: "No independent runtime benchmark is recorded." }],
        contradictions: [{ claim_key: "runtime_cost", status: "UNRESOLVED" }],
        unresolved_unknowns: ["Production latency baseline"],
        final_research_conclusion: { verdict: "SUPPORTED", summary: "Proceed to verification." },
      },
    };
    enriched.capability_readiness = {
      status: "PARTIALLY_READY", contract: "jarvis.v11.capability-requirements.v1",
      required_capability_ids: ["cap.repo.read"], missing_capabilities: ["cap.repo.write"],
      decision: "BLOCKED", executable: false,
    };
    enriched.project_context = {
      status: "AVAILABLE", contract: "jarvis.actionable-project-context.v1",
      stack: { language: "TypeScript" }, repository_state: { branch: "candidate" },
      context_gaps: ["CI receipt unavailable"],
    };
    enriched.verification_plan = {
      status: "READY", contract: "jarvis.verification-plan.v1",
      objective: "Verify retry latency reduction", acceptance_criteria: ["focused tests pass"],
      expected_artifacts: ["verification receipt"], forbidden_paths: [".env"],
    };
    renderAutonomy(<AutonomyView projection={projection({ candidates: [enriched] })} filter="all" onFilter={() => undefined} />);
    await userEvent.click(screen.getByRole("button", { name: /Inspect complete candidate brief/i }));
    const drawer = screen.getByRole("dialog", { name: "Candidate evidence brief" });
    expect(within(drawer).getByText("Evidence supports the bounded retry change.")).toBeInTheDocument();
    expect(within(drawer).getByText("No independent runtime benchmark is recorded.")).toBeInTheDocument();
    expect(within(drawer).getByText("cap.repo.write")).toBeInTheDocument();
    expect(within(drawer).getByText("Verify retry latency reduction")).toBeInTheDocument();
    await userEvent.click(within(drawer).getByRole("button", { name: "Close panel" }));
    expect(screen.queryByRole("dialog", { name: "Candidate evidence brief" })).not.toBeInTheDocument();
  });

  it("renders an honest empty state without candidate or activity placeholders", () => {
    renderAutonomy(<AutonomyView projection={projection({ status: "EMPTY", candidates: [], timeline: [], current_work: null })} filter="all" onFilter={() => undefined} />);
    expect(screen.getByText("No candidates match this durable projection.")).toBeInTheDocument();
    expect(screen.getByText("No material autonomy events are recorded.")).toBeInTheDocument();
    expect(screen.getByText("No eligible candidate or durable A3 mission bridge is present.")).toBeInTheDocument();
  });
});
