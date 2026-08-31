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
  it("shows UNKNOWN confidence when executive prediction is absent", async () => {
    renderOverview()
    await waitFor(() => expect(screen.getByText("Confidence")).toBeInTheDocument())
    expect(screen.getAllByText("UNKNOWN").length).toBeGreaterThan(0)
  })

  it("renders the GOD MODE command surface", async () => {
    renderOverview()
    await waitFor(() => expect(screen.getByRole("heading", { name: "Command JARVIS" })).toBeInTheDocument())
    expect(screen.getByPlaceholderText(/Tell JARVIS what you want/i)).toBeInTheDocument()
  })

  it("renders active operations and intelligence stream", async () => {
    renderOverview()
    await waitFor(() => expect(screen.getByText("Active operations")).toBeInTheDocument())
    expect(screen.getByText("Intelligence stream")).toBeInTheDocument()
  })
})
