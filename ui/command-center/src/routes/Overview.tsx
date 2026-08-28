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
}: OverviewData) {
  const { sourceMode } = useCommandCenter()
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

      <OwnerCommandSurface />

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
