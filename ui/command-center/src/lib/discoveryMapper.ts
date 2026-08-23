/**
 * Mapper from the additive backend snapshot.discovery section (spec §3.13)
 * to V3DiscoveryView.
 *
 * The backend payload is authoritative: every field maps 1:1 to an evidenced
 * backend name. Unknown additive enum values degrade without crashing; no
 * fixture fallback exists on this path.
 */
import type {
  V3Candidate,
  V3CandidateStatus,
  V3CycleOutcome,
  V3DiscoveryCycle,
  V3DiscoveryView,
  V3OutcomeMeasurement,
  V3PortfolioSummary,
  V3RejectedAlternative,
  V3Selection,
  V3TeamMember,
  V3VerifierState,
} from "./v3Models";

/** Raw JSON shapes of snapshot.discovery (see PAYLOAD_CONTRACT.md). */
interface BackendDiscovery {
  schema?: number;
  state?: string;
  flag_state?: string;
  policy_version?: string | null;
  latest_cycle?: Record<string, unknown> | null;
  cycles?: Record<string, unknown>[];
  candidates_recent?: Record<string, unknown>[];
  selected?: Record<string, unknown> | null;
  portfolio_summary?: { eligible?: number | null; deferred?: number | null; rejected?: number | null } | null;
  active_team?: Record<string, unknown>[];
  verifier?: Record<string, unknown> | null;
  outcomes?: Record<string, unknown>[];
  maintenance_due?: string | null;
  next_action?: string | null;
}

const CYCLE_OUTCOMES: ReadonlySet<string> = new Set(["COMPILED", "WAITING", "STOPPED", "BLOCKED"]);
const WAIT_KINDS: ReadonlySet<string> = new Set(["NO_ELIGIBLE_CANDIDATES", "COMPARATIVE_WAIT"]);

