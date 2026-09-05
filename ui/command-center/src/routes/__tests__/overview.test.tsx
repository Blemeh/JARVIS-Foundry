import { render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { describe, expect, it } from "vitest"
import { Overview } from "../Overview"
import { CommandCenterProvider } from "../../context/CommandCenterContext"
import userEvent from "@testing-library/user-event"

function renderOverview() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <CommandCenterProvider>
        <Overview />
      </CommandCenterProvider>
    </MemoryRouter>,
  )
}

describe("Overview truth lane", () => {
  it("shows UNKNOWN confidence when executive prediction is absent", async () => {
    renderOverview()
    await waitFor(() => expect(screen.getByText(/Confidence UNKNOWN/)).toBeInTheDocument())
  })

  it("opens the current work in focus mode", async () => {
    renderOverview()
    await waitFor(() => expect(screen.getByRole("heading", { name: "Hybrid Worker Activation" })).toBeInTheDocument())
    await userEvent.click(screen.getByRole("button", { name: /Focus mode/i }))
    expect(screen.getByRole("dialog", { name: /Focus mode/i })).toBeInTheDocument()
    expect(screen.getByRole("region", { name: "Next transition" })).toBeInTheDocument()
    expect(screen.getByText("Mission and workers")).toBeInTheDocument()
    expect(screen.getByText("Verification")).toBeInTheDocument()
    expect(screen.getAllByText("SIMULATED").length).toBeGreaterThan(0)
  })

  it("renders today, completion, discovery, and next-action depth", async () => {
    renderOverview()
    await waitFor(() => expect(screen.getByRole("heading", { name: "Today" })).toBeInTheDocument())
    expect(screen.getByRole("heading", { name: "Recently completed" })).toBeInTheDocument()
    expect(screen.getByRole("heading", { name: "Discovered" })).toBeInTheDocument()
    expect(screen.getByRole("heading", { name: "What happens next" })).toBeInTheDocument()
  })
})
