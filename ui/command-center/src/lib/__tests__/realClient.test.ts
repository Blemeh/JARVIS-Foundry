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

function withTimeline(
  sequence: number,
  timeline: FoundrySnapshot["timeline"],
): FoundrySnapshot {
  return {
    ...snapshot,
    generated_at: `2026-09-03T12:00:${String(sequence).padStart(2, "0")}Z`,
    source_of_truth: {
      ...snapshot.source_of_truth,
      ledger_head_sequence: sequence,
      event_count: sequence,
    },
    timeline,
  };
}

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
      if (url.endsWith("/control-session")) {
        return new Response(JSON.stringify({ status: "READY", transport: "http_only_session" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url.includes("/timeline?")) {
        return new Response(JSON.stringify({
          schema: "v13.command-center-event-page.v1",
          after_sequence: snapshot.source_of_truth.ledger_head_sequence,
          next_sequence: snapshot.source_of_truth.ledger_head_sequence,
          ledger_head_sequence: snapshot.source_of_truth.ledger_head_sequence,
          has_more: false,
          events: [],
        }), { status: 200, headers: { "Content-Type": "application/json" } });
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

  it("rejects a stale snapshot and reconnects to newer durable truth", async () => {
    const responses = [
      withTimeline(4, []),
      withTimeline(3, []),
      withTimeline(5, []),
    ];
    vi.stubGlobal("fetch", vi.fn(async () =>
      new Response(JSON.stringify(responses.shift()), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
    ));
    const client = new RealJarvisRuntimeAdapter({ apiBase: "/api", pollMs: 60000 });

    await client.fetchSnapshot();
    await expect(client.reconnectStream()).rejects.toThrow(
      "stale snapshot sequence 3 is behind accepted sequence 4",
    );
    expect(client.connection.source).toBe("DISCONNECTED");

    await client.reconnectStream();
    expect(client.connection.connected).toBe(true);
    expect(client.connection.source).toBe("LIVE");
  });

  it("emits incremental events once in ledger order", async () => {
    vi.useFakeTimers();
    const old = {
      sequence: 1,
      event_id: "event-1",
      event_type: "mission.started",
      occurred_at: "2026-09-03T12:00:01Z",
      recorded_at: "2026-09-03T12:00:01Z",
      stage: "mission",
      summary: "old",
    };
    const second = {
      ...old,
      sequence: 2,
      event_id: "event-2",
      occurred_at: "2026-09-03T12:00:03Z",
      recorded_at: "2026-09-03T12:00:03Z",
      summary: "second",
    };
    const third = {
      ...old,
      sequence: 3,
      event_id: "event-3",
      occurred_at: "2026-09-03T12:00:02Z",
      recorded_at: "2026-09-03T12:00:02Z",
      summary: "third",
    };
    let snapshotCalls = 0;
    let timelineCalls = 0;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo) => {
      const url = String(input);
      if (url.endsWith("/command-center")) {
        snapshotCalls += 1;
        const value = snapshotCalls === 1
          ? withTimeline(1, [old])
          : withTimeline(3, [third, second, old]);
        return new Response(JSON.stringify(value), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url.includes("/timeline?")) {
        timelineCalls += 1;
        const value = timelineCalls === 1
          ? {
              schema: "v13.command-center-event-page.v1",
              after_sequence: 1,
              next_sequence: 2,
              ledger_head_sequence: 3,
              has_more: true,
              events: [second, second],
            }
          : timelineCalls === 2
            ? {
                schema: "v13.command-center-event-page.v1",
                after_sequence: 2,
                next_sequence: 3,
                ledger_head_sequence: 3,
                has_more: false,
                events: [third, second],
              }
            : {
                schema: "v13.command-center-event-page.v1",
                after_sequence: 3,
                next_sequence: 3,
                ledger_head_sequence: 3,
                has_more: false,
                events: [],
              };
        return new Response(JSON.stringify(value), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response("not found", { status: 404 });
    }));
    const client = new RealJarvisRuntimeAdapter({ apiBase: "/api", pollMs: 20 });
    await client.fetchSnapshot();
    const received: string[] = [];
    const stream = client.subscribeEvents();
    const unsubscribe = stream.subscribe((event) => received.push(event.id));

    await vi.advanceTimersByTimeAsync(20);

    expect(received).toEqual(["event-2", "event-3"]);
    unsubscribe();
    vi.useRealTimers();
  });

  it("submits delegation mode through owner-command metadata", async () => {
    let ownerBody: Record<string, unknown> | null = null;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/control-session")) {
        expect(init?.credentials).toBe("include");
        return new Response(JSON.stringify({ status: "READY" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url.endsWith("/owner-command")) {
        expect(init?.credentials).toBe("include");
        ownerBody = JSON.parse(String(init?.body)) as Record<string, unknown>;
        return new Response(JSON.stringify({
          status: "APPLIED",
          command_type: "DELEGATE",
          directive_id: "d-1",
          event_id: "e-1",
        }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url.endsWith("/command-center")) {
        return new Response(JSON.stringify(snapshot), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response("not found", { status: 404 });
    }));
    const client = new RealJarvisRuntimeAdapter({ apiBase: "/api", pollMs: 60000 });
    const result = await client.submitOwnerGoal(
      "integration probe objective",
      "PREPARE",
    );
    expect(result.accepted).toBe(true);
    expect(result.directiveId).toBe("d-1");
    expect(ownerBody).toEqual({
      command_type: "DELEGATE",
      exact_text: "integration probe objective",
      target: "GLOBAL",
      metadata: {
        mode: "PREPARE",
        request_id: expect.stringMatching(/.+/),
      },
    });
  });

  it("resumes only one explicit owner hold", async () => {
    let ownerBody: Record<string, unknown> | null = null;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/control-session")) {
        return new Response(JSON.stringify({ status: "READY" }), { status: 200 });
      }
      if (url.endsWith("/owner-command")) {
        ownerBody = JSON.parse(String(init?.body)) as Record<string, unknown>;
        return new Response(JSON.stringify({ status: "APPLIED", command_type: "RESUME" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url.endsWith("/command-center")) {
        return new Response(JSON.stringify(snapshot), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response("not found", { status: 404 });
    }));
    const client = new RealJarvisRuntimeAdapter({ apiBase: "/api", pollMs: 60000 });

    await expect(client.resumeOperations()).resolves.toEqual({
      accepted: false,
      error: "Resume requires one exact active STOP or PAUSE directive.",
    });
    await expect(client.resumeOperations("directive-stop-1")).resolves.toEqual({
      accepted: true,
    });
    expect(ownerBody).toEqual({
      command_type: "RESUME",
      exact_text: "resume exact owner hold",
      target: "directive-stop-1",
    });
  });

  it("reads a bounded forensic record without mutating snapshot state", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo) => {
      expect(String(input)).toBe("/api/record/provider_attempt/attempt%2Ffixture")
      return new Response(JSON.stringify({
        schema: "v13.command-center-record.v1",
        status: "FOUND",
        kind: "provider_attempt",
        id: "attempt/fixture",
        record: {
          attempt_id: "attempt/fixture",
          mission_id: "mission-1",
          outcome: "COMPLETED",
          provider_ref: "receipt-1",
        },
      }), { status: 200, headers: { "Content-Type": "application/json" } })
    }))
    const client = new RealJarvisRuntimeAdapter({ apiBase: "/api", pollMs: 60000 })

    await expect(client.getForensicRecord("provider_attempt", "attempt/fixture")).resolves.toMatchObject({
      status: "FOUND",
      kind: "provider_attempt",
      id: "attempt/fixture",
      record: { outcome: "COMPLETED", provider_ref: "receipt-1" },
    })
  })

  it("requests the bounded autonomy endpoint with the selected filter", async () => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo) => {
      expect(String(input)).toBe("/api/autonomy?filter=rejected&limit=50&timeline_limit=100");
      return new Response(JSON.stringify({
        schema: "v11.command-center-autonomy.v1",
        status: "EMPTY",
        reason: null,
        generated_at: "2026-09-02T12:00:00Z",
        executive: { state: "WAIT", cycle_id: null, stage: null, last_observation_cycle: null, next_wake: null, wait_reason: null, active_wip: { value: null, status: "UNAVAILABLE" } },
        funnel: {}, candidates: [], sources: [], timeline: [], current_work: null,
        limits: { candidate_limit: 50, timeline_limit: 100 },
      }), { status: 200, headers: { "Content-Type": "application/json" } });
    }));
    const client = new RealJarvisRuntimeAdapter({ apiBase: "/api", pollMs: 60000 });
    const result = await client.getAutonomy("rejected");
    expect(result.status).toBe("EMPTY");
    expect(client.connection.connected).toBe(true);
  });

  it("submits typed voice text through the Command Center voice-text seam", async () => {
    let voiceBody: Record<string, unknown> | null = null;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/control-session")) {
        return new Response(JSON.stringify({ status: "READY" }), { status: 200 });
      }
      if (url.endsWith("/voice-text")) {
        voiceBody = JSON.parse(String(init?.body)) as Record<string, unknown>;
        return new Response(JSON.stringify({
          status: "COMPLETED",
          transcript: "ndalo",
          response_text: "U ndal.",
          command_type: "STOP_EVERYTHING",
          language: "sq",
          directive_id: "d-stop",
        }), { status: 200, headers: { "Content-Type": "application/json" } });
      }
      if (url.endsWith("/command-center")) {
        return new Response(JSON.stringify(snapshot), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response("not found", { status: 404 });
    }));
    const client = new RealJarvisRuntimeAdapter({ apiBase: "/api", pollMs: 60000 });
    await expect(client.submitVoiceText("ndalo", "sq")).resolves.toMatchObject({
      status: "COMPLETED",
      responseText: "U ndal.",
      commandType: "STOP_EVERYTHING",
      directiveId: "d-stop",
    });
    expect(voiceBody).toEqual({ text: "ndalo", language_hint: "sq" });
  });
});
