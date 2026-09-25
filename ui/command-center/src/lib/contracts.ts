/**
 * Frontend-facing data boundary for the JARVIS Command Center.
 *
 * The UI consumes these contracts exclusively. A future RealJarvisRuntimeAdapter
 * maps canonical Foundry ledger / Supervisor APIs onto the same shapes. The
 * current mission ships MockJarvisClient behind the same interface.
 */
import type { FoundryExecutive } from "./snapshotTypes";
import type { ObsidianVaultFile } from "./obsidianVault";
import type { V3DiscoveryView } from "./v3Models";

export type { ObsidianVaultFile } from "./obsidianVault";

export type DataSource = "SIMULATED" | "LIVE" | "DEGRADED" | "DISCONNECTED" | "UNKNOWN";

export interface ConnectionState {
  connected: boolean;
  source: DataSource;
  stale: boolean;
  lastFetchedAt: string | null;
  error: string | null;
}

export type MissionStatus =
  | "ACTIVE"
  | "IN_PROGRESS"
  | "STANDBY"
  | "BLOCKED"
  | "HALTED"
  | "INTERVENTION"
  | "COMPLETE"
  | "CANCELLED"
  | "UNKNOWN";

export type Priority = "CRITICAL" | "HIGH" | "ELEVATED" | "NORMAL";

export interface Mission {
  id: string;
  code: string;
  title: string;
  description: string;
  status: MissionStatus;
  priority: Priority;
  agentsActive: number;
  completion: number;
  nextAction: string;
  stage: string;
  startedAt: string;
  objective: string;
  owner: string;
}

export type WorkHierarchyLevelState =
  | "AVAILABLE"
  | "EMPTY"
  | "NOT_YET_INTEGRATED"
  | "UNAVAILABLE";

export interface WorkGoal {
  goal_id: string;
  directive_id: string;
  label: string;
  status: string;
  target: string;
  source_event_id?: string | null;
  objective_ids: string[];
}

export interface WorkProgram {
  program_id: string;
  label: string;
  status: string;
  source: string;
  project_ids: string[];
  objective_ids: string[];
}

export interface WorkProject {
  project_id: string;
  label: string;
  program_id?: string | null;
  status: string;
  source: string;
  objective_ids: string[];
}

export interface WorkObjective {
  objective_id: string;
  label: string;
  parent_id?: string | null;
  goal_id?: string | null;
  project_id?: string | null;
  program_id?: string | null;
  status: string;
  priority?: unknown;
  deadline?: string | null;
  campaign_ids: string[];
  mission_ids: string[];
  plan_ids: string[];
  action_ids: string[];
  progress_evidence: {
    status: string;
    confidence?: unknown;
    evidence_refs: string[];
  };
  source_event_id?: string | null;
  updated_at?: string | null;
}

export interface WorkCampaign {
  campaign_id: string;
  objective_id?: string | null;
  status: string;
  mission_ids: string[];
  child_count: number;
  accepted_child_count?: number | null;
  pending_count: number;
  wait_kinds: string[];
  action_ids: string[];
  checkpoint_id?: string | null;
  updated_at?: string | null;
  source: string;
}

export interface WorkMissionLink {
  mission_id: string;
  objective_id?: string | null;
  campaign_id?: string | null;
  status: string;
  action_ids: string[];
  worker_ids: string[];
  evidence_refs: string[];
  verdict_ids: string[];
  source_event_id?: string | null;
}

export interface WorkAction {
  action_id: string;
  step_id?: string | null;
  label: string;
  status: string;
  objective_id?: string | null;
  campaign_id?: string | null;
  mission_id?: string | null;
  worker_ids: string[];
  evidence_refs: string[];
  verdict_ids: string[];
  source: string;
}

export interface WorkHierarchy {
  schema: "v13.command-center-work.v1";
  status: "AVAILABLE" | "PARTIAL" | "EMPTY" | "UNAVAILABLE";
  reason?: string;
  authority: string;
  ui_authority: "NONE";
  levels: Record<string, WorkHierarchyLevelState>;
  goals: WorkGoal[];
  programs: WorkProgram[];
  projects: WorkProject[];
  objectives: WorkObjective[];
  campaigns: WorkCampaign[];
  missions: WorkMissionLink[];
  actions: WorkAction[];
  bounds?: Record<string, unknown>;
}

