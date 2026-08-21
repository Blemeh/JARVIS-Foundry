import { Clock, Database, Menu, OctagonX, ShieldQuestion, WifiOff } from "lucide-react";
import { useClock } from "../lib/useClock";
import { useCommandCenter } from "../context/CommandCenterContext";
import { StopControl } from "./EmergencyStop";
import { TrustDialog } from "./TrustDialog";
import { NotificationBell } from "./NotificationBell";
import { sourceModeLabel } from "../lib/sourceMode";

export function TopStatusBar({ onOpenMobileNav }: { onOpenMobileNav: () => void }) {
  const clock = useClock();
  const { stopActive, trustDialogOpen, openTrustDialog, closeTrustDialog, connection, sourceMode } = useCommandCenter();
  const pillClass =
    sourceMode === "DISCONNECTED"
      ? "data-source-pill data-source-disconnected"
      : sourceMode === "DEGRADED" || sourceMode === "UNKNOWN"
        ? "data-source-pill data-source-degraded"
        : sourceMode === "LIVE"
          ? "data-source-pill data-source-live"
          : "data-source-pill";

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
        </div>
        <div className="topbar-right">
          <NotificationBell />
          {stopActive ? (
            <span className="topbar-stop-chip" title={sourceMode === "LIVE" ? "STOP active in canonical runtime" : sourceMode === "SIMULATED" ? "SIMULATED STOP - UI CANDIDATE" : `STOP state ${sourceModeLabel(sourceMode)}; runtime state unavailable`}>
              <OctagonX size={11} aria-hidden="true" />
              <span className="t-label">Suspended</span>
            </span>
          ) : null}
          <button type="button" className="trust-pill" onClick={openTrustDialog} aria-label="Explain trust state">
            <ShieldQuestion size={13} aria-hidden="true" />
            <span className="t-label">
              Trust:{" "}
              <span className={stopActive ? "trust-state-error" : sourceMode === "LIVE" ? "trust-state-live" : sourceMode === "SIMULATED" ? "trust-state-sim" : "trust-state-error"}>
                {sourceModeLabel(sourceMode)}
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
