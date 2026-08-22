import type {
  EvidenceArtifact,
  EvidenceVerdict,
  Health,
  MemoryGraph,
  MemoryNode,
  Mission,
  MissionRun,
  MissionStatus,
  MissionStep,
  Notification,
  Priority,
  SystemComponent,
  SystemOverview,
  TimelineEvent,
  TrustSummary,
  Worker,
  WorkerStatus,
} from "./contracts";
import type { FoundrySnapshot, FoundryMission, FoundryVerification } from "./snapshotTypes";
import {
  isUnknownToken,
  MEASURED_COST_LABEL,
  NOT_REPORTED_NUMERIC,
} from "./truthFormat";

const ACTIVE_MISSION_STATES = new Set(["ACTION_SELECTION", "EXECUTING", "REPAIRING", "READY", "ORIENTING"]);
const STANDBY_MISSION_STATES = new Set(["PLANNED", "WAITING", "COMPLETE", "COMPLETED"]);

export function mapMissionStatus(state: string | undefined): MissionStatus {
  const normalized = (state ?? "UNKNOWN").toUpperCase();
  if (normalized === "BLOCKED") return "BLOCKED";
  if (normalized === "COMPLETE" || normalized === "COMPLETED") return "COMPLETE";
  if (normalized === "HALTED" || normalized === "FAILED") return "HALTED";
  if (ACTIVE_MISSION_STATES.has(normalized)) return normalized === "ORIENTING" ? "IN_PROGRESS" : "ACTIVE";
  if (STANDBY_MISSION_STATES.has(normalized)) return normalized.startsWith("COMP") ? "COMPLETE" : "STANDBY";
  return "STANDBY";
}

export function mapWorkerStatus(status: string | undefined): WorkerStatus {
  const normalized = (status ?? "UNKNOWN").toUpperCase();
  if (normalized === "RUNNING") return "ACTIVE";
  if (normalized === "FAILED") return "FAILED";
  if (normalized === "COMPLETED") return "STANDBY";
  if (normalized === "RECOVERING") return "RECOVERING";
  return "STANDBY";
}

export function mapHealth(value: string | undefined): Health {
  const normalized = (value ?? "UNKNOWN").toUpperCase();
  if (normalized === "HEALTHY" || normalized === "RUNNING" || normalized === "READY" || normalized === "PASS") return "HEALTHY";
  if (normalized === "DEGRADED" || normalized === "WARN") return "DEGRADED";
  if (normalized === "CRITICAL" || normalized === "FAILED" || normalized === "STOPPED") return "CRITICAL";
  return "UNKNOWN";
}

function mapEvidenceVerdict(status: string | undefined): EvidenceVerdict {
  const normalized = (status ?? "EVALUATING").toUpperCase();
  if (normalized === "PASS" || normalized === "VERIFIED") return "VERIFIED";
  if (normalized === "FAIL" || normalized === "FAILED") return "FAILED";
  if (normalized === "WARN") return "WARN";
  return "EVALUATING";
}

function missionPriority(_mission: FoundryMission): Priority {
  return "NORMAL";
}

function missionCompletion(state: string | undefined): number {
  const normalized = (state ?? "").toUpperCase();
  if (normalized === "COMPLETE" || normalized === "COMPLETED") return 100;
  if (normalized === "BLOCKED" || normalized === "FAILED" || normalized === "HALTED") return 0;
  return NOT_REPORTED_NUMERIC;
}

export function mapMissions(snapshot: FoundrySnapshot): Mission[] {
  const workerCounts = new Map<string, number>();
  for (const worker of snapshot.workers) {
    if (worker.mission_id && worker.status === "RUNNING") {
      workerCounts.set(worker.mission_id, (workerCounts.get(worker.mission_id) ?? 0) + 1);
    }
  }
  return snapshot.missions.map((m) => ({
    id: m.mission_id,
    code: m.mission_id,
    title: m.objective ?? m.mission_id,
    description: m.objective ?? "",
    status: mapMissionStatus(m.state),
    priority: missionPriority(m),
    agentsActive: workerCounts.get(m.mission_id) ?? 0,
    completion: missionCompletion(m.state),
    nextAction: m.next_action ?? "—",
    stage: m.state ?? "UNKNOWN",
    startedAt: m.updated_at ?? snapshot.generated_at,
    objective: m.objective ?? "",
    owner: "canonical-ledger",
  }));
}

