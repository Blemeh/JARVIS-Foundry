import { Link } from "react-router-dom"
import { Mic } from "lucide-react"
import { useCommandCenter } from "../context/CommandCenterContext"
import type { EvidenceArtifact, Mission, SystemComponent, SystemOverview, TimelineEvent, TrustSummary, Worker } from "../lib/contracts"
import { RouteQueryGate } from "../components/RouteQueryGate"
import { useRouteQuery } from "../hooks/useRouteQuery"
import { sourceCopy } from "../lib/sourceMode"
import {
  findMeasuredCostResource,
  formatMeasuredCostDisplay,
  formatFreshnessLabel,
  trustFactorNumericDetail,
} from "../lib/truthFormat"

function toneForStatus(status: string) {
  const s = (status ?? "UNKNOWN").toUpperCase()
  if (["BLOCKED", "HALTED", "FAILED", "CRITICAL", "STOPPED"].includes(s)) return "critical"
  if (["DEGRADED", "UNKNOWN", "NOT_TESTED", "NOT_AVAILABLE", "GATED", "WARN"].includes(s)) return "caution"
  return "nominal"
}

type OverviewData = {
  missions: Mission[]
  overview: SystemOverview
  trust: TrustSummary
  workers: Worker[]
  evidence: EvidenceArtifact[]
  timeline: TimelineEvent[]
  systems: SystemComponent[]
}

