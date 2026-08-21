import type { JarvisClient } from "./contracts";
import { MockJarvisClient } from "./mockClient";
import { RealJarvisRuntimeAdapter } from "./realClient";
import type { ClientMode } from "./sourceMode";

export type { ClientMode } from "./sourceMode";

export function resolveClientMode(): ClientMode {
  const mode = (import.meta.env.VITE_JARVIS_MODE ?? "simulated").toLowerCase();
  // An invalid explicit value must fail toward the real adapter, never toward
  // MockJarvisClient.  The mock remains available only when it is explicitly
  // selected or when the env var is absent for tests/demo mode.
  return mode === "simulated" ? "simulated" : "live";
}

export function createJarvisClient(): JarvisClient {
  if (resolveClientMode() === "live") {
    return new RealJarvisRuntimeAdapter();
  }
  return new MockJarvisClient();
}