export interface ForensicRecord {
  status: "FOUND";
  kind: string;
  record: Record<string, unknown>;
}

/** Lifecycle truth is deliberately closed over explicit absence. */
export type PipelineStageState = "COMPLETE" | "ACTIVE" | "PENDING" | "FAILED" | "UNKNOWN" | "UNAVAILABLE";

export interface AutonomyLifecycleRecord {
  key: string;
  label: string;
  state: PipelineStageState;
  detail: string;
  refs: EntityRef[];
  eventIds: string[];
}

export interface PipelineStage {
  key: string;
  label: string;
  state: PipelineStageState;
  detail: string;
}

export interface TelemetryEntry {
  at: string;
  source: string;
  level: "INFO" | "WARN" | "ERROR";
  text: string;
}

export interface DataStream {
  id: string;
  name: string;
  icon: "database" | "api" | "memory" | "route" | "ledger";
  status: string;
  state: "LIVE" | "POLLING" | "CACHED" | "WRITING" | "UNKNOWN" | "UNAVAILABLE";
}

export type VerdictState = "PASSED" | "WARN_ACCEPT" | "EVALUATING" | "FAILED";

export interface VerifierVerdict {
  id: string;
  title: string;
  state: VerdictState;
  detail: string;
  evidenceId?: string;
  workerId?: string;
}

export interface MissionRun {
  missionId: string;
  pipeline: PipelineStage[];
  lifecycle?: AutonomyLifecycleRecord[];
  telemetry: TelemetryEntry[];
  streams: DataStream[];
  verdicts: VerifierVerdict[];
  stats: {
    elapsed: string;
    computeCost: string;
    agentsActive: number;
    evidenceArtifacts: number;
  };
}

export type WorkerStatus = "ACTIVE" | "STANDBY" | "DEGRADED" | "RATE_LIMITED" | "FAILED" | "RECOVERING" | "UNKNOWN" | "UNAVAILABLE";
export type WorkerRunStatus = "ACTIVE" | "FAILED" | "RECOVERING" | "COMPLETE" | "QUEUED";

export interface Worker {
  id: string;
  name: string;
  role: string;
  provider: string;
  status: WorkerStatus;
  currentTask: string;
  uptime: string;
  latencyMs: number | null;
  log: string[];
  lastHeartbeat?: string;
  missionIds?: string[];
  systemId?: string;
  failures?: number;
  recoveries?: number;
  evidenceIds?: string[];
}

export type EvidenceVerdict = "VERIFIED" | "WARN" | "EVALUATING" | "FAILED" | "TAMPERED";

export interface EvidenceArtifact {
  id: string;
  kind: string;
  typeLabel: string;
  source: string;
  sourceUri: string;
  hash: string;
  verdict: EvidenceVerdict;
  capturedAt: string;
  missionRef: string;
  note: string;
  stepId?: string;
  workerId?: string;
  verdictId?: string;
  claim?: string;
  decision?: string;
  outcome?: string;
  relatedEventId?: string;
}

export type MemoryNodeCategory =
  | "MISSION"
  | "EVIDENCE"
  | "BELIEF"
  | "DECISION"
  | "PROCEDURE"
  | "OBSERVATION"
  | "RESOURCE"
  | "UNKNOWN";

export interface MemoryNode {
  id: string;
  label: string;
  category: MemoryNodeCategory;
  x: number;
  y: number;
  r: number;
  summary: string;
  confidence: number;
  freshness: string;
  unknowns: string[];
  ref?: EntityRef;
}

export interface MemoryEdge {
  from: string;
  to: string;
  label: string;
}

export interface MemoryGraph {
  nodes: MemoryNode[];
  edges: MemoryEdge[];
}

