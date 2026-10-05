import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { describe, expect, it } from "vitest"
import { MemoryRouter } from "react-router-dom"
import { CommandCenterProvider } from "../../context/CommandCenterContext"
import { TopStatusBar } from "../TopStatusBar"

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
