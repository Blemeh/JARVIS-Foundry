import type {
  AgentRun,
  AutonomyCandidateFilter,
  AutonomyProjection,
  ConnectionState,
  DataSource,
  EntityKind,
  EvidenceArtifact,
  ForensicRecord,
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
  OwnerDelegationMode,
  OwnerControlSurface,
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
  WorkHierarchy,
  ObsidianVaultFile,
} from "./contracts";
import { mapObsidianVault } from "./obsidianVault";
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
  mapOwnerControlSurface,
  mapSystemOverview,
  mapSystems,
  mapTimeline,
  mapTrustSummary,
  mapWorkers,
} from "./snapshotMapper";
import type {
  FoundrySnapshot,
  FoundryExecutive,
  FoundryTimelinePage,
  OwnerCommandResponse,
} from "./snapshotTypes";

const DEFAULT_API = "/api";
const SNAPSHOT_FETCH_TIMEOUT_MS = 120_000;

function fetchWithTimeout(url: string, init?: RequestInit): Promise<Response> {
  if (typeof AbortSignal !== "undefined" && "timeout" in AbortSignal) {
    return fetch(url, { ...init, signal: AbortSignal.timeout(SNAPSHOT_FETCH_TIMEOUT_MS) });
  }
  return fetch(url, init);
}

function unavailable<T>(feature: string): Promise<T> {
  return Promise.reject(new Error(`UNAVAILABLE in live mode: ${feature}`));
}

export class RealJarvisRuntimeAdapter implements JarvisClient {
  private _source: DataSource = "LIVE";
  private snapshot: FoundrySnapshot | null = null;
  private snapshotRequest: Promise<FoundrySnapshot> | null = null;
  private readonly seenEventIds = new Set<string>();
  private eventCursor: number | null = null;
  private controlSessionReady = false;
  private controlSessionRequest: Promise<void> | null = null;
  private lastError: string | null = null;
  private lastFetchedAt: string | null = null;
  private readonly stream: PollingEventStream;
  private readonly apiBase: string;
  private readonly pollMs: number;

