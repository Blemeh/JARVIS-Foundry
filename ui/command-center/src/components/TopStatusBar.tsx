import { Clock, Database, Menu, OctagonX, ShieldQuestion, WifiOff } from "lucide-react";
import { useClock } from "../lib/useClock";
import { useCommandCenter } from "../context/CommandCenterContext";
import { StopControl } from "./EmergencyStop";
import { TrustDialog } from "./TrustDialog";
import { NotificationBell } from "./NotificationBell";

function sourceLabel(source: string, stale: boolean) {
  if (source === "DISCONNECTED") return "Disconnected";
  if (stale) return "Stale";
  if (source === "DEGRADED") return "Degraded";
  if (source === "LIVE") return "Live";
  return "Simulated";
}

export function TopStatusBar({ onOpenMobileNav }: { onOpenMobileNav: () => void }) {
  const clock = useClock();
  const { stopActive, trustDialogOpen, openTrustDialog, closeTrustDialog, connection, mode } = useCommandCenter();
  const pillClass =
    connection.source === "DISCONNECTED"
      ? "data-source-pill data-source-disconnected"
      : connection.source === "DEGRADED" || connection.stale
        ? "data-source-pill data-source-degraded"
        : connection.source === "LIVE"
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
            {connection.source === "DISCONNECTED" ? <WifiOff size={11} aria-hidden="true" /> : <Database size={11} aria-hidden="true" />}
            <span className="t-label">Data Source: {sourceLabel(connection.source, connection.stale)}</span>
          </span>
        </div>
        <div className="topbar-right">
          <NotificationBell />
          {stopActive ? (
            <span className="topbar-stop-chip" title={mode === "live" ? "STOP active in canonical runtime" : "SIMULATED STOP - UI CANDIDATE"}>
              <OctagonX size={11} aria-hidden="true" />
              <span className="t-label">Suspended</span>
            </span>
          ) : null}
          <button type="button" className="trust-pill" onClick={openTrustDialog} aria-label="Explain trust state">
            <ShieldQuestion size={13} aria-hidden="true" />
            <span className="t-label">
              Trust:{" "}
              <span className={stopActive ? "trust-state-error" : mode === "live" ? "trust-state-live" : "trust-state-sim"}>
                {mode === "live" ? (connection.source === "DISCONNECTED" ? "UNKNOWN" : "LIVE") : "SIMULATED"}
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
