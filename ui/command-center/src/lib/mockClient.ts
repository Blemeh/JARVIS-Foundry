import type {
  AgentRun,
  ConnectionState,
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
import type { FoundryExecutive } from "./snapshotTypes";
import {
  applyEventToUniverse,
  applyResumeToUniverse,
  applyScenarioToUniverse,
  applyStopToUniverse,
  buildUniverse,
  lineageFor,
  makeEvent,
  memoryNodeForId,
  relatedFor,
  searchUniverse,
  type MockUniverse,
} from "./cohesion";
import { LocalMockEventStream } from "./eventStream";
import { validateUniverseIntegrity } from "./integrity";

const delay = () => new Promise((resolve) => setTimeout(resolve, 45));

/**
 * Simulated client for the isolated UI candidate.
 *
 * Every value is fixture data identified by `source: "SIMULATED"`. The client
 * owns one coherent mock universe: mutations applied by events and scenarios
 * update missions, workers, evidence, timeline, systems, memory, trust, and
 * notifications together. The future RealJarvisRuntimeAdapter implements the
 * same JarvisClient interface without touching this module.
 */
export class MockJarvisClient implements JarvisClient {
  readonly source = "SIMULATED" as const;
  readonly connection: ConnectionState = {
    connected: true,
    source: "SIMULATED",
    stale: false,
    lastFetchedAt: null,
    error: null,
  };
  private universe: MockUniverse = buildUniverse();
  private readonly stream = new LocalMockEventStream();

  /** Test/diagnostic helpers exposed on the concrete class only. */
  integrityIssues(): string[] {
    return validateUniverseIntegrity(this.universe);
  }

  private findWorker(id: string) {
    return this.universe.workers[id] ?? null;
  }

  async listMissions(): Promise<Mission[]> {
    await delay();
    return this.universe.missions;
  }

  async getMission(id: string): Promise<Mission | null> {
    await delay();
    return this.universe.missions.find((m) => m.id === id || m.code === id) ?? null;
  }

  async getMissionRun(missionId: string): Promise<MissionRun> {
    await delay();
    return this.universe.runs[missionId] ?? this.universe.runs["M-HYB-014"];
  }

  async getMissionSteps(missionId: string): Promise<MissionStep[]> {
    await delay();
    return this.universe.steps[missionId] ?? [];
  }

  async listWorkers(): Promise<Worker[]> {
    await delay();
    return Object.values(this.universe.workers);
  }

  async getAgent(id: string): Promise<Worker | null> {
    await delay();
    return this.findWorker(id);
  }

  async getAgentRuns(agentId: string): Promise<AgentRun[]> {
    await delay();
    return this.universe.agentRuns.filter((r) => r.workerId === agentId);
  }

  async listEvidence(): Promise<EvidenceArtifact[]> {
    await delay();
    return this.universe.evidence;
  }

  async getEvidence(id: string): Promise<EvidenceArtifact | null> {
    await delay();
    return this.universe.evidence.find((e) => e.id === id) ?? null;
  }

  async listTimeline(): Promise<TimelineEvent[]> {
    await delay();
    return this.universe.timeline;
  }

  async getTimelineEvent(id: string): Promise<TimelineEvent | null> {
    await delay();
    return this.universe.timeline.find((e) => e.id === id) ?? null;
  }

  async getMemoryGraph(): Promise<MemoryGraph> {
    await delay();
    return this.universe.memoryGraph;
  }

  async getExecutive(): Promise<FoundryExecutive> {
    await delay();
    // Simulated mode has no executive ledger; honest empty state.
    return {
      activation_epoch: null,
      admission_authority: "UNKNOWN",
      latest_decision: null,
      recent_decisions: [],
      calibrations: [],
    };
  }

  async getMemoryNode(id: string): Promise<MemoryNode | null> {
    await delay();
    return memoryNodeForId(this.universe, id);
  }

  async listSystemComponents(): Promise<SystemComponent[]> {
    await delay();
    return Object.values(this.universe.systems);
  }

  async getSystem(id: string): Promise<SystemComponent | null> {
    await delay();
    return this.universe.systems[id] ?? null;
  }

  async getSystemOverview(): Promise<SystemOverview> {
    await delay();
    return this.universe.overview;
  }

  async getTrustSummary(): Promise<TrustSummary> {
    await delay();
    return this.universe.trust;
  }

  async getLineage(missionId: string): Promise<LineageNode[]> {
    await delay();
    return lineageFor(this.universe, missionId);
  }

  async search(query: string): Promise<SearchResults> {
    await delay();
    return searchUniverse(this.universe, query);
  }

  async listNotifications(): Promise<Notification[]> {
    await delay();
    return this.universe.notifications;
  }

  async acknowledgeNotification(id: string): Promise<void> {
    await delay();
    const n = this.universe.notifications.find((x) => x.id === id);
    if (n && n.state === "UNREAD") n.state = "ACKNOWLEDGED";
  }

  async resolveNotification(id: string): Promise<void> {
    await delay();
    const n = this.universe.notifications.find((x) => x.id === id);
    if (n) n.state = "RESOLVED";
  }

  async getRelatedEntities(kind: EntityKind, id: string): Promise<RelatedEntities> {
    await delay();
    return relatedFor(this.universe, kind, id);
  }

  subscribeEvents(): MockEventStream {
    return this.stream;
  }

  async simulateEvent(type: MockEventType): Promise<MockEvent> {
    await delay();
    const event = makeEvent(this.universe, type, `${type} simulated locally`);
    applyEventToUniverse(this.universe, event);
    this.stream.emit(event);
    return event;
  }

  async runScenario(name: Scenario): Promise<void> {
    await delay();
    if (name === "STREAM_DISCONNECTED") {
      this.stream.disconnect();
      return;
    }
    const events = applyScenarioToUniverse(this.universe, name);
    for (const event of events) this.stream.emit(event);
    if (name === "NORMAL_OPERATION") {
      this.stream.reconnect();
    }
  }

  async disconnectStream(): Promise<void> {
    await delay();
    this.stream.disconnect();
  }

  async reconnectStream(): Promise<void> {
    await delay();
    this.stream.reconnect();
  }

  async requestGateReevaluation(missionId: string) {
    await delay();
    return { accepted: true as const, simulatedAt: new Date().toISOString(), missionId };
  }

  async requestHalt(missionId: string) {
    await delay();
    return { accepted: true as const, simulatedAt: new Date().toISOString(), missionId };
  }

  async submitOwnerGoal(_objective: string): Promise<OwnerGoalResult> {
    await delay();
    return { accepted: false, error: "simulated mode does not write to runtime" };
  }

  async activateStop(): Promise<StopResult> {
    return this.simulateStop();
  }

  async resumeOperations(): Promise<{ accepted: boolean; error?: string }> {
    await delay();
    await this.resumeSimulation();
    return { accepted: true };
  }

  async simulateStop(): Promise<StopResult> {
    await delay();
    const at = new Date().toISOString();
    if (!this.universe.stopActive) {
      applyStopToUniverse(this.universe, at);
      const event = makeEvent(this.universe, "stop.simulated", "SIMULATED STOP - UI CANDIDATE");
      this.stream.emit(event);
    }
    return {
      accepted: true,
      at,
      label: "SIMULATED STOP - UI CANDIDATE",
      note: "No production side effect. The future runtime adapter will replace this simulation.",
    };
  }

  /** Used by the resume control to move back to healthy simulated state. */
  async resumeSimulation(): Promise<void> {
    await delay();
    if (this.universe.stopActive) {
      applyResumeToUniverse(this.universe, new Date().toISOString());
      const event = makeEvent(this.universe, "resume", "Resume simulation: reorientation + reconciliation");
      this.stream.emit(event);
    }
  }
}

export const mockClient: JarvisClient = new MockJarvisClient();
