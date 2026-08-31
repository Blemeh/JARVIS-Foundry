import { render, screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"
import { MemoryRouter } from "react-router-dom"
import { CommandCenterProvider } from "../../context/CommandCenterContext"
import { NavRailContent } from "../NavRail"

describe("NavRail", () => {
  it("renders primary navigation sections in drawer mode", () => {
    render(
      <MemoryRouter>
        <CommandCenterProvider>
          <NavRailContent variant="drawer" />
        </CommandCenterProvider>
      </MemoryRouter>,
    )

    expect(screen.getByText("JARVIS")).toBeInTheDocument()
    expect(screen.getByText("Timeline")).toBeInTheDocument()
    expect(screen.getByText("Missions")).toBeInTheDocument()
  })
})
