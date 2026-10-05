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

    expect(screen.getByRole("link", { name: "Jarvis" })).toHaveAttribute("href", "/")
    expect(screen.getByRole("link", { name: "Work" })).toHaveAttribute("href", "/work")
    expect(screen.getByRole("link", { name: "World" })).toHaveAttribute("href", "/world")
    expect(screen.getByRole("link", { name: "Capabilities" })).toHaveAttribute("href", "/capabilities")
    expect(screen.getByRole("link", { name: "You" })).toHaveAttribute("href", "/you")
    expect(screen.getByRole("link", { name: "Timeline" })).toHaveAttribute("href", "/timeline")
  })
})