export function mapWorkers(snapshot: FoundrySnapshot): Worker[] {
  return snapshot.workers.map((w) => ({
    id: w.run_id,
    name: `${w.role ?? "worker"}-${w.run_id.slice(-4)}`,
    role: w.role ?? "worker",
    provider: w.provider ?? "unknown",
    status: mapWorkerStatus(w.status),
    currentTask: w.mission_id ? `mission ${w.mission_id}` : "idle",
    uptime: "—",
    latencyMs: null,
    log: w.failure_class ? [`failure: ${w.failure_class}`] : [],
    lastHeartbeat: w.heartbeat_at ?? w.updated_at,
    missionIds: w.mission_id ? [w.mission_id] : [],
    failures: w.status === "FAILED" ? 1 : 0,
  }));
}

export function mapEvidence(snapshot: FoundrySnapshot, verification: FoundryVerification[]): EvidenceArtifact[] {
  const verdictByRun = new Map<string, FoundryVerification>();
  for (const v of verification) {
    if (v.run_id) verdictByRun.set(v.run_id, v);
  }
  return snapshot.evidence.map((e) => {
    const hash = e.artifact_hashes ? Object.values(e.artifact_hashes)[0] ?? "—" : "—";
    return {
      id: e.bundle_id,
      kind: "bundle",
      typeLabel: e.status ?? "EVIDENCE",
      source: "ledger",
      sourceUri: `/api/record/mission/${encodeURIComponent(e.mission_id ?? "")}`,
      hash,
      verdict: mapEvidenceVerdict(e.status),
      capturedAt: e.updated_at ?? snapshot.generated_at,
      missionRef: e.mission_id ?? "—",
      note: e.objective ?? "",
      stepId: e.step_id,
    };
  });
}

export function mapTimeline(snapshot: FoundrySnapshot): TimelineEvent[] {
  return snapshot.timeline.map((e) => ({
    id: e.event_id,
    at: e.recorded_at ?? e.occurred_at ?? snapshot.generated_at,
    group: e.event_type.split(".")[0] ?? "system",
    category: e.event_type.includes("owner") ? "COMMAND" : e.event_type.includes("mission") ? "MILESTONE" : "SYSTEM",
    title: e.event_type,
    detail: e.summary ?? e.decision ?? e.event_type,
    meta: [{ label: "sequence", value: String(e.sequence) }],
    actions: [{ label: "View", kind: "view" }],
    severity: e.event_type.includes("fail") || e.event_type.includes("stop") ? "CRITICAL" : "INFO",
    missionId: e.evidence_refs?.find((r) => r.kind === "mission")?.id,
  }));
}

export function mapSystems(snapshot: FoundrySnapshot): SystemComponent[] {
  return snapshot.systems.map((s) => ({
    id: s.system_id,
    name: s.name ?? s.system_id,
    description: s.description ?? "",
    state: mapHealth(s.readiness_state) === "HEALTHY" ? "RUNNING" : "STANDBY",
    health: mapHealth(s.readiness_state),
    readiness: mapHealth(s.readiness_state) === "HEALTHY" ? "READY" : "DEGRADED",
    dependencies: [],
    lastHeartbeat: s.last_heartbeat_at ?? s.updated_at ?? snapshot.generated_at,
    recovery: "canonical projection",
    version: "live",
    checks: [{ label: "readiness", state: mapHealth(s.readiness_state) === "HEALTHY" ? "PASS" : "WARN", detail: s.readiness_state ?? "UNKNOWN" }],
  }));
}

export function mapMemoryGraph(snapshot: FoundrySnapshot): MemoryGraph {
  const nodes: MemoryNode[] = snapshot.memory.map((m, index) => ({
    id: m.memory_id,
    label: m.memory_id,
    category: "OBSERVATION",
    x: 120 + (index % 5) * 90,
    y: 100 + Math.floor(index / 5) * 80,
    r: 18,
    summary: `${m.mem_type ?? "memory"} · ${m.state ?? "UNKNOWN"}`,
    confidence: 0.7,
    freshness: m.updated_at ?? snapshot.generated_at,
    unknowns: [],
  }));
  return { nodes, edges: [] };
}