function OverviewContent({
  missions,
  overview,
  trust,
  workers,
  evidence,
  timeline,
}: OverviewData) {
  const { sourceMode } = useCommandCenter()

  const safeMissions = missions
  const safeWorkers = workers
  const safeEvidence = evidence
  const safeTimeline = timeline
  const safeTrust = trust
  const measuredCostBase = findMeasuredCostResource(overview.resourceUsage) ?? {
    display: "UNKNOWN",
    source: "NOT_REPORTED" as const,
    unknownMeasurements: null,
  }
  const measuredCostDisplay = formatMeasuredCostDisplay(measuredCostBase, sourceMode)
  const freshness = formatFreshnessLabel(overview.uptime, new Date().toISOString())
  const constraintsFactor = safeTrust.factors?.find((f) => f.id === "constraints")
  const staleLeasesFactor = safeTrust.factors?.find((f) => f.id === "stale-leases")
  const constraintCount = constraintsFactor ? trustFactorNumericDetail(constraintsFactor.detail) ?? 0 : 0
  const staleLeases = staleLeasesFactor ? trustFactorNumericDetail(staleLeasesFactor.detail) ?? 0 : 0
  const systemPosture = safeTrust.health === "HEALTHY" ? "NOMINAL" : safeTrust.health === "DEGRADED" ? "DEGRADED" : "UNKNOWN"
  const activeWorkers = safeWorkers.filter((w) => w.status === "ACTIVE")
  const verified = safeEvidence.filter((e) => e.verdict === "VERIFIED").length
  const blocked = safeMissions.filter((m) => m.status === "BLOCKED" || m.status === "HALTED").length

  const currentGoal = safeMissions.find((m) => m.status === "ACTIVE" || m.status === "IN_PROGRESS")?.title ?? safeMissions[0]?.title ?? "No durable goal recorded"
  const goalText = currentGoal

  const missionVectors = safeMissions.slice(0, 6)
  const decisionSignal = [...safeTimeline].slice(0, 8)

  return (
    <div className="command-deck-wrap">
      <div className="v4-downlink">
        <div className="v4-calibration" aria-hidden="true" />

        <div className="command-deck-grid">
          <div className="command-field">
            <div className="command-copy">
              <span className="signal-label">Owner Downlink</span>
              <h1>What should JARVIS move next?</h1>
              <p>
                Goals, constraints, vetoes, pause, resume, and deterministic STOP enter one audited control path. Routine operation continues without
                becoming an approval queue.
              </p>

              <div className="directive-box" aria-label="Owner communication preview">
                <Mic aria-hidden="true" />
                <input value="Owner console preview — authentication is not configured" readOnly aria-label="Owner directive preview" />
                <button type="button" disabled aria-label="Owner channel locked">
                  CHANNEL LOCKED
                </button>
              </div>

              <div className="directive-note">
                <span className="t-mono" style={{ color: "var(--caution-amber)" }}>
                  ● NOT_TESTED
                </span>
                <small>Real English STT/TTS remains explicitly gated.</small>
              </div>

              <p className="t-mono text-muted" style={{ marginTop: 12, fontSize: 11 }}>
                {sourceCopy(sourceMode, {
                  live: "Operational projection of the JARVIS autonomy runtime from the canonical Foundry ledger and Supervisor.",
                  simulated: "Operational projection of the JARVIS autonomy runtime using local fixture data for tests and demos.",
                  unavailable: `Operational projection unavailable: the Foundry data source is ${sourceMode.toLowerCase()}; no fixture fallback is shown.`,
                })}
              </p>
            </div>
          </div>

          <aside className="goal-panel" aria-label="Current owner intent">
            <div>
              <div className="core-orbit" aria-hidden="true">
                <span className="orbit-dot" />
                <span className="orbit-dot two" />
                <span className="core">JARVIS</span>
              </div>
              <blockquote>{goalText}</blockquote>
              <p className="t-mono text-muted" style={{ fontSize: 11 }}>
                Complete roadmap V4 without touching live state
              </p>
            </div>
            <div className="goal-meta">
              <div className="datum">
                <span>Constraints</span>
                <b aria-label={`${constraintCount} owner constraints`}>{String(constraintCount).padStart(2, "0")}</b>
              </div>
              <div className="datum">
                <span>Trust state</span>
                <b aria-label={`Trust state ${safeTrust.state}`}>{safeTrust.state}</b>
              </div>
            </div>
          </aside>
        </div>

        <div className="telemetry" aria-label="Cross-check telemetry">
          <article>
            <span>System posture</span>
            <strong className={systemPosture === "NOMINAL" ? "v4-nominal" : ""} aria-label={`System posture ${systemPosture}`}>{systemPosture}</strong>
            <small>{staleLeases > 0 ? `${staleLeases} stale lease${staleLeases === 1 ? "" : "s"}` : freshness === "STALE" ? "stale heartbeat" : freshness === "CURRENT" ? "heartbeat current" : "freshness unknown"}</small>
          </article>
          <article>
            <span>Active workers</span>
            <strong aria-label={`${activeWorkers.length} active workers`}>{String(activeWorkers.length).padStart(2, "0")}</strong>
            <small>{safeWorkers.length} durable run records</small>
          </article>
          <article>
            <span>Verified</span>
            <strong aria-label={`${verified} verified records`}>{String(verified).padStart(2, "0")}</strong>
            <small>independent verdicts passed</small>
          </article>
          <article>
            <span>Blocked work</span>
            <strong aria-label={`${blocked} blocked missions`}>{String(blocked).padStart(2, "0")}</strong>
            <small>reclaimable mission records</small>
          </article>
          <article>
            <span>Measured cost</span>
            <strong aria-label={`Measured cost ${measuredCostDisplay}`}>{measuredCostDisplay}</strong>
            <small>
              {measuredCostBase.unknownMeasurements === null
                ? "measurement source not reported"
                : `${measuredCostBase.unknownMeasurements} unknown measurement${measuredCostBase.unknownMeasurements === 1 ? "" : "s"}`}
            </small>
          </article>
        </div>

        <div className="operations">
          <section className="panel" aria-label="Mission operations">
            <header className="panel-head">
              <div>
                <h2>Mission operations</h2>
                <p>Current intent, execution state, and next action from durable records.</p>
              </div>
              <span className="t-label" style={{ color: "var(--telemetry-dim)", fontSize: 9 }}>
                ● BRIEFING PLANNED
              </span>
            </header>

            <div className="mission-list">
              {missionVectors.length === 0 ? (
                <div className="mission-thread" style={{ padding: 28, textAlign: "center", color: "var(--telemetry-muted)" }}>
                  No durable missions recorded. JARVIS is honestly waiting.
                </div>
              ) : (
                missionVectors.map((m) => {
                  const tone = toneForStatus(m.status)
                  return (
                    <Link key={m.id} to={`/missions/${m.id}`} className={`mission-thread ${tone}`} aria-label={`Open durable mission record for ${m.id}`}>
                      <div className="mission-thread-head">
                        <span className="t-mono" style={{ color: "var(--evidence-cyan)", fontSize: 10, letterSpacing: "0.08em", textTransform: "uppercase" }}>
                          {m.code ?? m.id}
                        </span>
                        <span className="t-label" style={{ color: tone === "critical" ? "var(--stop-pink)" : tone === "caution" ? "var(--caution-amber)" : "var(--electric-signal)", fontSize: 9, display: "inline-flex", alignItems: "center", gap: 6 }}>
                          <span aria-hidden="true" style={{ width: 6, height: 6, borderRadius: "50%", background: "currentColor", display: "inline-block", boxShadow: tone === "nominal" ? "0 0 9px rgba(138,255,239,0.6)" : "none" }} />
                          {m.status}
                        </span>
                      </div>
                      <h3>{m.title}</h3>
                      <div className="mission-thread-foot">
                        <span>next vector</span>
                        <strong>{m.nextAction ?? m.stage}</strong>
                      </div>
                      <div className="mission-progress">
                        <span>progress UNKNOWN / state-derived signal only</span>
                      </div>
                    </Link>
                  )
                })
              )}
            </div>
          </section>

          <aside className="panel timeline-panel" aria-label="Decision signal">
            <header className="panel-head">
              <div>
                <h2>Decision signal</h2>
                <p>Newest ledger events first.</p>
              </div>
              <Link to="/timeline" style={{ color: "var(--evidence-cyan)", fontFamily: "var(--font-mono)", fontSize: 10, fontWeight: 700, textDecoration: "none", letterSpacing: "0.06em" }}>
                OPEN JSON
              </Link>
            </header>

            <ol className="timeline-list">
              {decisionSignal.length === 0 ? (
                <li className="timeline-event">
                  <small className="t-label text-muted">No ledger events</small>
                  <strong>EMPTY STATE</strong>
                  <p>No durable decisions recorded yet.</p>
                </li>
              ) : (
                decisionSignal.map((e, idx) => {
                  const sev = (e.severity ?? "INFO").toUpperCase()
                  const sevTone = sev === "CRITICAL" ? "critical" : sev === "WARNING" ? "caution" : "nominal"
                  const linkedCount = (e as unknown as { evidenceIds?: string[] }).evidenceIds?.length
                  const linkedLabel = linkedCount === undefined
                    ? "linked records NOT REPORTED"
                    : `${linkedCount} linked record${linkedCount === 1 ? "" : "s"}`
                  return (
                    <li key={e.id} className={`timeline-event ${sevTone}`}>
                      <span className="timeline-node" aria-hidden="true" />
                      <div className="event-head">
                        <Link to={`/timeline?event=${e.id}`} style={{ color: "var(--evidence-cyan)" }}>
                          EVT-{String(idx + 1).padStart(4, "0")}
                        </Link>
                        <time>{e.at ? String(e.at).slice(11, 19) : "--:--:--"}</time>
                      </div>
                      <strong>{e.title ?? e.id}</strong>
                      <p>{e.detail ?? e.id}</p>
                      <span className="evidence-count">{linkedLabel}</span>
                    </li>
                  )
                })
              )}
            </ol>
          </aside>
        </div>

        <div className="v4-footer">
          <span>
            Foundry ledger projection • generated {new Date().toISOString()} • source {sourceMode === "SIMULATED" ? "SIMULATED" : sourceMode}
          </span>
          <a href="#main-content">Inspect evidence</a>
        </div>
      </div>
    </div>
  )
}

export function Overview() {
  const { client, version } = useCommandCenter()
  const overviewQuery = useRouteQuery(
    () =>
      Promise.all([
        client.listMissions(),
        client.getSystemOverview(),
        client.getTrustSummary(),
        client.listWorkers(),
        client.listEvidence(),
        client.listTimeline(),
        client.listSystemComponents(),
      ]).then(([missions, overview, trust, workers, evidence, timeline, systems]) => ({
        missions,
        overview,
        trust,
        workers,
        evidence,
        timeline,
        systems,
      } satisfies OverviewData)),
    [client, version],
  )

  return (
    <RouteQueryGate
      query={overviewQuery}
      loadingLabel="Loading command deck"
      errorLabel="Command deck unavailable"
      surface="Command deck"
      shellClass="command-deck-wrap"
    >
      {(data) => <OverviewContent {...data} />}
    </RouteQueryGate>
  )
}
