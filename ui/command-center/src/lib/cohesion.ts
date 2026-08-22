import type {
  AgentRun,
  DataStream,
  EntityRef,
  EvidenceArtifact,
  LineageNode,
  MemoryGraph,
  MemoryNode,
  Mission,
  MissionRun,
  MissionStep,
  MockEvent,
  MockEventType,
  Notification,
  RelatedEntities,
  Scenario,
  SearchResultItem,
  SearchResults,
  SystemComponent,
  SystemOverview,
  TelemetryEntry,
  TimelineEvent,
  TrustSummary,
  VerifierVerdict,
  Worker,
} from "./contracts";
import {
  evidenceFixtures,
  memoryGraphFixture,
  missionRuns as baseMissionRuns,
  missions as baseMissions,
  systemComponents as baseSystemComponents,
  systemOverview as baseSystemOverview,
  timelineEvents as baseTimeline,
  trustSummary as baseTrust,
  workers as baseWorkers,
} from "./fixtures";

/* ------------------------------------------------------------------ */
/* Helpers                                                             */
/* ------------------------------------------------------------------ */

function t(at: string, source: string, level: TelemetryEntry["level"], text: string): TelemetryEntry {
  return { at, source, level, text };
}

function st(
  missionId: string,
  key: string,
  label: string,
  state: MissionStep["state"],
  detail: string,
  workerId?: string,
  evidenceIds: string[] = [],
): MissionStep {
  return { id: `ST-${missionId}-${key}`, missionId, key, label, state, detail, workerId, evidenceIds };
}

function vd(
  id: string,
  title: string,
  state: VerifierVerdict["state"],
  detail: string,
  evidenceId?: string,
  workerId?: string,
): VerifierVerdict {
  return { id, title, state, detail, evidenceId, workerId };
}

function ev(
  id: string,
  kind: string,
  typeLabel: string,
  source: string,
  hash: string,
  verdict: EvidenceArtifact["verdict"],
  capturedAt: string,
  missionRef: string,
  note: string,
  extra: Partial<EvidenceArtifact> = {},
): EvidenceArtifact {
  return {
    id,
    kind,
    typeLabel,
    source,
    sourceUri: `foundry://records/${kind.toLowerCase()}/${id.toLowerCase()}`,
    hash,
    verdict,
    capturedAt,
    missionRef,
    note,
    ...extra,
  };
}

const tl = (
  id: string,
  at: string,
  group: string,
  category: TimelineEvent["category"],
  title: string,
  detail: string,
  meta: { label: string; value: string }[],
  extra: Partial<TimelineEvent> = {},
): TimelineEvent => ({
  id,
  at,
  group,
  category,
  title,
  detail,
  meta,
  actions: [{ label: "View record", kind: "view" }],
  severity: "INFO",
  ...extra,
});

const ref = (kind: EntityRef["kind"], id: string, label: string): EntityRef => ({ kind, id, label });

/* ------------------------------------------------------------------ */
/* Mission step / run specs for every mission (one coherent universe)  */
/* ------------------------------------------------------------------ */

interface StepSpec {
  key: string;
  label: string;
  state: MissionStep["state"];
  detail: string;
  workerId?: string;
  evidenceIds?: string[];
}

interface MissionSpec {
  steps: StepSpec[];
  telemetry: TelemetryEntry[];
  streams: DataStream[];
  verdicts: VerifierVerdict[];
  stats: MissionRun["stats"];
}

