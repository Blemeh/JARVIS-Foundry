import type {
  EvidenceArtifact,
  EvidenceVerdict,
  AutonomyLifecycleRecord,
  EntityRef,
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
  OwnerControlSurface,
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
  if (normalized === "CANCELLED" || normalized === "CANCELED") return "CANCELLED";
  if (normalized === "HALTED" || normalized === "FAILED") return "HALTED";
  if (ACTIVE_MISSION_STATES.has(normalized)) return normalized === "ORIENTING" ? "IN_PROGRESS" : "ACTIVE";
  if (STANDBY_MISSION_STATES.has(normalized)) return normalized.startsWith("COMP") ? "COMPLETE" : "STANDBY";
  return "UNKNOWN";
}

export function mapWorkerStatus(status: string | undefined): WorkerStatus {
  const normalized = (status ?? "UNKNOWN").toUpperCase();
  if (normalized === "RUNNING") return "ACTIVE";
  if (normalized === "FAILED") return "FAILED";
  if (normalized === "COMPLETED") return "STANDBY";
  if (normalized === "RECOVERING") return "RECOVERING";
  if (normalized === "CANCELLED" || normalized === "CANCELED") return "FAILED";
  return "UNKNOWN";
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
    nextAction: m.next_action ?? "UNKNOWN",
    stage: m.state ?? "UNKNOWN",
    startedAt: m.updated_at ?? "UNKNOWN",
    objective: m.objective ?? "UNKNOWN",
    owner: m.created_from ?? "UNKNOWN",
  }));
}

export function mapWorkers(snapshot: FoundrySnapshot): Worker[] {
  const evidenceByRun = new Map<string, string[]>();
  for (const evidence of snapshot.evidence) {
    const claim = evidence.builder_claim && typeof evidence.builder_claim === "object"
      ? evidence.builder_claim as Record<string, unknown>
      : null;
    const runId = typeof claim?.run_id === "string" ? claim.run_id : null;
    if (runId) evidenceByRun.set(runId, [...(evidenceByRun.get(runId) ?? []), evidence.bundle_id]);
  }
  return snapshot.workers.map((w) => ({
    id: w.run_id,
    name: `${w.role ?? "worker"}-${w.run_id.slice(-4)}`,
    role: w.role ?? "UNKNOWN",
    provider: w.provider ?? "UNKNOWN",
    status: mapWorkerStatus(w.status),
    currentTask: w.mission_id ? `mission ${w.mission_id}` : "UNKNOWN",
    uptime: "UNKNOWN",
    latencyMs: null,
    log: w.failure_class ? [`failure: ${w.failure_class}`] : [],
    lastHeartbeat: w.heartbeat_at ?? w.updated_at,
    missionIds: w.mission_id ? [w.mission_id] : [],
    failures: w.status === "FAILED" ? 1 : 0,
    evidenceIds: evidenceByRun.get(w.run_id) ?? [],
  }));
}

export function mapEvidence(snapshot: FoundrySnapshot, verification: FoundryVerification[]): EvidenceArtifact[] {
  const verdictByRun = new Map<string, FoundryVerification>();
  for (const v of verification) {
    if (v.run_id) verdictByRun.set(v.run_id, v);
  }
  return snapshot.evidence.map((e) => {
    const hash = e.artifact_hashes ? Object.values(e.artifact_hashes)[0] ?? "UNKNOWN" : "UNKNOWN";
    const claim = e.builder_claim && typeof e.builder_claim === "object"
      ? e.builder_claim as Record<string, unknown>
      : {};
    return {
      id: e.bundle_id,
      kind: "bundle",
      typeLabel: e.status ?? "EVIDENCE",
      source: "ledger",
      sourceUri: `/api/record/mission/${encodeURIComponent(e.mission_id ?? "")}`,
      hash,
      verdict: mapEvidenceVerdict(e.status),
      capturedAt: e.updated_at ?? snapshot.generated_at,
      missionRef: e.mission_id ?? "UNKNOWN",
      note: e.objective ?? "UNKNOWN",
      stepId: e.step_id,
      workerId: typeof claim.run_id === "string" ? claim.run_id : undefined,
      verdictId: e.verifier_verdict_id,
      claim: typeof claim.status === "string" ? claim.status : undefined,
      relatedEventId: e.last_event_id,
    };
  });
}

