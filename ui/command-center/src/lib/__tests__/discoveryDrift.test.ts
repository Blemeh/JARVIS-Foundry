/**
 * Mapper-drift detection against a GENERATED backend fixture (WP-C).
 *
 * discoveryBackendFixture.gen.ts is produced by scripts/v3_operational_e2e.py
 * from REAL backend output at this lane's HEAD — not hand-maintained UI
 * arithmetic. If the mapper or the backend shape drifts, this test fails.
 */
import { describe, expect, it } from "vitest";
import { mapDiscovery } from "../discoveryMapper";
import {
  backendDiscoveryPayload,
  EXPECTED_VIEW_KEYS,
} from "../__fixtures__/discoveryBackendFixture.gen";

describe("generated backend contract fixture", () => {
  it("maps the real backend payload to a fully available view", () => {
    const view = mapDiscovery(backendDiscoveryPayload);
    expect(view.available).toBe(true);
    if (!view.available) throw new Error("unreachable");
    expect(Object.keys(view).sort()).toEqual([...EXPECTED_VIEW_KEYS].sort());
    // Flag truth comes from the registered config carried by the backend.
    expect(view.flag).toEqual({ key: "discovery.cycle_enabled", enabled: true });
    expect(view.policyVersion).toBe(backendDiscoveryPayload.policy_version);
  });

  it("carries candidate score/policy/rejection verbatim from the ledger", () => {
    const view = mapDiscovery(backendDiscoveryPayload);
    if (!view.available) throw new Error("unreachable");
    for (const [i, backendCandidate] of backendDiscoveryPayload.candidates_recent.entries()) {
      const mapped = view.candidatesRecent[i];
      expect(mapped.id).toBe(backendCandidate.candidate_id);
      expect(mapped.objective).toBe(backendCandidate.objective);
      expect(mapped.status).toBe(backendCandidate.status);
      expect(mapped.evidenceRefCount).toBe(backendCandidate.evidence_ref_count);
      if (backendCandidate.score === null) {
        expect(mapped.score).toBeNull();
      } else {
        expect(mapped.score!.total).toBe(backendCandidate.score.total);
        expect(mapped.score!.policyVersion).toBe(backendCandidate.score.policy_version);
        expect(mapped.score!.calibrationAdjust).toBe(backendCandidate.score.calibration_adjust);
      }
      expect(mapped.rejection === null).toBe(backendCandidate.rejection == null);
      expect(mapped.missionId).toBe(backendCandidate.mission_id);
    }
  });

  it("keeps cycles newest-first with honest counts and wait kinds", () => {
    const view = mapDiscovery(backendDiscoveryPayload);
    if (!view.available) throw new Error("unreachable");
    expect(view.cycles.length).toBe(backendDiscoveryPayload.cycles.length);
    for (const [i, backendCycle] of backendDiscoveryPayload.cycles.entries()) {
      const mapped = view.cycles[i];
      expect(mapped.loopId).toBe(backendCycle.loop_id);
      expect(mapped.outcome).toBe(backendCycle.outcome);
      expect(mapped.observed).toBe(backendCycle.observed);
      expect(mapped.eligible).toBe(backendCycle.eligible);
      expect(mapped.rejected).toBe(backendCycle.rejected);
      expect(mapped.waitKind).toBe(backendCycle.wait_kind);
      expect(mapped.reason).toBe(backendCycle.reason);
    }
  });

  it("preserves predicted/actual distinction and honest UNKNOWN outcomes", () => {
    const view = mapDiscovery(backendDiscoveryPayload);
    if (!view.available) throw new Error("unreachable");
    expect(view.outcomeMeasurements.length).toBe(
      backendDiscoveryPayload.outcomes.length,
    );
    for (const [i, backendOutcome] of backendDiscoveryPayload.outcomes.entries()) {
      const mapped = view.outcomeMeasurements[i];
      expect(mapped.predicted).toEqual(backendOutcome.predicted);
      expect(mapped.actual).toEqual(backendOutcome.actual);
      expect(mapped.status).toBe(backendOutcome.status);
      // UNKNOWN status must never fabricate an actual value object.
      const backendStatus: string = backendOutcome.status;
      if (backendStatus === "UNKNOWN") {
        expect(mapped.actual).toBeNull();
      }
      expect(mapped.delta).toBe(backendOutcome.delta);
    }
  });

  it("exposes the derived next action and never invents cost_window", () => {
    const view = mapDiscovery(backendDiscoveryPayload);
    if (!view.available) throw new Error("unreachable");
    expect(view.nextAction).toBe(backendDiscoveryPayload.next_action);
    expect(view.maintenanceDue).toBe(backendDiscoveryPayload.maintenance_due);
    expect((backendDiscoveryPayload as Record<string, unknown>).cost_window).toBeUndefined();
  });
});