export const missionSpecs: Record<string, MissionSpec> = {
  "M-HYB-014": {
    steps: [
      st("M-HYB-014", "observe", "Observe", "COMPLETE", "1.2s", "WRK-HERMES", ["EV-1142-004"]),
      st("M-HYB-014", "plan", "Plan", "COMPLETE", "0.8s", "WRK-HERMES"),
      st("M-HYB-014", "build", "Build", "ACTIVE", "Executing...", "WRK-OPENHANDS", ["EV-1142-003"]),
      st("M-HYB-014", "verify", "Verify", "PENDING", "Pending", "WRK-VERIFIER"),
      st("M-HYB-014", "writeback", "Write Back", "PENDING", "Pending", "WRK-BRIDGE"),
      st("M-HYB-014", "next", "Next Action", "PENDING", "Pending"),
    ],
    telemetry: [
      t("14:02:44.12", "SYS_INIT", "INFO", "Mission parameters loaded. Engaging orchestrator agent."),
      t("14:02:44.89", "OBSERVER", "INFO", "Scanning worker pools and lease table. Found 2 stale leases on worker pool B."),
      t("14:02:45.30", "PLANNER", "INFO", "Drafting activation sequence. Required routines: bridge_probe, lease_acquire, gate_v2."),
      t("14:02:46.55", "BUILDER", "INFO", "Compiling worker handoff contract for hybrid routing."),
      t("14:02:47.10", "BUILDER", "WARN", "Provider latency elevated (1860ms). Backup tier engaged for non-critical calls."),
      t("14:02:48.04", "LEDGER", "INFO", "Event EV-1142-003 queued for append with provenance chain."),
    ],
    streams: [
      { id: "ledger", name: "FOUNDRY_LEDGER", icon: "ledger", status: "Appending", state: "LIVE" },
      { id: "memory", name: "MEMORY_STORE", icon: "memory", status: "Snapshotting", state: "WRITING" },
      { id: "verifier", name: "VERIFIER_RUN_1142", icon: "api", status: "Polling (5s)", state: "POLLING" },
      { id: "routing", name: "PROVIDER_ROUTING", icon: "route", status: "Backup tier", state: "LIVE" },
    ],
    verdicts: [
      vd("VF-2039", "Observation Integrity", "PASSED", "Lease and worker-pool observations confirmed against the ledger. No corruption detected in initial ingestion.", "EV-1142-004", "WRK-VERIFIER"),
      vd("VF-2040", "Plan Feasibility", "WARN_ACCEPT", "Plan requires substantial provider throughput. Accepted, but throttling may occur while provider latency exceeds 1500ms."),
      vd("VF-2041", "Build Structure Scan", "EVALUATING", "Analyzing the handoff payload against activation policy and STOP invariants...", "EV-1142-003", "WRK-VERIFIER"),
    ],
    stats: { elapsed: "00:04:12", computeCost: "—", agentsActive: 4, evidenceArtifacts: 2 },
  },
  "M-VER-031": {
    steps: [
      st("M-VER-031", "observe", "Observe", "COMPLETE", "0.9s", "WRK-VERIFIER"),
      st("M-VER-031", "plan", "Plan", "COMPLETE", "1.1s", "WRK-VERIFIER"),
      st("M-VER-031", "build", "Build", "COMPLETE", "12m", "WRK-VERIFIER", ["EV-1142-001"]),
      st("M-VER-031", "verify", "Verify", "ACTIVE", "Running...", "WRK-VERIFIER"),
      st("M-VER-031", "writeback", "Write Back", "PENDING", "Pending", "WRK-EVIDENCE"),
      st("M-VER-031", "next", "Next Action", "PENDING", "Pending"),
    ],
    telemetry: [
      t("12:04:00.10", "SYS_INIT", "INFO", "Read-only audit session started for Sol contract layer."),
      t("12:04:12.40", "OBSERVER", "INFO", "Located 14 audit targets across 3 contract groups."),
      t("12:05:31.02", "PLANNER", "INFO", "Classification plan locked: CONFIRMED / NOT_REPRODUCED / NOT_TESTED."),
      t("12:31:08.11", "VERIFIER", "INFO", "Finding VF-2047 attached to evidence EV-1142-001."),
    ],
    streams: [
      { id: "ledger", name: "FOUNDRY_LEDGER", icon: "ledger", status: "Reading", state: "LIVE" },
      { id: "verifier", name: "VERIFIER_RUN_1142", icon: "api", status: "Polling (5s)", state: "POLLING" },
      { id: "archive", name: "SOL_ARCHIVE", icon: "database", status: "Cached", state: "CACHED" },
    ],
    verdicts: [
      vd("VF-2036", "Evidence Completeness", "PASSED", "Every audited contract has a frozen artifact hash attached before classification.", "EV-1142-001", "WRK-VERIFIER"),
      vd("VF-2037", "Classification Consistency", "PASSED", "No finding promoted to REPAIRED without a fresh independent verification record."),
      vd("VF-2047", "Findings Review", "EVALUATING", "Reviewing open findings against point-in-time audit evidence...", "EV-1142-001", "WRK-VERIFIER"),
    ],
    stats: { elapsed: "02:41:55", computeCost: "—", agentsActive: 2, evidenceArtifacts: 14 },
  },
  "M-MEM-007": {
    steps: [
      st("M-MEM-007", "observe", "Observe", "COMPLETE", "0.7s", "WRK-MEMORY", ["EV-1139-004"]),
      st("M-MEM-007", "plan", "Plan", "ACTIVE", "Planning merge...", "WRK-MEMORY"),
      st("M-MEM-007", "build", "Build", "PENDING", "Pending", "WRK-MEMORY"),
      st("M-MEM-007", "verify", "Verify", "PENDING", "Pending", "WRK-VERIFIER"),
      st("M-MEM-007", "writeback", "Write Back", "PENDING", "Pending"),
      st("M-MEM-007", "next", "Next Action", "PENDING", "Pending"),
    ],
    telemetry: [
      t("18:40:11.02", "SYS_INIT", "INFO", "Memory reconciliation session started."),
      t("18:40:12.10", "MEMORY", "INFO", "Snapshot 004 loaded. 128 durable entries, 9 worker-scratch claims."),
      t("18:41:00.40", "MEMORY", "INFO", "Category integrity check passed; no untyped nodes."),
    ],
    streams: [
      { id: "memory", name: "MEMORY_STORE", icon: "memory", status: "Reading", state: "LIVE" },
      { id: "reconcile", name: "RECONCILE_R88", icon: "database", status: "Planning", state: "POLLING" },
    ],
    verdicts: [
      vd("VF-2025", "Snapshot Integrity", "PASSED", "Snapshot 004 hashes verified against the ledger before merge.", "EV-1139-004", "WRK-VERIFIER"),
      vd("VF-2026", "Conflict Classification", "EVALUATING", "Classifying worker-scratch claims that overlap durable entries."),
    ],
    stats: { elapsed: "00:41:20", computeCost: "—", agentsActive: 1, evidenceArtifacts: 1 },
  },
  "M-SUP-002": {
    steps: [
      st("M-SUP-002", "observe", "Observe", "COMPLETE", "2.4s", "WRK-WATCHDOG", ["EV-1128-002"]),
      st("M-SUP-002", "plan", "Plan", "PENDING", "Awaiting owner decision", "WRK-WATCHDOG"),
      st("M-SUP-002", "build", "Build", "PENDING", "Pending", "WRK-WATCHDOG"),
      st("M-SUP-002", "verify", "Verify", "PENDING", "Pending", "WRK-VERIFIER"),
      st("M-SUP-002", "writeback", "Write Back", "PENDING", "Pending"),
      st("M-SUP-002", "next", "Next Action", "PENDING", "Pending"),
    ],
    telemetry: [
      t("09:30:00.00", "SYS_INIT", "INFO", "Supervisor recovery drill armed. Halted pending owner decision."),
      t("09:30:01.20", "WATCHDOG", "INFO", "Restart procedure drafted: restart, lease re-acquisition, pool re-registration."),
      t("09:31:44.10", "WATCHDOG", "WARN", "Owner decision required before drill can restart."),
    ],
    streams: [
      { id: "watchdog", name: "WATCHDOG_LOOP", icon: "database", status: "Idle", state: "LIVE" },
      { id: "drill", name: "RECOVERY_DRILL", icon: "route", status: "Armed", state: "CACHED" },
    ],
    verdicts: [
      vd("VF-2030", "Drill Procedure Review", "WARN_ACCEPT", "Procedure is deterministic, but the drill window must be owner-confirmed.", "EV-1128-002", "WRK-VERIFIER"),
      vd("VF-2031", "Restart Path Readiness", "EVALUATING", "Verifying restart path against lease invariants."),
    ],
    stats: { elapsed: "05:12:00", computeCost: "—", agentsActive: 0, evidenceArtifacts: 1 },
  },
  "M-PRV-011": {
    steps: [
      st("M-PRV-011", "observe", "Observe", "COMPLETE", "1.1s", "WRK-ROUTER", ["EV-1138-003"]),
      st("M-PRV-011", "plan", "Plan", "COMPLETE", "0.6s", "WRK-ROUTER"),
      st("M-PRV-011", "build", "Build", "ACTIVE", "Rerouting non-critical calls...", "WRK-ROUTER"),
      st("M-PRV-011", "verify", "Verify", "PENDING", "Pending", "WRK-VERIFIER"),
      st("M-PRV-011", "writeback", "Write Back", "PENDING", "Pending"),
      st("M-PRV-011", "next", "Next Action", "PENDING", "Pending"),
    ],
    telemetry: [
      t("05:15:00.10", "SYS_INIT", "INFO", "Provider degradation response started."),
      t("05:15:12.40", "ROUTER", "WARN", "Primary provider latency 1860ms; threshold 1500ms."),
      t("05:15:13.02", "ROUTER", "INFO", "Backup tier engaged for non-critical calls; primary retry in 60s."),
    ],
    streams: [
      { id: "routing", name: "PROVIDER_ROUTING", icon: "route", status: "Backup tier", state: "LIVE" },
      { id: "latency", name: "LATENCY_TELEMETRY", icon: "api", status: "Sampling 5s", state: "POLLING" },
    ],
    verdicts: [
      vd("VF-2028", "Reroute Policy", "WARN_ACCEPT", "Fallback results match policy; sustained provider latency remains unresolved.", "EV-1138-003", "WRK-VERIFIER"),
      vd("VF-2029", "Fallback Veracity", "EVALUATING", "Verifying a sample of fallback results against primary baselines."),
    ],
    stats: { elapsed: "09:00:00", computeCost: "—", agentsActive: 2, evidenceArtifacts: 1 },
  },
  "M-CAN-019": {
    steps: [
      st("M-CAN-019", "observe", "Observe", "COMPLETE", "1.0s", "WRK-VERIFIER"),
      st("M-CAN-019", "plan", "Plan", "COMPLETE", "0.9s", "WRK-VERIFIER"),
      st("M-CAN-019", "build", "Build", "COMPLETE", "2h", "WRK-OPENHANDS", ["EV-1140-012"]),
      st("M-CAN-019", "verify", "Verify", "ACTIVE", "Promotion gate running...", "WRK-VERIFIER"),
      st("M-CAN-019", "writeback", "Write Back", "PENDING", "Pending", "WRK-BRIDGE"),
      st("M-CAN-019", "next", "Next Action", "PENDING", "Pending"),
    ],
    telemetry: [
      t("16:20:00.00", "SYS_INIT", "INFO", "Candidate promotion preparation started for V8 HybridMemory."),
      t("16:20:12.20", "BUILDER", "INFO", "Artifact manifest frozen with hashes for 14 files."),
      t("17:02:00.00", "VERIFIER", "INFO", "Promotion evidence gate V-8 opened."),
    ],
    streams: [
      { id: "manifest", name: "ARTIFACT_MANIFEST", icon: "database", status: "Frozen", state: "CACHED" },
      { id: "gate", name: "PROMOTION_GATE", icon: "api", status: "Polling (10s)", state: "POLLING" },
    ],
    verdicts: [
      vd("VF-2044", "Artifact Freeze", "PASSED", "Frozen manifest hashes verified; no drift since seal.", "EV-1140-012", "WRK-VERIFIER"),
      vd("VF-2045", "Promotion Evidence Gate", "EVALUATING", "Waiting for reconciliation mission to confirm main is quiescent."),
    ],
    stats: { elapsed: "21:40:00", computeCost: "—", agentsActive: 3, evidenceArtifacts: 14 },
  },
  "M-STP-004": {
    steps: [
      st("M-STP-004", "observe", "Observe", "COMPLETE", "0.3s", "WRK-STOP"),
      st("M-STP-004", "plan", "Plan", "ACTIVE", "Scheduling supervised drill", "WRK-STOP"),
      st("M-STP-004", "build", "Build", "PENDING", "Pending", "WRK-STOP"),
      st("M-STP-004", "verify", "Verify", "PENDING", "Pending", "WRK-VERIFIER"),
      st("M-STP-004", "writeback", "Write Back", "PENDING", "Pending"),
      st("M-STP-004", "next", "Next Action", "PENDING", "Pending"),
    ],
    telemetry: [
      t("02:00:00.10", "SYS_INIT", "INFO", "STOP drill mission created. Deterministic path self-test OK."),
      t("02:00:01.30", "STOP", "INFO", "Dry run completed: 12ms, no side effects, missions uninterrupted."),
    ],
    streams: [
      { id: "stop", name: "STOP_SUBSYSTEM", icon: "ledger", status: "Armed", state: "LIVE" },
      { id: "drill", name: "STOP_DRILL", icon: "api", status: "Scheduled", state: "CACHED" },
    ],
    verdicts: [
      vd("VF-2032", "STOP Determinism", "PASSED", "STOP path verified offline-safe, independent of Hermes, models, network, and UI health."),
      vd("VF-2033", "Dry Run Integrity", "PASSED", "Dry run completed with zero side effects and full audit record."),
    ],
    stats: { elapsed: "12:00:00", computeCost: "—", agentsActive: 1, evidenceArtifacts: 0 },
  },
  "M-CRS-008": {
    steps: [
      st("M-CRS-008", "observe", "Observe", "COMPLETE", "1.4s", "WRK-WATCHDOG", ["EV-1142-002"]),
      st("M-CRS-008", "plan", "Plan", "COMPLETE", "0.5s", "WRK-WATCHDOG"),
      st("M-CRS-008", "build", "Build", "ACTIVE", "Repair run A-91 executing...", "WRK-OPENHANDS", ["EV-1142-006"]),
      st("M-CRS-008", "verify", "Verify", "COMPLETE", "PASS", "WRK-VERIFIER"),
      st("M-CRS-008", "writeback", "Write Back", "PENDING", "Pending", "WRK-BRIDGE"),
      st("M-CRS-008", "next", "Next Action", "PENDING", "Pending"),
    ],
    telemetry: [
      t("06:42:00.00", "WATCHDOG", "ERROR", "OpenHands worker exited with code 137. Crash detected."),
      t("06:42:01.10", "WATCHDOG", "INFO", "Lease replayed from sealed checkpoint C-18."),
      t("06:42:09.00", "SUPERVISOR", "INFO", "Repair run A-91 dispatched; corrective ledger entries appended."),
      t("06:45:30.00", "VERIFIER", "INFO", "Post-recovery verification passed."),
    ],
    streams: [
      { id: "watchdog", name: "WATCHDOG_LOOP", icon: "database", status: "Watching", state: "LIVE" },
      { id: "repair", name: "REPAIR_A91", icon: "api", status: "Executing", state: "WRITING" },
    ],
    verdicts: [
      vd("VF-2042", "Crash Restart Integrity", "FAILED", "Initial restart attempt produced a partial lease handoff; corrective run required.", "EV-1142-002", "WRK-VERIFIER"),
      vd("VF-2043", "Post-Recovery Verification", "PASSED", "Repair run A-91 replayed the lease, restarted from checkpoint, and appended corrective entries.", "EV-1142-006", "WRK-VERIFIER"),
    ],
    stats: { elapsed: "08:18:00", computeCost: "—", agentsActive: 2, evidenceArtifacts: 2 },
  },
  "M-GAT-022": {
    steps: [
      st("M-GAT-022", "observe", "Observe", "COMPLETE", "0.8s", "WRK-EVIDENCE", ["EV-1131-007"]),
      st("M-GAT-022", "plan", "Plan", "COMPLETE", "0.4s", "WRK-VERIFIER"),
      st("M-GAT-022", "build", "Build", "COMPLETE", "3m", "WRK-EVIDENCE", ["EV-1131-007"]),
      st("M-GAT-022", "verify", "Verify", "ACTIVE", "Reconciliation pending", "WRK-VERIFIER"),
      st("M-GAT-022", "writeback", "Write Back", "PENDING", "Pending", "WRK-BRIDGE"),
      st("M-GAT-022", "next", "Next Action", "PENDING", "Pending"),
    ],
    telemetry: [
      t("03:10:00.00", "SYS_INIT", "INFO", "Evidence gate review opened for ledger entry 1142."),
      t("03:10:44.00", "LEDGER", "ERROR", "Hash reconciliation failed for EV-1131-007."),
      t("03:11:02.10", "VERIFIER", "WARN", "Mismatch classified; additive correction requires owner decision."),
    ],
    streams: [
      { id: "ledger", name: "FOUNDRY_LEDGER", icon: "ledger", status: "Held", state: "CACHED" },
      { id: "gate", name: "EVIDENCE_GATE", icon: "api", status: "Blocked", state: "POLLING" },
    ],
    verdicts: [
      vd("VF-2038", "Ledger Hash Reconciliation", "FAILED", "Expected hash a7ffc9... does not match stored value. Additive correction queued for owner approval.", "EV-1131-007", "WRK-VERIFIER"),
    ],
    stats: { elapsed: "11:00:00", computeCost: "—", agentsActive: 1, evidenceArtifacts: 1 },
  },
  "M-RES-015": {
    steps: [
      st("M-RES-015", "observe", "Observe", "COMPLETE", "1.6s", "WRK-WATCHDOG", ["EV-1135-009"]),
      st("M-RES-015", "plan", "Plan", "ACTIVE", "Awaiting probe results", "WRK-WATCHDOG"),
      st("M-RES-015", "build", "Build", "PENDING", "Pending"),
      st("M-RES-015", "verify", "Verify", "PENDING", "Pending", "WRK-VERIFIER"),
      st("M-RES-015", "writeback", "Write Back", "PENDING", "Pending"),
      st("M-RES-015", "next", "Next Action", "PENDING", "Pending"),
    ],
    telemetry: [
      t("23:20:00.10", "SYS_INIT", "INFO", "Resource readiness probe started."),
      t("23:21:40.00", "PROBE", "INFO", "Compute 42%, memory 68%, storage 34%. All above readiness threshold."),
    ],
    streams: [
      { id: "probe", name: "RESOURCE_PROBE", icon: "database", status: "Sampling", state: "POLLING" },
      { id: "pool", name: "WORKER_POOL", icon: "route", status: "Idle", state: "LIVE" },
    ],
    verdicts: [
      vd("VF-2035", "Readiness Measurement", "PASSED", "Measured capacity attached; no pool below the readiness threshold.", "EV-1135-009", "WRK-VERIFIER"),
    ],
    stats: { elapsed: "15:00:00", computeCost: "—", agentsActive: 1, evidenceArtifacts: 1 },
  },
  "M-WFL-006": {
    steps: [
      st("M-WFL-006", "observe", "Observe", "COMPLETE", "0.6s", "WRK-MEMORY"),
      st("M-WFL-006", "plan", "Plan", "COMPLETE", "0.3s", "WRK-MEMORY"),
      st("M-WFL-006", "build", "Build", "ACTIVE", "Reconcile job R-88 running", "WRK-MEMORY"),
      st("M-WFL-006", "verify", "Verify", "PENDING", "Pending", "WRK-VERIFIER"),
      st("M-WFL-006", "writeback", "Write Back", "PENDING", "Pending"),
      st("M-WFL-006", "next", "Next Action", "PENDING", "Pending"),
    ],
    telemetry: [
      t("14:00:00.00", "SYS_INIT", "INFO", "Reconcile workflow started against mission state and lease table."),
      t("14:00:12.20", "MEMORY", "INFO", "Projection consistency check R-88 running; 0 drift so far."),
    ],
    streams: [
      { id: "reconcile", name: "RECONCILE_R88", icon: "database", status: "Merging", state: "WRITING" },
      { id: "ledger", name: "FOUNDRY_LEDGER", icon: "ledger", status: "Tail reading", state: "LIVE" },
    ],
    verdicts: [
      vd("VF-2027", "Projection Consistency", "PASSED", "Mission state, worker leases, and ledger tail project to one consistent snapshot.", "EV-1139-004", "WRK-VERIFIER"),
    ],
    stats: { elapsed: "24:20:00", computeCost: "—", agentsActive: 2, evidenceArtifacts: 1 },
  },
  "M-EVI-003": {
    steps: [
      st("M-EVI-003", "observe", "Observe", "COMPLETE", "0.5s", "WRK-EVIDENCE"),
      st("M-EVI-003", "plan", "Plan", "COMPLETE", "0.4s", "WRK-EVIDENCE"),
      st("M-EVI-003", "build", "Build", "ACTIVE", "Ingesting run 1142 artifacts", "WRK-EVIDENCE", ["EV-1142-001"]),
      st("M-EVI-003", "verify", "Verify", "PENDING", "Pending", "WRK-VERIFIER"),
      st("M-EVI-003", "writeback", "Write Back", "PENDING", "Pending"),
      st("M-EVI-003", "next", "Next Action", "PENDING", "Pending"),
    ],
    telemetry: [
      t("04:05:00.00", "SYS_INIT", "INFO", "Evidence ingestion session started for verifier run 1142."),
      t("04:05:01.20", "EVIDENCE", "INFO", "Artifacts fingerprinted; provenance attached before visibility."),
    ],
    streams: [
      { id: "ingest", name: "INGEST_1142", icon: "api", status: "Ingesting", state: "WRITING" },
      { id: "ledger", name: "FOUNDRY_LEDGER", icon: "ledger", status: "Appending", state: "LIVE" },
    ],
    verdicts: [
      vd("VF-2034", "Provenance Attachment", "PASSED", "Every ingested row carries a source URI and fingerprint before display.", "EV-1142-001", "WRK-VERIFIER"),
    ],
    stats: { elapsed: "10:00:00", computeCost: "—", agentsActive: 2, evidenceArtifacts: 3 },
  },
};

