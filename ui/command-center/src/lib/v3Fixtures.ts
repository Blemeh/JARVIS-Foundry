import type {
  V3Candidate,
  V3CandidateType,
  V3DiscoveryCycle,
  V3DiscoveryView,
  V3OutcomeMeasurement,
  V3ScoreDimensions,
  V3Selection,
  V3TeamMember,
  V3VerifierState,
} from "./v3Models";

/**
 * Simulated-mode V3 discovery fixtures.
 *
 * Values follow docs/v3/03 §10 worked examples (A: selected maintenance repair
 * at 0.58, B: cosmetic optimization rejected BELOW_THRESHOLD at 0.18, C:
 * research override over a low-confidence build, D: honest WAIT with recorded
 * alternatives, E: STOP blocks selection) and the frozen contract shapes at
 * candidate/v3-integration @701aa74. Candidate ids are fixture-stable so tests
 * can assert against them. Lineage references real mock missions (M-HYB-014)
 * from fixtures.ts.
 */

const POLICY_VERSION = "v3-policy-2026-08-21";
const MIN_TOTAL_SCORE = 0.4;

function dims(partial: Partial<V3ScoreDimensions>): V3ScoreDimensions {
  return {
    strategic_fit: 0,
    expected_value: 0,
    information_gain: 0,
    urgency: 0,
    confidence: 0,
    effort: 0,
    cost: 0,
    risk: 0,
    reversibility: 0,
    opportunity_cost: 0,
    ...partial,
  };
}

/** Weights from the initial policy registration (docs/v3/03 §4). */
const WEIGHTS = dims({
  strategic_fit: 0.18,
  expected_value: 0.18,
  information_gain: 0.12,
  urgency: 0.1,
  confidence: 0.1,
  effort: 0.08,
  cost: 0.06,
  risk: 0.06,
  reversibility: 0.06,
  opportunity_cost: 0.06,
});

export function scoreFixture(total: number, dimensions: V3ScoreDimensions, calibrationAdjust = 0) {
  return {
    policyVersion: POLICY_VERSION,
    total,
    dimensions,
    weights: WEIGHTS,
    calibrationAdjust,
    thresholds: { min_total_score: MIN_TOTAL_SCORE },
  };
}

const candidateType = (t: V3CandidateType) => t;

