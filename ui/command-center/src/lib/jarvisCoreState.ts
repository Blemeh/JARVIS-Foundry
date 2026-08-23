import type { ResolvedSourceMode } from "./sourceMode";

export type JarvisCoreVisualState =
  | "HEALTHY_IDLE"
  | "ACTIVE_MISSION"
  | "VERIFYING"
  | "DEGRADED"
  | "STOPPED"
  | "DISCONNECTED";

export const JARVIS_CORE_STATE_LABEL: Record<JarvisCoreVisualState, string> = {
  HEALTHY_IDLE: "HEALTHY / IDLE",
  ACTIVE_MISSION: "ACTIVE MISSION",
  VERIFYING: "VERIFYING",
  DEGRADED: "DEGRADED",
  STOPPED: "STOPPED",
  DISCONNECTED: "DISCONNECTED",
};

type DeriveParams = {
  stopActive: boolean;
  sourceMode: ResolvedSourceMode;
  streamConnected: boolean;
  trustHealth?: string | null;
  activeMissionCount?: number;
  verifyingCount?: number;
};

export function deriveJarvisCoreState({
  stopActive,
  sourceMode,
  streamConnected,
  trustHealth,
  activeMissionCount = 0,
  verifyingCount = 0,
}: DeriveParams): JarvisCoreVisualState {
  if (stopActive) return "STOPPED";
  if (sourceMode === "DISCONNECTED" || !streamConnected) return "DISCONNECTED";
  if (sourceMode === "DEGRADED" || sourceMode === "UNKNOWN") return "DEGRADED";
  if (trustHealth === "DEGRADED") return "DEGRADED";
  if (verifyingCount > 0) return "VERIFYING";
  if (activeMissionCount > 0) return "ACTIVE_MISSION";
  return "HEALTHY_IDLE";
}

export function jarvisCoreStateClass(state: JarvisCoreVisualState): string {
  return `jarvis-core--${state.toLowerCase().replace(/_/g, "-")}`;
}