/* ------------------------------------------------------------------ */
/* Baseline evidence provenance for the fixture artifacts               */
/* ------------------------------------------------------------------ */

export const evidenceExtras: Record<string, Partial<EvidenceArtifact>> = {
  "EV-1142-001": {
    workerId: "WRK-EVIDENCE",
    stepId: "ST-M-VER-031-build",
    verdictId: "VF-2047",
    claim: "Verifier bundle for run 1142 is complete, fingerprinted, and provenance-attached.",
    decision: "Admitted to findings review.",
    outcome: "VF-2047 evaluating.",
    relatedEventId: "TL-0909",
  },
  "EV-1142-002": {
    workerId: "WRK-OPENHANDS",
    stepId: "ST-M-CRS-008-observe",
    verdictId: "VF-2042",
    claim: "Restart sequence replayed the lease and resumed from sealed checkpoint C-18.",
    decision: "Corrective ledger entries appended.",
    outcome: "Repair run A-91 completed and verified.",
    relatedEventId: "TL-0905",
  },
  "EV-1139-004": {
    workerId: "WRK-MEMORY",
    stepId: "ST-M-MEM-007-observe",
    claim: "Sealed snapshot 004 represents durable epistemic state before the merge.",
    decision: "Merge proceeds additively against worker-scratch claims.",
    outcome: "Reconciliation R-88 in progress.",
    relatedEventId: "TL-0906",
  },
  "EV-1131-007": {
    workerId: "WRK-EVIDENCE",
    stepId: "ST-M-GAT-022-build",
    verdictId: "VF-2038",
    claim: "Ledger entry 1142 hash matches the stored fingerprint.",
    decision: "Reconciliation failed; mismatch must be corrected additively.",
    outcome: "Owner decision required before correction.",
    relatedEventId: "TL-0912",
  },
  "EV-1140-012": {
    workerId: "WRK-OPENHANDS",
    stepId: "ST-M-CAN-019-build",
    verdictId: "VF-2044",
    claim: "Promotion report describes frozen artifacts with verified hashes.",
    decision: "Artifact freeze accepted.",
    outcome: "Promotion remains pending the reconciliation mission.",
    relatedEventId: "TL-0907",
  },
  "EV-1138-003": {
    workerId: "WRK-ROUTER",
    stepId: "ST-M-PRV-011-observe",
    verdictId: "VF-2028",
    claim: "Backup tier routing rules preserve policy during provider degradation.",
    decision: "Backup tier engaged for non-critical calls.",
    outcome: "Fallback veracity under review.",
    relatedEventId: "TL-0901",
  },
  "EV-1135-009": {
    workerId: "WRK-WATCHDOG",
    stepId: "ST-M-RES-015-observe",
    claim: "Compute, memory, and storage pools were measured above readiness thresholds.",
    decision: "Pools reported ready.",
    outcome: "Readiness probe recorded.",
    relatedEventId: "TL-0902",
  },
  "EV-1128-002": {
    workerId: "WRK-WATCHDOG",
    stepId: "ST-M-SUP-002-observe",
    verdictId: "VF-2030",
    claim: "Recovery drill command sequence was owner-authorized.",
    decision: "Drill armed but halted pending owner restart decision.",
    outcome: "Drill window unconfirmed.",
    relatedEventId: "TL-0908",
  },
};

export const baselineEvidence: EvidenceArtifact[] = [
  ...evidenceFixtures.map((e) => ({ ...e, ...evidenceExtras[e.id] })),
  ev(
    "EV-1142-003",
    "SNAP",
    "Handoff payload snapshot",
    "hybrid worker bridge",
    "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
    "VERIFIED",
    "2026-08-16T13:45:14Z",
    "M-HYB-014",
    "Snapshot of the activation payload handed from Hermes to the Hybrid Worker Bridge.",
    {
      workerId: "WRK-BRIDGE",
      stepId: "ST-M-HYB-014-build",
      verdictId: "VF-2041",
      claim: "Activation handoff payload is structurally valid and policy-compliant.",
      decision: "Admitted to gate V-2 structural evaluation.",
      outcome: "VF-2041 evaluating.",
      relatedEventId: "TL-0903",
    },
  ),
  ev(
    "EV-1142-004",
    "LOG",
    "Lease acquisition proof",
    "supervisor.watchdog",
    "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9",
    "VERIFIED",
    "2026-08-16T14:02:45Z",
    "M-HYB-014",
    "Lease acquisition for worker pool B confirmed during activation observation.",
    {
      workerId: "WRK-HERMES",
      stepId: "ST-M-HYB-014-observe",
      verdictId: "VF-2039",
      claim: "Two stale leases were observed and re-acquired.",
      decision: "Pool B admitted to the activation sequence.",
      outcome: "Observation integrity passed.",
      relatedEventId: "TL-0902",
    },
  ),
  ev(
    "EV-1142-006",
    "LOG",
    "Repair restart record",
    "supervisor.watchdog",
    "e7f6c011776e8db7cd330b54174fd76f7d0216b612387a5ffcfb81e6f0919683",
    "VERIFIED",
    "2026-08-16T06:45:30Z",
    "M-CRS-008",
    "Repair run A-91 restarted the worker from checkpoint C-18 and appended corrective entries.",
    {
      workerId: "WRK-OPENHANDS",
      stepId: "ST-M-CRS-008-build",
      verdictId: "VF-2043",
      claim: "Post-recovery state matches the sealed checkpoint and lease table.",
      decision: "Recovery accepted.",
      outcome: "VF-2043 passed.",
      relatedEventId: "TL-0905",
    },
  ),
];

/* ------------------------------------------------------------------ */
/* Timeline refs for the fixture events                                */
/* ------------------------------------------------------------------ */

export const timelineExtras: Record<string, Partial<TimelineEvent>> = {
  "TL-0901": { severity: "CRITICAL", missionId: "M-PRV-011", agentId: "WRK-ROUTER", systemId: "SYS-ROUTING" },
  "TL-0902": { severity: "INFO", systemId: "SYS-SUPERVISOR", agentId: "WRK-WATCHDOG" },
  "TL-0903": { severity: "INFO", missionId: "M-HYB-014", agentId: "WRK-HERMES", evidenceId: "EV-1142-003", systemId: "SYS-WORKERS" },
  "TL-0904": { severity: "WARNING", missionId: "M-HYB-014", verdictId: "VF-2041", systemId: "SYS-VERIFICATION" },
  "TL-0905": { severity: "CRITICAL", missionId: "M-CRS-008", agentId: "WRK-OPENHANDS", systemId: "SYS-OPENHANDS-WORKER", evidenceId: "EV-1142-002" },
  "TL-0906": { severity: "INFO", missionId: "M-MEM-007", agentId: "WRK-MEMORY", memoryId: "n-reconcile", systemId: "SYS-MEMORY" },
  "TL-0907": { severity: "INFO", missionId: "M-CAN-019", systemId: "SYS-MEMORY" },
  "TL-0908": { severity: "CRITICAL", systemId: "SYS-SUPERVISOR" },
  "TL-0909": { severity: "INFO", missionId: "M-HYB-014", agentId: "WRK-OPENHANDS", evidenceId: "EV-1142-001", systemId: "SYS-VERIFICATION" },
  "TL-0910": { severity: "INFO", systemId: "SYS-STOP", missionId: "M-STP-004" },
  "TL-0911": { severity: "INFO", systemId: "SYS-STOP", memoryId: "n-stop" },
  "TL-0912": { severity: "WARNING", evidenceId: "EV-1131-007", systemId: "SYS-LEDGER", missionId: "M-GAT-022" },
};

export const baselineTimeline: TimelineEvent[] = baseTimeline.map((e) => ({ ...e, ...timelineExtras[e.id] }));

/* ------------------------------------------------------------------ */
/* Worker overlays (operational relationships)                         */
/* ------------------------------------------------------------------ */

export const workerExtras: Record<string, Partial<Worker>> = {
  "WRK-HERMES": {
    systemId: "SYS-HERMES-WORKER",
    missionIds: ["M-HYB-014", "M-GAT-022"],
    failures: 0,
    recoveries: 0,
    evidenceIds: ["EV-1142-004"],
    lastHeartbeat: "2026-08-16T14:02:47Z",
  },
  "WRK-OPENHANDS": {
    systemId: "SYS-OPENHANDS-WORKER",
    missionIds: ["M-HYB-014", "M-CRS-008", "M-CAN-019"],
    failures: 2,
    recoveries: 2,
    evidenceIds: ["EV-1142-002", "EV-1142-006"],
    lastHeartbeat: "2026-08-16T14:02:39Z",
  },
  "WRK-BRIDGE": {
    systemId: "SYS-WORKERS",
    missionIds: ["M-HYB-014", "M-CAN-019"],
    failures: 0,
    recoveries: 0,
    evidenceIds: ["EV-1142-003"],
    lastHeartbeat: "2026-08-16T14:02:40Z",
  },
  "WRK-VERIFIER": {
    systemId: "SYS-VERIFICATION",
    missionIds: ["M-VER-031", "M-GAT-022", "M-CAN-019", "M-HYB-014", "M-SUP-002"],
    failures: 0,
    recoveries: 0,
    evidenceIds: ["EV-1142-001", "EV-1131-007"],
    lastHeartbeat: "2026-08-16T14:02:41Z",
  },
  "WRK-MEMORY": {
    systemId: "SYS-MEMORY",
    missionIds: ["M-MEM-007", "M-WFL-006"],
    failures: 0,
    recoveries: 1,
    evidenceIds: ["EV-1139-004"],
    lastHeartbeat: "2026-08-16T14:02:43Z",
  },
  "WRK-ROUTER": {
    systemId: "SYS-ROUTING",
    missionIds: ["M-PRV-011"],
    failures: 1,
    recoveries: 1,
    evidenceIds: ["EV-1138-003"],
    lastHeartbeat: "2026-08-16T14:02:42Z",
  },
  "WRK-WATCHDOG": {
    systemId: "SYS-SUPERVISOR",
    missionIds: ["M-SUP-002", "M-CRS-008", "M-RES-015"],
    failures: 0,
    recoveries: 0,
    evidenceIds: ["EV-1135-009", "EV-1128-002"],
    lastHeartbeat: "2026-08-16T14:02:47Z",
  },
  "WRK-STOP": {
    systemId: "SYS-STOP",
    missionIds: ["M-STP-004"],
    failures: 0,
    recoveries: 0,
    evidenceIds: [],
    lastHeartbeat: "2026-08-16T14:02:47Z",
  },
  "WRK-EVIDENCE": {
    systemId: "SYS-EVIDENCE",
    missionIds: ["M-EVI-003", "M-VER-031", "M-GAT-022"],
    failures: 0,
    recoveries: 0,
    evidenceIds: ["EV-1142-001", "EV-1131-007"],
    lastHeartbeat: "2026-08-16T14:02:38Z",
  },
};

export const baselineWorkers: Worker[] = baseWorkers.map((w) => ({ ...w, ...workerExtras[w.id] }));

