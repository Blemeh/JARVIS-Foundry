/**
 * Frontend-facing data boundary for the JARVIS Command Center.
 *
 * The UI consumes these contracts exclusively. A future RealJarvisRuntimeAdapter
 * maps canonical Foundry ledger / Supervisor APIs onto the same shapes. The
 * current mission ships MockJarvisClient behind the same interface.
 */
import type { FoundryExecutive } from "./snapshotTypes";

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
  | "COMPLETE";

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

export type PipelineStageState = "COMPLETE" | "ACTIVE" | "PENDING";

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
  state: "LIVE" | "POLLING" | "CACHED" | "WRITING";
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

export type WorkerStatus = "ACTIVE" | "STANDBY" | "DEGRADED" | "RATE_LIMITED" | "FAILED" | "RECOVERING";
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
  | "RESOURCE";

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

export type ComponentState = "RUNNING" | "STANDBY" | "STOPPED" | "ARMED" | "ACTIVE";
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

export type TrustState = "SIMULATED" | "PROVEN" | "DEGRADED" | "SUSPENDED";
export type GateStatus = "OPEN" | "GATED" | "PASS" | "FAIL" | "NOT_APPLICABLE";

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

export interface SystemOverview {
  version: string;
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

export interface JarvisClient {
  readonly source: DataSource;
  readonly connection: ConnectionState;
  listMissions(): Promise<Mission[]>;
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
  getMemoryNode(id: string): Promise<MemoryNode | null>;
  getExecutive(): Promise<FoundryExecutive>;
  listSystemComponents(): Promise<SystemComponent[]>;
  getSystem(id: string): Promise<SystemComponent | null>;
  getSystemOverview(): Promise<SystemOverview>;
  getTrustSummary(): Promise<TrustSummary>;
  getLineage(missionId: string): Promise<LineageNode[]>;
  search(query: string): Promise<SearchResults>;
  listNotifications(): Promise<Notification[]>;
  acknowledgeNotification(id: string): Promise<void>;
  resolveNotification(id: string): Promise<void>;
  getRelatedEntities(kind: EntityKind, id: string): Promise<RelatedEntities>;
  subscribeEvents(): MockEventStream;
  simulateEvent(type: MockEventType): Promise<MockEvent>;
  runScenario(name: Scenario): Promise<void>;
  disconnectStream(): Promise<void>;
  reconnectStream(): Promise<void>;
  requestGateReevaluation(missionId: string): Promise<{ accepted: true; simulatedAt: string; missionId: string }>;
  requestHalt(missionId: string): Promise<{ accepted: boolean; simulatedAt: string; missionId: string; error?: string }>;
  submitOwnerGoal(objective: string): Promise<OwnerGoalResult>;
  activateStop(reason: string): Promise<StopResult>;
  resumeOperations(reason?: string): Promise<{ accepted: boolean; error?: string }>;
  /** @deprecated simulated mode only — use activateStop in live mode */
  simulateStop(): Promise<StopResult>;
}
