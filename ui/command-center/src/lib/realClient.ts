import type {
  AgentRun,
  ConnectionState,
  DataSource,
  EntityKind,
  EvidenceArtifact,
  JarvisClient,
  LineageNode,
  MemoryGraph,
  MemoryNode,
  Mission,
  MissionRun,
  MissionStep,
  MockEvent,
  MockEventStream,
  MockEventType,
  Notification,
  OwnerGoalResult,
  RelatedEntities,
  Scenario,
  SearchResults,
  StopResult,
  SystemComponent,
  SystemOverview,
  TimelineEvent,
  TrustSummary,
  Worker,
} from "./contracts";
import { PollingEventStream } from "./pollingEventStream";
import type { V3DiscoveryView } from "./v3Models";
import { mapDiscovery } from "./discoveryMapper";
import {
  mapEvidence,
  mapMemoryGraph,
  mapMissionRun,
  mapMissionSteps,
  mapMissions,
  mapNotifications,
  mapSystemOverview,
  mapSystems,
  mapTimeline,
  mapTrustSummary,
  mapWorkers,
} from "./snapshotMapper";
import type { FoundrySnapshot, FoundryExecutive, OwnerCommandResponse } from "./snapshotTypes";

const DEFAULT_API = "/api";

function unavailable<T>(feature: string): Promise<T> {
  return Promise.reject(new Error(`UNAVAILABLE in live mode: ${feature}`));
}

export class RealJarvisRuntimeAdapter implements JarvisClient {
  private _source: DataSource = "LIVE";
  private snapshot: FoundrySnapshot | null = null;
  private snapshotRequest: Promise<FoundrySnapshot> | null = null;
  private lastError: string | null = null;
  private lastFetchedAt: string | null = null;
  private readonly stream: PollingEventStream;
  private readonly apiBase: string;
  private readonly pollMs: number;

  constructor(options?: { apiBase?: string; pollMs?: number }) {
    this.apiBase = options?.apiBase ?? import.meta.env.VITE_JARVIS_API_BASE ?? DEFAULT_API;
    this.pollMs = options?.pollMs ?? Number(import.meta.env.VITE_JARVIS_POLL_MS ?? 5000);
    this.stream = new PollingEventStream(this.pollMs, () => {
      void this.refreshSnapshot();
    });
  }

  get source(): DataSource {
    return this._source;
  }

  get connection(): ConnectionState {
    const degraded = Boolean(this.snapshot?.health.degraded);
    let source: DataSource = "LIVE";
    if (!this.lastFetchedAt || this.lastError) source = "DISCONNECTED";
    else if (degraded) source = "DEGRADED";
    const stale = this.lastFetchedAt
      ? Date.now() - new Date(this.lastFetchedAt).getTime() > this.pollMs * 2
      : true;
    return {
      connected: !this.lastError && Boolean(this.lastFetchedAt),
      source: this.lastError ? "DISCONNECTED" : stale && degraded ? "DEGRADED" : source,
      stale,
      lastFetchedAt: this.lastFetchedAt,
      error: this.lastError,
    };
  }

  private async refreshSnapshot(): Promise<FoundrySnapshot> {
    if (this.snapshotRequest) return this.snapshotRequest;
    const request = (async () => {
      try {
        const response = await fetch(`${this.apiBase}/command-center`, { headers: { Accept: "application/json" } });
        if (!response.ok) throw new Error(`snapshot HTTP ${response.status}`);
        const payload = (await response.json()) as FoundrySnapshot;
        this.snapshot = payload;
        this.lastFetchedAt = new Date().toISOString();
        this.lastError = null;
        this._source = payload.health.degraded ? "DEGRADED" : "LIVE";
        return payload;
      } catch (error) {
        this.lastError = error instanceof Error ? error.message : String(error);
        this._source = "DISCONNECTED";
        throw error;
      } finally {
        // @ts-expect-error request captured after assignment; TS false positive for closure use-before-assign
        if (this.snapshotRequest === request) this.snapshotRequest = null;
      }
    })();
    this.snapshotRequest = request;
    return request;
  }

  private async snapshotOrFetch(): Promise<FoundrySnapshot> {
    if (this.snapshot) return this.snapshot;
    return this.refreshSnapshot();
  }

