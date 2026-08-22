/**
 * Typed V3 discovery models for Command Center visibility.
 *
 * Shapes mirror the frozen backend contract at candidate/v3-integration
 * @701aa74 (v3_cycle.CycleReport, v3_scoring trace, v3_outcomes.record_outcome,
 * v3_teams verifier block, discovery_candidates typed record) and the V3
 * documents (docs/v3/01 §3.3 lifecycle, 02 §1 event catalog, 03 §4 formula).
 *
 * Only field names and value sets evidenced there are modeled. Free-text
 * contract fields (cycle reason, rejection detail) are carried verbatim;
 * nothing outside the event contract is invented.
 */

/** Unified lifecycle enum (docs/v3/01 §3.3; subset confirmed at 701aa74). */
export type V3CandidateStatus =
  | "NEW"
  | "SCORED"
  | "ELIGIBLE"
  | "SELECTED"
  | "MISSION_LINKED"
  | "EXECUTED"
  | "MEASURING"
  | "CLOSED"
  | "APPROVED"
  | "REJECTED"
  | "DEFERRED"
  | "SUPERSEDED";

export type V3CandidateType =
  | "repair"
  | "maintenance"
  | "research"
  | "build"
  | "optimization"
  | "knowledge_resolution"
  | "release"
  | "goal_advancement";

/** Normalized 0..1 scoring dimensions; order is part of the determinism contract. */
export interface V3ScoreDimensions {
  strategic_fit: number;
  expected_value: number;
  information_gain: number;
  urgency: number;
  confidence: number;
  effort: number;
  cost: number;
  risk: number;
  reversibility: number;
  opportunity_cost: number;
}

/** Score trace from discovery.candidate_scored / v3_scoring. */
export interface V3CandidateScore {
  policyVersion: string;
  /** Clamped composite: positives minus penalties plus calibration_adjust. */
  total: number;
  dimensions: V3ScoreDimensions;
  weights: V3ScoreDimensions;
  calibrationAdjust: number;
  thresholds: { min_total_score: number };
}

/** discovery.candidate_rejected carries reason_code + free-text reason. */
export interface V3Rejection {
  code: string;
  detail: string;
}

export interface V3Candidate {
  /** cand-{uuid12}. */
  id: string;
  fingerprint: string;
  type: V3CandidateType;
  objective: string;
  expectedOutcome: string | null;
  status: V3CandidateStatus;
  createdAt: string;
  /** Null until discovery.candidate_scored fires for the candidate. */
  score: V3CandidateScore | null;
  /** Non-null exactly when status is REJECTED. */
  rejection: V3Rejection | null;
  /** Set when discovery.candidate_superseded flips this row. */
  supersededBy: string | null;
  /** Cooldown end recorded by portfolio.candidate_deferred. */
  deferredUntil: string | null;
  evidenceRefCount: number;
  /** Mission linked through executive.decision_materialized, if any. */
  missionId: string | null;
  maintenanceExpectation: string | null;
}

/** CycleReport.outcome at 701aa74: COMPILED | WAITING | STOPPED | BLOCKED. */
export type V3CycleOutcome = "COMPILED" | "WAITING" | "STOPPED" | "BLOCKED";

/** Executive WAIT kinds (executive/eligibility.py at 701aa74). */
export type V3WaitKind = "NO_ELIGIBLE_CANDIDATES" | "COMPARATIVE_WAIT";

export interface V3DiscoveryCycle {
  loopId: string;
  startedAt: string;
  completedAt: string | null;
  outcome: V3CycleOutcome | null;
  decisionId: string | null;
  missionId: string | null;
  candidateId: string | null;
  observed: number;
  candidatesCreated: number;
  scored: number;
  eligible: number;
  rejected: number;
  degradedSources: string[];
  /** Honest idle/blocked reason carried verbatim from the report. */
  reason: string | null;
  waitKind: V3WaitKind | null;
  nextAction: string | null;
}

