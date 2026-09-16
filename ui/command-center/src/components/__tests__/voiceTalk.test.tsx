import { fireEvent, render, screen, waitFor } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { afterEach, describe, expect, it, vi } from "vitest"
import { CommandCenterProvider } from "../../context/CommandCenterContext"
import { LocaleProvider } from "../../i18n/LocaleContext"
import { MockJarvisClient } from "../../lib/mockClient"
import { OwnerCommandSurface } from "../OwnerCommandSurface"
import { PauseBanner } from "../PauseBanner"

class FakeRecognition {
  lang = ""
  interimResults = false
  continuous = false
  onresult: ((event: { results: ArrayLike<ArrayLike<{ transcript: string }>> }) => void) | null = null
  onerror: ((event: { error: string }) => void) | null = null
  onend: (() => void) | null = null
  static instance: FakeRecognition | null = null
  start() {
    FakeRecognition.instance = this
  }
  stop() {
    this.onend?.()
  }
  abort() {}
}

function renderOwner() {
  return render(
    <MemoryRouter>
      <LocaleProvider>
        <CommandCenterProvider>
          <OwnerCommandSurface />
          <PauseBanner />
        </CommandCenterProvider>
      </LocaleProvider>
    </MemoryRouter>,
  )
}

describe("owner voice talk", () => {
  afterEach(() => {
    FakeRecognition.instance = null
    delete (MockJarvisClient.prototype as { getOwnerControlSurface?: unknown }).getOwnerControlSurface
    vi.unstubAllGlobals()
    vi.restoreAllMocks()
  })

  it("listens on click and submits the transcript", async () => {
    const speechWindow = window as Window & { SpeechRecognition?: typeof FakeRecognition }
    speechWindow.SpeechRecognition = FakeRecognition
    const submit = vi.spyOn(MockJarvisClient.prototype, "submitVoiceText").mockResolvedValue({
      status: "COMPLETED",
      transcript: "pause",
      responseText: "Paused.",
      commandType: "PAUSE",
      language: "en",
    })
    renderOwner()
    fireEvent.click(screen.getByRole("button", { name: "Push to talk" }))
    expect(screen.getByRole("button", { name: "Listening…" })).toBeInTheDocument()
    FakeRecognition.instance?.onresult?.({ results: [[{ transcript: "pause" }]] })
    await waitFor(() => expect(submit).toHaveBeenCalledWith("pause", "en"))
    await waitFor(() => expect(screen.getByText("Paused.")).toBeInTheDocument())
  })

  it("shows a microphone denial instead of inventing a transcript", async () => {
    const speechWindow = window as Window & { SpeechRecognition?: typeof FakeRecognition }
    speechWindow.SpeechRecognition = FakeRecognition
    renderOwner()
    fireEvent.click(screen.getByRole("button", { name: "Push to talk" }))
    FakeRecognition.instance?.onerror?.({ error: "not-allowed" })
    expect(await screen.findByRole("alert")).toHaveTextContent("Microphone permission was denied.")
  })

  it("shows pause resume when owner hold is active", async () => {
    Object.assign(MockJarvisClient.prototype, {
      getOwnerControlSurface: vi.fn().mockResolvedValue({
        source: "SIMULATED",
        runtimeState: "PAUSED",
        stateReason: "owner pause",
        stateSource: "test",
        paused: true,
        stopped: false,
        resumeAvailable: true,
        resumableDirectiveIds: ["directive-pause-1"],
        goals: [],
        constraints: [],
        vetoes: [],
        reprioritizations: [],
        redirects: [],
        directiveHistory: [],
        briefings: [],
        resultTraceability: { status: "EMPTY", results: [], count: 0 },
        briefingPacket: null,
        briefingL10n: null,
        decisionQueue: { open: [], history: [], open_count: 0, history_count: 0, counts_by_classification: {} },
      }),
    })
    renderOwner()
    expect(await screen.findByText("Paused — owner hold is active")).toBeInTheDocument()
    expect(screen.getByRole("button", { name: "Resume" })).toBeEnabled()
  })
})
