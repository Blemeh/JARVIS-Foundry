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

    expect(screen.getByRole("link", { name: "JARVIS" })).toHaveAttribute("href", "/")
    expect(screen.getByRole("link", { name: "WORK" })).toHaveAttribute("href", "/work")
    expect(screen.getByRole("link", { name: "WORLD" })).toHaveAttribute("href", "/world")
    expect(screen.getByRole("link", { name: "CAPABILITIES" })).toHaveAttribute("href", "/capabilities")
    expect(screen.getByRole("link", { name: "YOU" })).toHaveAttribute("href", "/you")
    expect(screen.getByRole("link", { name: "Timeline" })).toHaveAttribute("href", "/timeline")
  })
})
