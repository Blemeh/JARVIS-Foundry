import { describe, expect, it } from "vitest";
import { mapMissionRun } from "../snapshotMapper";
import type { FoundrySnapshot } from "../snapshotTypes";

/**
 * Owner-facing lineage invariant regression.
 *
 * One mission page must derive ONE authoritative lineage:
 * owner directive -> objective -> plan -> mission -> worker runs ->
 * artifacts -> evidence -> verifier -> outcome -> learning -> briefing.
 * Every displayed identity must be causally related. Unprovable links
 * report NOT RECORDED / NOT REQUIRED, never a nearest-record substitute,
 * and terminal missions never report PENDING for impossible future work.
 */
const baseSnapshot = (): FoundrySnapshot => ({
  generated_at: "2026-10-06T00:00:00Z",
  source_of_truth: { kind: "append_only_event_ledger", ledger_head_sequence: 99, event_count: 99 },
  supervisor: {},
  system_state: {},
  stop: { active: false, record: null, source: "sentinel_or_durable_control" },
  controls: {
    goals: [
      { directive_id: "owner-causal", target: "GLOBAL", exact_text: "Causal owner goal text", updated_at: "2026-10-05T00:00:00Z" },
      { directive_id: "owner-newest", target: "GLOBAL", exact_text: "Newest unrelated goal text", updated_at: "2026-10-06T00:00:00Z" },
    ],
    constraints: [],
    vetoes: [],
    reprioritizations: [],
    redirects: [],
    paused: false,
    resume_available: false,
  },
  missions: [
    {
      mission_id: "mission-causal",
      objective: "Causal mission objective",
      state: "COMPLETE",
      updated_at: "2026-10-05T00:00:00Z",
      created_from: "portfolio:owner-goal:owner-causal",
      last_event_id: "event-mission",
    },
  ],
  // Bounded window aged the worker rows out, but durable evidence and
  // verifier rows (and hierarchy linkage) still prove the run.
  workers: [],
  evidence: [
    { bundle_id: "bundle-1", mission_id: "mission-causal", step_id: "01-plan", artifact_hashes: { "plan.md": "abc" }, verifier_verdict_id: "verdict-1", updated_at: "2026-10-05T00:00:00Z", last_event_id: "event-evidence" },
  ],
  verification: [
    { verdict_id: "verdict-1", mission_id: "mission-causal", status: "PASS", verifier_run_id: "run-aged-out-verify", updated_at: "2026-10-05T00:00:00Z", last_event_id: "event-verdict" },
  ],
  health: { supervisor_status: "RUNNING", trust_state: "SUSPENDED", degraded: false },
  systems: [],
  memory: [],
  procedures: [],
  mission_steps: [
    { contract_id: "contract-1", mission_id: "mission-causal", step_id: "01-plan", capability: "write_registered_worktree_files", last_event_id: "event-step", updated_at: "2026-10-05T00:00:00Z" },
  ],
  // Bounded 200-event window lost the mission's own lifecycle events.
  timeline: [],
  release_identity: {},
  costs: {},
  executive: {
    activation_epoch: null,
    admission_authority: "UNKNOWN",
    latest_decision: null,
    recent_decisions: [],
    calibrations: [],
  },
  work_hierarchy: {
    schema: "v13.command-center-work.v1",
    status: "AVAILABLE",
    authority: "READ_ONLY_CANONICAL_PROJECTIONS",
    ui_authority: "NONE",
    levels: {},
    goals: [
      { goal_id: "goal:owner-causal", directive_id: "owner-causal", label: "Causal owner goal text", status: "ACTIVE", target: "GLOBAL", objective_ids: ["owner-goal:owner-causal"] },
      { goal_id: "goal:owner-newest", directive_id: "owner-newest", label: "Newest unrelated goal text", status: "ACTIVE", target: "GLOBAL", objective_ids: [] },
    ],
    programs: [],
    projects: [],
    objectives: [
      {
        objective_id: "owner-goal:owner-causal",
        label: "Causal owner goal text",
        goal_id: "goal:owner-causal",
        status: "COMPLETED",
        campaign_ids: [],
        mission_ids: ["mission-causal"],
        plan_ids: ["plan:owner-goal:owner-causal:abc"],
        action_ids: ["plan:owner-goal:owner-causal:abc:execute", "contract-1"],
        progress_evidence: { status: "COMPLETED", evidence_refs: ["verdict-1"] },
      },
    ],
    campaigns: [],
    missions: [
      {
        mission_id: "mission-causal",
        objective_id: "owner-goal:owner-causal",
        status: "COMPLETE",
        action_ids: ["plan:owner-goal:owner-causal:abc:execute", "contract-1"],
        // Worker rows aged out of the bounded snapshot list, so the
        // hierarchy built from the same window is also empty here; the
        // evidence and verdict rows above still prove the historical run.
        worker_ids: [],
        evidence_refs: ["bundle-1"],
        verdict_ids: ["verdict-1"],
      },
    ],
    actions: [
      { action_id: "plan:owner-goal:owner-causal:abc:execute", step_id: "execute", label: "execute", status: "COMPLETED", objective_id: "owner-goal:owner-causal", mission_id: "mission-causal", worker_ids: [], evidence_refs: ["bundle-1"], verdict_ids: ["verdict-1"], source: "executive.plan.step" },
      { action_id: "contract-1", step_id: "01-plan", label: "write_registered_worktree_files", status: "RECORDED", objective_id: "owner-goal:owner-causal", mission_id: "mission-causal", worker_ids: [], evidence_refs: ["bundle-1"], verdict_ids: ["verdict-1"], source: "mission_step_contracts" },
    ],
  },
  briefing_packet: { packet: true },
});