/* ------------------------------------------------------------------ */
/* System overlays                                                     */
/* ------------------------------------------------------------------ */

export const systemExtras: Record<string, Partial<SystemComponent>> = {
  "SYS-SUPERVISOR": { missionIds: ["M-SUP-002", "M-CRS-008", "M-WFL-006"], incidentEventIds: ["TL-0908"] },
  "SYS-GATEWAY": { missionIds: ["M-HYB-014"] },
  "SYS-FOUNDRY": { missionIds: ["M-WFL-006", "M-EVI-003", "M-GAT-022"] },
  "SYS-MEMORY": { missionIds: ["M-MEM-007", "M-CAN-019"], incidentEventIds: ["TL-0906"] },
  "SYS-WORKERS": { missionIds: ["M-HYB-014", "M-CRS-008"], incidentEventIds: ["TL-0905"] },
  "SYS-HERMES-WORKER": { missionIds: ["M-HYB-014"] },
  "SYS-OPENHANDS-WORKER": { missionIds: ["M-CRS-008", "M-HYB-014"], incidentEventIds: ["TL-0905"] },
  "SYS-ROUTING": { missionIds: ["M-PRV-011"], incidentEventIds: ["TL-0901"] },
  "SYS-CONNECTORS": { missionIds: [] },
  "SYS-STOP": { missionIds: ["M-STP-004"], incidentEventIds: ["TL-0910", "TL-0911"] },
  "SYS-LEDGER": { missionIds: ["M-GAT-022", "M-WFL-006"], incidentEventIds: ["TL-0912"] },
  "SYS-VERIFICATION": { missionIds: ["M-VER-031", "M-CAN-019", "M-HYB-014"], incidentEventIds: ["TL-0904"] },
  "SYS-EVIDENCE": { missionIds: ["M-EVI-003", "M-GAT-022"] },
};

export const baselineSystems: SystemComponent[] = baseSystemComponents.map((c) => ({ ...c, ...systemExtras[c.id] }));

/* ------------------------------------------------------------------ */
/* Memory node operational refs                                        */
/* ------------------------------------------------------------------ */

export const memoryRefs: Record<string, EntityRef> = {
  "n-mission": ref("mission", "M-HYB-014", "M-HYB-014"),
  "n-bridge": ref("system", "SYS-WORKERS", "Worker subsystem"),
  "n-gate": ref("memory", "n-gate", "Gate V-2"),
  "n-verdict": ref("evidence", "EV-1142-003", "EV-1142-003"),
  "n-degrade": ref("system", "SYS-ROUTING", "Provider routing"),
  "n-router": ref("agent", "WRK-ROUTER", "WRK-ROUTER"),
  "n-reconcile": ref("mission", "M-MEM-007", "M-MEM-007"),
  "n-ledger": ref("evidence", "EV-1131-007", "EV-1131-007"),
  "n-stop": ref("system", "SYS-STOP", "SYS-STOP"),
};

export const baselineMemoryGraph: MemoryGraph = {
  nodes: memoryGraphFixture.nodes.map((n) => ({ ...n, ...(memoryRefs[n.id] ? { ref: memoryRefs[n.id] } : {}) })),
  edges: memoryGraphFixture.edges,
};

/* ------------------------------------------------------------------ */
/* Agent runs (worker runs with stable ids, incl. A-82 / A-91)         */
/* ------------------------------------------------------------------ */

export const baselineAgentRuns: AgentRun[] = [
  {
    id: "RUN-HERMES-014",
    missionId: "M-HYB-014",
    workerId: "WRK-HERMES",
    stepKey: "observe",
    state: "COMPLETE",
    summary: "Coordinated activation observation and lease acquisition.",
    startedAt: "2026-08-16T14:02:44Z",
    endedAt: "2026-08-16T14:02:45Z",
    evidenceIds: ["EV-1142-004"],
  },
  {
    id: "RUN-HERMES-022",
    missionId: "M-GAT-022",
    workerId: "WRK-HERMES",
    stepKey: "verify",
    state: "QUEUED",
    summary: "Standing by for owner decision on ledger correction.",
    startedAt: "2026-08-16T03:11:00Z",
    evidenceIds: [],
  },
  {
    id: "A-82",
    missionId: "M-CRS-008",
    workerId: "WRK-OPENHANDS",
    stepKey: "build",
    state: "FAILED",
    summary: "Build run exited with code 137; lease partially handed off before checkpoint.",
    startedAt: "2026-08-16T06:40:00Z",
    endedAt: "2026-08-16T06:42:00Z",
    evidenceIds: ["EV-1142-002"],
    verdictId: "VF-2042",
  },
  {
    id: "A-91",
    missionId: "M-CRS-008",
    workerId: "WRK-OPENHANDS",
    stepKey: "build",
    state: "COMPLETE",
    summary: "Repair run replayed the lease, restarted from checkpoint C-18, and appended corrective entries.",
    startedAt: "2026-08-16T06:42:09Z",
    endedAt: "2026-08-16T06:45:30Z",
    evidenceIds: ["EV-1142-006"],
    verdictId: "VF-2043",
  },
  {
    id: "RUN-OH-014",
    missionId: "M-HYB-014",
    workerId: "WRK-OPENHANDS",
    stepKey: "build",
    state: "ACTIVE",
    summary: "Executing build step B-3 for the activation handoff payload.",
    startedAt: "2026-08-16T14:02:46Z",
    evidenceIds: ["EV-1142-003"],
  },
  {
    id: "RUN-BRIDGE-014",
    missionId: "M-HYB-014",
    workerId: "WRK-BRIDGE",
    stepKey: "writeback",
    state: "ACTIVE",
    summary: "Handling worker handoff at gate V-2.",
    startedAt: "2026-08-16T13:45:00Z",
    evidenceIds: ["EV-1142-003"],
  },
  {
    id: "RUN-VER-031",
    missionId: "M-VER-031",
    workerId: "WRK-VERIFIER",
    stepKey: "verify",
    state: "ACTIVE",
    summary: "Reviewing findings against point-in-time audit evidence.",
    startedAt: "2026-08-15T12:31:00Z",
    evidenceIds: ["EV-1142-001"],
    verdictId: "VF-2047",
  },
  {
    id: "RUN-VER-022",
    missionId: "M-GAT-022",
    workerId: "WRK-VERIFIER",
    stepKey: "verify",
    state: "COMPLETE",
    summary: "Hash reconciliation failed; corrective correction requires owner approval.",
    startedAt: "2026-08-16T03:10:44Z",
    endedAt: "2026-08-16T03:11:02Z",
    evidenceIds: ["EV-1131-007"],
    verdictId: "VF-2038",
  },
  {
    id: "RUN-VER-019",
    missionId: "M-CAN-019",
    workerId: "WRK-VERIFIER",
    stepKey: "verify",
    state: "ACTIVE",
    summary: "Promotion evidence gate V-8 under evaluation.",
    startedAt: "2026-08-14T17:02:00Z",
    evidenceIds: ["EV-1140-012"],
    verdictId: "VF-2045",
  },
  {
    id: "RUN-MEM-007",
    missionId: "M-MEM-007",
    workerId: "WRK-MEMORY",
    stepKey: "plan",
    state: "ACTIVE",
    summary: "Reconciling durable memory against worker-scratch state.",
    startedAt: "2026-08-15T18:40:00Z",
    evidenceIds: ["EV-1139-004"],
  },
  {
    id: "RUN-MEM-006",
    missionId: "M-WFL-006",
    workerId: "WRK-MEMORY",
    stepKey: "build",
    state: "ACTIVE",
    summary: "Reconcile job R-88 merging ledger tail into the projection.",
    startedAt: "2026-08-15T14:00:00Z",
    evidenceIds: ["EV-1139-004"],
  },
  {
    id: "RUN-ROUTER-011",
    missionId: "M-PRV-011",
    workerId: "WRK-ROUTER",
    stepKey: "build",
    state: "ACTIVE",
    summary: "Rerouting non-critical calls to the backup tier.",
    startedAt: "2026-08-16T05:15:00Z",
    evidenceIds: ["EV-1138-003"],
  },
  {
    id: "RUN-WATCH-008",
    missionId: "M-CRS-008",
    workerId: "WRK-WATCHDOG",
    stepKey: "observe",
    state: "COMPLETE",
    summary: "Observed the crash, replayed the lease, and dispatched repair run A-91.",
    startedAt: "2026-08-16T06:42:00Z",
    endedAt: "2026-08-16T06:42:09Z",
    evidenceIds: ["EV-1142-002"],
  },
  {
    id: "RUN-STOP-004",
    missionId: "M-STP-004",
    workerId: "WRK-STOP",
    stepKey: "plan",
    state: "COMPLETE",
    summary: "Deterministic STOP dry run completed with zero side effects.",
    startedAt: "2026-08-15T23:05:00Z",
    endedAt: "2026-08-15T23:05:00Z",
    evidenceIds: [],
  },
  {
    id: "RUN-EVID-003",
    missionId: "M-EVI-003",
    workerId: "WRK-EVIDENCE",
    stepKey: "build",
    state: "ACTIVE",
    summary: "Ingesting verifier run 1142 artifacts and attaching provenance.",
    startedAt: "2026-08-16T04:05:00Z",
    evidenceIds: ["EV-1142-001"],
  },
  {
    id: "RUN-EVID-031",
    missionId: "M-VER-031",
    workerId: "WRK-EVIDENCE",
    stepKey: "observe",
    state: "COMPLETE",
    summary: "Fingerprinted the verifier bundle before findings review.",
    startedAt: "2026-08-15T12:31:00Z",
    endedAt: "2026-08-15T12:31:02Z",
    evidenceIds: ["EV-1142-001"],
  },
];

/** Static verdict -> mission lookup so verdict references can be navigated. */
export const verdictToMission: Record<string, string> = (() => {
  const map: Record<string, string> = {};
  for (const [missionId, spec] of Object.entries(missionSpecs)) {
    for (const v of spec.verdicts) map[v.id] = missionId;
  }
  return map;
})();

/** Static agent-run -> worker lookup so "open agent A-82" resolves to its worker. */
export const agentRunToWorker: Record<string, string> = Object.fromEntries(
  baselineAgentRuns.map((r) => [r.id, r.workerId]),
);

/* ------------------------------------------------------------------ */
/* Notification fixtures                                               */
/* ------------------------------------------------------------------ */