export type TimelineCategory = "INCIDENT" | "HANDOFF" | "MILESTONE" | "COMMAND" | "SYSTEM";
export type TimelineSeverity = "INFO" | "WARNING" | "CRITICAL";

export interface TimelineEvent {
  id: string;
  at: string;
  group: string;
  category: TimelineCategory;
  title: string;
  detail: string;
  meta: { label: string; value: string }[];
  actions: { label: string; kind: "view" }[];
  severity?: TimelineSeverity;
  missionId?: string;
  agentId?: string;
  systemId?: string;
  evidenceId?: string;
  verdictId?: string;
  memoryId?: string;
}

export type ComponentState = "RUNNING" | "STANDBY" | "STOPPED" | "ARMED" | "ACTIVE" | "UNKNOWN" | "UNAVAILABLE";
export type Health = "HEALTHY" | "DEGRADED" | "CRITICAL" | "UNKNOWN";
export type Readiness = "READY" | "NOT_READY" | "DEGRADED" | "UNKNOWN";

export interface SystemComponent {
  id: string;
  name: string;
  description: string;
  state: ComponentState;
  health: Health;
  readiness: Readiness;
  dependencies: string[];
  lastHeartbeat: string;
  recovery: string;
  version: string;
  checks: { label: string; state: "PASS" | "WARN" | "FAIL" | "NOT_TESTED"; detail: string }[];
  missionIds?: string[];
  incidentEventIds?: string[];
}

export type TrustState = "SIMULATED" | "PROVEN" | "DEGRADED" | "SUSPENDED" | "UNKNOWN" | "UNAVAILABLE";
export type GateStatus = "OPEN" | "GATED" | "PASS" | "FAIL" | "NOT_APPLICABLE" | "UNKNOWN" | "UNAVAILABLE";

export interface TrustFactor {
  id: string;
  label: string;
  state: "PASS" | "WARN" | "FAIL" | "PENDING";
  detail: string;
}

export interface TrustSummary {
  state: TrustState;
  score: number | null;
  gateStatus: GateStatus;
  evidenceCoverage: number;
  unresolvedFindings: number;
  health: Health;
  lastVerifiedAt: string;
  factors: TrustFactor[];
}

export interface StopResult {
  accepted: boolean;
  at: string;
  label: string;
  note: string;
  error?: string;
}

export interface OwnerGoalResult {
  accepted: boolean;
  directiveId?: string;
  eventId?: string;
  error?: string;
}

export interface VoiceTextResult {
  status: string;
  transcript: string;
  responseText: string;
  commandType?: string | null;
  language?: string;
  directiveId?: string;
  eventId?: string;
  error?: string;
}

export interface OwnerUiPrefs {
  locale: "en" | "sq";
  notificationRouting: "in-app" | "in-app-toast" | "off";
}

export interface AttentionToastDelivery {
  status: string;
  mode?: string;
  results: Array<{
    status: string;
    attention_id: string;
    delivered: boolean;
    detail: string;
  }>;
}

export type OwnerDelegationMode = "TELL_ME" | "PREPARE" | "HANDLE" | "WATCH";

export type OwnerAttentionClassification =
  | "INFORM"
  | "DECISION_REQUIRED"
  | "BLOCKED_EXTERNAL_DEPENDENCY"
  | "OWNER_VETO_ACTIVE"
  | "UNKNOWN";

export interface OwnerAttentionRecord {
  queue_id: string;
  status: "OPEN" | "RESOLVED" | "SUPERSEDED";
  classification: OwnerAttentionClassification;
  summary?: string;
  consequence?: string;
  options?: unknown[];
  source_event_id?: string;
  source_event_type?: string;
  evidence_refs?: string[];
  entity_kind?: string | null;
  entity_id?: string | null;
  mission_id?: string | null;
  resolution_event_id?: string | null;
  opened_at?: string;
  item?: Record<string, unknown>;
}

