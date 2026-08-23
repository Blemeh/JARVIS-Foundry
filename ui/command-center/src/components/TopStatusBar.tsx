import { Clock, Database, Menu, OctagonX, ShieldQuestion, Wifi, WifiOff } from "lucide-react";
import { useClock } from "../lib/useClock";
import { useCommandCenter } from "../context/CommandCenterContext";
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
    connection,
    sourceMode,
    streamConnected,
    reconnectStream,
    trustSummary,
    trustSummaryStatus,
  } = useCommandCenter();
  const trustStateLabel = trustSummaryStatus === "ready" && trustSummary
    ? trustSummary.state
    : trustSummaryStatus === "error"
      ? "UNAVAILABLE"
      : "LOADING";
  const pillClass =
    sourceMode === "DISCONNECTED"
      ? "data-source-pill data-source-disconnected"
      : sourceMode === "DEGRADED" || sourceMode === "UNKNOWN"
        ? "data-source-pill data-source-degraded"
        : sourceMode === "LIVE"
          ? "data-source-pill data-source-live"
        : "data-source-pill";
  const neuralState = stopActive
    ? "SUSPENDED"
    : !streamConnected
      ? "WAIT"
      : sourceMode === "LIVE"
        ? "LIVE"
        : sourceMode === "SIMULATED"
          ? "SIM"
          : "GATED";

  return (
    <>
      <header className="topbar" role="banner">
        <div className="topbar-left">
          <button type="button" className="icon-btn mobile-menu-btn" aria-label="Open navigation" onClick={onOpenMobileNav}>
            <Menu size={18} />
          </button>
          <span className={pillClass} title={connection.error ?? `Data source: ${connection.source}`}>
            {sourceMode === "DISCONNECTED" ? <WifiOff size={11} aria-hidden="true" /> : <Database size={11} aria-hidden="true" />}
            <span className="t-label">Data Source: {sourceModeLabel(sourceMode)}</span>
          </span>
          <span
            className={`neural-link-pill ${stopActive ? "neural-link-suspended" : neuralState === "LIVE" ? "neural-link-live" : neuralState === "SIM" ? "neural-link-sim" : ""}`}
            title={`Neural Link state: ${neuralState}. This is an interface status, not voice capability.`}
          >
            <Wifi size={11} aria-hidden="true" />
            <span className="t-label">Neural Link</span>
            <span className="t-label neural-link-state">{neuralState}</span>
          </span>
        </div>
        <div className="topbar-right">
          <NotificationBell />
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
            <span className="topbar-stop-chip" title={sourceMode === "LIVE" ? "STOP active in canonical runtime" : sourceMode === "SIMULATED" ? "SIMULATED STOP - UI CANDIDATE" : `STOP state ${sourceModeLabel(sourceMode)}; runtime state unavailable`}>
              <OctagonX size={11} aria-hidden="true" />
              <span className="t-label">Suspended</span>
            </span>
          ) : null}
          <button type="button" className="trust-pill" onClick={openTrustDialog} aria-label={`Trust state ${trustStateLabel}. Open trust explanation.`}>
            <ShieldQuestion size={13} aria-hidden="true" />
            <span className="t-label">
              Trust:{" "}
              <span className={stopActive || trustStateLabel === "SUSPENDED" || trustStateLabel === "UNAVAILABLE" ? "trust-state-error" : trustStateLabel === "SIMULATED" ? "trust-state-sim" : trustStateLabel === "PROVEN" ? "trust-state-live" : "trust-state-error"}>
                {trustStateLabel}
              </span>
            </span>
          </button>
          <span className="topbar-divider" aria-hidden="true" />
          <span className="topbar-clock" aria-label={`Local time ${clock.time}, ${clock.date}`}>
            <Clock size={14} aria-hidden="true" />
            <span className="t-num topbar-clock-time">{clock.time}</span>
            <span className="t-mono topbar-clock-date">{clock.date}</span>
            <span className="t-mono topbar-clock-tz">{clock.tz}</span>
          </span>
          <span className="topbar-divider" aria-hidden="true" />
          <StopControl compact />
        </div>
      </header>
      <TrustDialog open={trustDialogOpen} onClose={closeTrustDialog} />
    </>
  );
}