export const baselineNotifications: Notification[] = [
  {
    id: "NT-1001",
    severity: "CRITICAL",
    state: "UNREAD",
    category: "EVIDENCE_INTEGRITY",
    title: "Ledger hash mismatch detected",
    detail: "EV-1131-007 failed hash reconciliation. An additive correction is queued for owner approval.",
    createdAt: "2026-08-16T03:10:44Z",
    refs: [
      ref("evidence", "EV-1131-007", "EV-1131-007"),
      ref("mission", "M-GAT-022", "M-GAT-022"),
      ref("system", "SYS-LEDGER", "Event ledger"),
    ],
  },
  {
    id: "NT-1002",
    severity: "WARNING",
    state: "UNREAD",
    category: "PROVIDER_DEGRADED",
    title: "Provider latency above threshold",
    detail: "Primary provider latency crossed 1500ms. Backup tier engaged for non-critical calls.",
    createdAt: "2026-08-16T14:02:45Z",
    refs: [
      ref("mission", "M-PRV-011", "M-PRV-011"),
      ref("system", "SYS-ROUTING", "Provider routing"),
      ref("agent", "WRK-ROUTER", "WRK-ROUTER"),
      ref("timeline", "TL-0901", "TL-0901"),
    ],
  },
  {
    id: "NT-1003",
    severity: "WARNING",
    state: "UNREAD",
    category: "VERIFICATION_PENDING",
    title: "Build structure scan evaluating",
    detail: "Gate V-2 build structure scan is evaluating the handoff payload.",
    createdAt: "2026-08-16T13:15:00Z",
    refs: [
      ref("mission", "M-HYB-014", "M-HYB-014"),
      ref("verdict", "VF-2041", "VF-2041"),
      ref("evidence", "EV-1142-003", "EV-1142-003"),
    ],
  },
  {
    id: "NT-1004",
    severity: "INFO",
    state: "ACKNOWLEDGED",
    category: "WORKER_RECOVERED",
    title: "OpenHands worker recovered",
    detail: "Worker restarted from checkpoint C-18 and corrective ledger entries were appended.",
    createdAt: "2026-08-16T12:30:00Z",
    refs: [
      ref("agent", "WRK-OPENHANDS", "WRK-OPENHANDS"),
      ref("mission", "M-CRS-008", "M-CRS-008"),
      ref("timeline", "TL-0905", "TL-0905"),
      ref("evidence", "EV-1142-002", "EV-1142-002"),
    ],
  },
  {
    id: "NT-1005",
    severity: "INFO",
    state: "ACKNOWLEDGED",
    category: "HEARTBEAT_MISSED",
    title: "Supervisor heartbeat gap 14s",
    detail: "Watchdog observed a 14 second gap; supervisor restarted and re-acquired all leases.",
    createdAt: "2026-08-16T08:31:44Z",
    refs: [ref("system", "SYS-SUPERVISOR", "JARVIS Supervisor"), ref("timeline", "TL-0908", "TL-0908")],
  },
  {
    id: "NT-1006",
    severity: "INFO",
    state: "RESOLVED",
    category: "RECONCILIATION",
    title: "Memory snapshot 004 sealed",
    detail: "Snapshot sealed before the additive merge of worker-scratch claims.",
    createdAt: "2026-08-15T18:44:20Z",
    refs: [
      ref("mission", "M-MEM-007", "M-MEM-007"),
      ref("memory", "n-reconcile", "Memory Reconcile"),
      ref("timeline", "TL-0906", "TL-0906"),
    ],
  },
  {
    id: "NT-1007",
    severity: "WARNING",
    state: "RESOLVED",
    category: "CANDIDATE_PROMOTED",
    title: "V8 HybridMemory artifacts frozen",
    detail: "Candidate artifacts frozen; promotion remains pending the reconciliation mission.",
    createdAt: "2026-08-14T17:02:00Z",
    refs: [ref("mission", "M-CAN-019", "M-CAN-019"), ref("timeline", "TL-0907", "TL-0907")],
  },
];

/* ------------------------------------------------------------------ */
/* Universe                                                            */
/* ------------------------------------------------------------------ */

export interface MockUniverse {
  missions: Mission[];
  runs: Record<string, MissionRun>;
  steps: Record<string, MissionStep[]>;
  workers: Record<string, Worker>;
  evidence: EvidenceArtifact[];
  timeline: TimelineEvent[];
  memoryGraph: MemoryGraph;
  systems: Record<string, SystemComponent>;
  overview: SystemOverview;
  trust: TrustSummary;
  notifications: Notification[];
  agentRuns: AgentRun[];
  stopActive: boolean;
  seq: number;
}

export function buildUniverse(): MockUniverse {
  const runs: Record<string, MissionRun> = {};
  const steps: Record<string, MissionStep[]> = {};
  for (const mission of baseMissions) {
    const spec = missionSpecs[mission.id] ?? missionSpecs["M-HYB-014"];
    steps[mission.id] = spec.steps.map((s) => ({
      ...s,
      id: `ST-${mission.id}-${s.key}`,
      missionId: mission.id,
      evidenceIds: s.evidenceIds ?? [],
    }));
    runs[mission.id] = {
      missionId: mission.id,
      pipeline: spec.steps.map((s) => ({ key: s.key, label: s.label, state: s.state, detail: s.detail })),
      telemetry: spec.telemetry,
      streams: spec.streams,
      verdicts: spec.verdicts,
      stats: spec.stats,
    };
  }
  // Keep the two original runs intact so their richer telemetry survives.
  runs["M-HYB-014"] = structuredClone(baseMissionRuns["M-HYB-014"]);
  runs["M-VER-031"] = structuredClone(baseMissionRuns["M-VER-031"]);
  runs["M-HYB-014"].pipeline = missionSpecs["M-HYB-014"].steps.map((s) => ({
    key: s.key,
    label: s.label,
    state: s.state,
    detail: s.detail,
  }));
  runs["M-VER-031"].pipeline = missionSpecs["M-VER-031"].steps.map((s) => ({
    key: s.key,
    label: s.label,
    state: s.state,
    detail: s.detail,
  }));
  runs["M-HYB-014"].verdicts = missionSpecs["M-HYB-014"].verdicts;
  runs["M-VER-031"].verdicts = missionSpecs["M-VER-031"].verdicts;
  runs["M-HYB-014"].stats = missionSpecs["M-HYB-014"].stats;
  runs["M-VER-031"].stats = missionSpecs["M-VER-031"].stats;

  const workers: Record<string, Worker> = {};
  for (const w of baselineWorkers) workers[w.id] = structuredClone(w);
  const systems: Record<string, SystemComponent> = {};
  for (const c of baselineSystems) systems[c.id] = structuredClone(c);

  return {
    missions: structuredClone(baseMissions),
    runs,
    steps,
    workers,
    evidence: structuredClone(baselineEvidence),
    timeline: structuredClone(baselineTimeline),
    memoryGraph: structuredClone(baselineMemoryGraph),
    systems,
    overview: structuredClone(baseSystemOverview),
    trust: structuredClone(baseTrust),
    notifications: structuredClone(baselineNotifications),
    agentRuns: structuredClone(baselineAgentRuns),
    stopActive: false,
    seq: 0,
  };
}

export function makeEvent(u: MockUniverse, type: MockEventType, detail: string): MockEvent {
  u.seq += 1;
  return {
    id: `EV-${type.replace(/[^a-z0-9]+/gi, "-").toUpperCase()}-${String(u.seq).padStart(3, "0")}`,
    type,
    at: new Date().toISOString(),
    detail,
  };
}

/* ------------------------------------------------------------------ */
/* Event mutations (one event, coherent state change across domains)   */
/* ------------------------------------------------------------------ */

function timelineRefs(extra: Partial<TimelineEvent> = {}): Partial<TimelineEvent> {
  return { group: "NOW", ...extra };
}

function recomputeOpenFindings(u: MockUniverse): void {
  u.trust.unresolvedFindings =
    2 +
    u.notifications.filter(
      (n) =>
        (n.category === "VERIFICATION_FAILED" || n.category === "MEMORY_CONFLICT" || n.category === "WORKER_CRASH") &&
        n.state !== "RESOLVED",
    ).length;
}

