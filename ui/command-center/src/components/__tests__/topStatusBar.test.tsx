import { render, screen, waitFor } from "@testing-library/react"
import { describe, expect, it } from "vitest"
import { MemoryRouter } from "react-router-dom"
import { CommandCenterProvider } from "../../context/CommandCenterContext"
import { TopStatusBar } from "../TopStatusBar"

describe("TopStatusBar owner workflow", () => {
  it("shows trust and live status in god mode top bar", async () => {
    render(
      <MemoryRouter>
        <CommandCenterProvider>
          <TopStatusBar onOpenMobileNav={() => undefined} />
        </CommandCenterProvider>
      </MemoryRouter>,
    )

    const trustButton = await screen.findByRole("button", { name: /Trust/i })
    await waitFor(() => {
      expect(trustButton.textContent).toMatch(/NOMINAL|DEGRADED|UNAVAILABLE|LOADING|STOP/i)
    })
    expect(screen.getByText("JARVIS ACTIVE", { exact: true })).toBeInTheDocument()
    expect(screen.getByText(/Schema/i)).toBeInTheDocument()
  })

  it("shows JARVIS brand in top bar", async () => {
    render(
      <MemoryRouter>
        <CommandCenterProvider>
          <TopStatusBar onOpenMobileNav={() => undefined} />
        </CommandCenterProvider>
      </MemoryRouter>,
    )

    expect(await screen.findByText("J.A.R.V.I.S.")).toBeInTheDocument()
  })
})