const stageByKey = (snapshot: FoundrySnapshot, missionId: string) =>
  Object.fromEntries(
    (mapMissionRun(snapshot, missionId).lifecycle ?? []).map((stage) => [stage.key, stage]),
  );

describe("mission detail lineage invariant", () => {
  it("shows the causally linked owner goal, never the newest unrelated goal", () => {
    const stages = stageByKey(baseSnapshot(), "mission-causal");
    expect(stages.owner_goal.state).toBe("COMPLETE");
    expect(stages.owner_goal.detail).toContain("Causal owner goal text");
    expect(stages.owner_goal.detail).not.toContain("Newest unrelated goal text");
  });

  it("never fabricates a timeline link from a directive or goal id", () => {
    const stages = stageByKey(baseSnapshot(), "mission-causal");
    expect(stages.owner_goal.refs).toEqual([]);
    expect(stages.owner_goal.eventIds).toEqual([]);
  });

  it("proves plan from durable hierarchy joins, not the bounded timeline window", () => {
    const stages = stageByKey(baseSnapshot(), "mission-causal");
    expect(stages.plan.state).toBe("COMPLETE");
  });

  it("marks worker COMPLETE from historical evidence when run rows aged out", () => {
    const stages = stageByKey(baseSnapshot(), "mission-causal");
    expect(stages.worker.state).toBe("COMPLETE");
  });

  it("reports repair NOT REQUIRED for a successful mission with no repair", () => {
    const stages = stageByKey(baseSnapshot(), "mission-causal");
    expect(stages.repair_retry.state).toBe("NOT REQUIRED");
  });

  it("never reports PENDING for impossible future work on a terminal mission", () => {
    const stages = stageByKey(baseSnapshot(), "mission-causal");
    for (const stage of Object.values(stages)) {
      expect(stage.state).not.toBe("PENDING");
    }
    expect(stages.memory.state).toBe("NOT RECORDED");
  });

  it("reports NOT RECORDED instead of substituting an unrelated goal", () => {
    const snapshot = baseSnapshot();
    snapshot.missions = [
      { mission_id: "mission-system", objective: "System CI repair", state: "COMPLETE", updated_at: "2026-10-05T00:00:00Z", last_event_id: "event-system" },
    ];
    snapshot.evidence = [];
    snapshot.verification = [];
    snapshot.mission_steps = [];
    if (snapshot.work_hierarchy) {
      snapshot.work_hierarchy = {
        ...snapshot.work_hierarchy,
        objectives: [],
        missions: [{ mission_id: "mission-system", status: "COMPLETE", action_ids: [], worker_ids: [], evidence_refs: [], verdict_ids: [] }],
        actions: [],
      };
    }
    const stages = stageByKey(snapshot, "mission-system");
    expect(stages.owner_goal.state).toBe("NOT RECORDED");
    expect(stages.owner_goal.detail).not.toContain("Newest unrelated goal text");
    expect(stages.plan.state).toBe("NOT RECORDED");
  });

  it("keeps BLOCKED distinct from FAILED", () => {
    const snapshot = baseSnapshot();
    snapshot.missions = [
      { mission_id: "mission-blocked", objective: "Blocked work", state: "BLOCKED", next_action: "owner decision required", updated_at: "2026-10-05T00:00:00Z", last_event_id: "event-blocked" },
    ];
    const stages = stageByKey(snapshot, "mission-blocked");
    expect(stages.mission.state).toBe("BLOCKED");
  });
});
