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

export type SystemIndicator = "Healthy" | "Degraded" | "Down";
export type AutonomyIndicator = "Running" | "Waiting" | "Stopped";
export type TrustIndicator = "Proven" | "Suspended" | "Unknown";

export const TRUST_SUSPENDED_EXPLANATION =
  "Suspended — missing live-observation evidence. Permitted work can still run within constraints.";
const TRUST_PROVEN_EXPLANATION = "Proven — live-observation evidence is current.";
const TRUST_UNKNOWN_EXPLANATION = "Unknown — trust evidence is unavailable.";

/**
 * Display-only System indicator (Task 6). Derived from the supervisor status
 * string plus the degraded flag carried as trust health. Trust suspension
 * alone never produces Down.
 */
export function deriveSystemIndicator(
  supervisorStatus: string | null | undefined,
  trustHealth: string | null | undefined,
): SystemIndicator {
  const raw = (supervisorStatus ?? "").toUpperCase();
  if (/(FAIL|CRITICAL|\bDOWN\b|STOPPED|DEAD|OFFLINE)/.test(raw)) return "Down";
  const health = (trustHealth ?? "").toUpperCase();
  if (health === "DEGRADED" || health === "CRITICAL" || /DEGRAD/.test(raw)) return "Degraded";
  if (/(RUNNING|HEALTHY|READY|PASS|OPERATIONAL)/.test(raw) || health === "HEALTHY") return "Healthy";
  // Unknown or simulated-candidate supervisor state stays honest without
  // ever reading as system-down.
  return "Degraded";
}

/**
 * Display-only Autonomy indicator (Task 6). Derived from the autonomy
 * executive state plus the STOP sentinel. STOP semantics are untouched:
 * only the label changes.
 */
export function deriveAutonomyIndicator(
  executiveState: string | null | undefined,
  stopActive: boolean,
): AutonomyIndicator {
  if (stopActive) return "Stopped";
  const raw = (executiveState ?? "").toUpperCase();
  if (raw.includes("STOP")) return "Stopped";
  if (/(RUN|EXECUT|WORK|OBSERV|PLAN|RESEARCH|VERIF|ACTIVE|DOING|THINK|JUDG)/.test(raw)) return "Running";
  return "Waiting";
}

/**
 * Display-only Trust indicator (Task 6). Read-only over the mapped trust
 * summary: no change to snapshotMapper.mapTrustSummary or STOP semantics.
 */
export function deriveTrustIndicator(
  trustState: string | null | undefined,
  stopActive: boolean,
): { label: TrustIndicator; reason: string } {
  const raw = (trustState ?? "").toUpperCase();
  if (stopActive || raw === "SUSPENDED") {
    return { label: "Suspended", reason: TRUST_SUSPENDED_EXPLANATION };
  }
  if (raw === "PROVEN") {
    return { label: "Proven", reason: TRUST_PROVEN_EXPLANATION };
  }
  return { label: "Unknown", reason: TRUST_UNKNOWN_EXPLANATION };
}

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
  const autonomyQuery = useRouteQuery(() => client.getAutonomy(), [client, version]);
  const schemaLabel = schemaQuery.data?.schema == null ? "UNKNOWN" : String(schemaQuery.data.schema);
  const basePath = `/${location.pathname.split("/").filter(Boolean)[0] ?? ""}`;
  const pageKey = routeKeys[location.pathname] ?? routeKeys[basePath];
  const page = pageKey ? t(pageKey) : t("top.systemView");
  const trustState = stopActive
    ? "SUSPENDED"
    : trustSummaryStatus === "ready" && trustSummary
      ? trustSummary.state.toUpperCase()
      : trustSummaryStatus === "error" ? "UNAVAILABLE" : "LOADING";
  const systemLabel = deriveSystemIndicator(schemaQuery.data?.releaseState, trustSummary?.health);
  const autonomyLabel = deriveAutonomyIndicator(autonomyQuery.data?.executive?.state, stopActive);
  const trustView = deriveTrustIndicator(trustSummary?.state, stopActive);
  const trustSuspended = trustView.label === "Suspended";
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
                  <div className="topbar-system-row"><dt>System</dt><dd>{systemLabel}</dd></div>
                  <div className="topbar-system-row"><dt>Autonomy</dt><dd>{autonomyLabel}</dd></div>
                  <div
                    className="topbar-system-row"
                    title={trustSuspended ? TRUST_SUSPENDED_EXPLANATION : undefined}
                  ><dt>Trust</dt><dd>{trustView.reason}</dd></div>
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
                  title={trustSuspended ? TRUST_SUSPENDED_EXPLANATION : undefined}
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
