import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import userEvent from "@testing-library/user-event"
import { MemoryRouter, Route, Routes } from "react-router-dom"
import { afterEach, describe, expect, it, vi } from "vitest"
import { CommandCenterProvider } from "../../context/CommandCenterContext"
import { LocaleProvider } from "../../i18n/LocaleContext"
import { MockJarvisClient } from "../../lib/mockClient"
import { CommandBar } from "../CommandBar"
import { OwnerCommandSurface } from "../OwnerCommandSurface"

// Task 7: GOAL is the default primary tab, ASK and VETO are
// one click away, STOP is visually separate from the submit path.

function renderDock() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <CommandCenterProvider>
        <Routes>
          <Route path="*" element={<CommandBar />} />
        </Routes>
      </CommandCenterProvider>
    </MemoryRouter>,
  )
}

function renderSurface() {
  return render(
    <MemoryRouter initialEntries={["/"]}>
      <LocaleProvider>
        <CommandCenterProvider>
          <OwnerCommandSurface />
        </CommandCenterProvider>
      </LocaleProvider>
    </MemoryRouter>,
  )
}

const emptyResults = {
  missions: [],
  agents: [],
  evidence: [],
  systems: [],
  memory: [],
  timeline: [],
}

describe("command dock tabs (Task 7)", () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("shows GOAL as the default primary tab with ASK and VETO one click away", async () => {
    renderDock()

    const goalTab = await screen.findByRole("tab", { name: /GOAL/i })
    const askTab = screen.getByRole("tab", { name: /ASK/i })
    const vetoTab = screen.getByRole("tab", { name: /VETO/i })

    expect(goalTab).toHaveAttribute("aria-selected", "true")
    expect(askTab).toHaveAttribute("aria-selected", "false")
    expect(vetoTab).toHaveAttribute("aria-selected", "false")
    // GOAL reads as the primary tab.
    expect(goalTab.textContent).toMatch(/primary/i)

    const input = screen.getByRole("combobox", { name: /Direct JARVIS/i })
    expect(input).toHaveAttribute("placeholder", expect.stringMatching(/outcome/i))
  })

  it("maps GOAL to HANDLE, ASK to the Tell-me path, and VETO to submitVeto", async () => {
    const submitOwnerGoal = vi
      .spyOn(MockJarvisClient.prototype, "submitOwnerGoal")
      .mockResolvedValue({ accepted: true, directiveId: "directive-map-1", eventId: "event-map-1" })
    const submitVeto = vi
      .spyOn(MockJarvisClient.prototype, "submitVeto")
      .mockResolvedValue({ accepted: true, directiveId: "directive-veto-1", eventId: "event-veto-1" })
    vi.spyOn(MockJarvisClient.prototype, "search").mockResolvedValue(emptyResults)
    const user = userEvent.setup()
    renderDock()

    const input = await screen.findByRole("combobox", { name: /Direct JARVIS/i })

    await user.type(input, "Ship Hoowen beta")
    await user.click(screen.getByRole("button", { name: /Send command/i }))
    await waitFor(() => expect(submitOwnerGoal).toHaveBeenCalledWith("Ship Hoowen beta", "HANDLE"))

    await user.click(screen.getByRole("tab", { name: /ASK/i }))
    expect(screen.getByRole("tab", { name: /ASK/i })).toHaveAttribute("aria-selected", "true")
    expect(screen.getByRole("combobox", { name: /Direct JARVIS/i })).toHaveAttribute(
      "placeholder",
      expect.stringMatching(/advice|briefing/i),
    )
    await user.type(screen.getByRole("combobox", { name: /Direct JARVIS/i }), "Retry storm risk")
    await user.click(screen.getByRole("button", { name: /Send command/i }))
    await waitFor(() => expect(submitOwnerGoal).toHaveBeenCalledWith("Retry storm risk", "TELL_ME"))

    await user.click(screen.getByRole("tab", { name: /VETO/i }))
    expect(screen.getByRole("tab", { name: /VETO/i })).toHaveAttribute("aria-selected", "true")
    expect(screen.getByRole("combobox", { name: /Direct JARVIS/i })).toHaveAttribute(
      "placeholder",
      expect.stringMatching(/hold/i),
    )
    await user.type(screen.getByRole("combobox", { name: /Direct JARVIS/i }), "Hold deploy")
    await user.click(screen.getByRole("button", { name: /Send command/i }))
    await waitFor(() => expect(submitVeto).toHaveBeenCalledWith("Hold deploy"))
    expect(submitOwnerGoal).not.toHaveBeenCalledWith("Hold deploy", expect.anything())
  })

  it("keeps STOP visually separate from the submit path", async () => {
    renderDock()

    const stop = await screen.findByRole("button", { name: /Emergency STOP/i })
    expect(stop).toBeInTheDocument()
    // STOP is never inside the submit row.
    expect(stop.closest("form")).toBeNull()
  })

  it("preserves the keyboard shortcut and search-results behavior", async () => {
    vi.spyOn(MockJarvisClient.prototype, "search").mockResolvedValue({
      ...emptyResults,
      missions: [
        { id: "M-1", label: "Hoowen beta", sublabel: "Active", kind: "mission", to: "/missions/M-1" },
      ],
    })
    const user = userEvent.setup()
    renderDock()

    const input = await screen.findByRole("combobox", { name: /Direct JARVIS/i })
    expect(input).not.toHaveFocus()

    fireEvent.keyDown(document, { key: "k", ctrlKey: true })
    expect(input).toHaveFocus()

    input.blur()
    fireEvent.keyDown(document, { key: "/" })
    expect(input).toHaveFocus()

    await user.type(input, "Hoowen beta")
    expect(await screen.findByRole("listbox", { name: /Command matches/i })).toBeInTheDocument()
    expect(screen.getByText("Hoowen beta")).toBeInTheDocument()
  })

  it("carries directive and event IDs as secondary confirmation on accepted feedback", async () => {
    vi.spyOn(MockJarvisClient.prototype, "submitOwnerGoal").mockResolvedValue({
      accepted: true,
      directiveId: "directive-1",
      eventId: "event-1",
    })
    vi.spyOn(MockJarvisClient.prototype, "search").mockResolvedValue(emptyResults)
    const user = userEvent.setup()
    renderDock()

    const input = await screen.findByRole("combobox", { name: /Direct JARVIS/i })
    await user.type(input, "Ship Hoowen beta")
    await user.click(screen.getByRole("button", { name: /Send command/i }))

    const status = await screen.findByRole("status")
    expect(status.textContent).toMatch(/directive-1/)
    expect(status.textContent).toMatch(/event-1/)
  })
})

