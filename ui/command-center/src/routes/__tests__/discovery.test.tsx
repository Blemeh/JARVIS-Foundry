import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { Discovery } from "../Discovery";
import { CommandCenterProvider } from "../../context/CommandCenterContext";
import { MockJarvisClient } from "../../lib/mockClient";
import { RealJarvisRuntimeAdapter } from "../../lib/realClient";
import type { FoundrySnapshot } from "../../lib/snapshotTypes";

/** Minimal legacy snapshot with NO discovery section (pre-projection backend). */
function legacySnapshot(): FoundrySnapshot {
  return {
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
}

function renderDiscovery() {
  return render(
    <MemoryRouter initialEntries={["/discovery"]}>
      <CommandCenterProvider>
        <Discovery />
      </CommandCenterProvider>
    </MemoryRouter>,
  );
}

describe("Discovery", () => {
  it("labels the cycle flag as EXPERIMENTAL and shows its OFF value", async () => {
    renderDiscovery();
    await waitFor(() => expect(screen.getByText("EXPERIMENTAL")).toBeInTheDocument());
    expect(screen.getByText(/discovery\.cycle_enabled = FALSE/)).toBeInTheDocument();
    expect(screen.getByText(/OFF — the unified V3 cycle is disabled/i)).toBeInTheDocument();
  });

  it("renders candidates with type, lifecycle badge, and rejection reason", async () => {
    renderDiscovery();
    await waitFor(() =>
      expect(screen.getByText(/Fix support semantics: handoff claims marked UNSUPPORTED/)).toBeInTheDocument(),
    );
    expect(screen.getAllByText(/BELOW_THRESHOLD/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/policy v3-policy-2026-08-21/).length).toBeGreaterThan(0);
    expect(screen.getByText(/superseded_by cand-f44d92ea01bb/)).toBeInTheDocument();
  });

  it("shows total score and dimension breakdown for scored candidates", async () => {
    renderDiscovery();
    await waitFor(() => {
      const scoreHead = screen.getAllByText("Score")[0]!.closest("div");
      expect(scoreHead!.textContent).toContain("total 0.58");
      expect(scoreHead!.textContent).toContain("pass ≥ 40%");
    });
    expect(screen.getAllByText("Strategic fit").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Opportunity cost").length).toBeGreaterThan(0);
  });

  it("shows the executive selection with rejected alternatives", async () => {
    renderDiscovery();
    await waitFor(() => expect(screen.getByText(/Executive selection/)).toBeInTheDocument());
    expect(screen.getByText(/dec-v3-000117/)).toBeInTheDocument();
    expect(screen.getByText(/Rejected alternatives/)).toBeInTheDocument();
  });

  it("shows honest idle reason for WAITING cycles and STOP for stopped ones", async () => {
    renderDiscovery();
    await waitFor(() =>
      expect(screen.getByText(/All candidates below min_total_score 0.40 after filters./)).toBeInTheDocument(),
    );
    expect(screen.getByText(/STOP active: emergency stop engaged by owner/)).toBeInTheDocument();
  });

  it("keeps predicted and actual distinct when measurement is pending", async () => {
    renderDiscovery();
    await waitFor(() => expect(screen.getByText(/UNKNOWN — measurement pending/)).toBeInTheDocument());
    const recorded = screen.getByText(/delta -0.05/).closest("p");
    expect(recorded!.textContent).toContain("expected_success 0.64");
    expect(recorded!.textContent).toContain("Actual success true");
  });

  it("links candidate lineage to its mission", async () => {
    renderDiscovery();
    await waitFor(() => {
      const links = screen.getAllByRole("link");
      expect(links.some((l) => l.getAttribute("href") === "/missions/M-HYB-014")).toBe(true);
    });
  });
});

describe("V3 discovery adapter boundary", () => {
  it("mock serves the full simulated projection", async () => {
    const client = new MockJarvisClient();
    const view = await client.getDiscovery();
    expect(view.available).toBe(true);
    if (view.available) {
      expect(view.flag.enabled).toBe(false);
      expect(view.candidatesRecent.length).toBeGreaterThan(0);
      expect(view.policyVersion).toBe("v3-policy-2026-08-21");
    }
  });

  it("live adapter reports projection unavailable on a legacy snapshot without fixtures", async () => {
    // Legacy backend: fetch succeeds but ships NO discovery section.
    vi.stubGlobal("fetch", vi.fn(async () =>
      new Response(JSON.stringify(legacySnapshot()), { status: 200, headers: { "Content-Type": "application/json" } }),
    ));
    const client = new RealJarvisRuntimeAdapter({ apiBase: "/api", pollMs: 60000 });
    const view = await client.getDiscovery();
    expect(view).toEqual({ available: false, reason: "PROJECTION_NOT_AVAILABLE" });
  });
});
