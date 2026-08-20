import type { JarvisClient } from "./contracts";
import { MockJarvisClient } from "./mockClient";
import { RealJarvisRuntimeAdapter } from "./realClient";

export type ClientMode = "simulated" | "live";

export function resolveClientMode(): ClientMode {
  const mode = (import.meta.env.VITE_JARVIS_MODE ?? "simulated").toLowerCase();
  return mode === "live" ? "live" : "simulated";
}

export function createJarvisClient(): JarvisClient {
  if (resolveClientMode() === "live") {
    return new RealJarvisRuntimeAdapter();
  }
  return new MockJarvisClient();
}