export function mapTimeline(snapshot: FoundrySnapshot): TimelineEvent[] {
  return snapshot.timeline.map((e) => ({
    id: e.event_id,
    at: e.recorded_at ?? e.occurred_at ?? "UNKNOWN",
    group: e.event_type.split(".")[0] ?? "system",
    category: /fail|crash|blocked|degraded|frozen|mismatch/i.test(e.event_type)
      ? "INCIDENT"
      : e.event_type.includes("owner") || e.event_type.includes("directive")
        ? "COMMAND"
        : e.event_type.includes("mission") || e.event_type.includes("verification") || e.event_type.includes("evidence")
          ? "MILESTONE"
          : "SYSTEM",
    title: e.event_type,
    detail: e.summary ?? e.decision ?? "UNKNOWN",
    meta: [{ label: "sequence", value: String(e.sequence) }],
    actions: [{ label: "View", kind: "view" }],
    severity: /fail|crash|stop|blocked|frozen/i.test(e.event_type)
      ? "CRITICAL"
      : /degraded|unknown|not_tested/i.test(e.event_type) ? "WARNING" : "INFO",
    missionId: e.evidence_refs?.find((r) => r.kind === "mission")?.id,
    evidenceId: e.evidence_refs?.find((r) => r.kind === "evidence")?.id,
    verdictId: e.evidence_refs?.find((r) => r.kind === "verification")?.id,
    agentId: e.evidence_refs?.find((r) => r.kind === "agent_run")?.id,
  }));
}

export function mapSystems(snapshot: FoundrySnapshot): SystemComponent[] {
  return snapshot.systems.map((s) => {
    const rawReadiness = String(s.readiness_state ?? s.readiness ?? "UNKNOWN").toUpperCase();
    const rawHealth = String(s.health ?? "").toUpperCase();
    const health: Health = rawHealth === "HEALTHY" || rawHealth === "DEGRADED" || rawHealth === "CRITICAL"
      ? rawHealth
      : rawReadiness === "READY" ? "HEALTHY" : rawReadiness === "UNREADY" ? "DEGRADED" : "UNKNOWN";
    const readiness = rawReadiness === "READY" ? "READY" : rawReadiness === "UNREADY" ? "NOT_READY" : "UNKNOWN";
    const capabilities = s.capabilities && typeof s.capabilities === "object" ? s.capabilities as Record<string, unknown> : {};
    const dependencies = Array.isArray(capabilities.dependencies) ? capabilities.dependencies.filter((value): value is string => typeof value === "string") : [];
    return {
    id: s.system_id,
    name: s.name ?? s.system_id,
    description: s.description ?? "UNKNOWN",
    state: s.state === "STOPPED" ? "STOPPED" : s.state === "ACTIVE" ? "ACTIVE" : rawReadiness === "READY" ? "RUNNING" : "UNKNOWN",
    health,
    readiness,
    dependencies,
    lastHeartbeat: s.last_heartbeat_at ?? s.last_probe_at ?? "UNKNOWN",
    recovery: s.recovery_state ?? "UNKNOWN",
    version: s.version ?? "UNKNOWN",
    checks: [{ label: "readiness", state: rawReadiness === "READY" ? "PASS" : rawReadiness === "UNREADY" ? "FAIL" : "NOT_TESTED", detail: rawReadiness }],
    missionIds: [],
    incidentEventIds: s.last_event_id ? [s.last_event_id] : [],
  };
  });
}

