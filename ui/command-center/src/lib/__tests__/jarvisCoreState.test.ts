import { describe, expect, it } from "vitest";
import { deriveJarvisCoreState, JARVIS_CORE_STATE_LABEL } from "../jarvisCoreState";

describe("deriveJarvisCoreState", () => {
  it("maps STOP before other signals", () => {
    expect(
      deriveJarvisCoreState({
        stopActive: true,
        sourceMode: "LIVE",
        streamConnected: true,
        activeMissionCount: 3,
      }),
    ).toBe("STOPPED");
  });

  it("maps disconnected source and stream loss", () => {
    expect(
      deriveJarvisCoreState({
        stopActive: false,
        sourceMode: "DISCONNECTED",
        streamConnected: true,
      }),
    ).toBe("DISCONNECTED");

    expect(
      deriveJarvisCoreState({
        stopActive: false,
        sourceMode: "SIMULATED",
        streamConnected: false,
      }),
    ).toBe("DISCONNECTED");
  });

  it("maps degraded trust and source modes", () => {
    expect(
      deriveJarvisCoreState({
        stopActive: false,
        sourceMode: "DEGRADED",
        streamConnected: true,
      }),
    ).toBe("DEGRADED");

    expect(
      deriveJarvisCoreState({
        stopActive: false,
        sourceMode: "SIMULATED",
        streamConnected: true,
        trustHealth: "DEGRADED",
      }),
    ).toBe("DEGRADED");
  });

  it("maps verifying and active mission states", () => {
    expect(
      deriveJarvisCoreState({
        stopActive: false,
        sourceMode: "SIMULATED",
        streamConnected: true,
        verifyingCount: 2,
        activeMissionCount: 1,
      }),
    ).toBe("VERIFYING");

    expect(
      deriveJarvisCoreState({
        stopActive: false,
        sourceMode: "SIMULATED",
        streamConnected: true,
        activeMissionCount: 2,
      }),
    ).toBe("ACTIVE_MISSION");
  });

  it("defaults to healthy idle", () => {
    expect(
      deriveJarvisCoreState({
        stopActive: false,
        sourceMode: "SIMULATED",
        streamConnected: true,
      }),
    ).toBe("HEALTHY_IDLE");
    expect(JARVIS_CORE_STATE_LABEL.HEALTHY_IDLE).toBe("HEALTHY / IDLE");
  });
});
