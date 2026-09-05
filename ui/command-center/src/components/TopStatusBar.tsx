import { Activity, Clock, Database, Menu, RefreshCw, ShieldQuestion, WifiOff } from "lucide-react";
import { useLocation } from "react-router-dom";
import { useClock } from "../lib/useClock";
import { useCommandCenter } from "../context/CommandCenterContext";
import { useRouteQuery } from "../hooks/useRouteQuery";
import { StopControl } from "./EmergencyStop";
import { TrustDialog } from "./TrustDialog";
import { NotificationBell } from "./NotificationBell";
import { sourceModeLabel } from "../lib/sourceMode";

const routeNames: Record<string, string> = {
  "/": "Current focus",
  "/work": "Work",
  "/world": "World",
  "/capabilities": "Capabilities",
  "/you": "You",
  "/activity": "Activity",
  "/evidence": "Evidence",
  "/settings": "Settings",
};

export function TopStatusBar({ onOpenMobileNav }: { onOpenMobileNav: () => void }) {
  const clock = useClock();
  const location = useLocation();
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
    client,
    version,
    reconnectStream,
  } = useCommandCenter();
  const schemaQuery = useRouteQuery(() => client.getSystemOverview(), [client, version]);
  const schemaLabel = schemaQuery.data?.schema == null ? "UNKNOWN" : String(schemaQuery.data.schema);
  const basePath = `/${location.pathname.split("/").filter(Boolean)[0] ?? ""}`;
  const page = routeNames[location.pathname] ?? routeNames[basePath] ?? "System view";
  const trustState = stopActive
    ? "SUSPENDED"
    : trustSummaryStatus === "ready" && trustSummary
      ? trustSummary.state.toUpperCase()
      : trustSummaryStatus === "error" ? "UNAVAILABLE" : "LOADING";
  const source = sourceModeLabel(sourceMode);
  const operational = streamConnected && !stopActive && (sourceMode === "LIVE" || sourceMode === "SIMULATED");

  return (
    <>
      <header className="topbar topbar--god" role="banner">
        <div className="topbar-left">
          <button type="button" className="icon-btn mobile-menu-btn" aria-label="Open navigation" onClick={onOpenMobileNav}>
            <Menu size={18} aria-hidden="true" />
          </button>
          <div className="topbar-context">
            <span className={`topbar-state-dot ${operational ? "is-operational" : "is-gated"}`} aria-hidden="true" />
            <div>
              <p className="topbar-brand-title">J.A.R.V.I.S.</p>
              <p className="topbar-brand-sub">{page}</p>
            </div>
          </div>
        </div>
        <div className="topbar-right topbar-god-meta">
          {!streamConnected ? (
            <span className="topbar-disconnect">
              <WifiOff size={13} aria-hidden="true" />
              <span>Updates paused</span>
              <button
                type="button"
                className="topbar-stream-reconnect"
                onClick={() => void reconnectStream()}
                title="Reconnect to the canonical runtime projection"
              >
                <RefreshCw size={11} aria-hidden="true" />
                Reconnect
              </button>
            </span>
          ) : null}
          <span className="topbar-source data-source-pill" title={connection.error ?? `Data source: ${source}`}>
            <Database size={13} aria-hidden="true" />
            <span>DATA SOURCE: {source}</span>
          </span>
          <span className="topbar-schema" title="Active schema version">Schema {schemaLabel}</span>
          <button
            type="button"
            className={`topbar-trust trust-pill topbar-trust--${trustState.toLowerCase()}`}
            onClick={openTrustDialog}
            aria-label={`Trust state ${trustState}. Open trust explanation.`}
          >
            <ShieldQuestion size={14} aria-hidden="true" />
            <span>Trust {trustState}</span>
          </button>
          <NotificationBell />
          <span className="topbar-clock" aria-label={`Local time ${clock.time}`}>
            <Clock size={13} aria-hidden="true" />
            <span className="t-num topbar-clock-time">{clock.time}</span>
          </span>
          <span className="topbar-runtime" aria-label={`Runtime ${stopActive ? "stopped" : operational ? "operational" : "gated"}`}>
            <Activity size={13} aria-hidden="true" />{stopActive ? "Stopped" : operational ? "Operational" : "Gated"}
          </span>
          <StopControl compact />
        </div>
      </header>
      <TrustDialog open={trustDialogOpen} onClose={closeTrustDialog} />
    </>
  );
}
