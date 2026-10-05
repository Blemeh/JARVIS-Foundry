import { useEffect, useRef, useState } from "react";
import { Activity, Clock, Info, Menu, RefreshCw, ShieldQuestion, WifiOff } from "lucide-react";
import { useLocation } from "react-router-dom";
import { useClock } from "../lib/useClock";
import { useCommandCenter } from "../context/CommandCenterContext";
import { useRouteQuery } from "../hooks/useRouteQuery";
import { StopControl } from "./EmergencyStop";
import { TrustDialog } from "./TrustDialog";
import { NotificationBell } from "./NotificationBell";
import { sourceModeLabel } from "../lib/sourceMode";
import { LocaleToggle, useLocale } from "../i18n/LocaleContext";
import type { MessageKey } from "../i18n/LocaleContext";

const routeKeys: Record<string, MessageKey> = {
  "/": "top.currentFocus",
  "/work": "nav.work",
  "/world": "nav.world",
  "/capabilities": "nav.capabilities",
  "/you": "nav.you",
  "/activity": "nav.activity",
  "/evidence": "nav.evidence",
  "/settings": "nav.settings",
};

export function TopStatusBar({ onOpenMobileNav }: { onOpenMobileNav: () => void }) {
  const clock = useClock();
  const location = useLocation();
  const { t } = useLocale();
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
  const pageKey = routeKeys[location.pathname] ?? routeKeys[basePath];
  const page = pageKey ? t(pageKey) : t("top.systemView");
  const trustState = stopActive
    ? "SUSPENDED"
    : trustSummaryStatus === "ready" && trustSummary
      ? trustSummary.state.toUpperCase()
      : trustSummaryStatus === "error" ? "UNAVAILABLE" : "LOADING";
  const source = sourceModeLabel(sourceMode);
  const operational = streamConnected && !stopActive && (sourceMode === "LIVE" || sourceMode === "SIMULATED");
  const activity = stopActive ? "Stopped" : !streamConnected ? "Paused" : operational ? "Working" : "Waiting";
  const [systemOpen, setSystemOpen] = useState(false);
  const systemRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!systemOpen) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") setSystemOpen(false); };
    const onDown = (e: MouseEvent) => { if (systemRef.current && !systemRef.current.contains(e.target as Node)) setSystemOpen(false); };
    document.addEventListener("keydown", onKey, true);
    document.addEventListener("mousedown", onDown);
    return () => { document.removeEventListener("keydown", onKey, true); document.removeEventListener("mousedown", onDown); };
  }, [systemOpen]);

  return (
    <>
      <header className="topbar topbar--god" role="banner">
        <div className="topbar-left">
          <button type="button" className="icon-btn mobile-menu-btn" aria-label={t("nav.open")} onClick={onOpenMobileNav}>
            <Menu size={18} aria-hidden="true" />
          </button>
          <div className="topbar-context">
            <span className={`topbar-state-dot ${operational ? "is-operational" : "is-gated"}`} aria-hidden="true" />
            <div>
              <p className="topbar-brand-title">Jarvis</p>
              <p className="topbar-brand-sub">{page} · {activity}</p>
            </div>
          </div>
        </div>
        <div className="topbar-right topbar-god-meta">
          {!streamConnected ? (
            <button
              type="button"
              className="topbar-stream-reconnect chip"
              onClick={() => void reconnectStream()}
              title="Reconnect to the canonical runtime projection"
            >
              <WifiOff size={13} aria-hidden="true" />
              Reconnect
            </button>
          ) : null}
          <span className="topbar-clock" aria-label={`Local time ${clock.time}`}>
            <Clock size={14} aria-hidden="true" />
            <span className="t-num topbar-clock-time">{clock.time}</span>
          </span>
          <NotificationBell />
          <div ref={systemRef} style={{ position: "relative" }}>
            <button
              type="button"
              className="topbar-system-btn"
              aria-expanded={systemOpen}
              aria-haspopup="dialog"
              onClick={() => setSystemOpen((v) => !v)}
            >
              <Info size={14} aria-hidden="true" />
              System
            </button>
            {systemOpen ? (
              <div className="topbar-system-pop" role="dialog" aria-label="System details">
                <h3>System</h3>
                <dl>
                  <div><dt>Status</dt><dd>{stopActive ? "STOPPED" : operational ? "OPERATIONAL" : "GATED"}</dd></div>
                  <div><dt>Activity</dt><dd>{activity.toUpperCase()}</dd></div>
                  <div><dt>Data source</dt><dd>{t("top.dataSource")}: {source}</dd></div>
                  <div><dt>Schema</dt><dd>{t("top.schema")} {schemaLabel}</dd></div>
                  <div><dt>Runtime</dt><dd>{stopActive ? t("top.stopped") : operational ? t("top.operational") : t("top.gated")}</dd></div>
                  {version ? <div><dt>Release</dt><dd>{version}</dd></div> : null}
                  {connection.error ? <div><dt>Connection</dt><dd>{connection.error}</dd></div> : null}
                </dl>
                <button
                  type="button"
                  className={`topbar-trust trust-pill topbar-trust--${trustState.toLowerCase()}`}
                  onClick={openTrustDialog}
                  aria-label={`${t("top.trust")} ${trustState}. Open trust explanation.`}
                >
                  <ShieldQuestion size={14} aria-hidden="true" />
                  <span>{t("top.trust")} {trustState}</span>
                </button>
                {!streamConnected ? (
                  <button type="button" className="btn" onClick={() => void reconnectStream()}>
                    <RefreshCw size={13} aria-hidden="true" />
                    {t("top.reconnect")}
                  </button>
                ) : null}
              </div>
            ) : null}
          </div>
          <LocaleToggle compact />
          <span className="topbar-runtime" aria-label={`Runtime ${stopActive ? "stopped" : operational ? "operational" : "gated"}`}>
            <Activity size={13} aria-hidden="true" />{activity}
          </span>
          <StopControl compact />
        </div>
      </header>
      <TrustDialog open={trustDialogOpen} onClose={closeTrustDialog} />
    </>
  );
}