export const v3CandidateFixtures: V3Candidate[] = [
  {
    id: "cand-a41c9f2e77b3",
    fingerprint: "9f2d01ab4c7e5d80",
    type: candidateType("maintenance"),
    objective: "Fix support semantics: handoff claims marked UNSUPPORTED",
    expectedOutcome: "Unsupported handoff claims drop to zero and stay zero across one full cycle.",
    status: "SELECTED",
    createdAt: "2026-08-21T09:14:00Z",
    score: scoreFixture(0.58, dims({ strategic_fit: 0.62, expected_value: 0.72, information_gain: 0.45, urgency: 0.6, confidence: 0.7, effort: 0.3, cost: 0.15, risk: 0.2, reversibility: 0.8, opportunity_cost: 0.25 })),
    rejection: null,
    supersededBy: null,
    deferredUntil: null,
    evidenceRefCount: 3,
    missionId: "M-HYB-014",
    maintenanceExpectation: null,
  },
  {
    id: "cand-b7e30d5c19a2",
    fingerprint: "31bd6f90aa27cc14",
    type: candidateType("optimization"),
    objective: "Rename variable x to y in test file",
    expectedOutcome: "Test file uses the new variable name; behavior unchanged.",
    status: "REJECTED",
    createdAt: "2026-08-21T09:20:00Z",
    score: scoreFixture(0.18, dims({ strategic_fit: 0.05, expected_value: 0.12, confidence: 0.5, effort: 0.15, reversibility: 0.6 })),
    rejection: { code: "BELOW_THRESHOLD", detail: "BELOW_THRESHOLD score=0.18" },
    supersededBy: null,
    deferredUntil: null,
    evidenceRefCount: 1,
    missionId: null,
    maintenanceExpectation: null,
  },
  {
    id: "cand-c93f81ba40de",
    fingerprint: "77c0e2f19b35da68",
    type: candidateType("research"),
    objective: "Investigate why provider latency regressed after worker pool resize",
    expectedOutcome: "Root cause identified with a reproduced measurement, not a correlation claim.",
    status: "ELIGIBLE",
    createdAt: "2026-08-21T11:02:00Z",
    score: scoreFixture(0.39, dims({ strategic_fit: 0.4, expected_value: 0.35, information_gain: 0.62, urgency: 0.45, confidence: 0.55, effort: 0.35, risk: 0.1 })),
    rejection: null,
    supersededBy: null,
    deferredUntil: null,
    evidenceRefCount: 2,
    missionId: null,
    maintenanceExpectation: null,
  },
  {
    id: "cand-d52ac60ef817",
    fingerprint: "e04a97c32bf8512d",
    type: candidateType("build"),
    objective: "Add retry backoff to external effect broker",
    expectedOutcome: "Broker retries with exponential backoff under injected 503s.",
    status: "DEFERRED",
    createdAt: "2026-08-21T10:47:00Z",
    score: scoreFixture(0.42, dims({ strategic_fit: 0.45, expected_value: 0.55, urgency: 0.3, confidence: 0.32, effort: 0.45, cost: 0.3, risk: 0.35 })),
    rejection: null,
    supersededBy: null,
    deferredUntil: "2026-08-22T10:47:00Z",
    evidenceRefCount: 2,
    missionId: null,
    maintenanceExpectation: null,
  },
  {
    id: "cand-e18b74d29fc0",
    fingerprint: "5be1d0a86234cf97",
    type: candidateType("repair"),
    objective: "Repair stale lease reaper double-acquire race",
    expectedOutcome: "Reaper reclaim no longer issues a second lease while the first is live.",
    status: "SUPERSEDED",
    createdAt: "2026-08-20T16:30:00Z",
    score: scoreFixture(0.51, dims({ strategic_fit: 0.5, expected_value: 0.6, urgency: 0.55, confidence: 0.65, effort: 0.35, risk: 0.3 })),
    rejection: null,
    supersededBy: "cand-f44d92ea01bb",
    deferredUntil: null,
    evidenceRefCount: 2,
    missionId: null,
    maintenanceExpectation: null,
  },
  {
    id: "cand-f44d92ea01bb",
    fingerprint: "a83f56c10e79db24",
    type: candidateType("repair"),
    objective: "Repair stale lease reaper double-acquire race (new provenance)",
    expectedOutcome: "Reaper reclaim no longer issues a second lease while the first is live.",
    status: "NEW",
    createdAt: "2026-08-21T13:05:00Z",
    score: null,
    rejection: null,
    supersededBy: null,
    deferredUntil: null,
    evidenceRefCount: 1,
    missionId: null,
    maintenanceExpectation: null,
  },
  {
    id: "cand-0af7ce53b6d9",
    fingerprint: "c72b48ae90fd1365",
    type: candidateType("knowledge_resolution"),
    objective: "Resolve UNSUPPORTED claim about schema migration ordering",
    expectedOutcome: "Claim resolved with cited migration ledger evidence.",
    status: "MEASURING",
    createdAt: "2026-08-20T08:12:00Z",
    score: scoreFixture(0.46, dims({ strategic_fit: 0.42, expected_value: 0.4, information_gain: 0.58, confidence: 0.6, effort: 0.3 })),
    rejection: null,
    supersededBy: null,
    deferredUntil: null,
    evidenceRefCount: 3,
    missionId: "M-MEM-007",
    maintenanceExpectation: null,
  },
];

export const v3SelectionFixture: V3Selection = {
  decisionId: "dec-v3-000117",
  candidateId: "cand-a41c9f2e77b3",
  totalScore: 0.58,
  rejectedAlternatives: [
    { candidateId: "cand-b7e30d5c19a2", totalScore: 0.18, reason: "BELOW_THRESHOLD" },
    { candidateId: "cand-c93f81ba40de", totalScore: 0.39, reason: "research override not applied: build within margin" },
  ],
  deferredAlternatives: [{ candidateId: "cand-d52ac60ef817", reason: "capacity: max_active_missions reached" }],
  comparativeRationale: "Maintenance repair outranked research on strategic fit; margin above comparative_wait_margin.",
  missionId: "M-HYB-014",
  blockReason: null,
};