  private async ownerCommand(command_type: string, exact_text: string, target?: string | null): Promise<OwnerCommandResponse> {
    const response = await fetch(`${this.apiBase}/owner-command`, {
      method: "POST",
      headers: { "Content-Type": "application/json", Accept: "application/json" },
      body: JSON.stringify({ command_type, exact_text, target: target ?? null }),
    });
    const payload = (await response.json()) as OwnerCommandResponse;
    if (!response.ok) {
      throw new Error(payload.reason ?? `owner command HTTP ${response.status}`);
    }
    await this.refreshSnapshot();
    return payload;
  }

  async listMissions(): Promise<Mission[]> {
    return mapMissions(await this.snapshotOrFetch());
  }

  async getMission(id: string): Promise<Mission | null> {
    return (await this.listMissions()).find((m) => m.id === id || m.code === id) ?? null;
  }

  async getMissionRun(missionId: string): Promise<MissionRun> {
    return mapMissionRun(await this.snapshotOrFetch(), missionId);
  }

  async getMissionSteps(missionId: string): Promise<MissionStep[]> {
    return mapMissionSteps(await this.snapshotOrFetch(), missionId);
  }

  async listWorkers(): Promise<Worker[]> {
    return mapWorkers(await this.snapshotOrFetch());
  }

  async getAgent(id: string): Promise<Worker | null> {
    return (await this.listWorkers()).find((w) => w.id === id) ?? null;
  }

  async getAgentRuns(agentId: string): Promise<AgentRun[]> {
    const snap = await this.snapshotOrFetch();
    return snap.workers
      .filter((w) => w.run_id === agentId)
      .map((w) => ({
        id: w.run_id,
        missionId: w.mission_id ?? "",
        workerId: w.run_id,
        state: w.status === "RUNNING" ? "ACTIVE" : w.status === "FAILED" ? "FAILED" : "COMPLETE",
        summary: w.role ?? "agent run",
        startedAt: w.updated_at ?? snap.generated_at,
        evidenceIds: [],
      }));
  }

  async listEvidence(): Promise<EvidenceArtifact[]> {
    const snap = await this.snapshotOrFetch();
    return mapEvidence(snap, snap.verification);
  }

  async getEvidence(id: string): Promise<EvidenceArtifact | null> {
    return (await this.listEvidence()).find((e) => e.id === id) ?? null;
  }

  async listTimeline(): Promise<TimelineEvent[]> {
    return mapTimeline(await this.snapshotOrFetch());
  }

  async getTimelineEvent(id: string): Promise<TimelineEvent | null> {
    return (await this.listTimeline()).find((e) => e.id === id) ?? null;
  }

  async getMemoryGraph(): Promise<MemoryGraph> {
    return mapMemoryGraph(await this.snapshotOrFetch());
  }

  async getExecutive(): Promise<FoundryExecutive> {
    const snapshot = await this.snapshotOrFetch();
    if (!snapshot.executive) {
      // Older backend without executive projection: honest empty state.
      return {
        activation_epoch: null,
        admission_authority: "UNKNOWN",
        latest_decision: null,
        recent_decisions: [],
        calibrations: [],
      };
    }
    return snapshot.executive;
  }

  async getMemoryNode(id: string): Promise<MemoryNode | null> {
    return (await this.getMemoryGraph()).nodes.find((n) => n.id === id) ?? null;
  }

  async getDiscovery(): Promise<V3DiscoveryView> {
    // Additive §3.13 projection: map the backend snapshot.discovery section
    // when present. No fixture fallback — absence stays honest-unavailable.
    const snapshot = await this.snapshotOrFetch();
    const discovery = (snapshot as { discovery?: unknown }).discovery;
    if (discovery === undefined || discovery === null) {
      return { available: false, reason: "PROJECTION_NOT_AVAILABLE" };
    }
    return mapDiscovery(discovery);
  }

  async listSystemComponents(): Promise<SystemComponent[]> {
    return mapSystems(await this.snapshotOrFetch());
  }

  async getSystem(id: string): Promise<SystemComponent | null> {
    return (await this.listSystemComponents()).find((s) => s.id === id) ?? null;
  }

  async getSystemOverview(): Promise<SystemOverview> {
    return mapSystemOverview(await this.snapshotOrFetch());
  }

  async getTrustSummary(): Promise<TrustSummary> {
    return mapTrustSummary(await this.snapshotOrFetch());
  }

