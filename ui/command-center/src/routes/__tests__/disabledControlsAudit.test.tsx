import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { describe, expect, it } from "vitest"
import { CommandCenterProvider } from "../../context/CommandCenterContext"
import { Overview } from "../Overview"
import { Evidence } from "../Evidence"
import { Agents } from "../Agents"
import { MissionDetail } from "../MissionDetail"

function renderRoute(path: string, routePath: string, element: React.ReactNode) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <CommandCenterProvider>
        <Routes>
          <Route path={routePath} element={element} />
        </Routes>
      </CommandCenterProvider>
    </MemoryRouter>,
  )
}

const disabledControlExpectations = [
  {
    path: "/",
    routePath: "*",
    label: /Owner channel locked/i,
    reason: /locked/i,
  },
  {
    path: "/evidence",
    routePath: "*",
    label: /Export ledger/i,
    reason: /simulated/i,
    enabledInSimulated: true,
  },
  {
    path: "/agents",
    routePath: "*",
    label: /Deploy Agent/i,
    reason: /simulated|frozen/i,
    enabledInSimulated: true,
  },
  {
    path: "/missions/M-HYB-014",
    routePath: "/missions/:missionId",
    label: /Request halt|Mission halt unavailable/i,
    reason: /simulated|unavailable/i,
    enabledInSimulated: true,
  },
] as const

describe("disabled control audit (simulated mode)", () => {
  for (const control of disabledControlExpectations) {
    it(`${control.path} exposes honest control semantics for ${control.label}`, async () => {
      const routeElement =
        control.path === "/"
          ? <Overview />
          : control.path === "/evidence"
            ? <Evidence />
            : control.path === "/agents"
              ? <Agents />
              : <MissionDetail />

      renderRoute(control.path, control.routePath, routeElement)

      const button = await screen.findByRole("button", { name: control.label })
      if ("enabledInSimulated" in control && control.enabledInSimulated) {
        expect(button).toBeEnabled()
      } else if ("enabledInSimulated" in control) {
        expect(button).toBeDisabled()
        expect(button.getAttribute("aria-label") ?? button.getAttribute("title") ?? "").toMatch(control.reason)
      } else {
        expect(button).toBeDisabled()
        expect(button.getAttribute("aria-label") ?? button.getAttribute("title") ?? "").toMatch(control.reason)
      }
    })
  }

  it("overview owner channel cannot be activated", async () => {
    renderRoute("/", "*", <Overview />)
    const locked = await screen.findByRole("button", { name: /Owner channel locked/i })
    expect(locked).toBeDisabled()
    await userEvent.click(locked)
    expect(locked).toBeDisabled()
  })

  it("mission halt explains simulated-only contract when enabled", async () => {
    renderRoute("/missions/M-HYB-014", "/missions/:missionId", <MissionDetail />)
    await waitFor(() => expect(screen.getByRole("button", { name: /Request halt/i })).toBeEnabled())
    expect(screen.getByRole("button", { name: /Request halt/i })).toHaveAttribute("aria-label", expect.stringMatching(/simulated/i))
  })
})
