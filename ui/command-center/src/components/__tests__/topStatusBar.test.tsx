import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { afterEach, describe, expect, it, vi } from "vitest"
import { MemoryRouter } from "react-router-dom"
import { CommandCenterProvider } from "../../context/CommandCenterContext"
import { TopStatusBar } from "../TopStatusBar"
import { MockJarvisClient } from "../../lib/mockClient"
import type { AutonomyProjection, SystemOverview, TrustSummary } from "../../lib/contracts"

describe("TopStatusBar owner workflow", () => {
  it("keeps technical details inspectable inside the System popover", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <CommandCenterProvider>
          <TopStatusBar onOpenMobileNav={() => undefined} />
        </CommandCenterProvider>
      </MemoryRouter>,
    )

    // Prime UI stays minimal: activity + notifications + stop, no constant schema pill.
    expect(await screen.findByText("Jarvis")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "System" }));
    const trustButton = await screen.findByRole("button", { name: /Trust/i })
    await waitFor(() => {
      expect(trustButton.textContent).toMatch(/NOMINAL|DEGRADED|UNAVAILABLE|LOADING|STOP|SUSPENDED|SIMULATED|PROVEN/i)
    })
    expect(screen.getByText(/DATA SOURCE: SIMULATED/i)).toBeInTheDocument()
    expect(screen.getAllByText(/Schema/i).length).toBeGreaterThan(0)
  })

  it("shows JARVIS brand in top bar", async () => {
    render(
      <MemoryRouter>
        <CommandCenterProvider>
          <TopStatusBar onOpenMobileNav={() => undefined} />
        </CommandCenterProvider>
      </MemoryRouter>,
    )

    expect(await screen.findByText("Jarvis")).toBeInTheDocument()
  })
})

describe("TopStatusBar health/trust separation (Task 6)", () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("renders System, Autonomy, and Trust as separate indicators; SUSPENDED trust carries the permitted-work explanation and never reads as system-down", async () => {
    const suspendedTrust: TrustSummary = {
      state: "SUSPENDED",
      score: null,
      gateStatus: "GATED",
      evidenceCoverage: 42,
      unresolvedFindings: 1,
      health: "HEALTHY",
      lastVerifiedAt: "2026-10-08T12:00:00Z",
      factors: [
        { id: "supervisor", label: "Supervisor", state: "PASS", detail: "RUNNING" },
        { id: "stop", label: "STOP sentinel", state: "PASS", detail: "CLEAR" },
      ],
    }
    const healthyOverview: SystemOverview = {
      version: "test",
      schema: 1,
      build: "7",
      releaseState: "RUNNING",
      uptime: "2026-10-08T12:00:00Z",
      resourceUsage: [],
    }
    const runningAutonomy: AutonomyProjection = {
      schema: "v11.command-center-autonomy.v1",
      status: "AVAILABLE",
      reason: null,
      generated_at: "2026-10-08T12:00:00Z",
      executive: {
        state: "EXECUTING",
        cycle_id: null,
        stage: null,
        last_observation_cycle: null,
        next_wake: null,
        wait_reason: null,
        active_wip: { value: null, status: "AVAILABLE" },
      },
      funnel: {},
      candidates: [],
      sources: [],
      timeline: [],
      current_work: null,
      limits: { candidate_limit: 50, timeline_limit: 100 },
    }
    vi.spyOn(MockJarvisClient.prototype, "getTrustSummary").mockResolvedValue(suspendedTrust)
    vi.spyOn(MockJarvisClient.prototype, "getSystemOverview").mockResolvedValue(healthyOverview)
    vi.spyOn(MockJarvisClient.prototype, "getAutonomy").mockResolvedValue(runningAutonomy)

    const user = userEvent.setup();
    render(
      <MemoryRouter>
        <CommandCenterProvider>
          <TopStatusBar onOpenMobileNav={() => undefined} />
        </CommandCenterProvider>
      </MemoryRouter>,
    )

    await user.click(screen.getByRole("button", { name: "System" }));
    const popover = await screen.findByRole("dialog", { name: "System details" });

    // Three labeled rows/indicators render separately.
    expect(popover.textContent).toMatch(/System/)
    expect(popover.textContent).toMatch(/Autonomy/)
    expect(popover.textContent).toMatch(/Trust/)
    await waitFor(() => expect(popover.textContent).toMatch(/Healthy/))
    expect(popover.textContent).toMatch(/Running/)
    expect(popover.textContent).toMatch(/Suspended/)
    // Trust SUSPENDED carries the permitted-work explanation.
    expect(popover.textContent).toMatch(/Permitted work can still run within constraints/)
    const trustButton = await screen.findByRole("button", { name: /Trust/i })
    expect(trustButton.getAttribute("title") ?? "").toMatch(/Permitted work can still run within constraints/)
    // Trust suspension never reads as system-down.
    expect(popover.textContent).not.toMatch(/system down|jarvis is dead/i)
    expect(popover.textContent).not.toMatch(/\bDown\b/)
  })
})