  async getLineage(missionId: string): Promise<LineageNode[]> {
    const snap = await this.snapshotOrFetch();
    return snap.timeline
      .filter((e) => e.evidence_refs?.some((r) => r.id === missionId))
      .slice(-6)
      .map((e) => ({
        id: e.event_id,
        kind: "evidence",
        label: e.event_type,
        detail: e.summary ?? "",
        status: e.event_type.includes("fail") ? "FAIL" : "PASS",
        refs: [{ kind: "mission", id: missionId, label: missionId }],
      }));
  }

  async search(query: string): Promise<SearchResults> {
    const q = query.trim().toLowerCase();
    if (!q) return { missions: [], agents: [], evidence: [], systems: [], memory: [], timeline: [] };
    const snap = await this.snapshotOrFetch();
    const missions = mapMissions(snap)
      .filter((m) => m.title.toLowerCase().includes(q) || m.id.toLowerCase().includes(q))
      .map((m) => ({ id: m.id, label: m.title, sublabel: m.status, kind: "mission" as const, to: `/missions/${m.id}` }));
    const agents = mapWorkers(snap)
      .filter((w) => w.name.toLowerCase().includes(q) || w.id.toLowerCase().includes(q))
      .map((w) => ({ id: w.id, label: w.name, sublabel: w.role, kind: "agent" as const, to: `/agents/${w.id}` }));
    return { missions, agents, evidence: [], systems: [], memory: [], timeline: [] };
  }

  async listNotifications(): Promise<Notification[]> {
    return mapNotifications(await this.snapshotOrFetch());
  }

  async acknowledgeNotification(_id: string): Promise<void> {
    /* UI-local only; canonical ledger has no notification ack surface */
  }

  async resolveNotification(_id: string): Promise<void> {
    /* UI-local only */
  }

  async getRelatedEntities(_kind: EntityKind, _id: string): Promise<RelatedEntities> {
    return { missions: [], agents: [], evidence: [], systems: [], memory: [], timeline: [], notifications: [] };
  }

  subscribeEvents(): MockEventStream {
    void this.refreshSnapshot();
    return this.stream;
  }

  async simulateEvent(_type: MockEventType): Promise<MockEvent> {
    return unavailable("simulateEvent");
  }

  async runScenario(_name: Scenario): Promise<void> {
    return unavailable("runScenario");
  }

  async disconnectStream(): Promise<void> {
    this.stream.disconnect();
  }

  async reconnectStream(): Promise<void> {
    this.stream.reconnect();
    await this.refreshSnapshot();
  }

  async requestGateReevaluation(missionId: string) {
    return { accepted: true as const, simulatedAt: new Date().toISOString(), missionId };
  }

  async requestHalt(missionId: string) {
    try {
      await this.ownerCommand("STOP_MISSION", `halt mission ${missionId}`, missionId);
      return { accepted: true as const, simulatedAt: new Date().toISOString(), missionId };
    } catch (error) {
      return {
        accepted: false as const,
        simulatedAt: new Date().toISOString(),
        missionId,
        error: error instanceof Error ? error.message : String(error),
      };
    }
  }

  async submitOwnerGoal(objective: string): Promise<OwnerGoalResult> {
    try {
      const result = await this.ownerCommand("GOAL", objective, "GLOBAL");
      return { accepted: result.status === "APPLIED", directiveId: result.directive_id, eventId: result.event_id };
    } catch (error) {
      return { accepted: false, error: error instanceof Error ? error.message : String(error) };
    }
  }

  async activateStop(reason: string): Promise<StopResult> {
    try {
      await this.ownerCommand("STOP_EVERYTHING", reason, "GLOBAL");
      return {
        accepted: true,
        at: new Date().toISOString(),
        label: "LIVE STOP - CANONICAL DIRECTIVE",
        note: "STOP_EVERYTHING recorded on durable owner directive ledger and sentinel when configured.",
      };
    } catch (error) {
      return {
        accepted: false,
        at: new Date().toISOString(),
        label: "STOP FAILED",
        note: "",
        error: error instanceof Error ? error.message : String(error),
      };
    }
  }

  async resumeOperations(reason = "resume jarvis"): Promise<{ accepted: boolean; error?: string }> {
    try {
      await this.ownerCommand("RESUME", reason, "GLOBAL");
      return { accepted: true };
    } catch (error) {
      return { accepted: false, error: error instanceof Error ? error.message : String(error) };
    }
  }

  async simulateStop(): Promise<StopResult> {
    return this.activateStop("owner requested STOP from command center UI");
  }
}
