import type { EntityRef, TimelineEvent } from "./contracts";
import { agentRunToWorker } from "./cohesion";

/** Build navigable refs from a timeline event's cross-link fields. */
export function refsFromTimelineEvent(e: TimelineEvent): EntityRef[] {
  const refs: EntityRef[] = [];
  if (e.missionId) refs.push({ kind: "mission", id: e.missionId, label: e.missionId });
  if (e.agentId) refs.push({ kind: "agent", id: e.agentId, label: e.agentId });
  if (e.systemId) refs.push({ kind: "system", id: e.systemId, label: e.systemId });
  if (e.evidenceId) refs.push({ kind: "evidence", id: e.evidenceId, label: e.evidenceId });
  if (e.verdictId) refs.push({ kind: "verdict", id: e.verdictId, label: e.verdictId });
  if (e.memoryId) refs.push({ kind: "memory", id: e.memoryId, label: e.memoryId });
  return refs;
}

/** Map timeline meta label/value pairs to navigable entity refs when the contract supports it. */
export function metaLabelToRef(label: string, value: string): EntityRef | null {
  const key = label.trim().toUpperCase();
  const id = value.trim();
  if (!id || id === "UNKNOWN" || id === "—") return null;

  switch (key) {
    case "MISSION":
    case "MISSION ID":
      return { kind: "mission", id, label: id };
    case "WORKER":
    case "AGENT":
      return { kind: "agent", id, label: id };
    case "EVIDENCE":
    case "EVIDENCE ID":
      return { kind: "evidence", id, label: id };
    case "VERDICT":
    case "VERDICT ID":
      return { kind: "verdict", id, label: id };
    case "SYSTEM":
      return { kind: "system", id, label: id };
    case "REPAIR":
    case "REPAIR RUN": {
      const worker = agentRunToWorker[id];
      return worker ? { kind: "agent", id: worker, label: `${id} · ${worker}` } : null;
    }
    default:
      return null;
  }
}
