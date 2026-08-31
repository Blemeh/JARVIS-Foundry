import { ObsidianGraphView } from "../components/ObsidianGraphView"
import { JarvisCoreStage } from "../components/jarvis-core/JarvisCoreStage"
import { IntelligenceRail } from "../components/IntelligenceRail"
import { OwnerCommandSurface } from "../components/OwnerCommandSurface"
import { ActiveOperations } from "../components/ActiveOperations"
import { IntelligenceStream } from "../components/IntelligenceStream"
import { useCommandCenter } from "../context/CommandCenterContext"
import { RouteQueryGate } from "../components/RouteQueryGate"
import { useRouteQuery } from "../hooks/useRouteQuery"
import { loadOverviewData, type OverviewData } from "../lib/overviewLoader"
import { sourceModeLabel } from "../lib/sourceMode"
import { findMeasuredCostResource, formatMeasuredCostDisplay } from "../lib/truthFormat"
import { OperationalSignalPanel } from "../components/OperationalSignalPanel"

function OverviewContent({
  missions,
  overview,
  workers,
  timeline,
  evidence,
  trust,
  executive,
  ownerAttention,
  obsidianVault,
  ownerSurface,
  operationalIntelligence,
}: OverviewData) {
  const { sourceMode, stopActive, streamConnected } = useCommandCenter()
  const activeMissionCount = missions.filter((mission) => mission.status === "ACTIVE" || mission.status === "IN_PROGRESS").length
  const verifyingCount = evidence.filter((item) => item.verdict === "EVALUATING").length
  const measuredCost = findMeasuredCostResource(overview.resourceUsage) ?? {
    display: "UNKNOWN",
    source: "NOT_REPORTED" as const,
    unknownMeasurements: null,
  }
  const measuredCostDisplay = formatMeasuredCostDisplay(measuredCost, sourceMode)

  const vaultStatus =
    obsidianVault.length > 0
      ? `Obsidian vault · ${obsidianVault.length} indexed notes · two-finger scroll to zoom`
      : sourceMode === "LIVE"
        ? "Obsidian vault · awaiting index sync"
        : `Obsidian vault · ${sourceModeLabel(sourceMode)}`
  const ownerGoal = ownerSurface?.goals[0]
  const ownerGoalText = ownerGoal && typeof ownerGoal.exact_text === "string" && ownerGoal.exact_text.trim()
    ? ownerGoal.exact_text
    : ownerGoal && typeof ownerGoal.objective === "string" && ownerGoal.objective.trim()
      ? ownerGoal.objective
      : null
  const activeMission = missions.find((mission) => mission.status === "ACTIVE" || mission.status === "IN_PROGRESS")
  const goalText = ownerGoalText ?? activeMission?.title ?? "No durable owner goal recorded"
  const goalMeta = ownerGoal
    ? `directive ${String(ownerGoal.directive_id ?? ownerGoal.event_id ?? "UNKNOWN")}`
    : activeMission
      ? `active mission ${activeMission.id}`
      : "owner goal UNKNOWN"
  const runtimeState = ownerSurface?.runtimeState || "UNKNOWN"
  const sourceReady = sourceMode === "LIVE" || sourceMode === "SIMULATED"
  const linkState = stopActive ? "SUSPENDED" : sourceReady ? sourceModeLabel(sourceMode) : "GATED"

  return (
    <div className="jarvis-os">
      <header className="jarvis-os-heading">
        <span className="jarvis-os-kicker">OWNER CONTROL // AUTONOMOUS OPERATING SURFACE</span>
        <h1>What should JARVIS move next?</h1>
        <p>
          Goals, constraints, vetoes, pause, resume, and deterministic STOP enter one audited control path. Routine operation continues without becoming an approval queue.
        </p>
      </header>
      <div className="jarvis-os-hero">
        <div className="jarvis-os-core-zone jarvis-os-core-zone--obsidian">
          <div className="jarvis-os-cinematic-core" aria-label="JARVIS core status">
            <JarvisCoreStage
              activeMissionCount={activeMissionCount}
              verifyingCount={verifyingCount}
              trustHealth={trust.health}
            />
          </div>
          <ObsidianGraphView key={sourceMode} live={sourceMode === "LIVE"} />
          <p className="jarvis-os-core-status">{vaultStatus}</p>
          <p className="jarvis-os-cost-status" aria-label={`Measured cost ${measuredCostDisplay}`}>
            Measured cost {measuredCostDisplay}
          </p>
        </div>

        <IntelligenceRail missions={missions} executive={executive} ownerAttention={ownerAttention} />
      </div>

      <div className="telemetry overview-truth-strip" aria-label="Operational truth at a glance">
        <article>
          <span>Runtime</span>
          <strong>{runtimeState}</strong>
          <small>{ownerSurface?.stateReason ?? "Runtime state UNKNOWN"}</small>
        </article>
        <article>
          <span>Trust</span>
          <strong>{trust.state}</strong>
          <small>gate {trust.gateStatus}</small>
        </article>
        <article>
          <span>Active mission</span>
          <strong>{activeMissionCount ? String(activeMissionCount).padStart(2, "0") : "NONE"}</strong>
          <small>{activeMission?.id ?? "No mission is active"}</small>
        </article>
        <article>
          <span>Health</span>
          <strong>{trust.health}</strong>
          <small>{linkState}</small>
        </article>
        <article>
          <span>Alerts</span>
          <strong>{ownerSurface ? String(ownerSurface.decisionQueue.open_count).padStart(2, "0") : "UNKNOWN"}</strong>
          <small>{ownerSurface ? "open owner attention items" : "decision queue unavailable"}</small>
        </article>
      </div>

      <section className="panel owner-goal-panel" aria-label="Current owner intent">
        <div>
          <h2>Current owner goal</h2>
          <p>{goalText}</p>
          <small className="t-mono text-muted">{goalMeta}</small>
        </div>
        <div className="t-mono text-muted" aria-label={`Event stream ${streamConnected ? "connected" : "disconnected"}`}>
          {streamConnected ? "EVENT STREAM CONNECTED" : "EVENT STREAM DISCONNECTED"}
        </div>
      </section>

      <OwnerCommandSurface />

      <OperationalSignalPanel operational={operationalIntelligence} />

      <div className="jarvis-os-lower">
        <ActiveOperations missions={missions} workers={workers} />
        <IntelligenceStream events={timeline} />
      </div>
    </div>
  )
}

export function Overview() {
  const { client, version } = useCommandCenter()
  const overviewQuery = useRouteQuery(
    () => loadOverviewData(client),
    [client, version],
    { keepPreviousWhileLoading: true },
  )

  return (
    <RouteQueryGate
      query={overviewQuery}
      loadingLabel="Loading JARVIS"
      errorLabel="Overview unavailable"
      surface="Overview projection"
    >
      {(data) => <OverviewContent {...data} />}
    </RouteQueryGate>
  )
}
