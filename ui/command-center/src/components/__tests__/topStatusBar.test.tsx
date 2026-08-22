import { render, screen, waitFor } from "@testing-library/react"
import { describe, expect, it } from "vitest"
import { MemoryRouter } from "react-router-dom"
import { CommandCenterProvider } from "../../context/CommandCenterContext"
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
})