function num(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function str(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

export function mapDiscovery(raw: unknown): V3DiscoveryView {
  const d = (raw ?? {}) as BackendDiscovery;
  if (!raw || typeof raw !== "object") {
    return { available: false, reason: "PROJECTION_NOT_AVAILABLE" };
  }

  const cycles: V3DiscoveryCycle[] = (d.cycles ?? []).map((c) => ({
    loopId: str(c.loop_id) ?? "",
    startedAt: str(c.started_at) ?? "",
    completedAt: str(c.completed_at),
    outcome: (typeof c.outcome === "string" && CYCLE_OUTCOMES.has(c.outcome)
      ? (c.outcome as V3CycleOutcome)
      : str(c.outcome)),
    decisionId: str(c.decision_id),
    missionId: str(c.mission_id),
    candidateId: str(c.candidate_id),
    observed: num(c.observed) ?? 0,
    candidatesCreated: num(c.candidates_created) ?? 0,
    scored: num(c.scored),
    eligible: num(c.eligible),
    rejected: num(c.rejected),
    degradedSources: Array.isArray(c.degraded_sources)
      ? c.degraded_sources.filter((x): x is string => typeof x === "string")
      : [],
    reason: str(c.reason),
    waitKind: typeof c.wait_kind === "string" && WAIT_KINDS.has(c.wait_kind)
      ? (c.wait_kind as V3DiscoveryCycle["waitKind"])
      : null,
    nextAction: str(c.next_action),
  }));

  const candidatesRecent: V3Candidate[] = (d.candidates_recent ?? []).map((c) => {
    const s = (c.score ?? null) as Record<string, unknown> | null;
    return {
      id: str(c.candidate_id) ?? "",
      fingerprint: str(c.fingerprint) ?? "",
      type: (str(c.type) ?? "repair") as V3Candidate["type"],
      objective: str(c.objective) ?? "",
      expectedOutcome: str(c.expected_outcome),
      status: (str(c.status) ?? "NEW") as V3CandidateStatus,
      createdAt: str(c.created_at) ?? str(c.updated_at) ?? "",
      score:
        s &&
        typeof s === "object" &&
        (num(s.total) !== null ||
          typeof s.policy_version === "string" ||
          (s.dimensions !== undefined && s.dimensions !== null))
          ? {
              policyVersion: str(s.policy_version) ?? "UNKNOWN",
              total: num(s.total) ?? 0,
              dimensions: (s.dimensions ?? {}) as V3Candidate["score"] extends null ? never : Record<string, number> & import("./v3Models").V3ScoreDimensions,
              weights: (s.weights ?? {}) as import("./v3Models").V3ScoreDimensions,
              calibrationAdjust: num(s.calibration_adjust) ?? 0,
              thresholds: {
                min_total_score: num(
                  (s.thresholds as Record<string, unknown> | null | undefined)?.min_total_score,
                ) ?? 0.4,
              },
            }
          : null,
      rejection:
        c.rejection != null && typeof c.rejection === "object"
          ? { code: str((c.rejection as Record<string, unknown>).code) ?? "", detail: str((c.rejection as Record<string, unknown>).detail) ?? "" }
          : null,
      supersededBy: str(c.superseded_by),
      deferredUntil: str(c.deferred_until),
      evidenceRefCount: num(c.evidence_ref_count) ?? 0,
      missionId: str(c.mission_id),
      maintenanceExpectation: str(c.maintenance_expectation),
    };
  });

  let selected: V3Selection | null = null;
  if (d.selected && typeof d.selected === "object") {
    const s = d.selected as Record<string, unknown>;
    selected = {
      decisionId: str(s.decision_id) ?? "",
      candidateId: str(s.candidate_id) ?? "",
      totalScore: num(s.total_score) ?? 0,
      rejectedAlternatives: ((s.rejected_alternatives ?? []) as Record<string, unknown>[]).map<V3RejectedAlternative>((r) => ({
        candidateId: str(r.candidate_id) ?? "",
        totalScore: num(r.total_score),
        reason: str(r.reason) ?? "",
      })),
      deferredAlternatives: ((s.deferred_alternatives ?? []) as Record<string, unknown>[]).map((r) => ({
        candidateId: str(r.candidate_id) ?? "",
        reason: str(r.reason) ?? "",
      })),
      comparativeRationale: str(s.comparative_rationale),
      missionId: str(s.mission_id),
      blockReason: str(s.block_reason),
    };
  }

  const outcomeMeasurements: V3OutcomeMeasurement[] = (d.outcomes ?? []).map((o) => ({
    candidateId: str(o.candidate_id) ?? "",
    missionId: str(o.mission_id) ?? "",
    window: str(o.window) ?? "immediate",
    predicted: (o.predicted ?? {}) as Record<string, string>,
    actual:
      o.actual === null || o.actual === undefined
        ? null
        : (o.actual as Record<string, string>),
    status: o.status === "RECORDED" ? "RECORDED" : "UNKNOWN",
    confidence: num(o.confidence) ?? 0,
    delta: num(o.delta),
  }));

  const activeTeam: V3TeamMember[] = (d.active_team ?? []).map((m) => ({
    role: (str(m.role) ?? "builder") as V3TeamMember["role"],
    missionId: str(m.mission_id),
    provider: str(m.provider) ?? "",
    status: m.status === "RUNNING" || m.status === "ACTIVE"
      ? "ACTIVE"
      : m.status === "FAILED"
        ? "FAILED"
        : m.status === "COMPLETE" || m.status === "COMPLETED"
          ? "COMPLETE"
          : "STANDBY",
  }));

  const v = d.verifier;
  const verifierState: V3VerifierState | null =
    v && typeof v === "object"
      ? {
          missionId: str(v.mission_id) ?? "",
          verdict: ["PASS", "FAIL", "BLOCKED", "NOT_TESTED"].includes(str(v.verdict) ?? "")
            ? (str(v.verdict) as V3VerifierState["verdict"])
            : null,
          freshSession: v.fresh_session === true,
          independence: v.independence === "REDUCED" ? "REDUCED" : "INDEPENDENT",
          provider: str(v.provider) ?? "",
          repairRoundsUsed: num(v.repair_rounds_used) ?? 0,
          repairBudget: num(v.repair_budget) ?? 3,
        }
      : null;

  const p = d.portfolio_summary;
  const portfolioSummary: V3PortfolioSummary = {
    eligible: num(p?.eligible) ?? 0,
    deferred: num(p?.deferred) ?? 0,
    rejected: num(p?.rejected) ?? 0,
  };

  return {
    available: true,
    flag: {
      key: "discovery.cycle_enabled",
      // Only a registered true is ON; false/UNREGISTERED/unknown values are OFF.
      enabled: d.flag_state === "ON",
    },
    policyVersion: d.policy_version ?? "UNKNOWN",
    candidatesRecent,
    selected,
    portfolioSummary,
    cycles,
    activeTeam,
    verifierState,
    outcomeMeasurements,
    maintenanceDue: d.maintenance_due ?? null,
    nextAction: d.next_action ?? null,
  };
}