export function mapMemoryGraph(snapshot: FoundrySnapshot): MemoryGraph {
  const nodes: MemoryNode[] = snapshot.memory.map((m, index) => {
    const rawType = String(m.mem_type ?? "").toUpperCase();
    const category: MemoryNode["category"] = rawType === "PROCEDURAL"
      ? "PROCEDURE"
      : rawType === "CORE"
        ? "BELIEF"
        : rawType === "EPISODIC"
          ? "OBSERVATION"
          : "UNKNOWN";
    const content = typeof m.content === "string" && m.content.trim() ? m.content : "UNKNOWN";
    const confidence = typeof m.confidence === "number" && Number.isFinite(m.confidence)
      ? Math.round(Math.max(0, Math.min(1, m.confidence)) * 100)
      : NOT_REPORTED_NUMERIC;
    const unknowns = [
      ...(content === "UNKNOWN" ? ["content"] : []),
      ...(confidence === NOT_REPORTED_NUMERIC ? ["confidence"] : []),
      ...(m.evidence_ids?.length ? [] : ["evidence linkage"]),
    ];
    const ref: EntityRef | undefined = m.mission_id
      ? { kind: "mission", id: m.mission_id, label: m.mission_id }
      : m.agent_run_id
        ? { kind: "agent", id: m.agent_run_id, label: m.agent_run_id }
        : undefined;
    return {
    id: m.memory_id,
    label: m.memory_id,
    category,
    x: 120 + (index % 5) * 90,
    y: 100 + Math.floor(index / 5) * 80,
    r: 18,
    summary: content,
    confidence,
    freshness: m.updated_at ?? "UNKNOWN",
    unknowns,
    ref,
  };
  });
  const nodeIds = new Set(nodes.map((node) => node.id));
  const edges = snapshot.memory.flatMap((memory) => {
    const sources = [...(memory.source_ids ?? []), ...(memory.evidence_ids ?? [])];
    return sources.filter((source) => nodeIds.has(source)).map((source) => ({ from: memory.memory_id, to: source, label: "source" }));
  });
  return { nodes, edges };
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
    return { label: MEASURED_COST_LABEL, value: NOT_REPORTED_NUMERIC, unit: "NOT_REPORTED" }
  }
  return { label: MEASURED_COST_LABEL, value: NOT_REPORTED_NUMERIC, unit: "NOT_REPORTED" }
}

export function mapSystemOverview(snapshot: FoundrySnapshot): SystemOverview {
  const current = snapshot.release_identity?.current;
  const sha = typeof current === "string" ? current.slice(0, 12) : "unknown";
  const resourceUsage = [...mapResourceUsage(snapshot), mapMeasuredCostResource(snapshot)];
  return {
    version: sha,
    build: String(snapshot.source_of_truth.ledger_head_sequence),
    releaseState: snapshot.health.supervisor_status ?? "UNKNOWN",
    uptime: snapshot.health.heartbeat_at ?? "UNKNOWN",
    resourceUsage,
  };
}

const mapTrustState = (trust: string, stop: boolean): TrustSummary["state"] => {
  if (stop) return "SUSPENDED"
  const normalized = trust.toUpperCase()
  if (normalized === "PROVEN") return "PROVEN"
  if (normalized === "SIMULATED") return "SIMULATED"
  if (normalized === "LAB" || normalized === "PROVING" || normalized === "DEGRADED") {
    return "DEGRADED"
  }
  return "UNKNOWN"
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
  const assessment = snapshot.health.trust_assessment as Record<string, unknown> | undefined;
  const gateStatus: TrustSummary["gateStatus"] = stop
    ? "GATED"
    : assessment && assessment.eligible === true
      ? "PASS"
      : assessment
        ? "GATED"
        : "UNKNOWN";
  return {
    state: mapTrustState(trust, stop),
    score: null,
    gateStatus,
    evidenceCoverage: coverage,
    unresolvedFindings: snapshot.verification.filter((v) => v.status === "FAIL").length,
    health: mapHealth(snapshot.health.supervisor_status),
    lastVerifiedAt: snapshot.generated_at,
    factors: [
      { id: "supervisor", label: "Supervisor", state: snapshot.health.supervisor_status === "RUNNING" ? "PASS" : "WARN", detail: snapshot.health.supervisor_status ?? "UNKNOWN" },
      { id: "stop", label: "STOP sentinel", state: stop ? "FAIL" : "PASS", detail: stop ? "ACTIVE" : "CLEAR" },
      { id: "ledger", label: "Ledger head", state: snapshot.source_of_truth ? "PASS" : "PENDING", detail: snapshot.source_of_truth ? String(snapshot.source_of_truth.ledger_head_sequence) : "UNKNOWN" },
      { id: "coverage", label: "Evidence coverage", state: coverage === NOT_REPORTED_NUMERIC ? "PENDING" : "PASS", detail: coverage === NOT_REPORTED_NUMERIC ? "No verification records in canonical projection." : `${coverage}% of verification records passed.` },
      { id: "constraints", label: "Owner constraints", state: constraints.length > 0 ? "WARN" : "PASS", detail: `${constraints.length} active constraint${constraints.length === 1 ? "" : "s"} in durable controls.` },
      { id: "stale-leases", label: "Stale leases", state: staleLeases.length > 0 ? "WARN" : "PASS", detail: `${staleLeases.length} stale lease${staleLeases.length === 1 ? "" : "s"} reported by supervisor health.` },
    ],
  };
}

