import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
const state = vi.hoisted(() => ({
  sourceMode: "LIVE", stopActive: false, ownerCommandsEnabled: true, streamConnected: true,
  connection: { connected: true, stale: false, source: "LIVE", error: null },
  trustSummary: { state: "SUSPENDED" }, trustSummaryStatus: "ready", version: 0,
  client: {
    getSystemOverview: vi.fn(async () => ({ releaseState: "RUNNING", schema: 41 })),
    submitVeto: vi.fn(async () => ({ accepted: true, directiveId: "veto-durable" })),
    submitOwnerGoal: vi.fn(async () => ({ accepted: true, directiveId: "ask-durable" })),
    search: vi.fn(async () => ({ missions: [], agents: [], evidence: [], systems: [], memory: [], timeline: [] })),
  },
}));
vi.mock("../../context/CommandCenterContext", () => ({ useCommandCenter: () => state }));
vi.mock("../EmergencyStop", () => ({ StopControl: () => null }));
vi.mock("../TrustDialog", () => ({ TrustDialog: () => null }));
vi.mock("../NotificationBell", () => ({ NotificationBell: () => null }));
import { CommandBar } from "../CommandBar";
import { TopStatusBar } from "../TopStatusBar";

describe("owner command and health truth", () => {
  beforeEach(() => {
    state.sourceMode = "LIVE";
    state.connection.source = "LIVE";
    state.ownerCommandsEnabled = true;
    vi.clearAllMocks();
  });
  it("shows healthy suspended runtime as active with trust separate", async () => {
    render(<MemoryRouter><TopStatusBar onOpenMobileNav={() => undefined} /></MemoryRouter>);
    await waitFor(() => expect(screen.getByText("JARVIS ACTIVE")).toBeInTheDocument());
    expect(screen.getByRole("button", { name: /trust suspended/i })).toBeInTheDocument();
  });
  it("routes VETO to canonical veto submission while operational health is degraded", async () => {
    state.sourceMode = "DEGRADED";
    state.connection.source = "DEGRADED";
    const user = userEvent.setup();
    render(<MemoryRouter><CommandBar /></MemoryRouter>);
    await user.click(screen.getByRole("button", { name: /VETO/ }));
    await user.type(screen.getByRole("combobox", { name: /Message JARVIS/ }), "Do not contact external people");
    await user.click(screen.getByRole("button", { name: "Send command" }));
    expect(state.client.submitVeto).toHaveBeenCalledWith("Do not contact external people");
    expect(state.client.submitOwnerGoal).not.toHaveBeenCalled();
    expect(await screen.findByText(/Owner command recorded.*veto-durable/)).toBeInTheDocument();
  });
  it("routes ASK to the existing TELL_ME owner mode", async () => {
    const user = userEvent.setup();
    render(<MemoryRouter><CommandBar /></MemoryRouter>);
    await user.type(screen.getByRole("combobox", { name: /Message JARVIS/ }), "What requires my attention?");
    await user.click(screen.getByRole("button", { name: "Send command" }));
    expect(state.client.submitOwnerGoal).toHaveBeenCalledWith("What requires my attention?", "TELL_ME");
  });
});
