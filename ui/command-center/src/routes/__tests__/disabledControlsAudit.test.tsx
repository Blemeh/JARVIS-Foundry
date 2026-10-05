import { render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { afterEach, describe, expect, it, vi } from "vitest"
import { CommandCenterProvider } from "../../context/CommandCenterContext"
import { MockJarvisClient } from "../../lib/mockClient"
import { Evidence } from "../Evidence"
import { Agents } from "../Agents"
import { MissionDetail } from "../MissionDetail"
import { CommandBar } from "../../components/CommandBar"

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
  afterEach(() => vi.restoreAllMocks())

  for (const control of disabledControlExpectations) {
    it(`${control.path} exposes honest control semantics for ${control.label}`, async () => {
      const routeElement =
        control.path === "/evidence"
          ? <Evidence />
          : control.path === "/agents"
            ? <Agents />
            : <MissionDetail />

      renderRoute(control.path, control.routePath, routeElement)

      const button = await screen.findByRole("button", { name: control.label })
      if (control.enabledInSimulated) {
        expect(button).toBeEnabled()
      } else {
        expect(button).toBeDisabled()
        expect(button.getAttribute("aria-label") ?? button.getAttribute("title") ?? "").toMatch(control.reason)
      }
    })
  }

  it("persistent command dock accepts owner input in simulated mode", async () => {
    renderRoute("/", "*", <CommandBar />)
    const input = await screen.findByRole("combobox", { name: /Direct JARVIS/i })
    expect(input).toBeEnabled()
    const submit = screen.getByRole("button", { name: /Send command/i })
    expect(submit).toBeDisabled()
  })

  it("submits exact owner text without encoding the default mode", async () => {
    const submitOwnerGoal = vi
      .spyOn(MockJarvisClient.prototype, "submitOwnerGoal")
      .mockResolvedValue({ accepted: false, error: "simulated rejection" })
    const user = userEvent.setup()
    renderRoute("/", "*", <CommandBar />)

    const input = await screen.findByRole("combobox", { name: /Direct JARVIS/i })
    await user.type(input, "Summarize retry risk")
    await user.click(screen.getByRole("button", { name: /Send command/i }))

    await waitFor(() => expect(submitOwnerGoal).toHaveBeenCalledWith("Summarize retry risk", "HANDLE"))
  })

  it("keeps delegation mode separate from authenticated owner text", async () => {
    const submitOwnerGoal = vi
      .spyOn(MockJarvisClient.prototype, "submitOwnerGoal")
      .mockResolvedValue({ accepted: false, error: "simulated rejection" })
    const user = userEvent.setup()
    renderRoute("/", "*", <CommandBar />)

    const input = await screen.findByRole("combobox", { name: /Direct JARVIS/i })
    await user.type(input, "Track provider recovery")
    await user.click(screen.getByRole("button", { name: /Watch/i }))
    expect(input).toHaveValue("Track provider recovery")
    expect(screen.getByRole("button", { name: /Watch/i })).toHaveAttribute("aria-pressed", "true")
    await user.click(screen.getByRole("button", { name: /Send command/i }))

    await waitFor(() => expect(submitOwnerGoal).toHaveBeenCalledWith("Track provider recovery", "WATCH"))
  })

  it("mission halt explains simulated-only contract when enabled", async () => {
    renderRoute("/missions/M-HYB-014", "/missions/:missionId", <MissionDetail />)
    await waitFor(() => expect(screen.getByRole("button", { name: /Request halt/i })).toBeEnabled())
    expect(screen.getByRole("button", { name: /Request halt/i })).toHaveAttribute("aria-label", expect.stringMatching(/simulated/i))
  })
})