export const v3CyclesFixture: V3DiscoveryCycle[] = [
  {
    loopId: "loop-20260821T1310",
    startedAt: "2026-08-21T13:10:00Z",
    completedAt: "2026-08-21T13:12:41Z",
    outcome: "COMPILED",
    decisionId: "dec-v3-000117",
    missionId: "M-HYB-014",
    candidateId: "cand-a41c9f2e77b3",
    observed: 34,
    candidatesCreated: 2,
    scored: 5,
    eligible: 3,
    rejected: 1,
    degradedSources: ["ci:monitor_unavailable"],
    reason: null,
    waitKind: null,
    nextAction: "Verify builder output for M-HYB-014 at gate V-2",
  },
  {
    loopId: "loop-20260821T1110",
    startedAt: "2026-08-21T11:10:00Z",
    completedAt: "2026-08-21T11:11:38Z",
    outcome: "WAITING",
    decisionId: "dec-v3-000116",
    missionId: null,
    candidateId: null,
    observed: 28,
    candidatesCreated: 1,
    scored: 4,
    eligible: 2,
    rejected: 2,
    degradedSources: [],
    reason: "All candidates below min_total_score 0.40 after filters.",
    waitKind: "NO_ELIGIBLE_CANDIDATES",
    nextAction: null,
  },
  {
    loopId: "loop-20260821T0910",
    startedAt: "2026-08-21T09:10:00Z",
    completedAt: "2026-08-21T09:10:52Z",
    outcome: "STOPPED",
    decisionId: null,
    missionId: null,
    candidateId: null,
    observed: 0,
    candidatesCreated: 0,
    scored: 0,
    eligible: 0,
    rejected: 0,
    degradedSources: [],
    reason: "STOP active: emergency stop engaged by owner",
    waitKind: null,
    nextAction: null,
  },
];

export const v3TeamFixture: V3TeamMember[] = [
  { role: "builder", missionId: "M-HYB-014", provider: "claude-code", status: "ACTIVE" },
  { role: "critic", missionId: "M-HYB-014", provider: "opencode", status: "ACTIVE" },
];

export const v3VerifierFixture: V3VerifierState = {
  missionId: "M-HYB-014",
  verdict: null,
  freshSession: true,
  independence: "INDEPENDENT",
  provider: "codex",
  repairRoundsUsed: 0,
  repairBudget: 3,
};

export const v3OutcomesFixture: V3OutcomeMeasurement[] = [
  {
    candidateId: "cand-0af7ce53b6d9",
    missionId: "M-MEM-007",
    window: "immediate",
    predicted: { expected_success: "0.71", expected_risk: "LOW" },
    actual: null,
    status: "UNKNOWN",
    confidence: 0.6,
    delta: null,
  },
  {
    candidateId: "cand-e18b74d29fc0",
    missionId: "M-SUP-002",
    window: "immediate",
    predicted: { expected_success: "0.64", expected_risk: "MEDIUM" },
    actual: { success: "true", repairs: "1" },
    status: "RECORDED",
    confidence: 0.65,
    delta: -0.05,
  },
];

export function buildV3DiscoveryView(): V3DiscoveryView {
  return {
    available: true,
    flag: { key: "discovery.cycle_enabled", enabled: false },
    policyVersion: POLICY_VERSION,
    candidatesRecent: structuredClone(v3CandidateFixtures),
    selected: structuredClone(v3SelectionFixture),
    portfolioSummary: {
      eligible: 3,
      deferred: 1,
      rejected: 1,
    },
    cycles: structuredClone(v3CyclesFixture),
    activeTeam: structuredClone(v3TeamFixture),
    verifierState: structuredClone(v3VerifierFixture),
    outcomeMeasurements: structuredClone(v3OutcomesFixture),
    maintenanceDue: "Post-mission scan due after M-HYB-014 verification completes",
    nextAction: "Verify builder output for M-HYB-014 at gate V-2",
  };
}
