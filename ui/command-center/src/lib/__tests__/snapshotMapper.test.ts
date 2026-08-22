import { describe, expect, it } from "vitest";
import { mapMissionStatus, mapMissions, mapSystemOverview, mapTrustSummary, mapWorkers } from "../snapshotMapper";
import type { FoundrySnapshot } from "../snapshotTypes";
import { MEASURED_COST_LABEL, NOT_REPORTED_NUMERIC } from "../truthFormat";
import { allSnapshotTruthCases } from "./snapshotTruthCases";

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

  it("does not fabricate resource usage or trust percentages", () => {
    const snapshot = baseSnapshot();
    const overview = mapSystemOverview(snapshot);
    expect(overview.resourceUsage.filter((r) => r.label !== MEASURED_COST_LABEL)).toEqual([]);
    const costRow = overview.resourceUsage.find((r) => r.label === MEASURED_COST_LABEL);
    expect(costRow?.value).toBe(NOT_REPORTED_NUMERIC);

    const trust = mapTrustSummary(snapshot);
    expect(trust.score).toBeNull();
    expect(trust.evidenceCoverage).toBe(NOT_REPORTED_NUMERIC);
    expect(trust.state).toBe("DEGRADED");
    expect(trust.factors.some((f) => f.id === "constraints")).toBe(true);
    expect(trust.factors.some((f) => f.id === "stale-leases")).toBe(true);
  });

  it("treats UNKNOWN cost token as not reported", () => {
    const snapshot = baseSnapshot();
    snapshot.costs = { measured_actual_total_usd: "UNKNOWN", unknown_measurements: 1 };
    const overview = mapSystemOverview(snapshot);
    const costRow = overview.resourceUsage.find((r) => r.label === MEASURED_COST_LABEL);
    expect(costRow?.value).toBe(NOT_REPORTED_NUMERIC);
  });

  it("withholds invented mission completion for in-progress states", () => {
    const snapshot = baseSnapshot();
    const missions = mapMissions(snapshot);
    expect(missions[0].completion).toBe(NOT_REPORTED_NUMERIC);
  });

  it("maps authoritative zero cost and computed verification coverage", () => {
    const snapshot = baseSnapshot();
    snapshot.costs = { measured_actual_total_usd: 0, unknown_measurements: 0 };
    snapshot.verification = [
      { verdict_id: "v-1", status: "PASS", mission_id: "m-1" },
      { verdict_id: "v-2", status: "FAIL", mission_id: "m-1" },
    ];
    const overview = mapSystemOverview(snapshot);
    const costRow = overview.resourceUsage.find((r) => r.label === MEASURED_COST_LABEL);
    expect(costRow?.value).toBe(0);

    const trust = mapTrustSummary(snapshot);
    expect(trust.evidenceCoverage).toBe(50);
  });

  describe("snapshotTruthCases boundary matrix", () => {
    for (const { case: truthCase, snapshot } of allSnapshotTruthCases()) {
      it(`${truthCase.id} (${truthCase.boundary}) maps without fabricated defaults`, () => {
        const overview = mapSystemOverview(snapshot);
        const trust = mapTrustSummary(snapshot);
        const missions = mapMissions(snapshot);

        const costRow = overview.resourceUsage.find((r) => r.label === MEASURED_COST_LABEL);
        expect(costRow).toBeDefined();
        if (truthCase.id === "zero-cost") {
          expect(costRow?.value).toBe(0);
        } else if (truthCase.id === "large-cost") {
          expect(costRow?.value).toBe(1_000_000_000.99);
        } else if (truthCase.id === "negative-cost") {
          expect(costRow?.value).toBe(-1);
        } else if (truthCase.id === "unknown-token" || truthCase.id === "null-cost" || truthCase.id === "malformed-cost") {
          expect(costRow?.value).toBe(NOT_REPORTED_NUMERIC);
        }

        expect(trust.score).toBeNull();
        expect(trust.factors.some((f) => f.id === "constraints")).toBe(true);
        expect(trust.factors.some((f) => f.id === "stale-leases")).toBe(true);

        if (truthCase.id === "constraints-present") {
          expect(trust.factors.find((f) => f.id === "constraints")?.detail).toContain("2 active constraint")
        }
        if (truthCase.id === "stale-leases") {
          expect(trust.factors.find((f) => f.id === "stale-leases")?.detail).toContain("1 stale lease")
        }
        if (truthCase.id === "contradictory-trust") {
          expect(trust.state).toBe("SUSPENDED")
        }

        if (truthCase.id === "unicode-objective") {
          expect(missions[0].title).toContain("🛰️")
        }

        for (const resource of overview.resourceUsage.filter((r) => r.label !== MEASURED_COST_LABEL)) {
          expect(resource.value).not.toBe(92)
          expect(resource.value).not.toBe(85)
        }
      })
    }
  })
});
