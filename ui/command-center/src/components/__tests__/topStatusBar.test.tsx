import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"
import { MemoryRouter } from "react-router-dom"
import { CommandCenterProvider } from "../../context/CommandCenterContext"
import { ScenarioBootstrap } from "../../ScenarioBootstrap"
import { TopStatusBar } from "../TopStatusBar"

describe("TopStatusBar owner workflow", () => {
  it("shows trust summary state instead of data source in trust pill", async () => {
    render(
      <MemoryRouter>
        <CommandCenterProvider>
          <TopStatusBar onOpenMobileNav={() => undefined} />
        </CommandCenterProvider>
      </MemoryRouter>,
    )

    const trustButton = await screen.findByRole("button", { name: /Trust state/i })
    await waitFor(() => {
      expect(trustButton.textContent).toMatch(/SIMULATED|DEGRADED|PROVEN|SUSPENDED/)
    })
    expect(screen.getByText(/Data Source: SIMULATED/i)).toBeInTheDocument()
  })

  it("shows stream disconnected status when event stream is down", async () => {
    render(
      <MemoryRouter initialEntries={["/?scenario=STREAM_DISCONNECTED"]}>
        <CommandCenterProvider>
          <ScenarioBootstrap />
          <TopStatusBar onOpenMobileNav={() => undefined} />
        </CommandCenterProvider>
      </MemoryRouter>,
    )

    expect(await screen.findByRole("status", { name: /Stream disconnected/i })).toBeInTheDocument()

    await userEvent.click(screen.getByRole("button", { name: /Reconnect simulated event stream/i }))
    await waitFor(() => {
      expect(screen.queryByRole("status", { name: /Stream disconnected/i })).not.toBeInTheDocument()
    })
  })
})
