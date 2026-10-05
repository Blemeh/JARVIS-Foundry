import { render, screen } from "@testing-library/react"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { describe, expect, it } from "vitest"
import { DetailRouteQueryGate } from "../../components/DetailRouteQueryGate"
import { RouteQueryGate } from "../../components/RouteQueryGate"
import { CommandCenterProvider } from "../../context/CommandCenterContext"
import type { RouteQueryResult } from "../../hooks/useRouteQuery"
import { MissionDetail } from "../MissionDetail"
import { Timeline } from "../Timeline"

import type { ReactNode } from "react"

function wrap(ui: ReactNode) {
  return render(
    <MemoryRouter>
      <CommandCenterProvider>{ui}</CommandCenterProvider>
    </MemoryRouter>,
  )
}

describe("route query gates", () => {
  it("renders loading, error, and ready states for list routes", () => {
    const loading: RouteQueryResult<string[]> = {
      status: "loading",
      data: null,
      error: null,
      reload: () => undefined,
    }
    const { rerender } = wrap(
      <RouteQueryGate
        query={loading}
        loadingLabel="Loading missions"
        errorLabel="Missions unavailable"
        surface="Mission vectors"
      >
        {() => <div>ready</div>}
      </RouteQueryGate>,
    )
    expect(screen.getByText(/Loading missions/i)).toBeInTheDocument()

    rerender(
      <MemoryRouter>
        <CommandCenterProvider>
          <RouteQueryGate
            query={{ status: "error", data: null, error: "network down", reload: () => undefined }}
            loadingLabel="Loading missions"
            errorLabel="Missions unavailable"
            surface="Mission vectors"
          >
            {() => <div>ready</div>}
          </RouteQueryGate>
        </CommandCenterProvider>
      </MemoryRouter>,
    )
    expect(screen.getByText("Missions unavailable")).toBeInTheDocument()
    expect(screen.getByText("network down")).toBeInTheDocument()
  })

  it("distinguishes detail not-found from fetch errors", () => {
    wrap(
      <DetailRouteQueryGate
        query={{ status: "ready", data: null, error: null, reload: () => undefined }}
        loadingLabel="Loading mission detail"
        notFoundLabel="Mission not found"
        notFoundDetail="No simulated mission exists at this identifier."
        errorLabel="Mission detail unavailable"
        surface="Mission detail"
      >
        {() => <div>detail</div>}
      </DetailRouteQueryGate>,
    )
    expect(screen.getByText("Mission not found")).toBeInTheDocument()
    expect(screen.getByText("No simulated mission exists at this identifier.")).toBeInTheDocument()

    wrap(
      <DetailRouteQueryGate
        query={{ status: "error", data: null, error: "ledger read failed", reload: () => undefined }}
        loadingLabel="Loading mission detail"
        notFoundLabel="Mission not found"
        notFoundDetail="No simulated mission exists at this identifier."
        errorLabel="Mission detail unavailable"
        surface="Mission detail"
      >
        {() => <div>detail</div>}
      </DetailRouteQueryGate>,
    )
    expect(screen.getByText("Mission detail unavailable")).toBeInTheDocument()
    expect(screen.getByText("ledger read failed")).toBeInTheDocument()
  })
})

describe("resilient route surfaces", () => {
  it("loads timeline without hanging on the simulated client", async () => {
    render(
      <MemoryRouter initialEntries={["/timeline"]}>
        <CommandCenterProvider>
          <Timeline />
        </CommandCenterProvider>
      </MemoryRouter>,
    )
    expect(await screen.findByRole("heading", { name: "Activity" })).toBeInTheDocument()
  })

  it("shows not-found for unknown mission ids instead of infinite loading", async () => {
    render(
      <MemoryRouter initialEntries={["/missions/MISSING-999"]}>
        <CommandCenterProvider>
          <Routes>
            <Route path="/missions/:missionId" element={<MissionDetail />} />
          </Routes>
        </CommandCenterProvider>
      </MemoryRouter>,
    )
    expect(await screen.findByText("Mission not found")).toBeInTheDocument()
  })
})
