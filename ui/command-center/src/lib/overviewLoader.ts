import type {
  EvidenceArtifact,
  JarvisClient,
  Mission,
  OwnerAttentionRecord,
  OwnerControlSurface,
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
}

/** One snapshot round-trip for live mode; parallel mock calls for simulated mode. */
export async function loadOverviewData(client: JarvisClient): Promise<OverviewData> {
  if (client instanceof RealJarvisRuntimeAdapter) {
    const snapshot = await client.fetchSnapshot()
    const ownerSurface = mapOwnerControlSurface(snapshot)
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
  }
}