export function applyEventToUniverse(u: MockUniverse, event: MockEvent): void {
  const t0 = event.at;
  const time = new Date(t0).toLocaleTimeString("en-GB", { hour12: false });
  switch (event.type) {
    case "mission.started": {
      const m = u.missions.find((x) => x.id === "M-RES-015");
      if (m) {
        m.status = "ACTIVE";
        m.stage = "Observe";
        m.nextAction = "Probe results expected within 2 minutes";
      }
      u.timeline.unshift(
        tl(`TL-EVT-${u.seq}`, time, "NOW", "MILESTONE", "mission.started", "Resource readiness probe mission started.", [
          { label: "MISSION", value: "M-RES-015" },
        ], timelineRefs({ missionId: "M-RES-015", severity: "INFO" })),
      );
      u.notifications.unshift({
        id: `NT-EVT-${u.seq}`,
        severity: "INFO",
        state: "UNREAD",
        category: "MISSION_STARTED",
        title: "Mission started",
        detail: "M-RES-015 Resource Readiness Probe is now active.",
        createdAt: t0,
        refs: [ref("mission", "M-RES-015", "M-RES-015"), ref("timeline", `TL-EVT-${u.seq}`, `TL-EVT-${u.seq}`)],
      });
      break;
    }
    case "step.started": {
      const steps = u.steps["M-HYB-014"];
      const verify = steps?.find((s) => s.key === "verify");
      if (verify) {
        verify.state = "ACTIVE";
        verify.detail = "Evaluating...";
      }
      const run = u.runs["M-HYB-014"];
      if (run) {
        const p = run.pipeline.find((s) => s.key === "verify");
        if (p) {
          p.state = "ACTIVE";
          p.detail = "Evaluating...";
        }
      }
      u.timeline.unshift(
        tl(`TL-EVT-${u.seq}`, time, "NOW", "MILESTONE", "step.started", "Gate V-2 verification step started for the activation payload.", [
          { label: "MISSION", value: "M-HYB-014" },
          { label: "STEP", value: "VERIFY" },
        ], timelineRefs({ missionId: "M-HYB-014", verdictId: "VF-2041", severity: "INFO" })),
      );
      break;
    }
    case "worker.active": {
      const w = u.workers["WRK-EVIDENCE"];
      if (w) {
        w.status = "ACTIVE";
        w.currentTask = "Ingesting verifier run 1142 artifacts";
      }
      u.timeline.unshift(
        tl(`TL-EVT-${u.seq}`, time, "NOW", "SYSTEM", "worker.active", "Evidence Collector resumed active ingestion.", [
          { label: "WORKER", value: "WRK-EVIDENCE" },
        ], timelineRefs({ agentId: "WRK-EVIDENCE", systemId: "SYS-EVIDENCE", severity: "INFO" })),
      );
      break;
    }
    case "evidence.arrived": {
      u.evidence.unshift(
        ev(
          `EV-EVT-${u.seq}`,
          "PROBE",
          "Resource probe result",
          "worker pool probe",
          "c4ca4238a0b923820dcc509a6f75849b" + "e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1",
          "VERIFIED",
          t0,
          "M-RES-015",
          "Fresh probe measurement attached after mission start.",
          { workerId: "WRK-WATCHDOG", stepId: "ST-M-RES-015-observe", claim: "Pools measured above readiness threshold.", decision: "Pools reported ready.", outcome: "Readiness probe recorded." },
        ),
      );
      u.timeline.unshift(
        tl(`TL-EVT-${u.seq}`, time, "NOW", "HANDOFF", "evidence.arrived", "New probe artifact attached to the resource readiness mission.", [
          { label: "EVIDENCE", value: `EV-EVT-${u.seq}` },
          { label: "MISSION", value: "M-RES-015" },
        ], timelineRefs({ evidenceId: `EV-EVT-${u.seq}`, missionId: "M-RES-015", severity: "INFO" })),
      );
      u.notifications.unshift({
        id: `NT-EVT-${u.seq}`,
        severity: "INFO",
        state: "UNREAD",
        category: "MISSION_STARTED",
        title: "Evidence attached",
        detail: `Probe artifact EV-EVT-${u.seq} attached to M-RES-015.`,
        createdAt: t0,
        refs: [ref("evidence", `EV-EVT-${u.seq}`, `EV-EVT-${u.seq}`), ref("mission", "M-RES-015", "M-RES-015")],
      });
      break;
    }
    case "verification.started": {
      const run = u.runs["M-HYB-014"];
      const verdict = run?.verdicts.find((v) => v.id === "VF-2041");
      if (verdict && verdict.state === "EVALUATING") verdict.state = "EVALUATING";
      u.timeline.unshift(
        tl(`TL-EVT-${u.seq}`, time, "NOW", "MILESTONE", "verification.started", "Verifier dispatch confirmed for the build structure scan.", [
          { label: "VERDICT", value: "VF-2041" },
          { label: "MISSION", value: "M-HYB-014" },
        ], timelineRefs({ verdictId: "VF-2041", missionId: "M-HYB-014", severity: "INFO" })),
      );
      break;
    }
    case "verification.failed": {
      const run = u.runs["M-VER-031"];
      const verdict = run?.verdicts.find((v) => v.id === "VF-2047");
      if (verdict) verdict.state = "FAILED";
      const m = u.missions.find((x) => x.id === "M-VER-031");
      if (m) {
        m.status = "INTERVENTION";
        m.nextAction = "Owner decision required: repair run for finding VF-2047";
        m.stage = "Verify";
      }
      const verifyStep = u.steps["M-VER-031"]?.find((s) => s.key === "verify");
      if (verifyStep) {
        verifyStep.state = "ACTIVE";
        verifyStep.detail = "FAILED - repair pending";
      }
      const pipelineVerify = run?.pipeline.find((s) => s.key === "verify");
      if (pipelineVerify) {
        pipelineVerify.state = "ACTIVE";
        pipelineVerify.detail = "FAILED - repair pending";
      }
      const failedEvidence = ev(
        `EV-EVT-${u.seq}`,
        "VERDICT",
        "Failed verification result",
        "verifier.run.1142",
        "d41d8cd98f00b204e9800998ecf8427e" + "f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6",
        "FAILED",
        t0,
        "M-VER-031",
        "Build structure scan contradicted by the artifact fingerprint. Repair run required.",
        {
          workerId: "WRK-VERIFIER",
          stepId: "ST-M-VER-031-verify",
          verdictId: "VF-2047",
          claim: "Artifact hash contradicts the builder assertion for run 1142.",
          decision: "Repair run required before re-verification.",
          outcome: "Repair run A-91 queued.",
        },
      );
      u.evidence.unshift(failedEvidence);
      const eventId = `TL-EVT-${u.seq}`;
      u.timeline.unshift(
        tl(eventId, time, "NOW", "INCIDENT", "verification.failed", "Findings review rejected the verifier bundle; repair run required.", [
          { label: "MISSION", value: "M-VER-031" },
          { label: "VERDICT", value: "VF-2047" },
          { label: "EVIDENCE", value: failedEvidence.id },
        ], timelineRefs({ missionId: "M-VER-031", verdictId: "VF-2047", evidenceId: failedEvidence.id, agentId: "WRK-VERIFIER", severity: "CRITICAL" })),
      );
      u.notifications.unshift({
        id: `NT-EVT-${u.seq}`,
        severity: "CRITICAL",
        state: "UNREAD",
        category: "VERIFICATION_FAILED",
        title: "Verification failed",
        detail: `Finding VF-2047 failed on mission M-VER-031. Repair run required.`,
        createdAt: t0,
        refs: [
          ref("mission", "M-VER-031", "M-VER-031"),
          ref("verdict", "VF-2047", "VF-2047"),
          ref("evidence", failedEvidence.id, failedEvidence.id),
          ref("timeline", eventId, eventId),
        ],
      });
      const verifier = u.workers["WRK-VERIFIER"];
      if (verifier) verifier.currentTask = "Verdict VF-2047 FAILED - findings attached to evidence";
      u.trust.gateStatus = "FAIL";
      recomputeOpenFindings(u);
      const f2 = u.trust.factors.find((f) => f.id === "F-2");
      if (f2) {
        f2.state = "FAIL";
        f2.detail = "Findings review failed on M-VER-031; repair run required before re-verification.";
      }
      break;
    }
    case "repair.created": {
      // Idempotent: repeated simulation cycles must not duplicate repair runs.
      const hasRepairRun = u.agentRuns.some((r) => r.id === "A-104" && r.missionId === "M-VER-031");
      if (!hasRepairRun) {
        u.agentRuns.unshift({
          id: "A-104",
          missionId: "M-VER-031",
          workerId: "WRK-OPENHANDS",
          stepKey: "build",
          state: "ACTIVE",
          summary: "Repair run rebuilding the contradicted artifact for finding VF-2047.",
          startedAt: t0,
          evidenceIds: [],
          verdictId: "VF-2047",
        });
      }
      const w = u.workers["WRK-OPENHANDS"];
      if (w) w.currentTask = "Repair run A-104 for mission M-VER-031";
      const m = u.missions.find((x) => x.id === "M-VER-031");
      if (m) m.nextAction = "Repair run A-104 building replacement artifact";
      const eventId = `TL-EVT-${u.seq}`;
      u.timeline.unshift(
        tl(eventId, time, "NOW", "HANDOFF", "repair.created", "Repair run A-104 dispatched to rebuild the contradicted artifact.", [
          { label: "MISSION", value: "M-VER-031" },
          { label: "REPAIR", value: "A-104" },
          { label: "WORKER", value: "WRK-OPENHANDS" },
        ], timelineRefs({ missionId: "M-VER-031", agentId: "WRK-OPENHANDS", verdictId: "VF-2047", severity: "WARNING" })),
      );
      u.notifications.unshift({
        id: `NT-EVT-${u.seq}`,
        severity: "WARNING",
        state: "UNREAD",
        category: "VERIFICATION_FAILED",
        title: "Repair run created",
        detail: "Repair run A-104 is rebuilding the artifact for finding VF-2047.",
        createdAt: t0,
        refs: [ref("mission", "M-VER-031", "M-VER-031"), ref("agent", "WRK-OPENHANDS", "WRK-OPENHANDS"), ref("verdict", "VF-2047", "VF-2047"), ref("timeline", eventId, eventId)],
      });
      break;
    }
    case "worker.recovered": {
      const w = u.workers["WRK-OPENHANDS"];
      const repairRun = u.agentRuns.find((r) => r.id === "A-104");
      const recoveryMission = repairRun?.missionId ?? "M-CRS-008";
      if (w) {
        w.status = "ACTIVE";
        w.currentTask = repairRun?.missionId === "M-VER-031" ? "Repair run A-104 artifact ready for re-verification" : "Recovered from checkpoint C-18; normal operations resumed";
        w.recoveries = (w.recoveries ?? 0) + 1;
      }
      const sys = u.systems["SYS-OPENHANDS-WORKER"];
      if (sys) {
        sys.health = "HEALTHY";
        sys.readiness = "READY";
        sys.recovery = "Restart complete";
      }
      const eventId = `TL-EVT-${u.seq}`;
      u.timeline.unshift(
        tl(eventId, time, "NOW", "SYSTEM", "worker.recovered", repairRun?.missionId === "M-VER-031" ? "Repair worker recovered; replacement artifact ready for re-verification." : "Worker recovered from crash; restart from checkpoint completed.", [
          { label: "WORKER", value: "WRK-OPENHANDS" },
          { label: "MISSION", value: recoveryMission },
        ], timelineRefs({ agentId: "WRK-OPENHANDS", systemId: "SYS-OPENHANDS-WORKER", missionId: recoveryMission, severity: "INFO" })),
      );
      u.notifications.unshift({
        id: `NT-EVT-${u.seq}`,
        severity: "INFO",
        state: "UNREAD",
        category: "WORKER_RECOVERED",
        title: "Worker recovered",
        detail: repairRun?.missionId === "M-VER-031" ? "Replacement artifact ready; verifier re-evaluating finding VF-2047." : "Worker restarted from checkpoint; corrective entries appended.",
        createdAt: t0,
        refs: [ref("agent", "WRK-OPENHANDS", "WRK-OPENHANDS"), ref("mission", recoveryMission, recoveryMission), ref("timeline", eventId, eventId)],
      });
      break;
    }
    case "verification.passed": {
      const run = u.runs["M-VER-031"];
      const verdict = run?.verdicts.find((v) => v.id === "VF-2047");
      if (verdict) verdict.state = "PASSED";
      const m = u.missions.find((x) => x.id === "M-VER-031");
      if (m) {
        m.status = "ACTIVE";
        m.completion = 100;
        m.nextAction = "Write back verification result to ledger";
        m.stage = "Write Back";
      }
      for (const failed of u.evidence) {
        if (failed.verdict !== "FAILED") continue;
        failed.verdict = "VERIFIED";
        failed.outcome = "Verification accepted after repair run A-91.";
      }
      u.notifications.forEach((n) => {
        if (n.category === "VERIFICATION_FAILED" && n.state !== "RESOLVED") n.state = "RESOLVED";
      });
      u.trust.gateStatus = "PASS";
      recomputeOpenFindings(u);
      const f2 = u.trust.factors.find((f) => f.id === "F-2");
      if (f2) {
        f2.state = "PASS";
        f2.detail = "Findings review passed after repair run A-91 re-verification.";
      }
      const eventId = `TL-EVT-${u.seq}`;
      u.timeline.unshift(
        tl(eventId, time, "NOW", "MILESTONE", "verification.passed", "Re-verification passed after repair run A-91.", [
          { label: "MISSION", value: "M-VER-031" },
          { label: "VERDICT", value: "VF-2047" },
        ], timelineRefs({ missionId: "M-VER-031", verdictId: "VF-2047", severity: "INFO" })),
      );
      break;
    }
    case "mission.completed": {
      const m = u.missions.find((x) => x.id === "M-CAN-019");
      if (m) {
        m.status = "COMPLETE";
        m.completion = 100;
        m.nextAction = "Awaiting reconciliation mission";
      }
      u.timeline.unshift(
        tl(`TL-EVT-${u.seq}`, time, "NOW", "MILESTONE", "mission.completed", "Candidate promotion preparation reached its end state; promotion deferred.", [
          { label: "MISSION", value: "M-CAN-019" },
          { label: "STATUS", value: "FROZEN" },
        ], timelineRefs({ missionId: "M-CAN-019", severity: "INFO" })),
      );
      u.notifications.unshift({
        id: `NT-EVT-${u.seq}`,
        severity: "INFO",
        state: "UNREAD",
        category: "MISSION_COMPLETED",
        title: "Mission completed",
        detail: "M-CAN-019 reached its prepared end state. Promotion remains a later mission.",
        createdAt: t0,
        refs: [ref("mission", "M-CAN-019", "M-CAN-019"), ref("timeline", `TL-EVT-${u.seq}`, `TL-EVT-${u.seq}`)],
      });
      break;
    }
    case "system.degraded": {
      const sys = u.systems["SYS-ROUTING"];
      if (sys) {
        sys.health = "DEGRADED";
        sys.readiness = "DEGRADED";
        sys.recovery = "Backup tier engaged";
      }
      const w = u.workers["WRK-ROUTER"];
      if (w) w.status = "DEGRADED";
      u.timeline.unshift(
        tl(`TL-EVT-${u.seq}`, time, "NOW", "INCIDENT", "system.degraded", "Provider routing degraded; backup tier engaged.", [
          { label: "SYSTEM", value: "SYS-ROUTING" },
          { label: "SEVERITY", value: "HIGH" },
        ], timelineRefs({ systemId: "SYS-ROUTING", agentId: "WRK-ROUTER", missionId: "M-PRV-011", severity: "CRITICAL" })),
      );
      u.notifications.unshift({
        id: `NT-EVT-${u.seq}`,
        severity: "WARNING",
        state: "UNREAD",
        category: "PROVIDER_DEGRADED",
        title: "Provider routing degraded",
        detail: "Primary latency above threshold; backup tier engaged.",
        createdAt: t0,
        refs: [ref("system", "SYS-ROUTING", "Provider routing"), ref("mission", "M-PRV-011", "M-PRV-011"), ref("timeline", `TL-EVT-${u.seq}`, `TL-EVT-${u.seq}`)],
      });
      break;
    }
    case "provider.outage": {
      const sys = u.systems["SYS-ROUTING"];
      if (sys) {
        sys.health = "CRITICAL";
        sys.readiness = "NOT_READY";
        sys.recovery = "Primary unreachable";
      }
      const w = u.workers["WRK-ROUTER"];
      if (w) {
        w.status = "RATE_LIMITED";
        w.currentTask = "Primary provider unreachable - backup tier saturated";
      }
      u.timeline.unshift(
        tl(`TL-EVT-${u.seq}`, time, "NOW", "INCIDENT", "provider.outage", "Primary provider unreachable; backup tier saturated.", [
          { label: "SYSTEM", value: "SYS-ROUTING" },
          { label: "STATE", value: "CRITICAL" },
        ], timelineRefs({ systemId: "SYS-ROUTING", missionId: "M-PRV-011", severity: "CRITICAL" })),
      );
      u.notifications.unshift({
        id: `NT-EVT-${u.seq}`,
        severity: "CRITICAL",
        state: "UNREAD",
        category: "PROVIDER_DEGRADED",
        title: "Provider outage",
        detail: "Primary provider unreachable; worker traffic limited.",
        createdAt: t0,
        refs: [ref("system", "SYS-ROUTING", "Provider routing"), ref("agent", "WRK-ROUTER", "WRK-ROUTER"), ref("timeline", `TL-EVT-${u.seq}`, `TL-EVT-${u.seq}`)],
      });
      break;
    }
    case "memory.conflict": {
      const m = u.missions.find((x) => x.id === "M-MEM-007");
      if (m) {
        m.status = "BLOCKED";
        m.nextAction = "Owner: resolve memory conflict in snapshot 004";
      }
      const node = u.memoryGraph.nodes.find((n) => n.id === "n-reconcile");
      if (node && !node.unknowns.includes("Overlapping worker-scratch claims in snapshot 004.")) {
        node.unknowns = [...node.unknowns, "Overlapping worker-scratch claims in snapshot 004."];
      }
      u.timeline.unshift(
        tl(`TL-EVT-${u.seq}`, time, "NOW", "INCIDENT", "memory.conflict", "Memory merge detected overlapping claims in snapshot 004.", [
          { label: "MISSION", value: "M-MEM-007" },
          { label: "SNAPSHOT", value: "004" },
        ], timelineRefs({ missionId: "M-MEM-007", memoryId: "n-reconcile", severity: "WARNING" })),
      );
      u.notifications.unshift({
        id: `NT-EVT-${u.seq}`,
        severity: "WARNING",
        state: "UNREAD",
        category: "MEMORY_CONFLICT",
        title: "Memory conflict detected",
        detail: "Snapshot 004 contains overlapping claims requiring owner resolution.",
        createdAt: t0,
        refs: [ref("mission", "M-MEM-007", "M-MEM-007"), ref("memory", "n-reconcile", "Memory Reconcile"), ref("timeline", `TL-EVT-${u.seq}`, `TL-EVT-${u.seq}`)],
      });
      recomputeOpenFindings(u);
      break;
    }
    case "stop.simulated": {
      applyStopToUniverse(u, t0);
      break;
    }
    case "resume": {
      applyResumeToUniverse(u, t0);
      break;
    }
  }
}

