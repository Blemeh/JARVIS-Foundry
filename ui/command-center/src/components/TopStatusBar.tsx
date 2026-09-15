import { Activity, Clock, Database, Menu, RefreshCw, ShieldQuestion, WifiOff } from "lucide-react";
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
              <p className="topbar-brand-title">J.A.R.V.I.S.</p>
              <p className="topbar-brand-sub">{page}</p>
            </div>
          </div>
        </div>
        <div className="topbar-right topbar-god-meta">
          {!streamConnected ? (
            <span className="topbar-disconnect">
              <WifiOff size={13} aria-hidden="true" />
              <span>{t("top.updatesPaused")}</span>
              <button
                type="button"
                className="topbar-stream-reconnect"
                onClick={() => void reconnectStream()}
                title="Reconnect to the canonical runtime projection"
              >
                <RefreshCw size={11} aria-hidden="true" />
                {t("top.reconnect")}
              </button>
            </span>
          ) : null}
          <span className="topbar-source data-source-pill" title={connection.error ?? `Data source: ${source}`}>
            <Database size={13} aria-hidden="true" />
            <span>{t("top.dataSource")}: {source}</span>
          </span>
          <span className="topbar-schema" title="Active schema version">{t("top.schema")} {schemaLabel}</span>
          <button
            type="button"
            className={`topbar-trust trust-pill topbar-trust--${trustState.toLowerCase()}`}
            onClick={openTrustDialog}
            aria-label={`${t("top.trust")} ${trustState}. Open trust explanation.`}
          >
            <ShieldQuestion size={14} aria-hidden="true" />
            <span>{t("top.trust")} {trustState}</span>
          </button>
          <LocaleToggle compact />
          <NotificationBell />
          <span className="topbar-clock" aria-label={`Local time ${clock.time}`}>
            <Clock size={13} aria-hidden="true" />
            <span className="t-num topbar-clock-time">{clock.time}</span>
          </span>
          <span className="topbar-runtime" aria-label={`Runtime ${stopActive ? "stopped" : operational ? "operational" : "gated"}`}>
            <Activity size={13} aria-hidden="true" />{stopActive ? t("top.stopped") : operational ? t("top.operational") : t("top.gated")}
          </span>
          <StopControl compact />
        </div>
      </header>
      <TrustDialog open={trustDialogOpen} onClose={closeTrustDialog} />
    </>
  );
}