const mapResourceUsage = (snapshot: FoundrySnapshot): SystemOverview["resourceUsage"] => {
  const resources = (snapshot as FoundrySnapshot & { resources?: Record<string, unknown>[] }).resources ?? []
  const measured: SystemOverview["resourceUsage"] = []

  for (const resource of resources) {
    const label = typeof resource.name === "string"
      ? resource.name
      : typeof resource.resource_id === "string"
        ? resource.resource_id
        : null
    if (!label) continue

    const usage = resource.utilization_pct ?? resource.usage_pct ?? resource.value
    if (typeof usage === "number" && Number.isFinite(usage)) {
      measured.push({ label, value: usage, unit: "%" })
      continue
    }

    measured.push({ label, value: NOT_REPORTED_NUMERIC, unit: "%" })
  }

  return measured
}

const mapMeasuredCostResource = (snapshot: FoundrySnapshot): SystemOverview["resourceUsage"][number] => {
  const costs = snapshot.costs
  const total = costs?.measured_actual_total_usd
  if (typeof total === "number" && Number.isFinite(total)) {
    return { label: MEASURED_COST_LABEL, value: total, unit: "USD" }
  }
  if (isUnknownToken(total)) {
    return { label: MEASURED_COST_LABEL, value: NOT_REPORTED_NUMERIC, unit: "USD" }
  }
  return { label: MEASURED_COST_LABEL, value: NOT_REPORTED_NUMERIC, unit: "USD" }
}

export function mapSystemOverview(snapshot: FoundrySnapshot): SystemOverview {
  const current = snapshot.release_identity?.current;
  const sha = typeof current === "string" ? current.slice(0, 12) : "unknown";
  const resourceUsage = [...mapResourceUsage(snapshot), mapMeasuredCostResource(snapshot)];
  return {
    version: sha,
    build: String(snapshot.source_of_truth.ledger_head_sequence),
    releaseState: snapshot.health.supervisor_status ?? "UNKNOWN",
    uptime: snapshot.health.heartbeat_at ?? snapshot.generated_at,
    resourceUsage,
  };
}

const mapTrustState = (trust: string, stop: boolean): TrustSummary["state"] => {
  if (stop) return "SUSPENDED"
  const normalized = trust.toUpperCase()
  if (normalized === "PROVEN") return "PROVEN"
  if (normalized === "SIMULATED") return "SIMULATED"
  if (normalized === "DEGRADED" || normalized === "LAB" || normalized === "UNKNOWN" || normalized === "NOT_AVAILABLE" || normalized === "NOT_REPORTED") {
    return "DEGRADED"
  }
  return "DEGRADED"
}

const mapEvidenceCoverage = (snapshot: FoundrySnapshot): number => {
  if (snapshot.verification.length === 0) return NOT_REPORTED_NUMERIC
  const verified = snapshot.verification.filter((v) => {
    const status = (v.status ?? "").toUpperCase()
    return status === "PASS" || status === "VERIFIED"
  }).length
  return Math.round((verified / snapshot.verification.length) * 100)
}

export function mapTrustSummary(snapshot: FoundrySnapshot): TrustSummary {
  const trust = (snapshot.health.trust_state ?? "UNKNOWN").toUpperCase();
  const stop = snapshot.stop.active;
  const staleLeases = snapshot.health.stale_leases ?? [];
  const constraints = snapshot.controls.constraints ?? [];
  const coverage = mapEvidenceCoverage(snapshot);
  return {
    state: mapTrustState(trust, stop),
    score: null,
    gateStatus: stop ? "GATED" : "OPEN",
    evidenceCoverage: coverage,
    unresolvedFindings: snapshot.verification.filter((v) => v.status === "FAIL").length,
    health: mapHealth(snapshot.health.supervisor_status),
    lastVerifiedAt: snapshot.generated_at,
    factors: [
      { id: "supervisor", label: "Supervisor", state: snapshot.health.supervisor_status === "RUNNING" ? "PASS" : "WARN", detail: snapshot.health.supervisor_status ?? "UNKNOWN" },
      { id: "stop", label: "STOP sentinel", state: stop ? "FAIL" : "PASS", detail: stop ? "ACTIVE" : "CLEAR" },
      { id: "ledger", label: "Ledger head", state: "PASS", detail: String(snapshot.source_of_truth.ledger_head_sequence) },
      { id: "coverage", label: "Evidence coverage", state: coverage === NOT_REPORTED_NUMERIC ? "PENDING" : "PASS", detail: coverage === NOT_REPORTED_NUMERIC ? "No verification records in canonical projection." : `${coverage}% of verification records passed.` },
      { id: "constraints", label: "Owner constraints", state: constraints.length > 0 ? "WARN" : "PASS", detail: `${constraints.length} active constraint${constraints.length === 1 ? "" : "s"} in durable controls.` },
      { id: "stale-leases", label: "Stale leases", state: staleLeases.length > 0 ? "WARN" : "PASS", detail: `${staleLeases.length} stale lease${staleLeases.length === 1 ? "" : "s"} reported by supervisor health.` },
    ],
  };
}

