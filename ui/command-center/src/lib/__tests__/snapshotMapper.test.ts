import { describe, expect, it } from "vitest";
import { mapMissionStatus, mapMissions, mapWorkers } from "../snapshotMapper";
import type { FoundrySnapshot } from "../snapshotTypes";

const baseSnapshot = (): FoundrySnapshot => ({
  generated_at: "2026-08-20T12:00:00Z",
  source_of_truth: { kind: "append_only_event_ledger", ledger_head_sequence: 3, event_count: 3 },
  supervisor: {},
  system_state: {},
  stop: { active: false, record: null, source: "sentinel_or_durable_control" },
  controls: { goals: [], constraints: [], vetoes: [], reprioritizations: [], redirects: [], paused: false, resume_available: false },
  missions: [{ mission_id: "m-1", objective: "probe", state: "ACTION_SELECTION", next_action: "dispatch", updated_at: "2026-08-20T12:00:00Z" }],
  workers: [{ run_id: "run-1", mission_id: "m-1", role: "builder", provider: "opencode_go", status: "RUNNING", updated_at: "2026-08-20T12:00:00Z" }],
  evidence: [],
  verification: [],
  health: { supervisor_status: "RUNNING", trust_state: "LAB", degraded: false },
  systems: [],
  memory: [],
  procedures: [],
  mission_steps: [],
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
});

describe("snapshotMapper", () => {
  it("maps canonical mission states to UI statuses", () => {
    expect(mapMissionStatus("ACTION_SELECTION")).toBe("ACTIVE");
    expect(mapMissionStatus("BLOCKED")).toBe("BLOCKED");
    expect(mapMissionStatus("COMPLETE")).toBe("COMPLETE");
  });

  it("maps missions and workers from ledger snapshot", () => {
    const snapshot = baseSnapshot();
    const missions = mapMissions(snapshot);
    expect(missions[0].id).toBe("m-1");
    expect(missions[0].status).toBe("ACTIVE");
    const workers = mapWorkers(snapshot);
    expect(workers[0].status).toBe("ACTIVE");
    expect(workers[0].missionIds).toEqual(["m-1"]);
  });
});
