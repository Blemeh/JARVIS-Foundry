import { Link } from "react-router-dom"
import { StatusBadge } from "./StatusBadge"
import type { OperationalIntelligenceSurface, OperationalSignalSurface } from "../lib/contracts"

const STATUS_BADGES = new Set([
  "AVAILABLE",
  "PARTIAL",
  "EMPTY_LEDGER",
  "UNKNOWN",
  "STOPPED",
])

function displayText(value: string | null | undefined): string {
  return typeof value === "string" && value.trim().length > 0 ? value : "UNKNOWN"
}

function displayNumber(value: number | null | undefined, digits = 0): string {
  return typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : "UNKNOWN"
}

function displayCount(value: number | null | undefined): string {
  return typeof value === "number" && Number.isFinite(value) ? String(value) : "UNKNOWN"
}

function projectionStatus(value: Record<string, unknown> | undefined): string {
  return value && typeof value.status === "string" && value.status.length > 0 ? value.status : "UNKNOWN"
}

function badgeStatus(value: string): Parameters<typeof StatusBadge>[0]["status"] {
  const normalized = value.toUpperCase()
  return (STATUS_BADGES.has(normalized) ? normalized : "UNKNOWN") as Parameters<typeof StatusBadge>[0]["status"]
}

function tone(signal: OperationalSignalSurface): string {
  if (signal.salience === "URGENT") return "operational-signal--urgent"
  if (signal.salience === "OWNER_ATTENTION" || signal.salience === "CREATE_WORK") return "operational-signal--attention"
  if (signal.salience === "RECOMMEND") return "operational-signal--recommend"
  return ""
}

function entityPath(kind: string | null | undefined, id: string | null | undefined): string | null {
  if (!kind || !id) return null
  const encoded = encodeURIComponent(id)
  switch (kind.toLowerCase()) {
    case "mission": return `/missions/${encoded}`
    case "agent": return `/agents/${encoded}`
    case "evidence": return `/evidence/${encoded}`
    case "system": return `/systems/${encoded}`
    case "memory": return `/memory/${encoded}`
    case "timeline": return `/timeline?event=${encoded}`
    default: return null
  }
}

function ReferenceLink({ kind, id }: { kind: "evidence" | "timeline"; id: string }) {
  const to = kind === "evidence"
    ? `/evidence/${encodeURIComponent(id)}`
    : `/timeline?event=${encodeURIComponent(id)}`
  return <Link className="operational-ref t-mono" to={to}>{id}</Link>
}

function SignalTrace({ signal }: { signal: OperationalSignalSurface }) {
  const evidenceRefs = (signal.evidence_refs ?? []).filter((ref) => ref.length > 0)
  const sourceEventIds = (signal.source_event_ids ?? []).filter((ref) => ref.length > 0)
  const entityHref = entityPath(signal.entity_kind, signal.entity_id)
  return (
    <div className="operational-signal-trace" aria-label={`Provenance for ${signal.signal_id}`}>
      <span className="t-label operational-trace-label">Trace</span>
      {signal.entity_id ? (
        entityHref ? (
          <Link className="operational-ref t-mono" to={entityHref}>{displayText(signal.entity_kind)}:{signal.entity_id}</Link>
        ) : (
          <span className="t-mono">{displayText(signal.entity_kind)}:{signal.entity_id}</span>
        )
      ) : <span className="t-mono">entity UNKNOWN</span>}
      <span className="operational-ref-group">
        <span className="t-label">Evidence</span>
        {evidenceRefs.length
          ? evidenceRefs.map((ref) => <ReferenceLink key={`evidence-${ref}`} kind="evidence" id={ref} />)
          : <span className="t-mono">UNKNOWN</span>}
      </span>
      <span className="operational-ref-group">
        <span className="t-label">Events</span>
        {sourceEventIds.length
          ? sourceEventIds.map((ref) => <ReferenceLink key={`event-${ref}`} kind="timeline" id={ref} />)
          : <span className="t-mono">UNKNOWN</span>}
      </span>
    </div>
  )
}

function SignalRow({ signal }: { signal: OperationalSignalSurface }) {
  const recurrence = displayCount(signal.recurrence_count)
  return (
    <li className={`operational-signal ${tone(signal)}`} data-signal-id={signal.signal_id}>
      <div className="operational-signal-head">
        <span className="t-label">{displayText(signal.salience)}</span>
        <span className="t-mono">{displayText(signal.state)}</span>
      </div>
      <strong>{displayText(signal.summary)}</strong>
      <p>
        {displayText(signal.kind)} · score {displayNumber(signal.score, 1)} · {recurrence} occurrence{recurrence === "1" ? "" : "s"}
      </p>
      <small>{displayText(signal.next_action)}</small>
      <SignalTrace signal={signal} />
    </li>
  )
}

export function OperationalSignalPanel({
  operational,
}: {
  operational?: OperationalIntelligenceSurface | null
}) {
  if (!operational) return null
  const signals = (Array.isArray(operational.signals) ? operational.signals : [])
    .filter((signal) => signal.state === "OPEN" && signal.salience !== "IGNORE")
    .slice(0, 5)
  const attention = operational.attention
  const status = displayText(operational.status)
  const followupCount = Array.isArray(operational.followups) ? String(operational.followups.length) : "UNKNOWN"
  const emptyLedger = status.toUpperCase() === "EMPTY_LEDGER" || status.toUpperCase() === "EMPTY"
  return (
    <section className="panel operational-panel" aria-label="Operational intelligence">
      <header className="panel-head">
        <div>
          <h2>Operational signals</h2>
          <p>Evidence-backed attention from the canonical ledger · generated {displayText(operational.generated_at)}</p>
        </div>
        <span role="status" aria-label={`Operational intelligence status: ${status}`}>
          <StatusBadge status={badgeStatus(status)} />
        </span>
      </header>
      <div className="operational-summary t-mono">
        <span>{displayCount(attention?.open_count)} open</span>
        <span>{displayCount(attention?.deduped_occurrences)} repeats suppressed</span>
        <span>{followupCount} follow-ups</span>
        <span>schema {displayText(operational.schema)}</span>
      </div>
      <div className="operational-projections" aria-label="Operational projection statuses">
        <span>Providers {projectionStatus(operational.providers)}</span>
        <span>Routing {projectionStatus(operational.routing)}</span>
        <span>Context {projectionStatus(operational.context)}</span>
        <span>Memory {projectionStatus(operational.memory)}</span>
        <span>Knowledge {projectionStatus(operational.knowledge)}</span>
        <span>Evolution {projectionStatus(operational.evolution)}</span>
        <span>Recurring work {projectionStatus(operational.recurring_work)}</span>
      </div>
      {signals.length ? (
        <ul className="operational-signal-list">
          {signals.map((signal) => <SignalRow key={signal.signal_id} signal={signal} />)}
        </ul>
      ) : (
        <p className="operational-empty">
          {emptyLedger ? "No operational signals recorded." : "No open signal is ready for attention."}
        </p>
      )}
    </section>
  )
}