/* ------------------------------------------------------------------ */
/* STOP / resume propagation                                           */
/* ------------------------------------------------------------------ */

export function applyStopToUniverse(u: MockUniverse, at = new Date().toISOString()): void {
  if (u.stopActive) return;
  u.stopActive = true;
  const stop = u.systems["SYS-STOP"];
  if (stop) {
    stop.state = "ACTIVE";
    stop.recovery = "SIMULATED STOP - UI CANDIDATE";
    stop.checks = [
      { label: "Determinism", state: "PASS", detail: "Path verified offline-safe." },
      { label: "Simulated state", state: "WARN", detail: "STOP active in the UI candidate only; no runtime side effect." },
    ];
  }
  const time = new Date(at).toLocaleTimeString("en-GB", { hour12: false });
  u.timeline.unshift(
    tl(`TL-STP-${u.seq}`, time, "NOW", "COMMAND", "emergency_stop.requested", "Owner requested STOP in the UI candidate.", [
      { label: "AUTHORITY", value: "OWNER" },
      { label: "MODE", value: "SIMULATED" },
    ], timelineRefs({ systemId: "SYS-STOP", missionId: "M-STP-004", severity: "CRITICAL" })),
  );
  u.timeline.unshift(
    tl(`TL-STP-FR-${u.seq}`, time, "NOW", "SYSTEM", "emergency_stop.frozen", "Operational surfaces frozen in the UI candidate.", [
      { label: "SYSTEM", value: "SYS-STOP" },
      { label: "STATE", value: "ACTIVE" },
    ], timelineRefs({ systemId: "SYS-STOP", severity: "CRITICAL" })),
  );
  u.notifications.unshift({
    id: `NT-STP-${u.seq}`,
    severity: "CRITICAL",
    state: "UNREAD",
    category: "STOP_ACTIVATED",
    title: "STOP activated (simulated)",
    detail: "SIMULATED STOP - UI CANDIDATE. Operations frozen in the UI; no production side effect.",
    createdAt: at,
    refs: [ref("system", "SYS-STOP", "SYS-STOP"), ref("mission", "M-STP-004", "M-STP-004")],
  });
  u.trust.state = "SUSPENDED";
  u.trust.gateStatus = "GATED";
}

export function applyResumeToUniverse(u: MockUniverse, at = new Date().toISOString()): void {
  if (!u.stopActive) return;
  u.stopActive = false;
  const stop = u.systems["SYS-STOP"];
  if (stop) {
    stop.state = "ARMED";
    stop.recovery = "Self-test ok";
    stop.checks = [
      { label: "Determinism", state: "PASS", detail: "Path verified offline-safe." },
      { label: "Dry run", state: "PASS", detail: "12ms, no side effects." },
    ];
  }
  const time = new Date(at).toLocaleTimeString("en-GB", { hour12: false });
  u.timeline.unshift(
    tl(`TL-RSM-${u.seq}`, time, "NOW", "COMMAND", "resume.reorientation", "Surfaces reoriented after simulated STOP.", [
      { label: "SYSTEM", value: "SYS-STOP" },
      { label: "MODE", value: "SIMULATED" },
    ], timelineRefs({ systemId: "SYS-STOP", severity: "INFO" })),
  );
  u.timeline.unshift(
    tl(`TL-RSM-RC-${u.seq}`, time, "NOW", "MILESTONE", "resume.reconciliation", "Reconciliation run queued to return the projection to healthy mock state.", [
      { label: "JOB", value: "R-88" },
      { label: "STATE", value: "QUEUED" },
    ], timelineRefs({ systemId: "SYS-FOUNDRY", missionId: "M-WFL-006", severity: "INFO" })),
  );
  const stopNote = u.notifications.find((n) => n.category === "STOP_ACTIVATED" && n.state !== "RESOLVED");
  if (stopNote) stopNote.state = "RESOLVED";
  u.trust.state = "SIMULATED";
}

/* ------------------------------------------------------------------ */
/* Scenarios                                                           */
/* ------------------------------------------------------------------ */

export const scenarioEvents: Record<string, MockEventType[]> = {
  VERIFICATION_FAILURE: ["verification.failed"],
  PROVIDER_DEGRADED: ["system.degraded"],
  MISSION_BLOCKED: [],
  MEMORY_CONFLICT: ["memory.conflict"],
  STOP_ACTIVE: ["stop.simulated"],
  MISSION_COMPLETE: ["mission.completed"],
  NORMAL_OPERATION: [],
  EMPTY_STATE: [],
  STREAM_DISCONNECTED: [],
};

function applyWorkerCrash(u: MockUniverse): MockEvent[] {
  const w = u.workers["WRK-OPENHANDS"];
  if (w) {
    w.status = "FAILED";
    w.currentTask = "CRASHED - lease replayed, restart pending from checkpoint C-18";
    w.failures = (w.failures ?? 0) + 1;
  }
  const sys = u.systems["SYS-OPENHANDS-WORKER"];
  if (sys) {
    sys.health = "DEGRADED";
    sys.readiness = "DEGRADED";
    sys.recovery = "Crash detected - restart pending";
  }
  const m = u.missions.find((x) => x.id === "M-CRS-008");
  if (m) {
    m.nextAction = "Repair run A-91 restarting worker from checkpoint C-18";
    m.completion = 52;
  }
  const buildStep = u.steps["M-CRS-008"]?.find((s) => s.key === "build");
  if (buildStep) {
    buildStep.state = "ACTIVE";
    buildStep.detail = "INTERRUPTED - crash";
  }
  const run = u.runs["M-CRS-008"];
  const pipelineBuild = run?.pipeline.find((s) => s.key === "build");
  if (pipelineBuild) {
    pipelineBuild.state = "ACTIVE";
    pipelineBuild.detail = "INTERRUPTED - crash";
  }
  u.seq += 1;
  const eventId = `TL-CRS-${u.seq}`;
  u.timeline.unshift(
    tl(eventId, new Date().toLocaleTimeString("en-GB", { hour12: false }), "NOW", "INCIDENT", "worker.crash", "OpenHands worker crashed; lease replayed and restart queued from checkpoint C-18.", [
      { label: "WORKER", value: "WRK-OPENHANDS" },
      { label: "MISSION", value: "M-CRS-008" },
      { label: "STATE", value: "FAILED" },
    ], timelineRefs({ agentId: "WRK-OPENHANDS", systemId: "SYS-OPENHANDS-WORKER", missionId: "M-CRS-008", evidenceId: "EV-1142-002", severity: "CRITICAL" })),
  );
  u.notifications.unshift({
    id: `NT-CRS-${u.seq}`,
    severity: "CRITICAL",
    state: "UNREAD",
    category: "WORKER_CRASH",
    title: "Worker crashed",
    detail: "WRK-OPENHANDS exited with code 137. Repair run A-91 queued.",
    createdAt: new Date().toISOString(),
    refs: [
      ref("agent", "WRK-OPENHANDS", "WRK-OPENHANDS"),
      ref("mission", "M-CRS-008", "M-CRS-008"),
      ref("system", "SYS-OPENHANDS-WORKER", "OpenHands worker"),
      ref("timeline", eventId, eventId),
    ],
  });
  u.trust.health = "DEGRADED";
  recomputeOpenFindings(u);
  return [];
}

function applyRollback(u: MockUniverse): MockEvent[] {
  const m = u.missions.find((x) => x.id === "M-CAN-019");
  if (m) {
    m.status = "HALTED";
    m.nextAction = "Rollback completed - re-plan promotion window";
    m.completion = 24;
  }
  u.seq += 1;
  const eventId = `TL-RB-${u.seq}`;
  u.timeline.unshift(
    tl(eventId, new Date().toLocaleTimeString("en-GB", { hour12: false }), "NOW", "COMMAND", "rollback.completed", "Candidate promotion preparation rolled back; artifacts unsealed.", [
      { label: "MISSION", value: "M-CAN-019" },
      { label: "STATUS", value: "HALTED" },
    ], timelineRefs({ missionId: "M-CAN-019", systemId: "SYS-MEMORY", severity: "WARNING" })),
  );
  const mem = u.systems["SYS-MEMORY"];
  if (mem) mem.recovery = "Rollback applied (fixture)";
  u.notifications.unshift({
    id: `NT-RB-${u.seq}`,
    severity: "INFO",
    state: "UNREAD",
    category: "ROLLBACK_COMPLETED",
    title: "Rollback completed",
    detail: "M-CAN-019 promotion preparation rolled back without touching canonical artifacts.",
    createdAt: new Date().toISOString(),
    refs: [ref("mission", "M-CAN-019", "M-CAN-019"), ref("timeline", eventId, eventId)],
  });
  return [];
}

export function applyScenarioToUniverse(u: MockUniverse, name: Scenario): MockEvent[] {
  if (name === "NORMAL_OPERATION") {
    const fresh = buildUniverse();
    Object.assign(u, fresh);
    return [];
  }
  if (name === "EMPTY_STATE") {
    u.missions = [];
    u.evidence = [];
    u.timeline = [];
    u.workers = {};
    u.systems = {};
    u.notifications = [];
    return [];
  }
  if (name === "STREAM_DISCONNECTED") {
    return [];
  }
  if (name === "WORKER_CRASH") {
    return applyWorkerCrash(u);
  }
  if (name === "RECOVERY") {
    applyWorkerCrash(u);
    const events: MockEvent[] = [];
    for (const type of ["worker.recovered"] as MockEventType[]) {
      const event = makeEvent(u, type, `${type} (scenario RECOVERY)`);
      applyEventToUniverse(u, event);
      events.push(event);
    }
    return events;
  }
  if (name === "ROLLBACK") {
    return applyRollback(u);
  }
  if (name === "MISSION_BLOCKED") {
    const m = u.missions.find((x) => x.id === "M-EVI-003");
    if (m) {
      m.status = "BLOCKED";
      m.nextAction = "Owner: unblock evidence ingestion queue";
    }
    const eventId = `TL-BLK-${u.seq + 1}`;
    u.timeline.unshift(
      tl(eventId, new Date().toLocaleTimeString("en-GB", { hour12: false }), "NOW", "INCIDENT", "mission.blocked", "Evidence ingestion mission blocked on a full queue.", [
        { label: "MISSION", value: "M-EVI-003" },
        { label: "REASON", value: "QUEUE_FULL" },
      ], timelineRefs({ missionId: "M-EVI-003", severity: "WARNING" })),
    );
    u.notifications.unshift({
      id: "NT-BLK-1",
      severity: "WARNING",
      state: "UNREAD",
      category: "MISSION_BLOCKED",
      title: "Mission blocked",
      detail: "M-EVI-003 blocked on a full ingestion queue.",
      createdAt: new Date().toISOString(),
      refs: [ref("mission", "M-EVI-003", "M-EVI-003"), ref("timeline", eventId, eventId)],
    });
    return [];
  }
  const events: MockEvent[] = [];
  for (const type of scenarioEvents[name] ?? []) {
    const event = makeEvent(u, type, `${type} (scenario ${name})`);
    applyEventToUniverse(u, event);
    events.push(event);
  }
  return events;
}

/* ------------------------------------------------------------------ */
/* Lineage (verification / repair presentation only)                   */
/* ------------------------------------------------------------------ */

