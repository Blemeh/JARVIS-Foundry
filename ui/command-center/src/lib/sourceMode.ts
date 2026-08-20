import type { ConnectionState } from "./contracts";

export type ClientMode = "simulated" | "live";

/**
 * Explicit source contract for every Command Center surface.
 *
 * The client mode describes which adapter was selected; the connection state
 * describes whether that adapter has a trustworthy current response.  A live
 * adapter that has not connected is therefore never represented as LIVE, and
 * a disconnected live adapter is never allowed to fall back to fixtures.
 */
export type ResolvedSourceMode = "LIVE" | "SIMULATED" | "DISCONNECTED" | "DEGRADED" | "UNKNOWN";

export function resolveSourceMode(mode: ClientMode, connection: ConnectionState): ResolvedSourceMode {
  if (mode === "simulated") return "SIMULATED";
  if (!connection.connected || connection.source === "DISCONNECTED") return "DISCONNECTED";
  if (connection.source === "DEGRADED" || connection.stale) return "DEGRADED";
  if (connection.source === "LIVE") return "LIVE";
  return "UNKNOWN";
}

export function sourceCopy(
  source: ResolvedSourceMode,
  copy: { live: string; simulated: string; unavailable: string },
): string {
  if (source === "LIVE") return copy.live;
  if (source === "SIMULATED") return copy.simulated;
  return copy.unavailable;
}

export function sourceModeLabel(source: ResolvedSourceMode): string {
  return source;
}
