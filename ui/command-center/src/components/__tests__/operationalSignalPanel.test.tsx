import { render, screen } from "@testing-library/react"
import { MemoryRouter } from "react-router-dom"
import { describe, expect, it } from "vitest"
import { OperationalSignalPanel } from "../OperationalSignalPanel"

describe("OperationalSignalPanel", () => {
  it("renders only evidence-backed open signals and preserves explicit state", () => {
    render(
      <MemoryRouter>
        <OperationalSignalPanel
          operational={{
          schema: "v8.operational-intelligence.v1",
          status: "AVAILABLE",
          generated_at: "2026-08-29T12:00:00Z",
          signals: [
            {
              signal_id: "signal-1",
              fingerprint: "fingerprint-1",
              salience: "OWNER_ATTENTION",
              score: 72.5,
              state: "OPEN",
              kind: "FAILED",
              summary: "provider timeout",
              recurrence_count: 2,
              suppressed_count: 1,
              next_action: "recheck capability health",
              entity_kind: "mission",
              entity_id: "M-1",
              source_event_ids: ["evt-1"],
              evidence_refs: ["proof-1"],
            },
            {
              signal_id: "signal-2",
              fingerprint: "fingerprint-2",
              salience: "URGENT",
              score: 93,
              state: "RESOLVED",
              kind: "FAILED",
              summary: "resolved failure",
              recurrence_count: 1,
              suppressed_count: 0,
              next_action: "monitor",
            },
          ],
          followups: [],
          attention: { open_count: 1, deduped_occurrences: 1 },
          }}
        />
      </MemoryRouter>,
    )

    expect(screen.getByRole("heading", { name: "Operational signals" })).toBeInTheDocument()
    expect(screen.getByText("provider timeout")).toBeInTheDocument()
    expect(screen.getByText("OWNER_ATTENTION")).toBeInTheDocument()
    expect(screen.getByText(/FAILED.*score 72\.5/)).toBeInTheDocument()
    expect(screen.getByRole("link", { name: "proof-1" })).toHaveAttribute("href", "/evidence/proof-1")
    expect(screen.getByRole("link", { name: "evt-1" })).toHaveAttribute("href", "/timeline?event=evt-1")
    expect(screen.getByRole("link", { name: "mission:M-1" })).toHaveAttribute("href", "/missions/M-1")
    expect(screen.queryByText("resolved failure")).not.toBeInTheDocument()
  })

  it("keeps absent numeric truth explicit instead of inventing zero", () => {
    render(
      <MemoryRouter>
        <OperationalSignalPanel
          operational={{
          schema: "v8.operational-intelligence.v1",
          status: "PARTIAL",
          generated_at: "2026-08-29T12:00:00Z",
          signals: [{
            signal_id: "signal-unknown",
            fingerprint: "fingerprint-unknown",
            salience: "MONITOR",
            score: null,
            state: "OPEN",
            kind: "OBSERVATION",
            summary: "provider observation",
            recurrence_count: null,
            suppressed_count: null,
            next_action: "UNKNOWN",
          }],
          followups: [],
          attention: { open_count: 1 },
          }}
        />
      </MemoryRouter>,
    )

    expect(screen.getByText("UNKNOWN repeats suppressed")).toBeInTheDocument()
    expect(screen.getByText(/score UNKNOWN/)).toBeInTheDocument()
    expect(screen.getByText(/UNKNOWN occurrences/)).toBeInTheDocument()
  })

  it("does not invent a panel when the live projection is absent", () => {
    const { container } = render(<OperationalSignalPanel operational={null} />)
    expect(container).toBeEmptyDOMElement()
  })
})
