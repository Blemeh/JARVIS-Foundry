import { Clock, Database, Menu, OctagonX, ShieldQuestion, Wifi, WifiOff } from "lucide-react";
import { useClock } from "../lib/useClock";
import { useCommandCenter } from "../context/CommandCenterContext";
import { useRouteQuery } from "../hooks/useRouteQuery";
import { StopControl } from "./EmergencyStop";
import { TrustDialog } from "./TrustDialog";
import { NotificationBell } from "./NotificationBell";
import { sourceModeLabel } from "../lib/sourceMode";

export function TopStatusBar({ onOpenMobileNav }: { onOpenMobileNav: () => void }) {
  const clock = useClock();
  const {
    stopActive,
    trustDialogOpen,
    openTrustDialog,
    closeTrustDialog,
    sourceMode,
    streamConnected,
    trustSummary,
    trustSummaryStatus,
    connection,
    reconnectStream,
    client,
    version,
  } = useCommandCenter();

  const schemaQuery = useRouteQuery(() => client.getSystemOverview(), [client, version]);
  const schemaLabel = schemaQuery.data?.version ?? "UNKNOWN";

  // Keep source mode and trust state separate.  Source mode tells the owner
  // where this projection came from; trust state tells them whether its
  // claims have a canonical proof.  Neither is silently promoted to LIVE.
  const trustStateLabel = stopActive
    ? "SUSPENDED"
    : trustSummaryStatus === "ready" && trustSummary
      ? trustSummary.state?.toUpperCase() ?? "UNKNOWN"
      : trustSummaryStatus === "error"
        ? "UNAVAILABLE"
        : "LOADING";

  const sourcePillClass =
    sourceMode === "LIVE"
      ? "data-source-live topbar-god-pill--live"
      : sourceMode === "SIMULATED"
        ? "topbar-god-pill--caution"
        : sourceMode === "DEGRADED" || sourceMode === "UNKNOWN"
          ? "data-source-degraded topbar-god-pill--caution"
          : "data-source-disconnected topbar-god-pill--critical";

  const neuralState = stopActive
    ? "SUSPENDED"
    : !streamConnected
      ? "WAIT"
      : sourceMode === "LIVE"
        ? "LIVE"
        : sourceMode === "SIMULATED"
          ? "SIM"
          : "GATED";

  const neuralClass = stopActive
    ? "neural-link-suspended"
    : neuralState === "LIVE"
      ? "neural-link-live"
      : neuralState === "SIM"
        ? "neural-link-sim"
        : "";

  const trustClass =
    stopActive || trustStateLabel === "UNAVAILABLE" || trustStateLabel === "SUSPENDED"
      ? "topbar-god-pill--critical"
      : trustStateLabel === "PROVEN"
        ? "topbar-god-pill--nominal"
        : "topbar-god-pill--caution";

  const schemaVersion = schemaLabel;

  return (
    <>
      <header className="topbar topbar--god" role="banner">
        <div className="topbar-left">
          <button type="button" className="icon-btn mobile-menu-btn" aria-label="Open navigation" onClick={onOpenMobileNav}>
            <Menu size={18} />
          </button>
          <div className="topbar-brand-block">
            <p className="topbar-brand-title">J.A.R.V.I.S.</p>
            <p className="topbar-brand-sub">Autonomous intelligence system</p>
          </div>
        </div>
        <div className="topbar-right topbar-god-meta">
          <NotificationBell />
          <span
            className={`topbar-god-pill data-source-pill ${sourcePillClass}`}
            title={connection.error ?? `Data source: ${sourceModeLabel(sourceMode)}`}
          >
            {sourceMode === "DISCONNECTED" ? <WifiOff size={11} aria-hidden="true" /> : <Database size={11} aria-hidden="true" />}
            <span>DATA SOURCE: {sourceModeLabel(sourceMode)}</span>
          </span>
          <span
            className={`topbar-god-pill neural-link-pill ${neuralClass}`}
            title={`Neural Link state: ${neuralState}. This is an interface status, not voice capability.`}
          >
            <Wifi size={11} aria-hidden="true" />
            <span>NEURAL LINK</span>
            <span className="neural-link-state">{neuralState}</span>
          </span>
          {!streamConnected ? (
            <span
              className="topbar-stream-chip"
              role="status"
              title={
                sourceMode === "SIMULATED"
                  ? "Simulated event stream disconnected; refresh or run NORMAL_OPERATION to reconnect."
                  : "Canonical event stream disconnected; live updates paused until reconnect."
              }
            >
              <WifiOff size={11} aria-hidden="true" />
              <span className="t-label">Stream disconnected</span>
              {sourceMode === "SIMULATED" ? (
                <button
                  type="button"
                  className="topbar-stream-reconnect"
                  onClick={() => void reconnectStream()}
                  aria-label="Reconnect simulated event stream"
                >
                  Reconnect
                </button>
              ) : null}
            </span>
          ) : null}
          {stopActive ? (
            <span
              className="topbar-stop-chip"
              title={
                sourceMode === "LIVE"
                  ? "STOP active in canonical runtime"
                  : sourceMode === "SIMULATED"
                    ? "SIMULATED STOP - UI CANDIDATE"
                    : `STOP state ${sourceModeLabel(sourceMode)}; runtime state unavailable`
              }
            >
              <OctagonX size={11} aria-hidden="true" />
              <span className="t-label">Suspended</span>
            </span>
          ) : null}
          <span className="topbar-god-pill topbar-schema-pill" title="Active schema version">
            Schema {schemaVersion}
          </span>
          <button
            type="button"
            className={`topbar-god-pill trust-pill ${trustClass}`}
            onClick={openTrustDialog}
            aria-label={`Trust state ${trustStateLabel}. Open trust explanation.`}
            title={trustSummary ? `Trust state ${trustStateLabel}; health ${trustSummary.health}.` : "Trust state is not available."}
          >
            <ShieldQuestion size={13} aria-hidden="true" />
            <span>Trust {trustStateLabel}</span>
          </button>
          <span className="topbar-clock" aria-label={`Local time ${clock.time}`}>
            <Clock size={14} aria-hidden="true" />
            <span className="t-num topbar-clock-time">{clock.time}</span>
          </span>
          <StopControl compact />
        </div>
      </header>
      <TrustDialog open={trustDialogOpen} onClose={closeTrustDialog} />
    </>
  );
}