export function lineageFor(u: MockUniverse, missionId: string): LineageNode[] {
  const run = u.runs[missionId];
  const verdicts = run?.verdicts ?? [];
  const missionEvidence = u.evidence.filter((e) => e.missionRef === missionId);
  const runsForMission = u.agentRuns.filter((r) => r.missionId === missionId);
  const failed = verdicts.find((v) => v.state === "FAILED");
  const evaluating = verdicts.find((v) => v.state === "EVALUATING");
  const passed = verdicts.filter((v) => v.state === "PASSED" || v.state === "WARN_ACCEPT");
  const repair = runsForMission.find((r) => r.id === "A-91");
  const nodes: LineageNode[] = [];

  const builderEvidence = missionEvidence[0];
  nodes.push({
    id: "builder",
    kind: "builder",
    label: "Builder result",
    detail: builderEvidence
      ? `${builderEvidence.id} (${builderEvidence.typeLabel}) produced by ${builderEvidence.workerId ?? "unknown worker"}.`
      : `${missionId} builder output recorded in the fixture ledger.`,
    status: builderEvidence?.verdict === "VERIFIED" ? "PASS" : builderEvidence?.verdict === "FAILED" ? "FAIL" : "ACTIVE",
    refs: builderEvidence ? [ref("evidence", builderEvidence.id, builderEvidence.id)] : [],
  });

  if (failed) {
    nodes.push({
      id: "verifier-fail",
      kind: "verifier",
      label: `Verifier ${failed.id}`,
      detail: failed.detail,
      status: "FAIL",
      refs: [
        ref("mission", missionId, missionId),
        ref("verdict", failed.id, failed.id),
        ...(failed.evidenceId ? [ref("evidence", failed.evidenceId, failed.evidenceId)] : []),
      ],
    });
    const finding = missionEvidence.find((e) => e.verdict === "TAMPERED" || e.verdict === "FAILED" || e.verdict === "WARN");
    nodes.push({
      id: "finding",
      kind: "finding",
      label: "Finding",
      detail: finding ? `${finding.id}: ${finding.claim ?? finding.note}` : "Contradicting evidence classified for corrective work.",
      status: "FAIL",
      refs: finding ? [ref("evidence", finding.id, finding.id)] : [],
    });
    if (repair) {
      nodes.push({
        id: "repair",
        kind: "repair",
        label: `Repair run ${repair.id}`,
        detail: repair.summary,
        status: repair.state === "COMPLETE" ? "PASS" : "ACTIVE",
        refs: [
          ref("agent", repair.workerId, repair.workerId),
          ...repair.evidenceIds.map((eid) => ref("evidence", eid, eid)),
        ],
      });
    } else {
      nodes.push({
        id: "repair",
        kind: "repair",
        label: "Repair run",
        detail: "Repair run queued pending owner decision.",
        status: "PENDING",
        refs: [],
      });
    }
    const postPass = verdicts.find((v) => v.id !== failed.id && (v.state === "PASSED" || v.state === "WARN_ACCEPT"));
    nodes.push({
      id: "reverify",
      kind: "verifier",
      label: postPass ? `Verifier ${postPass.id}` : "New verifier",
      detail: postPass ? postPass.detail : "Re-verification pending.",
      status: postPass ? "PASS" : "PENDING",
      refs: postPass ? [ref("verdict", postPass.id, postPass.id)] : [],
    });
    nodes.push({
      id: "pass",
      kind: "pass",
      label: "PASS",
      detail: postPass ? "Repair lineage closed with independent verification." : "Pass requires independent verification of the repaired artifact.",
      status: postPass ? "PASS" : "PENDING",
      refs: [],
    });
    return nodes;
  }

  const active = evaluating ?? run?.pipeline.find((p) => p.state === "ACTIVE");
  nodes.push({
    id: "verifier-active",
    kind: "verifier",
    label: evaluating ? `Verifier ${evaluating.id}` : "Verifier",
    detail: evaluating ? evaluating.detail : "Verification pending.",
    status: "ACTIVE",
    refs: evaluating
      ? [ref("mission", missionId, missionId), ref("verdict", evaluating.id, evaluating.id), ...(evaluating.evidenceId ? [ref("evidence", evaluating.evidenceId, evaluating.evidenceId)] : [])]
      : [],
  });
  nodes.push({
    id: "pass",
    kind: "pass",
    label: "PASS",
    detail: passed.length > 0 ? `${passed.length} accepted verdict${passed.length > 1 ? "s" : ""} recorded; remaining gates pending.` : "Pass requires the current gate to close.",
    status: passed.length > 0 && !active ? "PASS" : "PENDING",
    refs: [],
  });
  return nodes;
}

/* ------------------------------------------------------------------ */
/* Search                                                              */
/* ------------------------------------------------------------------ */

function pushResult(items: SearchResultItem[], item: SearchResultItem) {
  if (!items.some((x) => x.id === item.id && x.kind === item.kind)) items.push(item);
}

export function searchUniverse(u: MockUniverse, rawQuery: string): SearchResults {
  const q = rawQuery.trim().toLowerCase();
  const results: SearchResults = { missions: [], agents: [], evidence: [], systems: [], memory: [], timeline: [] };
  if (!q) return results;
  const match = (...fields: string[]) => fields.some((f) => f.toLowerCase().includes(q));

  for (const m of u.missions) {
    if (match(m.id, m.code, m.title, m.objective, m.owner, m.stage, m.status, m.priority)) {
      pushResult(results.missions, { id: m.id, label: m.title, sublabel: `${m.id} Â· ${m.status}`, kind: "mission", to: `/missions/${m.id}` });
    }
    for (const v of u.runs[m.id]?.verdicts ?? []) {
      if (match(v.id, v.title, v.state)) {
        pushResult(results.missions, { id: v.id, label: `${v.id} Â· ${v.title}`, sublabel: `Verdict on ${m.id}`, kind: "mission", to: `/missions/${m.id}` });
      }
    }
  }
  for (const w of Object.values(u.workers)) {
    if (match(w.id, w.name, w.role, w.provider, w.status, w.currentTask)) {
      pushResult(results.agents, { id: w.id, label: w.name, sublabel: `${w.id} Â· ${w.role} Â· ${w.status}`, kind: "agent", to: `/agents/${w.id}` });
    }
  }
  for (const e of u.evidence) {
    if (match(e.id, e.kind, e.typeLabel, e.source, e.verdict, e.missionRef)) {
      pushResult(results.evidence, { id: e.id, label: e.id, sublabel: `${e.typeLabel} Â· ${e.verdict}`, kind: "evidence", to: `/evidence/${e.id}` });
    }
  }
  for (const s of Object.values(u.systems)) {
    if (match(s.id, s.name, s.description, s.health, s.readiness, s.state)) {
      pushResult(results.systems, { id: s.id, label: s.name, sublabel: `${s.id} Â· ${s.health} / ${s.readiness}`, kind: "system", to: `/systems/${s.id}` });
    }
  }
  for (const n of u.memoryGraph.nodes) {
    if (match(n.id, n.label, n.category, n.summary)) {
      pushResult(results.memory, { id: n.id, label: n.label, sublabel: `${n.category} Â· knowledge node`, kind: "memory", to: `/memory/${n.id}` });
    }
  }
  for (const e of u.timeline) {
    if (match(e.id, e.title, e.detail, e.category)) {
      pushResult(results.timeline, { id: e.id, label: e.title, sublabel: `${e.id} Â· ${e.category}`, kind: "timeline", to: `/timeline` });
    }
  }
  return results;
}

/* ------------------------------------------------------------------ */
/* Related entities                                                    */
/* ------------------------------------------------------------------ */

export function relatedFor(u: MockUniverse, kind: string, id: string): RelatedEntities {
  const related: RelatedEntities = { missions: [], agents: [], evidence: [], systems: [], memory: [], timeline: [], notifications: [] };
  const push = (list: EntityRef[], item: EntityRef) => {
    if (!list.some((x) => x.id === item.id && x.kind === item.kind)) list.push(item);
  };

  for (const m of u.missions) {
    if (kind === "mission" && m.id === id) continue;
    if (kind === "agent" && (u.workers[id]?.missionIds ?? []).includes(m.id)) push(related.missions, ref("mission", m.id, m.id));
    if (kind === "evidence" && u.evidence.find((e) => e.id === id)?.missionRef === m.id) push(related.missions, ref("mission", m.id, m.id));
    if (kind === "system" && (u.systems[id]?.missionIds ?? []).includes(m.id)) push(related.missions, ref("mission", m.id, m.id));
    if (kind === "memory") {
      const node = u.memoryGraph.nodes.find((n) => n.id === id);
      if (node?.ref?.kind === "mission" && node.ref.id === m.id) push(related.missions, ref("mission", m.id, m.id));
    }
    if (kind === "timeline" && u.timeline.find((e) => e.id === id)?.missionId === m.id) push(related.missions, ref("mission", m.id, m.id));
  }
  for (const w of Object.values(u.workers)) {
    if (kind === "agent" && w.id === id) continue;
    if (kind === "mission" && (w.missionIds ?? []).includes(id)) push(related.agents, ref("agent", w.id, w.id));
    if (kind === "system" && w.systemId === id) push(related.agents, ref("agent", w.id, w.id));
    if (kind === "evidence" && (w.evidenceIds ?? []).includes(id)) push(related.agents, ref("agent", w.id, w.id));
    if (kind === "memory" && u.memoryGraph.nodes.find((n) => n.id === id)?.ref?.id === w.id) push(related.agents, ref("agent", w.id, w.id));
  }
  for (const e of u.evidence) {
    if (kind === "evidence" && e.id === id) continue;
    if (kind === "mission" && e.missionRef === id) push(related.evidence, ref("evidence", e.id, e.id));
    if (kind === "agent" && e.workerId === id) push(related.evidence, ref("evidence", e.id, e.id));
    if (kind === "system" && e.source.toLowerCase().includes(u.systems[id]?.name.toLowerCase() ?? "\u0000")) push(related.evidence, ref("evidence", e.id, e.id));
  }
  for (const s of Object.values(u.systems)) {
    if (kind === "system" && s.id === id) continue;
    if (kind === "mission" && (s.missionIds ?? []).includes(id)) push(related.systems, ref("system", s.id, s.id));
    if (kind === "agent" && Object.values(u.workers).find((w) => w.id === id)?.systemId === s.id) push(related.systems, ref("system", s.id, s.id));
    if (kind === "evidence") {
      const item = u.evidence.find((e) => e.id === id);
      if (item && (s.incidentEventIds ?? []).some((tid) => u.timeline.find((e) => e.id === tid)?.evidenceId === id)) push(related.systems, ref("system", s.id, s.id));
    }
  }
  for (const n of u.memoryGraph.nodes) {
    if (kind === "memory" && n.id === id) continue;
    if (kind === "mission" && n.ref?.kind === "mission" && n.ref.id === id) push(related.memory, ref("memory", n.id, n.label));
    if (kind === "agent" && n.ref?.kind === "agent" && n.ref.id === id) push(related.memory, ref("memory", n.id, n.label));
    if (kind === "evidence" && n.ref?.kind === "evidence" && n.ref.id === id) push(related.memory, ref("memory", n.id, n.label));
    if (kind === "system" && n.ref?.kind === "system" && n.ref.id === id) push(related.memory, ref("memory", n.id, n.label));
  }
  for (const e of u.timeline) {
    if (kind === "timeline" && e.id === id) continue;
    if (kind === "mission" && e.missionId === id) push(related.timeline, ref("timeline", e.id, e.id));
    if (kind === "agent" && e.agentId === id) push(related.timeline, ref("timeline", e.id, e.id));
    if (kind === "system" && e.systemId === id) push(related.timeline, ref("timeline", e.id, e.id));
    if (kind === "evidence" && e.evidenceId === id) push(related.timeline, ref("timeline", e.id, e.id));
  }
  for (const n of u.notifications) {
    if (n.refs.some((r) => r.kind === kind && r.id === id)) push(related.notifications, ref("timeline", n.id, n.id));
  }
  return related;
}

/* ------------------------------------------------------------------ */
/* Memory node helper                                                  */
/* ------------------------------------------------------------------ */

export function memoryNodeForId(u: MockUniverse, id: string): MemoryNode | null {
  return u.memoryGraph.nodes.find((n) => n.id === id) ?? null;
}