export interface OwnerControlSurface {
  source: DataSource;
  runtimeState: string;
  stateReason: string;
  stateSource: string;
  paused: boolean;
  stopped: boolean;
  resumeAvailable: boolean;
  resumableDirectiveIds: string[];
  goals: Record<string, unknown>[];
  constraints: Record<string, unknown>[];
  vetoes: Record<string, unknown>[];
  reprioritizations: Record<string, unknown>[];
  redirects: Record<string, unknown>[];
  directiveHistory: Record<string, unknown>[];
  ownerContext?: {
    contract: string;
    status: string;
    authority: {
      model: string;
      ownerControls: string;
      execution: string;
    };
    sections: Record<string, Record<string, unknown>[]>;
    currentCount: number;
    historyCount: number;
    warnings: string[];
    reason?: string;
  };
  briefings: Record<string, unknown>[];
  resultTraceability: {
    status: string;
    results: Record<string, unknown>[];
    count: number;
  };
  briefingPacket: Record<string, unknown> | null;
  briefingL10n: Record<string, unknown> | null;
  effectTruth?: Record<string, unknown>;
  evolutionTruth?: Record<string, unknown>;
  decisionQueue: {
    open: OwnerAttentionRecord[];
    history: OwnerAttentionRecord[];
    open_count: number;
    history_count: number;
    counts_by_classification: Record<string, number>;
  };
}

/** Additive V8 operational-intelligence surface; absent means unavailable. */
export type OperationalSalience =
  | "IGNORE"
  | "REMEMBER"
  | "MONITOR"
  | "RECOMMEND"
  | "CREATE_WORK"
  | "OWNER_ATTENTION"
  | "URGENT"

export interface OperationalSignalSurface {
  signal_id: string
  fingerprint: string
  salience: OperationalSalience | string
  score: number | null
  state: "OPEN" | "RESOLVED" | "SUPPRESSED" | "UNKNOWN" | string
  kind: string
  summary: string
  recurrence_count: number | null
  suppressed_count: number | null
  next_action: string
  entity_kind?: string | null
  entity_id?: string | null
  source_event_ids?: string[]
  evidence_refs?: string[]
}

export interface OperationalIntelligenceSurface {
  schema: string
  status: string
  generated_at: string
  signals: OperationalSignalSurface[]
  followups: Record<string, unknown>[]
  attention?: {
    open_count?: number
    owner_attention_count?: number
    deduped_occurrences?: number
    counts_by_salience?: Record<string, number>
  }
  providers?: Record<string, unknown>
  routing?: Record<string, unknown>
  recurring_work?: Record<string, unknown>
  context?: Record<string, unknown>
  memory?: Record<string, unknown>
  knowledge?: Record<string, unknown>
  evolution?: Record<string, unknown>
}

export interface SystemOverview {
  version: string;
  /** Authoritative numeric schema from the canonical snapshot, when available. */
  schema: number | null;
  build: string;
  releaseState: string;
  uptime: string;
  resourceUsage: { label: string; value: number; unit: string }[];
}

/* ------------------------------------------------------------------ */
/* Cohesion layer: shared entity references across every surface.     */
/* ------------------------------------------------------------------ */

export type EntityKind =
  | "mission"
  | "agent"
  | "evidence"
  | "system"
  | "memory"
  | "timeline"
  | "verdict"
  | "procedure";

export interface EntityRef {
  kind: EntityKind;
  id: string;
  label: string;
}

export interface MissionStep {
  id: string;
  missionId: string;
  key: string;
  label: string;
  state: PipelineStageState;
  detail: string;
  workerId?: string;
  evidenceIds: string[];
}

export interface AgentRun {
  id: string;
  missionId: string;
  workerId: string;
  stepKey?: string;
  state: WorkerRunStatus;
  summary: string;
  startedAt: string;
  endedAt?: string;
  evidenceIds: string[];
  verdictId?: string;
}

