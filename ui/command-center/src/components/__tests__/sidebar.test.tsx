import { render, screen } from "@testing-library/react"
import { describe, expect, it } from "vitest"
import { MemoryRouter } from "react-router-dom"
import { CommandCenterProvider } from "../../context/CommandCenterContext"
import { SidebarContent } from "../Sidebar"

describe("Sidebar owner workflow", () => {
  it("labels OWNER-01 as a simulated fixture persona", () => {
    render(
      <MemoryRouter>
        <CommandCenterProvider>
          <SidebarContent />
        </CommandCenterProvider>
      </MemoryRouter>,
    )

    expect(screen.getByText(/ID: OWNER-01 · SIMULATED/i)).toBeInTheDocument()
  })
})
