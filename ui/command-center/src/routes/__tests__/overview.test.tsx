import { render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { describe, expect, it } from "vitest"
import { Overview } from "../Overview"
import { CommandCenterProvider } from "../../context/CommandCenterContext"

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
  it("does not show fabricated currency when measured cost is absent", async () => {
    const { container } = renderOverview()
    await waitFor(() => expect(screen.getByLabelText(/Measured cost/i)).toBeInTheDocument())
    expect(container.textContent).not.toContain("$0.38")
    expect(screen.getByLabelText(/Measured cost UNKNOWN · SIMULATED/i)).toBeInTheDocument()
  })

  it("labels simulated source explicitly in the footer", async () => {
    renderOverview()
    await waitFor(() => expect(screen.getByText(/source SIMULATED/i)).toBeInTheDocument())
  })

  it("renders mission operations when partial data is still loading", async () => {
    renderOverview()
    await waitFor(() => expect(screen.getByRole("heading", { name: "Mission operations" })).toBeInTheDocument())
  })
})