export function mapNotifications(snapshot: FoundrySnapshot): Notification[] {
  const items: Notification[] = [];
  if (snapshot.stop.active) {
    items.push({
      id: "notif-stop",
      severity: "CRITICAL",
      state: "UNREAD",
      category: "STOP_ACTIVATED",
      title: "STOP active",
      detail: "Emergency stop sentinel or durable STOP directive is active.",
      createdAt: snapshot.generated_at,
      refs: [],
    });
  }
  for (const m of snapshot.missions.filter((x) => x.state === "BLOCKED")) {
    items.push({
      id: `notif-blocked-${m.mission_id}`,
      severity: "WARNING",
      state: "UNREAD",
      category: "MISSION_BLOCKED",
      title: `Mission blocked: ${m.mission_id}`,
      detail: m.next_action ?? "Blocked in durable projection",
      createdAt: m.updated_at ?? snapshot.generated_at,
      refs: [{ kind: "mission", id: m.mission_id, label: m.mission_id }],
    });
  }
  for (const w of snapshot.workers.filter((x) => x.status === "FAILED")) {
    items.push({
      id: `notif-worker-${w.run_id}`,
      severity: "CRITICAL",
      state: "UNREAD",
      category: "WORKER_CRASH",
      title: `Worker failed: ${w.run_id}`,
      detail: w.failure_class ?? "agent_run.failed",
      createdAt: w.updated_at ?? snapshot.generated_at,
      refs: [{ kind: "agent", id: w.run_id, label: w.run_id }],
    });
  }
  return items;
}

export function mapMissionRun(snapshot: FoundrySnapshot, missionId: string): MissionRun {
  const mission = snapshot.missions.find((m) => m.mission_id === missionId);
  const steps = snapshot.mission_steps.filter((s) => s.mission_id === missionId);
  const evidenceCount = snapshot.evidence.filter((e) => e.mission_id === missionId).length;
  const activeWorkers = snapshot.workers.filter((w) => w.mission_id === missionId && w.status === "RUNNING").length;
  return {
    missionId,
    pipeline: steps.length
      ? steps.map((s, i) => ({
          key: String(s.step_id ?? i),
          label: String(s.capability ?? s.step_id ?? `step-${i + 1}`),
          state: i === 0 ? "ACTIVE" : "PENDING",
          detail: String(s.capability ?? ""),
        }))
      : [{ key: "state", label: mission?.state ?? "UNKNOWN", state: "ACTIVE", detail: mission?.next_action ?? "" }],
    telemetry: snapshot.timeline
      .filter((e) => e.evidence_refs?.some((r) => r.id === missionId))
      .slice(-8)
      .map((e) => ({
        at: e.recorded_at ?? snapshot.generated_at,
        source: e.event_type,
        level: e.event_type.includes("fail") ? "ERROR" : "INFO",
        text: e.summary ?? e.event_type,
      })),
    streams: [{ id: "ledger", name: "Event ledger", icon: "ledger", status: "connected", state: "LIVE" }],
    verdicts: snapshot.verification
      .filter((v) => v.mission_id === missionId)
      .map((v) => ({
        id: v.verdict_id,
        title: v.status ?? "verification",
        state: v.status === "PASS" ? "PASSED" : v.status === "FAIL" ? "FAILED" : "EVALUATING",
        detail: String(v.findings ?? ""),
        workerId: v.run_id,
      })),
    stats: {
      elapsed: "—",
      computeCost: "—",
      agentsActive: activeWorkers,
      evidenceArtifacts: evidenceCount,
    },
  };
}

export function mapMissionSteps(snapshot: FoundrySnapshot, missionId: string): MissionStep[] {
  return snapshot.mission_steps
    .filter((s) => s.mission_id === missionId)
    .map((s, i) => ({
      id: String(s.contract_id ?? `${missionId}-step-${i}`),
      missionId,
      key: String(s.step_id ?? i),
      label: String(s.capability ?? s.step_id ?? `step-${i + 1}`),
      state: i === 0 ? "ACTIVE" : "PENDING",
      detail: String(s.capability ?? ""),
      evidenceIds: snapshot.evidence.filter((e) => e.mission_id === missionId && e.step_id === s.step_id).map((e) => e.bundle_id),
    }));
}