describe("owner command surface tabs (Task 7)", () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it("exposes the same GOAL primary / ASK / VETO tabs with plain-language placeholders", async () => {
    renderSurface()

    const goalTab = await screen.findByRole("tab", { name: /GOAL/i })
    expect(goalTab).toHaveAttribute("aria-selected", "true")
    expect(goalTab.textContent).toMatch(/primary/i)
    expect(screen.getByRole("tab", { name: /ASK/i })).toBeInTheDocument()
    expect(screen.getByRole("tab", { name: /VETO/i })).toBeInTheDocument()
    expect(screen.getByLabelText(/Owner objective/i)).toHaveAttribute(
      "placeholder",
      expect.stringMatching(/outcome/i),
    )
  })

  it("routes ASK to the Tell-me path and VETO to submitVeto with IDs as secondary confirmation", async () => {
    const submitOwnerGoal = vi
      .spyOn(MockJarvisClient.prototype, "submitOwnerGoal")
      .mockResolvedValue({ accepted: true, directiveId: "directive-ask-1", eventId: "event-ask-1" })
    const submitVeto = vi
      .spyOn(MockJarvisClient.prototype, "submitVeto")
      .mockResolvedValue({ accepted: true, directiveId: "directive-veto-1", eventId: "event-veto-1" })
    const user = userEvent.setup()
    renderSurface()

    await screen.findByRole("tab", { name: /GOAL/i })

    await user.click(screen.getByRole("tab", { name: /ASK/i }))
    await user.type(screen.getByLabelText(/Owner objective/i), "Retry storm risk")
    await user.click(screen.getByRole("button", { name: /Submit/i }))
    await waitFor(() => expect(submitOwnerGoal).toHaveBeenCalledWith("Retry storm risk", "TELL_ME"))
    expect(await screen.findByText(/directive-ask-1/)).toBeInTheDocument()

    await user.click(screen.getByRole("tab", { name: /VETO/i }))
    await user.type(screen.getByLabelText(/Owner objective/i), "Hold deploy")
    await user.click(screen.getByRole("button", { name: /Submit/i }))
    await waitFor(() => expect(submitVeto).toHaveBeenCalledWith("Hold deploy"))
    expect(await screen.findByText(/directive-veto-1/)).toBeInTheDocument()
  })
})
