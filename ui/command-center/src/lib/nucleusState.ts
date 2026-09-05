import type { MissionStatus, Scenario } from "./contracts";
import type { ResolvedSourceMode } from "./sourceMode";

export type JarvisNucleusState =
  | "WAITING"
  | "OBSERVING"
  | "PLANNING"
  | "RESEARCHING"
  | "EXECUTING"
  | "VERIFYING"
  | "RECOVERING"
  | "BLOCKED"
  | "NEEDS_OWNER"
  | "STOPPED"
  | "UNKNOWN";

export type NucleusStateSignals = {
  stopActive: boolean;
  sourceMode: ResolvedSourceMode;
  streamConnected: boolean;
  connectionStale: boolean;
  ownerAttention: boolean;
  scenario: Scenario | null;
  executiveState: string | null | undefined;
  autonomyStatus: "AVAILABLE" | "EMPTY" | "UNAVAILABLE";
  activeMissionStatus: MissionStatus | null;
};

export function resolveNucleusState({
  stopActive,
  sourceMode,
  streamConnected,
  connectionStale,
  ownerAttention,
  scenario,
  executiveState,
  autonomyStatus,
  activeMissionStatus,
}: NucleusStateSignals): JarvisNucleusState {
  if (stopActive) return "STOPPED";
  if (
    connectionStale
    || !streamConnected
    || sourceMode === "DISCONNECTED"
    || sourceMode === "UNKNOWN"
  ) return "UNKNOWN";
  if (ownerAttention) return "NEEDS_OWNER";
  if (sourceMode === "SIMULATED" && (scenario === "RECOVERY" || scenario === "WORKER_CRASH")) {
    return "RECOVERING";
  }
  if (sourceMode === "SIMULATED" && scenario === "MISSION_BLOCKED") return "BLOCKED";

  const raw = (executiveState ?? "").toUpperCase();
  if (raw.includes("OWNER")) return "NEEDS_OWNER";
  if (raw.includes("RECOVER")) return "RECOVERING";
  if (raw.includes("BLOCK")) return "BLOCKED";
  if (raw.includes("VERIF")) return "VERIFYING";
  if (raw.includes("EXECUT") || raw.includes("WORK")) return "EXECUTING";
  if (raw.includes("RESEARCH")) return "RESEARCHING";
  if (raw.includes("THINK") || raw.includes("PLAN") || raw.includes("JUDG")) return "PLANNING";
  if (raw.includes("OBSERV")) return "OBSERVING";
  if (raw.includes("WAIT") || raw.includes("IDLE")) return "WAITING";
  if (
    activeMissionStatus
    && ["ACTIVE", "IN_PROGRESS", "INTERVENTION"].includes(activeMissionStatus)
  ) return "EXECUTING";
  return autonomyStatus === "UNAVAILABLE" ? "UNKNOWN" : "WAITING";
}
