import { describe, expect, it } from "vitest";
import type { ConnectionState } from "../contracts";
import { resolveSourceMode, sourceCopy } from "../sourceMode";

const connection = (overrides: Partial<ConnectionState> = {}): ConnectionState => ({
  connected: true,
  source: "LIVE",
  stale: false,
  lastFetchedAt: "2026-08-20T12:00:00Z",
  error: null,
  ...overrides,
});

describe("explicit Command Center source mode", () => {
  it("labels an explicit mock client SIMULATED even when it has data", () => {
    expect(resolveSourceMode("simulated", connection({ source: "SIMULATED" }))).toBe("SIMULATED");
  });

  it("labels a connected canonical runtime LIVE", () => {
    expect(resolveSourceMode("live", connection())).toBe("LIVE");
  });

  it("fails closed as DISCONNECTED when the live bridge is unavailable", () => {
    expect(resolveSourceMode("live", connection({ connected: false, source: "DISCONNECTED", lastFetchedAt: null, error: "connection refused" }))).toBe("DISCONNECTED");
  });

  it("distinguishes a stale or degraded runtime from live data", () => {
    expect(resolveSourceMode("live", connection({ stale: true }))).toBe("DEGRADED");
    expect(resolveSourceMode("live", connection({ source: "DEGRADED" }))).toBe("DEGRADED");
  });

  it("preserves an unknown source as UNKNOWN instead of inventing a live state", () => {
    expect(resolveSourceMode("live", connection({ source: "UNKNOWN" }))).toBe("UNKNOWN");
  });

  it("never substitutes simulated copy for an unavailable source", () => {
    const copy = { live: "canonical", simulated: "fixture", unavailable: "unavailable" };
    expect(sourceCopy("LIVE", copy)).toBe("canonical");
    expect(sourceCopy("SIMULATED", copy)).toBe("fixture");
    expect(sourceCopy("DISCONNECTED", copy)).toBe("unavailable");
  });
});