export interface V3RejectedAlternative {
  candidateId: string;
  totalScore: number | null;
  reason: string;
}

export interface V3Selection {
  decisionId: string;
  candidateId: string;
  totalScore: number;
  rejectedAlternatives: V3RejectedAlternative[];
  deferredAlternatives: { candidateId: string; reason: string }[];
  comparativeRationale: string | null;
  /** Set once executive.decision_materialized fires. */
  missionId: string | null;
  /** Non-null when executive.materialization_blocked fired instead. */
  blockReason: string | null;
}

/** Role names come from v3_teams.ROLE_MANIFEST (bounded manifest). */
export type V3TeamRole =
  | "scout"
  | "researcher"
  | "architect"
  | "builder"
  | "tester"
  | "critic"
  | "verifier"
  | "repairer"
  | "documenter"
  | "knowledge_curator";

export interface V3TeamMember {
  role: V3TeamRole;
  missionId: string | null;
  provider: string;
  status: "ACTIVE" | "STANDBY" | "COMPLETE" | "FAILED";
}

/** Verdicts are PASS | FAIL | BLOCKED | NOT_TESTED only (docs/v3/01 §3.7). */
export interface V3VerifierState {
  missionId: string;
  verdict: "PASS" | "FAIL" | "BLOCKED" | "NOT_TESTED" | null;
  freshSession: boolean;
  independence: "INDEPENDENT" | "REDUCED";
  provider: string;
  repairRoundsUsed: number;
  repairBudget: number;
}

/**
 * Immediate-window outcome (v3_outcomes): predicted and actual stay DISTINCT;
 * missing measurement data records UNKNOWN, never manufactured PASS.
 */
export interface V3OutcomeMeasurement {
  candidateId: string;
  missionId: string;
  window: string;
  predicted: Record<string, string>;
  actual: Record<string, string> | null;
  status: "RECORDED" | "UNKNOWN";
  confidence: number;
  /** Numeric calibration delta when both values exist. */
  delta: number | null;
}

export interface V3PortfolioSummary {
  eligible: number;
  deferred: number;
  rejected: number;
}

export interface V3FlagState {
  key: "discovery.cycle_enabled";
  enabled: boolean;
}

/**
 * Additive §3.13 projection consumed by the Discovery surface. `available`
 * distinguishes a real projection from its absence: the live adapter returns
 * `{ available: false }` while the backend snapshot carries no discovery
 * section (true at 701aa74), and the UI shows an explicit unavailable state —
 * never fixtures.
 */
export type V3DiscoveryView =
  | { available: false; reason: "PROJECTION_NOT_AVAILABLE" }
  | {
      available: true;
      flag: V3FlagState;
      policyVersion: string;
      candidatesRecent: V3Candidate[];
      selected: V3Selection | null;
      portfolioSummary: V3PortfolioSummary;
      /** Latest first; cycles[0] is the current cycle. */
      cycles: V3DiscoveryCycle[];
      activeTeam: V3TeamMember[];
      verifierState: V3VerifierState | null;
      outcomeMeasurements: V3OutcomeMeasurement[];
      maintenanceDue: string | null;
      nextAction: string | null;
    };

export const V3_DIMENSION_LABELS: Record<keyof V3ScoreDimensions, string> = {
  strategic_fit: "Strategic fit",
  expected_value: "Expected value",
  information_gain: "Information gain",
  urgency: "Urgency",
  confidence: "Confidence",
  effort: "Effort",
  cost: "Cost",
  risk: "Risk",
  reversibility: "Reversibility",
  opportunity_cost: "Opportunity cost",
};

/** Dimensions that subtract from the total (v3_scoring.compute_total). */
export const V3_NEGATIVE_DIMENSIONS: ReadonlySet<keyof V3ScoreDimensions> = new Set([
  "effort",
  "cost",
  "risk",
  "opportunity_cost",
] satisfies (keyof V3ScoreDimensions)[]);
