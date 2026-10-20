import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { JarvisClient } from "../../lib/contracts";
import { RealJarvisRuntimeAdapter } from "../../lib/realClient";
import type { FoundrySnapshot } from "../../lib/snapshotTypes";

const clientState = vi.hoisted(() => ({ current: null as JarvisClient | null }));

vi.mock("../../lib/clientFactory", () => ({
  createJarvisClient: () => {
    if (!clientState.current) throw new Error("test client is not configured");
    return clientState.current;
  },
  resolveClientMode: () => "live",
}));

import { CommandCenterProvider, useCommandCenter } from "../CommandCenterContext";

const suspendedWithoutStop: FoundrySnapshot = {
  generated_at: "2026-09-03T12:00:00Z",
  source_of_truth: {
    kind: "append_only_event_ledger",
    ledger_head_sequence: 9,
    event_count: 9,
  },
  supervisor: {},
  system_state: {},
  stop: { active: false, record: null, source: "sentinel_or_durable_control" },
  controls: {
    goals: [],
    constraints: [],
    vetoes: [],
    reprioritizations: [],
    redirects: [],
    paused: false,
    resume_available: false,
    runtime_state: "RUNNING",
  },
  missions: [],
  workers: [],
  evidence: [],
  verification: [],
  health: {
    supervisor_status: "RUNNING",
    trust_state: "SUSPENDED",
    degraded: false,
  },
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
  owner_directives: { records: [], active: [], unknown: [] },
  decision_queue: { open: [], history: [], open_count: 0, history_count: 0 },
};

function Probe() {
  const { stopActive, streamConnected, reconnectStream } = useCommandCenter();
  return (
    <>
      <span>{stopActive ? "STOPPED" : "STOP CLEAR"}</span>
      <span>{streamConnected ? "CONNECTED" : "DISCONNECTED"}</span>
      <button type="button" onClick={() => void reconnectStream()}>Reconnect probe</button>
    </>
  );
}

describe("CommandCenterProvider live authority boundaries", () => {
  beforeEach(() => {
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo) => {
      const url = String(input);
      if (url.endsWith("/command-center")) {
        return new Response(JSON.stringify(suspendedWithoutStop), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response("not found", { status: 404 });
    }));
  });

  it("keeps trust suspension separate from canonical STOP and reconnects live clients", async () => {
    const client = new RealJarvisRuntimeAdapter({ apiBase: "/api", pollMs: 60000 });
    const reconnect = vi.spyOn(client, "reconnectStream");
    clientState.current = client;

    render(
      <CommandCenterProvider>
        <Probe />
      </CommandCenterProvider>,
    );

    await waitFor(() => expect(screen.getByText("STOP CLEAR")).toBeInTheDocument());
    await waitFor(() => expect(screen.getByText("CONNECTED")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "Reconnect probe" }));
    await waitFor(() => expect(reconnect).toHaveBeenCalledTimes(1));
  });
  it("reconciles trust and routes after polling recovers without any new ledger event", async () => {
    let online = false;
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo) => {
      if (!online) return new Response("offline", { status: 503 });
      if (String(input).includes("/timeline?")) return new Response(JSON.stringify({
        schema: "v13.command-center-event-page.v1", events: [], next_sequence: 9,
        ledger_head_sequence: 9, has_more: false,
      }));
      return new Response(JSON.stringify(suspendedWithoutStop));
    }));
    clientState.current = new RealJarvisRuntimeAdapter({ apiBase: "/api", pollMs: 250 });
    function RecoveryProbe() {
      const { trustSummaryStatus, version } = useCommandCenter();
      return <span>{trustSummaryStatus}:{version}</span>;
    }
    render(<CommandCenterProvider><RecoveryProbe /></CommandCenterProvider>);
    await waitFor(() => expect(screen.getByText("error:0")).toBeInTheDocument());
    online = true;
    await waitFor(() => expect(screen.getByText(/^ready:[1-9]/)).toBeInTheDocument(), { timeout: 3000 });
  });

  it("allows fresh canonical owner commands during degraded health while keeping ordinary effects gated", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => new Response(JSON.stringify({ ...suspendedWithoutStop, health: { ...suspendedWithoutStop.health, degraded: true } }))));
    clientState.current = new RealJarvisRuntimeAdapter({ apiBase: "/api", pollMs: 60000 });
    function CommandAvailabilityProbe() {
      const { ownerCommandsEnabled, mutationsEnabled } = useCommandCenter();
      return <span>{ownerCommandsEnabled ? "OWNER AVAILABLE" : "OWNER HELD"}:{mutationsEnabled ? "EFFECTS AVAILABLE" : "EFFECTS HELD"}</span>;
    }
    render(<CommandCenterProvider><CommandAvailabilityProbe /></CommandCenterProvider>);
    await waitFor(() => expect(screen.getByText("OWNER AVAILABLE:EFFECTS HELD")).toBeInTheDocument());
  });

});
