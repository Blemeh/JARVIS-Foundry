import type { FoundrySnapshot } from "../snapshotTypes"

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
})

export type SnapshotTruthCase = {
  id: string
  boundary: string
  mutate: (snapshot: FoundrySnapshot) => void
}

export const snapshotTruthCases: SnapshotTruthCase[] = [
  {
    id: "constraints-present",
    boundary: "constraints",
    mutate: (s) => {
      s.controls.constraints = [{ id: "c-1" }, { id: "c-2" }]
    },
  },
  {
    id: "stale-leases",
    boundary: "stale leases",
    mutate: (s) => {
      s.health.stale_leases = [{ lease_id: "lease-1" }]
    },
  },
  {
    id: "missing-factors-input",
    boundary: "missing nested objects",
    mutate: (s) => {
      s.controls = { goals: [], constraints: [], vetoes: [], reprioritizations: [], redirects: [], paused: false, resume_available: false }
      s.health.stale_leases = undefined
    },
  },
  {
    id: "zero-cost",
    boundary: "zero values",
    mutate: (s) => {
      s.costs = { measured_actual_total_usd: 0, unknown_measurements: 0 }
    },
  },
  {
    id: "false-stop",
    boundary: "false values",
    mutate: (s) => {
      s.stop.active = false
    },
  },
  {
    id: "null-cost",
    boundary: "null values",
    mutate: (s) => {
      s.costs = { measured_actual_total_usd: null as unknown as number }
    },
  },
  {
    id: "malformed-cost",
    boundary: "malformed values",
    mutate: (s) => {
      s.costs = { measured_actual_total_usd: "not-a-number" as unknown as number }
    },
  },
  {
    id: "contradictory-trust",
    boundary: "contradictory factors",
    mutate: (s) => {
      s.health.trust_state = "PROVEN"
      s.health.supervisor_status = "FAILED"
      s.stop.active = true
    },
  },
  {
    id: "negative-cost",
    boundary: "negative values",
    mutate: (s) => {
      s.costs = { measured_actual_total_usd: -1 }
    },
  },
  {
    id: "large-cost",
    boundary: "extremely large values",
    mutate: (s) => {
      s.costs = { measured_actual_total_usd: 1_000_000_000.99 }
    },
  },
  {
    id: "unknown-token",
    boundary: "UNKNOWN string tokens",
    mutate: (s) => {
      s.costs = { measured_actual_total_usd: "UNKNOWN", unknown_measurements: 2 }
    },
  },
  {
    id: "stale-timestamp",
    boundary: "stale timestamps",
    mutate: (s) => {
      s.generated_at = "2026-08-20T12:00:00Z"
      s.health.heartbeat_at = "2026-08-20T10:00:00Z"
    },
  },
  {
    id: "future-timestamp",
    boundary: "future timestamps",
    mutate: (s) => {
      s.generated_at = "2026-08-20T12:00:00Z"
      s.health.heartbeat_at = "2099-01-01T00:00:00Z"
    },
  },
  {
    id: "unicode-objective",
    boundary: "Unicode and hostile long text",
    mutate: (s) => {
      s.missions[0].objective = "🛰️".repeat(40) + " probe " + "A".repeat(200)
    },
  },
]

export function buildSnapshotTruthCase(caseId: string): FoundrySnapshot {
  const spec = snapshotTruthCases.find((c) => c.id === caseId)
  if (!spec) throw new Error(`Unknown snapshot truth case: ${caseId}`)
  const snapshot = baseSnapshot()
  spec.mutate(snapshot)
  return snapshot
}

export function allSnapshotTruthCases(): Array<{ case: SnapshotTruthCase; snapshot: FoundrySnapshot }> {
  return snapshotTruthCases.map((truthCase) => {
    const snapshot = baseSnapshot()
    truthCase.mutate(snapshot)
    return { case: truthCase, snapshot }
  })
}