export type NotificationSeverity = "CRITICAL" | "WARNING" | "INFO";
export type NotificationState = "UNREAD" | "ACKNOWLEDGED" | "RESOLVED";
export type NotificationCategory =
  | "VERIFICATION_FAILED"
  | "VERIFICATION_PENDING"
  | "MISSION_BLOCKED"
  | "WORKER_CRASH"
  | "WORKER_RECOVERED"
  | "PROVIDER_DEGRADED"
  | "MEMORY_CONFLICT"
  | "HEARTBEAT_MISSED"
  | "STOP_ACTIVATED"
  | "CANDIDATE_PROMOTED"
  | "ROLLBACK_COMPLETED"
  | "EVIDENCE_INTEGRITY"
  | "MISSION_COMPLETED"
  | "MISSION_STARTED"
  | "RECONCILIATION";

export interface Notification {
  id: string;
  severity: NotificationSeverity;
  state: NotificationState;
  category: NotificationCategory;
  title: string;
  detail: string;
  createdAt: string;
  refs: EntityRef[];
}

export type Scenario =
  | "NORMAL_OPERATION"
  | "VERIFICATION_FAILURE"
  | "WORKER_CRASH"
  | "PROVIDER_DEGRADED"
  | "MISSION_BLOCKED"
  | "MEMORY_CONFLICT"
  | "STOP_ACTIVE"
  | "RECOVERY"
  | "ROLLBACK"
  | "MISSION_COMPLETE"
  | "EMPTY_STATE"
  | "STREAM_DISCONNECTED";

export type MockEventType =
  | "ledger.event"
  | "mission.started"
  | "step.started"
  | "worker.active"
  | "evidence.arrived"
  | "verification.started"
  | "verification.failed"
  | "repair.created"
  | "worker.recovered"
  | "verification.passed"
  | "mission.completed"
  | "system.degraded"
  | "provider.outage"
  | "memory.conflict"
  | "stop.simulated"
  | "resume";

export interface MockEvent {
  id: string;
  type: MockEventType;
  at: string;
  detail: string;
}

export interface MockEventStream {
  subscribe(listener: (event: MockEvent) => void): () => void;
  emit(event: MockEvent): void;
  disconnect(): void;
  reconnect(): void;
  readonly connected: boolean;
}

export interface SearchResultItem {
  id: string;
  label: string;
  sublabel: string;
  kind: EntityKind;
  to: string;
}

export interface SearchResults {
  missions: SearchResultItem[];
  agents: SearchResultItem[];
  evidence: SearchResultItem[];
  systems: SearchResultItem[];
  memory: SearchResultItem[];
  timeline: SearchResultItem[];
}

export interface RelatedEntities {
  missions: EntityRef[];
  agents: EntityRef[];
  evidence: EntityRef[];
  systems: EntityRef[];
  memory: EntityRef[];
  timeline: EntityRef[];
  notifications: EntityRef[];
}

export type LineageNodeKind = "builder" | "verifier" | "finding" | "repair" | "evidence" | "pass";
export type LineageNodeStatus = "PASS" | "FAIL" | "ACTIVE" | "PENDING";

export interface LineageNode {
  id: string;
  kind: LineageNodeKind;
  label: string;
  detail: string;
  status: LineageNodeStatus;
  refs: EntityRef[];
}

export type AutonomyCandidateFilter = "all" | "eligible" | "rejected" | "researched" | "stale";
export type ProjectionAvailability = "AVAILABLE" | "UNAVAILABLE";

export interface AutonomyMetric {
  value: number | null;
  status: ProjectionAvailability;
}

export interface AutonomyFactor {
  factor: string;
  value: number | string | boolean | null;
  kind: string;
  contribution: number | null;
}

export interface AutonomyWorkField {
  status: string;
  [key: string]: unknown;
}

/** Additive V11 contracts stay data-only and may be unavailable on older ledgers. */
export interface AutonomyContractProjection {
  status: string;
  contract?: string;
  authority?: string;
  reason?: string | null;
  [key: string]: unknown;
}

export interface AutonomyOutcome {
  state: string;
  mission_state: string;
  verification: {
    status: string;
    verdict_id: string | null;
    acceptance: string;
    acceptance_event_id: string | null;
    reason: string | null;
    acceptance_receipt?: Record<string, unknown> | null;
  };
  repair_attempts: number | null;
  actual: Record<string, unknown> | null;
  measurement_status: string;
  learned_calibration: Record<string, unknown> | null;
  next_action: string | null;
  candidate_id: string;
}

