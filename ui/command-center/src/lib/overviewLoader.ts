import type {
  EvidenceArtifact,
  JarvisClient,
  Mission,
  OwnerAttentionRecord,
  OwnerControlSurface,
  OperationalIntelligenceSurface,
  OperationalSignalSurface,
  SystemOverview,
  TimelineEvent,
  TrustSummary,
  Worker,
} from "./contracts"
import type { ObsidianVaultFile } from "./obsidianVault"
import { mapObsidianVault } from "./obsidianVault"
import { RealJarvisRuntimeAdapter } from "./realClient"
import {
  mapEvidence,
  mapMissions,
  mapOwnerControlSurface,
  mapSystemOverview,
  mapTimeline,
  mapTrustSummary,
  mapWorkers,
} from "./snapshotMapper"
import type { FoundryExecutive } from "./snapshotTypes"

export type OverviewData = {
  missions: Mission[]
  overview: SystemOverview
  trust: TrustSummary
  workers: Worker[]
  evidence: EvidenceArtifact[]
  timeline: TimelineEvent[]
  executive: FoundryExecutive | null
  ownerAttention: OwnerAttentionRecord[]
  obsidianVault: ObsidianVaultFile[]
  ownerSurface: OwnerControlSurface | null
  operationalIntelligence?: OperationalIntelligenceSurface | null
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value)
}

function stringValue(value: unknown, fallback = "UNKNOWN"): string {
  return typeof value === "string" && value.trim().length > 0 ? value : fallback
}

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null
}

function optionalNumber(value: unknown): number | undefined {
  return finiteNumber(value) ?? undefined
}

function stringArray(value: unknown): string[] | undefined {
  if (!Array.isArray(value)) return undefined
  return value.filter((item): item is string => typeof item === "string" && item.length > 0)
}

function recordArray(value: unknown): Record<string, unknown>[] | undefined {
  if (!Array.isArray(value)) return undefined
  return value.filter(isRecord)
}

function normalizeOperationalSignal(value: unknown): OperationalSignalSurface | null {
  if (!isRecord(value) || typeof value.signal_id !== "string" || value.signal_id.length === 0) return null
  return {
    signal_id: value.signal_id,
    fingerprint: stringValue(value.fingerprint),
    salience: stringValue(value.salience),
    score: finiteNumber(value.score),
    state: stringValue(value.state),
    kind: stringValue(value.kind),
    summary: stringValue(value.summary),
    recurrence_count: finiteNumber(value.recurrence_count),
    suppressed_count: finiteNumber(value.suppressed_count),
    next_action: stringValue(value.next_action),
    entity_kind: typeof value.entity_kind === "string" ? value.entity_kind : null,
    entity_id: typeof value.entity_id === "string" ? value.entity_id : null,
    source_event_ids: stringArray(value.source_event_ids),
    evidence_refs: stringArray(value.evidence_refs),
  }
}

function normalizeOperationalIntelligence(value: unknown): OperationalIntelligenceSurface | null {
  if (!isRecord(value) || !Array.isArray(value.signals)) return null
  const followups = recordArray(value.followups)
  if (!followups) return null
  const attention = isRecord(value.attention) ? value.attention : undefined
  const normalizedSignals = value.signals
    .map(normalizeOperationalSignal)
    .filter((signal): signal is OperationalSignalSurface => signal !== null)
  return {
    schema: stringValue(value.schema),
    status: stringValue(value.status),
    generated_at: stringValue(value.generated_at),
    signals: normalizedSignals,
    followups,
    attention: attention
      ? {
          open_count: optionalNumber(attention.open_count),
          owner_attention_count: optionalNumber(attention.owner_attention_count),
          deduped_occurrences: optionalNumber(attention.deduped_occurrences),
          counts_by_salience: isRecord(attention.counts_by_salience) ? attention.counts_by_salience as Record<string, number> : undefined,
        }
      : undefined,
    providers: isRecord(value.providers) ? value.providers : undefined,
    routing: isRecord(value.routing) ? value.routing : undefined,
    recurring_work: isRecord(value.recurring_work) ? value.recurring_work : undefined,
    context: isRecord(value.context) ? value.context : undefined,
    memory: isRecord(value.memory) ? value.memory : undefined,
    knowledge: isRecord(value.knowledge) ? value.knowledge : undefined,
    evolution: isRecord(value.evolution) ? value.evolution : undefined,
  }
}

/** One snapshot round-trip for live mode; parallel mock calls for simulated mode. */
export async function loadOverviewData(client: JarvisClient): Promise<OverviewData> {
  if (client instanceof RealJarvisRuntimeAdapter) {
      const snapshot = await client.fetchSnapshot()
      const ownerSurface = mapOwnerControlSurface(snapshot)
      const discovery = isRecord(snapshot.discovery) ? snapshot.discovery : null
      const operationalRaw = discovery?.operational_intelligence
    return {
      missions: mapMissions(snapshot),
      overview: mapSystemOverview(snapshot),
      trust: mapTrustSummary(snapshot),
      workers: mapWorkers(snapshot),
      evidence: mapEvidence(snapshot, snapshot.verification ?? []),
      timeline: mapTimeline(snapshot),
      executive: snapshot.executive ?? null,
      ownerAttention: ownerSurface.decisionQueue?.open ?? [],
      obsidianVault: mapObsidianVault(snapshot),
      ownerSurface,
        operationalIntelligence: normalizeOperationalIntelligence(operationalRaw),
    }
  }

  const [
    missions,
    overview,
    trust,
    workers,
    evidence,
    timeline,
    executive,
    ownerSurface,
    obsidianVault,
  ] = await Promise.all([
    client.listMissions(),
    client.getSystemOverview(),
    client.getTrustSummary(),
    client.listWorkers(),
    client.listEvidence(),
    client.listTimeline(),
    client.getExecutive().catch(() => null),
    client.getOwnerControlSurface?.().catch(() => null) ?? Promise.resolve(null),
    client.getObsidianVault(),
  ])

  return {
    missions,
    overview,
    trust,
    workers,
    evidence,
    timeline,
    executive,
    ownerAttention: ownerSurface?.decisionQueue?.open ?? [],
    obsidianVault,
    ownerSurface,
    operationalIntelligence: null,
  }
}
