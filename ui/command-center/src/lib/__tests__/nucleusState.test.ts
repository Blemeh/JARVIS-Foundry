import { describe, expect, it } from "vitest";
import { resolveNucleusState, type NucleusStateSignals } from "../nucleusState";

const base: NucleusStateSignals = {
  stopActive: false,
  sourceMode: "LIVE",
  streamConnected: true,
  connectionStale: false,
  ownerAttention: false,
  scenario: null,
  executiveState: "WAITING",
  autonomyStatus: "AVAILABLE",
  activeMissionStatus: null,
};

describe("resolveNucleusState", () => {
  it("gives canonical STOP absolute precedence", () => {
    expect(resolveNucleusState({
      ...base,
      stopActive: true,
      connectionStale: true,
      ownerAttention: true,
      executiveState: "EXECUTING",
    })).toBe("STOPPED");
  });

  it("never projects stale or disconnected truth as executing", () => {
    expect(resolveNucleusState({
      ...base,
      connectionStale: true,
      activeMissionStatus: "ACTIVE",
      executiveState: "EXECUTING",
    })).toBe("UNKNOWN");
    expect(resolveNucleusState({
      ...base,
      sourceMode: "DISCONNECTED",
      activeMissionStatus: "ACTIVE",
    })).toBe("UNKNOWN");
  });

  it("uses deterministic owner, recovery, blocked, and verification precedence", () => {
    expect(resolveNucleusState({
      ...base,
      ownerAttention: true,
      executiveState: "RECOVERING",
    })).toBe("NEEDS_OWNER");
    expect(resolveNucleusState({
      ...base,
      executiveState: "RECOVERING",
      activeMissionStatus: "ACTIVE",
    })).toBe("RECOVERING");
    expect(resolveNucleusState({
      ...base,
      executiveState: "BLOCKED",
      activeMissionStatus: "ACTIVE",
    })).toBe("BLOCKED");
    expect(resolveNucleusState({
      ...base,
      executiveState: "VERIFYING",
      activeMissionStatus: "ACTIVE",
    })).toBe("VERIFYING");
  });

  it("keeps simulated scenario states out of live mode", () => {
    expect(resolveNucleusState({
      ...base,
      scenario: "RECOVERY",
    })).toBe("WAITING");
    expect(resolveNucleusState({
      ...base,
      sourceMode: "SIMULATED",
      scenario: "RECOVERY",
    })).toBe("RECOVERING");
  });

  it("uses an active mission only as a fresh fallback", () => {
    expect(resolveNucleusState({
      ...base,
      executiveState: "UNKNOWN",
      activeMissionStatus: "IN_PROGRESS",
    })).toBe("EXECUTING");
    expect(resolveNucleusState({
      ...base,
      executiveState: "UNKNOWN",
      autonomyStatus: "UNAVAILABLE",
    })).toBe("UNKNOWN");
  });
});
