import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { RealJarvisRuntimeAdapter } from "../realClient";
import type { FoundrySnapshot } from "../snapshotTypes";

const snapshot: FoundrySnapshot = {
  generated_at: "2026-08-20T12:00:00Z",
  source_of_truth: { kind: "append_only_event_ledger", ledger_head_sequence: 1, event_count: 1 },
  supervisor: {},
  system_state: {},
  stop: { active: false, record: null, source: "sentinel_or_durable_control" },
  controls: { goals: [], constraints: [], vetoes: [], reprioritizations: [], redirects: [], paused: false, resume_available: false },
  missions: [],
  workers: [],
  evidence: [],
  verification: [],
  health: { supervisor_status: "RUNNING", trust_state: "LAB", degraded: false },
  systems: [],
  memory: [],
  procedures: [],
  mission_steps: [],
  timeline: [],
  release_identity: {},
  costs: {},
  executive: {
    activation_epoch: null,
    admission_authority: "UNKNOWN",
    latest_decision: null,
    recent_decisions: [],
    calibrations: [],
  },
};

describe("RealJarvisRuntimeAdapter", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo) => {
      const url = String(input);
      if (url.endsWith("/command-center")) {
        return new Response(JSON.stringify(snapshot), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url.endsWith("/owner-command")) {
        return new Response(JSON.stringify({ status: "APPLIED", command_type: "GOAL", directive_id: "d-1", event_id: "e-1" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response("not found", { status: 404 });
    }));
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("reads live snapshot without mock fallback", async () => {
    const client = new RealJarvisRuntimeAdapter({ apiBase: "/api", pollMs: 60000 });
    expect(client.source).toBe("LIVE");
    const missions = await client.listMissions();
    expect(missions).toEqual([]);
    expect(client.connection.connected).toBe(true);
  });

  it("marks disconnected on fetch failure", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response("fail", { status: 503 })));
    const client = new RealJarvisRuntimeAdapter({ apiBase: "/api", pollMs: 60000 });
    await expect(client.listMissions()).rejects.toThrow();
    expect(client.connection.source).toBe("DISCONNECTED");
  });

  it("deduplicates concurrent live snapshot reads", async () => {
    let calls = 0;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo) => {
      calls += 1;
      await new Promise((resolve) => setTimeout(resolve, 5));
      expect(String(input)).toBe("/api/command-center");
      return new Response(JSON.stringify(snapshot), { status: 200, headers: { "Content-Type": "application/json" } });
    }));
    const client = new RealJarvisRuntimeAdapter({ apiBase: "/api", pollMs: 60000 });
    await Promise.all([client.listMissions(), client.listWorkers(), client.listNotifications()]);
    expect(calls).toBe(1);
    expect(client.connection.source).toBe("LIVE");
    expect(client.connection.connected).toBe(true);
  });

  it("submits owner goal through owner-command endpoint", async () => {
    const client = new RealJarvisRuntimeAdapter({ apiBase: "/api", pollMs: 60000 });
    const result = await client.submitOwnerGoal("integration probe objective");
    expect(result.accepted).toBe(true);
    expect(result.directiveId).toBe("d-1");
  });
});