export function mapNotifications(snapshot: FoundrySnapshot): Notification[] {
  const items: Notification[] = [];
  for (const record of snapshot.decision_queue?.open ?? []) {
    const classification = String(record.classification ?? "UNKNOWN").toUpperCase();
    const category: Notification["category"] = classification === "BLOCKED_EXTERNAL_DEPENDENCY"
      ? "PROVIDER_DEGRADED"
      : classification === "OWNER_VETO_ACTIVE"
        ? "STOP_ACTIVATED"
        : classification === "DECISION_REQUIRED"
          ? "VERIFICATION_PENDING"
          : "RECONCILIATION";
    const missionId = typeof record.mission_id === "string" ? record.mission_id : null;
    items.push({
      id: String(record.queue_id ?? record.source_event_id ?? `attention-${items.length}`),
      severity: classification === "BLOCKED_EXTERNAL_DEPENDENCY" || classification === "OWNER_VETO_ACTIVE" ? "CRITICAL" : "WARNING",
      state: "UNREAD",
      category,
      title: typeof record.summary === "string" && record.summary ? record.summary : `Owner attention: ${classification}`,
      detail: typeof record.consequence === "string" && record.consequence ? record.consequence : "Owner attention record is open in the canonical decision queue.",
      createdAt: typeof record.opened_at === "string" ? record.opened_at : "UNKNOWN",
      refs: missionId ? [{ kind: "mission", id: missionId, label: missionId }] : [],
    });
  }
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

export function mapOwnerControlSurface(snapshot: FoundrySnapshot): OwnerControlSurface {
  const controls = snapshot.controls ?? ({} as FoundrySnapshot["controls"]);
  const queue = snapshot.decision_queue ?? {};
  const rawState = typeof controls.runtime_state === "string"
    ? controls.runtime_state
    : snapshot.stop.active
      ? "STOPPED"
      : snapshot.health.supervisor_status ?? "UNKNOWN";
  return {
    source: snapshot.owner_directives ? "LIVE" : "UNKNOWN",
    runtimeState: rawState,
    stateReason: controls.state_reason ?? "Canonical runtime state reason is unavailable.",
    stateSource: controls.state_source ?? "canonical-ledger",
    paused: Boolean(controls.paused),
    stopped: Boolean(snapshot.stop.active || rawState === "STOPPED"),
    resumeAvailable: Boolean(controls.resume_available),
    goals: controls.goals ?? [],
    constraints: controls.constraints ?? [],
    vetoes: controls.vetoes ?? [],
    reprioritizations: controls.reprioritizations ?? [],
    redirects: controls.redirects ?? [],
    directiveHistory: snapshot.owner_directives?.records ?? [],
    briefings: snapshot.briefings ?? [],
    effectTruth: snapshot.effect_truth ?? undefined,
    evolutionTruth: snapshot.evolution_truth ?? undefined,
    resultTraceability: {
      status: snapshot.result_traceability?.status ?? "UNKNOWN",
      results: snapshot.result_traceability?.results ?? [],
      count: Number(snapshot.result_traceability?.count ?? 0),
    },
    briefingPacket: snapshot.briefing_packet ?? null,
    decisionQueue: {
      open: (queue.open ?? []) as unknown as OwnerControlSurface["decisionQueue"]["open"],
      history: (queue.history ?? []) as unknown as OwnerControlSurface["decisionQueue"]["history"],
      open_count: Number(queue.open_count ?? 0),
      history_count: Number(queue.history_count ?? 0),
      counts_by_classification: queue.counts_by_classification ?? {},
    },
  };
}

const lifecycleRef = (kind: EntityRef["kind"], id: unknown): EntityRef[] =>
  typeof id === "string" && id.trim() ? [{ kind, id, label: id }] : [];

function missionEventRefs(event: FoundrySnapshot["timeline"][number], missionId: string): EntityRef[] {
  const refs: EntityRef[] = [];
  for (const raw of event.evidence_refs ?? []) {
    if (!raw.id || raw.id === missionId && raw.kind !== "mission") continue;
    const kind = raw.kind === "agent_run" ? "agent"
      : raw.kind === "verification" ? "verdict"
        : raw.kind === "directive" ? "timeline"
          : raw.kind === "mission" ? "mission"
            : raw.kind === "evidence" ? "evidence"
              : raw.kind === "system" ? "system"
                : raw.kind === "memory" ? "memory"
                  : null;
    if (kind) refs.push({ kind, id: raw.id, label: raw.id });
  }
  if (!refs.some((ref) => ref.kind === "mission")) refs.push({ kind: "mission", id: missionId, label: missionId });
  return refs;
}

function lifecycleForMission(snapshot: FoundrySnapshot, missionId: string): AutonomyLifecycleRecord[] {
  const mission = snapshot.missions.find((item) => item.mission_id === missionId);
  const workers = snapshot.workers.filter((item) => item.mission_id === missionId);
  const evidence = snapshot.evidence.filter((item) => item.mission_id === missionId);
  const verdicts = snapshot.verification.filter((item) => item.mission_id === missionId);
  const events = snapshot.timeline.filter((event) =>
    event.evidence_refs?.some((ref) => ref.id === missionId && ref.kind === "mission")
      || event.evidence_refs?.some((ref) => ref.id === missionId),
  );
  const eventIds = (...patterns: RegExp[]): string[] => events
    .filter((event) => patterns.some((pattern) => pattern.test(event.event_type)))
    .map((event) => event.event_id);
  const eventRefs = (...patterns: RegExp[]): EntityRef[] => events
    .filter((event) => patterns.some((pattern) => pattern.test(event.event_type)))
    .flatMap((event) => missionEventRefs(event, missionId))
    .filter((ref, index, all) => all.findIndex((candidate) => candidate.kind === ref.kind && candidate.id === ref.id) === index);
  const state = String(mission?.state ?? "UNKNOWN").toUpperCase();
  const missionState: AutonomyLifecycleRecord["state"] =
    ["COMPLETE", "COMPLETED"].includes(state) ? "COMPLETE"
      : ["BLOCKED", "FAILED", "HALTED", "CANCELLED", "CANCELED"].includes(state) ? "FAILED"
        : ACTIVE_MISSION_STATES.has(state) ? "ACTIVE"
          : STANDBY_MISSION_STATES.has(state) ? "PENDING"
            : "UNKNOWN";
  const make = (
    key: string,
    label: string,
    lifecycleState: AutonomyLifecycleRecord["state"],
    detail: string,
    refs: EntityRef[],
    ids: string[],
  ): AutonomyLifecycleRecord => ({ key, label, state: lifecycleState, detail, refs, eventIds: ids });

  const goal = snapshot.controls?.goals?.find((record) => {
    const target = String(record.target ?? "GLOBAL");
    return target === "GLOBAL" || target === missionId;
  });
  const goalId = goal?.directive_id ?? goal?.id;
  const goalEvents = eventIds(/^owner_directive\.recorded$/);
  const planEvents = eventIds(/mission\.created|mission_step\.recorded|executive\.decision_materialized|portfolio\..*selected|worker_loop\.portfolio_selected/);
  const workerEvents = eventIds(/agent_run\.|worker\.(route_decided|run_recorded|crashed|fallback_triggered)/);
  const actionEvents = eventIds(/system\.action_(started|completed|failed)|effect\./);
  const evidenceEvents = eventIds(/evidence\.created|worker\.evidence_reconciled/);
  const verifierEvents = eventIds(/verification\.(started|passed|failed|blocked)/);
  const repairStateEvents = events
    .filter((event) => event.event_type === "mission.state_changed" && String(event.state ?? "").toUpperCase() === "REPAIRING")
    .map((event) => event.event_id);
  const repairEvents = eventIds(/fallback|repair|retry/i).concat(repairStateEvents);
  const memoryEvents = eventIds(/memory\./);
  const briefingEvents = eventIds(/briefing\./);
  const completedWorker = workers.find((worker) => String(worker.status).toUpperCase() === "COMPLETED");
  const failedWorker = workers.find((worker) => ["FAILED", "CANCELLED"].includes(String(worker.status).toUpperCase()));
  const runningWorker = workers.find((worker) => String(worker.status).toUpperCase() === "RUNNING");
  const latestVerdict = verdicts[0];
  const hasArtifact = evidence.some((item) => Object.keys(item.artifact_hashes ?? {}).length > 0);
  const latestBriefing = snapshot.briefing_packet;

  return [
    make("owner_goal", "Owner goal", goal || goalEvents.length ? "COMPLETE" : "UNKNOWN",
      typeof goal?.exact_text === "string" ? goal.exact_text : goal ? "Recorded owner goal" : "No owner goal is recorded for this mission.",
      lifecycleRef("timeline", goalId).concat(eventRefs(/^owner_directive\.recorded$/)), goalEvents),
    make("plan", "Plan", planEvents.length ? "COMPLETE" : mission ? "PENDING" : "UNKNOWN",
      planEvents.length ? "Plan and mission admission are recorded in the ledger." : "No plan admission event is recorded.",
      eventRefs(/mission\.created|mission_step\.recorded|executive\.|portfolio\./), planEvents),
    make("mission", "Mission", missionState,
      mission ? `${state}${mission.next_action ? ` · next ${mission.next_action}` : ""}` : "Mission record unavailable.",
      lifecycleRef("mission", missionId).concat(lifecycleRef("timeline", mission?.last_event_id)),
      mission?.last_event_id ? [mission.last_event_id] : []),
    make("worker", "Worker", failedWorker ? "FAILED" : completedWorker ? "COMPLETE" : runningWorker ? "ACTIVE" : mission ? "PENDING" : "UNKNOWN",
      failedWorker ? `Worker failure: ${failedWorker.failure_class ?? "UNKNOWN"}` : completedWorker ? "Worker run completed." : runningWorker ? "Worker run active." : "No worker run is recorded.",
      workers.flatMap((worker) => lifecycleRef("agent", worker.run_id)).concat(eventRefs(/agent_run\.|worker\./)),
      workerEvents.concat(workers.map((worker) => worker.last_event_id).filter((id): id is string => Boolean(id)))),
    make("action_artifact", "Action / artifact", hasArtifact ? "COMPLETE" : actionEvents.some((id) => id) ? "ACTIVE" : mission ? "PENDING" : "UNKNOWN",
      hasArtifact ? "Artifact hash is recorded in an evidence bundle." : actionEvents.length ? "Effect/action event is recorded; completion is not yet evidenced." : "No action or artifact record is available.",
      eventRefs(/system\.action_|effect\.|evidence\.created/), actionEvents),
    make("evidence", "Evidence", evidence.length ? "COMPLETE" : mission ? "PENDING" : "UNKNOWN",
      evidence.length ? `${evidence.length} evidence bundle${evidence.length === 1 ? "" : "s"} recorded.` : "No evidence bundle is recorded.",
      evidence.flatMap((item) => lifecycleRef("evidence", item.bundle_id)).concat(eventRefs(/evidence\.|worker\.evidence_reconciled/)),
      evidenceEvents.concat(evidence.map((item) => item.last_event_id).filter((id): id is string => Boolean(id)))),
    make("verifier", "Verifier", latestVerdict?.status === "PASS" ? "COMPLETE" : latestVerdict?.status === "FAIL" ? "FAILED" : latestVerdict?.status === "STARTED" ? "ACTIVE" : mission ? "PENDING" : "UNKNOWN",
      latestVerdict ? `${latestVerdict.status}${latestVerdict.findings ? ` · ${String(latestVerdict.findings)}` : ""}` : "No independent verifier verdict is recorded.",
      verdicts.flatMap((item) => lifecycleRef("verdict", item.verdict_id)).concat(eventRefs(/verification\./)),
      verifierEvents.concat(verdicts.map((item) => item.last_event_id).filter((id): id is string => Boolean(id)))),
    make("repair_retry", "Repair / retry", repairEvents.length ? "COMPLETE" : latestVerdict?.status === "FAIL" ? "PENDING" : "PENDING",
      repairEvents.length ? "Repair or retry lineage is recorded." : latestVerdict?.status === "FAIL" ? "Verifier failed; no repair event is recorded yet." : "No repair or retry is recorded.",
      eventRefs(/fallback|repair|retry/i), repairEvents),
    make("memory", "Memory", snapshot.memory.some((item) => item.mission_id === missionId) ? "COMPLETE" : missionState === "COMPLETE" ? "UNKNOWN" : mission ? "PENDING" : "UNKNOWN",
      snapshot.memory.some((item) => item.mission_id === missionId) ? "Mission-linked memory write is recorded." : "No mission-linked memory write is recorded.",
      snapshot.memory.filter((item) => item.mission_id === missionId).flatMap((item) => lifecycleRef("memory", item.memory_id)).concat(eventRefs(/memory\./)),
      memoryEvents),
    make("owner_briefing", "Owner briefing", latestBriefing ? "COMPLETE" : "UNKNOWN",
      latestBriefing ? "Briefing packet projection is available." : "No briefing packet projection is available.",
      eventRefs(/briefing\./), briefingEvents),
  ];
}

export function mapMissionRun(snapshot: FoundrySnapshot, missionId: string): MissionRun {
  const mission = snapshot.missions.find((m) => m.mission_id === missionId);
  const steps = snapshot.mission_steps.filter((s) => s.mission_id === missionId);
  const evidenceCount = snapshot.evidence.filter((e) => e.mission_id === missionId).length;
  const activeWorkers = snapshot.workers.filter((w) => w.mission_id === missionId && w.status === "RUNNING").length;
  return {
    missionId,
    lifecycle: lifecycleForMission(snapshot, missionId),
    pipeline: steps.length
      ? steps.map((s, i) => ({
          key: String(s.step_id ?? i),
          label: String(s.capability ?? s.step_id ?? `step-${i + 1}`),
          state: "UNKNOWN",
          detail: String(s.capability ?? "UNKNOWN"),
        }))
      : [{ key: "state", label: mission?.state ?? "UNKNOWN", state: mission ? (mapMissionStatus(mission.state) === "COMPLETE" ? "COMPLETE" : mapMissionStatus(mission.state) === "UNKNOWN" ? "UNKNOWN" : "ACTIVE") : "UNKNOWN", detail: mission?.next_action ?? "UNKNOWN" }],
    telemetry: snapshot.timeline
      .filter((e) => e.evidence_refs?.some((r) => r.id === missionId))
      .slice(-8)
      .map((e) => ({
        at: e.recorded_at ?? snapshot.generated_at,
        source: e.event_type,
        level: e.event_type.includes("fail") ? "ERROR" : "INFO",
        text: e.summary ?? e.event_type,
      })),
    streams: [{ id: "ledger", name: "Event ledger", icon: "ledger", status: "canonical snapshot", state: "POLLING" }],
    verdicts: snapshot.verification
      .filter((v) => v.mission_id === missionId)
      .map((v) => ({
        id: v.verdict_id,
        title: v.status ?? "verification",
        state: v.status === "PASS" ? "PASSED" : v.status === "FAIL" ? "FAILED" : "EVALUATING",
        detail: v.findings ? String(v.findings) : "UNKNOWN",
        workerId: v.verifier_run_id ?? v.run_id,
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
      state: "UNKNOWN",
      detail: String(s.capability ?? "UNKNOWN"),
      evidenceIds: snapshot.evidence.filter((e) => e.mission_id === missionId && e.step_id === s.step_id).map((e) => e.bundle_id),
    }));
}