  constructor(options?: { apiBase?: string; pollMs?: number }) {
    this.apiBase = options?.apiBase ?? import.meta.env.VITE_JARVIS_API_BASE ?? DEFAULT_API;
    this.pollMs = options?.pollMs ?? Number(import.meta.env.VITE_JARVIS_POLL_MS ?? 5000);
    this.stream = new PollingEventStream(this.pollMs, () => this.pollLedgerEvents());
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
        const response = await fetchWithTimeout(`${this.apiBase}/command-center`, {
          headers: { Accept: "application/json" },
        });
        if (!response.ok) throw new Error(`snapshot HTTP ${response.status}`);
        const payload = (await response.json()) as Partial<FoundrySnapshot>;
        if (!payload || typeof payload !== "object" || !payload.health || !payload.source_of_truth) {
          throw new Error("snapshot is missing canonical health or source-of-truth metadata");
        }
        const snapshot = payload as FoundrySnapshot;
        const incomingSequence = snapshot.source_of_truth.ledger_head_sequence;
        const acceptedSequence = this.snapshot?.source_of_truth.ledger_head_sequence;
        if (!Number.isSafeInteger(incomingSequence) || incomingSequence < 0) {
          throw new Error("snapshot has an invalid ledger head sequence");
        }
        if (acceptedSequence !== undefined && incomingSequence < acceptedSequence) {
          throw new Error(`stale snapshot sequence ${incomingSequence} is behind accepted sequence ${acceptedSequence}`);
        }
        this.snapshot = snapshot;
        if (this.eventCursor === null) {
          this.eventCursor = incomingSequence;
          for (const event of snapshot.timeline) this.seenEventIds.add(event.event_id);
        }
        this.lastFetchedAt = new Date().toISOString();
        this.lastError = null;
        this._source = snapshot.health.degraded ? "DEGRADED" : "LIVE";
        return snapshot;
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

  /** Public entry for routes that map many surfaces from one snapshot. */
  async fetchSnapshot(): Promise<FoundrySnapshot> {
    return this.snapshotOrFetch();
  }

  private async snapshotOrFetch(): Promise<FoundrySnapshot> {
    const stale = !this.lastFetchedAt || Date.now() - new Date(this.lastFetchedAt).getTime() > this.pollMs * 2;
    if (this.snapshot && !this.lastError && !stale) return this.snapshot;
    return this.refreshSnapshot();
  }

  private async pollLedgerEvents(): Promise<MockEvent[]> {
    try {
      const initial = await this.snapshotOrFetch();
      let cursor = this.eventCursor ?? initial.source_of_truth.ledger_head_sequence;
      const pending: FoundryTimelinePage["events"] = [];
      for (let pageNumber = 0; pageNumber < 100; pageNumber += 1) {
        const query = new URLSearchParams({
          after_sequence: String(cursor),
          limit: "500",
        });
        const response = await fetchWithTimeout(
          `${this.apiBase}/timeline?${query.toString()}`,
          { headers: { Accept: "application/json" } },
        );
        if (!response.ok) throw new Error(`timeline HTTP ${response.status}`);
        const page = (await response.json()) as Partial<FoundryTimelinePage>;
        if (
          page.schema !== "v13.command-center-event-page.v1"
          || !Array.isArray(page.events)
          || !Number.isSafeInteger(page.next_sequence)
          || !Number.isSafeInteger(page.ledger_head_sequence)
          || page.next_sequence! < cursor
          || page.ledger_head_sequence! < cursor
        ) {
          throw new Error("timeline page has an invalid or stale durable cursor");
        }
        pending.push(...page.events);
        const next = page.next_sequence!;
        if (!page.has_more) {
          cursor = page.ledger_head_sequence!;
          break;
        }
        if (next === cursor) {
          throw new Error("timeline page did not advance its durable cursor");
        }
        cursor = next;
        if (pageNumber === 99) {
          throw new Error("timeline backlog exceeded bounded catch-up pages");
        }
      }
      const snapshot = await this.refreshSnapshot();
      if (snapshot.source_of_truth.ledger_head_sequence < cursor) {
        throw new Error(
          `snapshot sequence ${snapshot.source_of_truth.ledger_head_sequence} is behind event cursor ${cursor}`,
        );
      }
      this.eventCursor = cursor;
      const events = pending
        .slice()
        .sort((left, right) => left.sequence - right.sequence)
        .filter((event) => {
          if (this.seenEventIds.has(event.event_id)) return false;
          this.seenEventIds.add(event.event_id);
          return true;
        })
        .map((event) => ({
          id: event.event_id,
          type: "ledger.event" as const,
          at: event.occurred_at ?? event.recorded_at ?? snapshot.generated_at,
          detail: event.summary ?? event.event_type,
        }));
      return events;
    } catch (error) {
      this.lastError = error instanceof Error ? error.message : String(error);
      this._source = "DISCONNECTED";
      throw error;
    }
  }

  private async ownerCommand(
    command_type: string,
    exact_text: string,
    target?: string | null,
    metadata?: Record<string, unknown>,
  ): Promise<OwnerCommandResponse> {
    for (let attempt = 0; attempt < 2; attempt += 1) {
      await this.ensureControlSession();
      const response = await fetchWithTimeout(`${this.apiBase}/owner-command`, {
        method: "POST",
        credentials: "include",
        headers: { "Content-Type": "application/json", Accept: "application/json" },
        body: JSON.stringify({
          command_type,
          exact_text,
          target: target ?? null,
          ...(metadata ? { metadata } : {}),
        }),
      });
      const payload = (await response.json()) as OwnerCommandResponse;
      if (response.ok) {
        await this.refreshSnapshot();
        return payload;
      }
      if (response.status === 401 && attempt === 0) {
        this.controlSessionReady = false;
        continue;
      }
      throw new Error(payload.reason ?? `owner command HTTP ${response.status}`);
    }
    throw new Error("owner command browser session could not be authenticated");
  }

  private async ensureControlSession(): Promise<void> {
    if (this.controlSessionReady) return;
    if (this.controlSessionRequest) return this.controlSessionRequest;
    const request = (async () => {
      const response = await fetchWithTimeout(`${this.apiBase}/control-session`, {
        credentials: "include",
        headers: { Accept: "application/json" },
      });
      if (!response.ok) {
        throw new Error(`owner control session HTTP ${response.status}`);
      }
      this.controlSessionReady = true;
    })().finally(() => {
      if (this.controlSessionRequest === request) {
        this.controlSessionRequest = null;
      }
    });
    this.controlSessionRequest = request;
    return request;
  }

  async listMissions(): Promise<Mission[]> {
    return mapMissions(await this.snapshotOrFetch());
  }

  async getWorkHierarchy(): Promise<WorkHierarchy> {
    const snapshot = await this.snapshotOrFetch();
    const hierarchy = snapshot.work_hierarchy;
    if (
      hierarchy?.schema === "v13.command-center-work.v1"
      && Array.isArray(hierarchy.objectives)
      && Array.isArray(hierarchy.missions)
      && Array.isArray(hierarchy.actions)
    ) {
      return hierarchy;
    }
    return {
      schema: "v13.command-center-work.v1",
      status: "UNAVAILABLE",
      reason: "The connected runtime does not publish the V13 Work hierarchy projection.",
      authority: "READ_ONLY_CANONICAL_PROJECTIONS",
      ui_authority: "NONE",
      levels: {},
      goals: [],
      programs: [],
      projects: [],
      objectives: [],
      campaigns: [],
      missions: [],
      actions: [],
    };
  }

  async getForensicRecord(kind: string, id: string): Promise<ForensicRecord | null> {
    try {
      const response = await fetchWithTimeout(
        `${this.apiBase}/record/${encodeURIComponent(kind)}/${encodeURIComponent(id)}`,
        { headers: { Accept: "application/json" } },
      );
      if (response.status === 404) return null;
      if (!response.ok) throw new Error(`forensic record HTTP ${response.status}`);
      const payload = (await response.json()) as Partial<ForensicRecord>;
      if (payload.status !== "FOUND" || payload.kind !== kind || !payload.record) {
        throw new Error("forensic record contract is invalid");
      }
      return payload as ForensicRecord;
    } catch (error) {
      this.lastError = error instanceof Error ? error.message : String(error);
      this._source = "DISCONNECTED";
      throw error;
    }
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
    const evidence = mapEvidence(snap, snap.verification);
    return snap.workers
      .filter((w) => w.run_id === agentId)
      .map((w) => ({
        id: w.run_id,
        missionId: w.mission_id ?? "",
        workerId: w.run_id,
        state: w.status === "RUNNING" ? "ACTIVE" : w.status === "FAILED" || w.status === "CANCELLED" ? "FAILED" : w.status === "COMPLETED" ? "COMPLETE" : "QUEUED",
        summary: w.failure_class ?? w.role ?? "UNKNOWN",
        startedAt: w.updated_at ?? snap.generated_at,
        endedAt: w.status === "RUNNING" ? undefined : w.updated_at,
        evidenceIds: evidence.filter((item) => item.workerId === w.run_id).map((item) => item.id),
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

  async getObsidianVault(): Promise<ObsidianVaultFile[]> {
    return mapObsidianVault(await this.snapshotOrFetch());
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

  async getAutonomy(filter: AutonomyCandidateFilter = "all"): Promise<AutonomyProjection> {
    try {
      const query = new URLSearchParams({ filter, limit: "50", timeline_limit: "100" });
      const response = await fetchWithTimeout(`${this.apiBase}/autonomy?${query.toString()}`, {
        headers: { Accept: "application/json" },
      });
      if (!response.ok) throw new Error(`autonomy projection HTTP ${response.status}`);
      const payload = (await response.json()) as Partial<AutonomyProjection>;
      if (payload.schema !== "v11.command-center-autonomy.v1" || !payload.executive || !payload.funnel) {
        throw new Error("autonomy projection contract is invalid");
      }
      this.lastFetchedAt = new Date().toISOString();
      this.lastError = null;
      this._source = "LIVE";
      return payload as AutonomyProjection;
    } catch (error) {
      this.lastError = error instanceof Error ? error.message : String(error);
      this._source = "DISCONNECTED";
      throw error;
    }
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
    const lifecycle = mapMissionRun(snap, missionId).lifecycle ?? [];
    return lifecycle.map((record) => ({
      id: record.eventIds[record.eventIds.length - 1] ?? `${missionId}:${record.key}`,
      kind: record.key === "verifier" ? "verifier" : record.key === "repair_retry" ? "repair" : record.key === "evidence" || record.key === "memory" || record.key === "action_artifact" || record.key === "owner_briefing" ? "evidence" : "builder",
      label: record.label,
      detail: record.detail,
      status: record.state === "COMPLETE" ? "PASS" : record.state === "FAILED" ? "FAIL" : record.state === "ACTIVE" ? "ACTIVE" : "PENDING",
      refs: record.refs,
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
    const evidence = mapEvidence(snap, snap.verification)
      .filter((item) => `${item.id} ${item.typeLabel} ${item.missionRef} ${item.note}`.toLowerCase().includes(q))
      .map((item) => ({ id: item.id, label: item.id, sublabel: item.verdict, kind: "evidence" as const, to: `/evidence/${item.id}` }));
    const systems = mapSystems(snap)
      .filter((item) => `${item.id} ${item.name} ${item.health} ${item.readiness}`.toLowerCase().includes(q))
      .map((item) => ({ id: item.id, label: item.name, sublabel: item.health, kind: "system" as const, to: `/systems/${item.id}` }));
    const memory = mapMemoryGraph(snap).nodes
      .filter((item) => `${item.id} ${item.label} ${item.category} ${item.summary}`.toLowerCase().includes(q))
      .map((item) => ({ id: item.id, label: item.label, sublabel: item.category, kind: "memory" as const, to: `/memory/${item.id}` }));
    const timeline = mapTimeline(snap)
      .filter((item) => `${item.id} ${item.title} ${item.detail}`.toLowerCase().includes(q))
      .map((item) => ({ id: item.id, label: item.title, sublabel: item.at, kind: "timeline" as const, to: `/timeline?event=${encodeURIComponent(item.id)}` }));
    return { missions, agents, evidence, systems, memory, timeline };
  }

  async listNotifications(): Promise<Notification[]> {
    return mapNotifications(await this.snapshotOrFetch());
  }

  async getOwnerControlSurface(): Promise<OwnerControlSurface> {
    return mapOwnerControlSurface(await this.snapshotOrFetch());
  }

  async acknowledgeNotification(_id: string): Promise<void> {
    return unavailable("acknowledgeNotification");
  }

  async resolveNotification(_id: string): Promise<void> {
    return unavailable("resolveNotification");
  }

  async getRelatedEntities(kind: EntityKind, id: string): Promise<RelatedEntities> {
    const snap = await this.snapshotOrFetch();
    const missions = mapMissions(snap);
    const agents = mapWorkers(snap);
    const evidence = mapEvidence(snap, snap.verification);
    const systems = mapSystems(snap);
    const memory = mapMemoryGraph(snap).nodes;
    const timeline = mapTimeline(snap);
    const notifications = mapNotifications(snap);
    const ref = (refKind: EntityKind, refId: string, label: string): { kind: EntityKind; id: string; label: string } => ({ kind: refKind, id: refId, label });
    const missionIds = new Set<string>();
    if (kind === "mission") missionIds.add(id);
    if (kind === "agent") agents.filter((item) => item.id === id).flatMap((item) => item.missionIds ?? []).forEach((missionId) => missionIds.add(missionId));
    if (kind === "evidence") evidence.filter((item) => item.id === id && item.missionRef !== "UNKNOWN").forEach((item) => missionIds.add(item.missionRef));
    if (kind === "memory") memory.filter((item) => item.id === id && item.ref?.kind === "mission").forEach((item) => missionIds.add(item.ref!.id));
    if (kind === "timeline") timeline.filter((item) => item.id === id && item.missionId).forEach((item) => missionIds.add(item.missionId!));
    if (kind === "system") systems.filter((item) => item.id === id).flatMap((item) => item.missionIds ?? []).forEach((missionId) => missionIds.add(missionId));
    const relatedMissionRows = missions.filter((item) => missionIds.has(item.id));
    const relatedAgentRows = agents.filter((item) => item.missionIds?.some((missionId) => missionIds.has(missionId)) || (kind === "mission" && item.id === id));
    const relatedEvidenceRows = evidence.filter((item) => missionIds.has(item.missionRef) || (kind === "evidence" && item.id === id));
    const relatedMemoryRows = memory.filter((item) => (item.ref?.kind === "mission" && missionIds.has(item.ref.id)) || (kind === "memory" && item.id === id));
    const relatedTimelineRows = timeline.filter((item) => (item.missionId && missionIds.has(item.missionId)) || (kind === "timeline" && item.id === id));
    const relatedNotifications = notifications.filter((item) => item.refs.some((itemRef) => itemRef.id === id));
    return {
      missions: relatedMissionRows.map((item) => ref("mission", item.id, item.title)),
      agents: relatedAgentRows.map((item) => ref("agent", item.id, item.name)),
      evidence: relatedEvidenceRows.map((item) => ref("evidence", item.id, item.id)),
      systems: kind === "system" ? systems.filter((item) => item.id === id).map((item) => ref("system", item.id, item.name)) : [],
      memory: relatedMemoryRows.map((item) => ref("memory", item.id, item.label)),
      timeline: relatedTimelineRows.map((item) => ref("timeline", item.id, item.title)),
      notifications: relatedNotifications.map((item) => ref("timeline", item.id, item.title)),
    };
  }

  subscribeEvents(): MockEventStream {
    void this.snapshotOrFetch();
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
    await this.refreshSnapshot();
    this.stream.reconnect();
  }

  async requestGateReevaluation(missionId: string) {
    return {
      accepted: false,
      simulatedAt: new Date().toISOString(),
      missionId,
      error: "UNAVAILABLE in live mode: canonical gate re-evaluation command is not implemented",
    };
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

  async submitOwnerGoal(
    objective: string,
    mode?: OwnerDelegationMode,
  ): Promise<OwnerGoalResult> {
    try {
      const requestId = globalThis.crypto?.randomUUID?.()
        ?? `command-center-${Date.now()}-${Math.random().toString(16).slice(2)}`;
      const result = mode
        ? await this.ownerCommand("DELEGATE", objective, "GLOBAL", {
            mode,
            request_id: requestId,
          })
        : await this.ownerCommand("GOAL", objective, "GLOBAL");
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

  async resumeOperations(
    targetDirectiveId?: string,
    reason = "resume exact owner hold",
  ): Promise<{ accepted: boolean; error?: string }> {
    if (!targetDirectiveId) {
      return {
        accepted: false,
        error: "Resume requires one exact active STOP or PAUSE directive.",
      };
    }
    try {
      await this.ownerCommand("RESUME", reason, targetDirectiveId);
      return { accepted: true };
    } catch (error) {
      return { accepted: false, error: error instanceof Error ? error.message : String(error) };
    }
  }

  async simulateStop(): Promise<StopResult> {
    return this.activateStop("owner requested STOP from command center UI");
  }
}