export interface AutonomyCurrentWork {
  candidate?: { candidate_id: string; title: string; state: string; event_id: string | null };
  objective: AutonomyWorkField;
  mission: AutonomyWorkField;
  worktree: AutonomyWorkField;
  current_step: AutonomyWorkField;
  worker: AutonomyWorkField;
  verifier: AutonomyWorkField;
  verification_plan?: AutonomyContractProjection;
  final_outcome: AutonomyOutcome;
}

export interface AutonomyCandidate {
  candidate_id: string;
  title: string;
  expected_outcome: string | null;
  project: string | null;
  sources: Array<{
    kind: string | null;
    identity: string | null;
    ref: string | null;
    authority: string | null;
    event_id: string | null;
  }>;
  score: number | null;
  rank: number | null;
  confidence: number | null;
  research: {
    status: string;
    research_id: string | null;
    source_ref: string | null;
    budget_used: number | null;
    completed_at: string | null;
    event_id: string | null;
    dossier?: AutonomyContractProjection;
  };
  capability_readiness?: AutonomyContractProjection;
  project_context?: AutonomyContractProjection;
  verification_plan?: AutonomyContractProjection;
  state: string;
  rejection: { reason_code: string | null; reason: string | null; event_id: string | null } | null;
  judgment: {
    decision_id: string | null;
    policy_version: string | null;
    do_nothing_won: boolean;
    reason_code: string | null;
    reason: string | null;
    evidence: Record<string, AutonomyFactor[] | Record<string, unknown>>;
    event_id: string | null;
  };
  age_seconds: number | null;
  expires_at: string | null;
  ttl_seconds: number | null;
  stale: boolean;
  current_work: AutonomyCurrentWork;
}

export interface AutonomyProjection {
  schema: "v11.command-center-autonomy.v1";
  status: "AVAILABLE" | "EMPTY" | "UNAVAILABLE";
  reason: string | null;
  generated_at: string;
  executive: {
    state: string;
    supervisor_state?: string;
    trust_state?: string;
    cycle_id: string | null;
    stage: string | null;
    last_observation_cycle: string | null;
    next_wake: string | null;
    wait_reason: string | null;
    wait_boundary?: string | null;
    wait_reason_code?: string | null;
    owner_action?: {
      value: AutonomyOwnerAction | null;
      status: ProjectionAvailability | "PARTIAL";
      items: AutonomyOwnerAction[];
      count: number;
      open_count: number | null;
      scan_truncated: boolean;
      items_truncated: boolean;
      scan_limit: number;
    };
    active_wip: AutonomyMetric;
  };
  funnel: Record<string, AutonomyMetric>;
  candidates: AutonomyCandidate[];
  candidate_filter?: AutonomyCandidateFilter;
  sources: Array<{
    source_kind: string;
    source_identity: string;
    state: string;
    readiness: string;
    last_checked: string | null;
    last_meaningful_change: string | null;
    next_due: string | null;
    last_error: string | null;
    observations: number;
    candidates: number;
    event_id: string | null;
    meaningful_changes?: Array<Record<string, unknown>>;
    candidate_worthy_signals?: Array<Record<string, unknown>>;
    noise_suppressed?: number | null;
  }>;
  timeline: Array<{
    sequence: number;
    event_id: string;
    event_type: string;
    occurred_at: string;
    stage: string;
    summary: string;
    candidate_id: string | null;
    mission_id: string | null;
    objective_id: string | null;
    verdict_id: string | null;
    decision: string | null;
  }>;
  current_work: AutonomyCurrentWork | null;
  overview?: AutonomyContractProjection;
  source_digest?: AutonomyContractProjection;
  learning?: AutonomyContractProjection;
  wait?: AutonomyContractProjection;
  contract_versions?: Record<string, string>;
  limits: Record<string, number>;
}

export interface AutonomyOwnerAction {
  escalation_id: string;
  objective_id: string | null;
  decision: string;
  why: string;
  recommendation: string;
  options: string[];
  delay_consequence: string | null;
  created_at: string | null;
  event_id: string | null;
}

export interface JarvisClient {
  readonly source: DataSource;
  readonly connection: ConnectionState;
  listMissions(): Promise<Mission[]>;
  /** Additive V13 read projection. Missing backends stay explicitly unavailable. */
  getWorkHierarchy?(): Promise<WorkHierarchy>;
  /** Read one bounded projection record; never a raw database row. */
  getForensicRecord?(kind: string, id: string): Promise<ForensicRecord | null>;
  getMission(id: string): Promise<Mission | null>;
  getMissionRun(missionId: string): Promise<MissionRun>;
  getMissionSteps(missionId: string): Promise<MissionStep[]>;
  listWorkers(): Promise<Worker[]>;
  getAgent(id: string): Promise<Worker | null>;
  getAgentRuns(agentId: string): Promise<AgentRun[]>;
  listEvidence(): Promise<EvidenceArtifact[]>;
  getEvidence(id: string): Promise<EvidenceArtifact | null>;
  listTimeline(): Promise<TimelineEvent[]>;
  getTimelineEvent(id: string): Promise<TimelineEvent | null>;
  getMemoryGraph(): Promise<MemoryGraph>;
  getObsidianVault(): Promise<ObsidianVaultFile[]>;
  getMemoryNode(id: string): Promise<MemoryNode | null>;
  getExecutive(): Promise<FoundryExecutive>;
  /** Bounded, read-only V11 projection. Runtime clients must not synthesize records. */
  getAutonomy(filter?: AutonomyCandidateFilter): Promise<AutonomyProjection>;
  /**
   * V3 discovery projection (§3.13). Live backends without the additive
   * snapshot section return { available: false } instead of fixtures.
   */
  getDiscovery(): Promise<V3DiscoveryView>;
  listSystemComponents(): Promise<SystemComponent[]>;
  getSystem(id: string): Promise<SystemComponent | null>;
  getSystemOverview(): Promise<SystemOverview>;
  getTrustSummary(): Promise<TrustSummary>;
  getLineage(missionId: string): Promise<LineageNode[]>;
  search(query: string): Promise<SearchResults>;
  listNotifications(): Promise<Notification[]>;
  getOwnerControlSurface?(): Promise<OwnerControlSurface>;
  acknowledgeNotification(id: string): Promise<void>;
  resolveNotification(id: string): Promise<void>;
  getRelatedEntities(kind: EntityKind, id: string): Promise<RelatedEntities>;
  subscribeEvents(): MockEventStream;
  simulateEvent(type: MockEventType): Promise<MockEvent>;
  runScenario(name: Scenario): Promise<void>;
  disconnectStream(): Promise<void>;
  reconnectStream(): Promise<void>;
  requestGateReevaluation(missionId: string): Promise<{ accepted: boolean; simulatedAt: string; missionId: string; error?: string }>;
  requestHalt(missionId: string): Promise<{ accepted: boolean; simulatedAt: string; missionId: string; error?: string }>;
  submitOwnerGoal(objective: string, mode?: OwnerDelegationMode): Promise<OwnerGoalResult>;
  pauseOperations(reason: string): Promise<OwnerGoalResult>;
  submitVeto(text: string): Promise<OwnerGoalResult>;
  submitVoiceText(text: string, languageHint?: string): Promise<VoiceTextResult>;
  saveOwnerUiPrefs(prefs: OwnerUiPrefs): Promise<OwnerUiPrefs>;
  getOwnerUiPrefs(): Promise<OwnerUiPrefs>;
  deliverAttentionToasts(): Promise<AttentionToastDelivery>;
  getAttentionToastStatus(): Promise<AttentionToastDelivery>;
  activateStop(reason: string): Promise<StopResult>;
  resumeOperations(targetDirectiveId?: string, reason?: string): Promise<{ accepted: boolean; error?: string }>;
  /** @deprecated simulated mode only — use activateStop in live mode */
  simulateStop(): Promise<StopResult>;
}
