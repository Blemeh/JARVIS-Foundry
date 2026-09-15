import { ArrowUpRight, Zap } from "lucide-react";
import { Link } from "react-router-dom";
import { useCommandCenter } from "../context/CommandCenterContext";
import { NotificationCenter } from "../components/NotificationCenter";
import { MetricCard } from "../components/MetricCard";
import { SectionHeader } from "../components/SectionHeader";
import { EmptyState, ErrorState, LoadingState } from "../components/states";
import { routeFetchErrorDetail, shouldShowRouteFetchError } from "../lib/routeResilience";
import { sourceModeLabel } from "../lib/sourceMode";
import { useLocale } from "../i18n/LocaleContext";

export function Notifications() {
  const {
    notifications,
    notificationsStatus,
    notificationsError,
    unreadCount,
    acknowledgeNotification,
    resolveNotification,
    acknowledgeAll,
    simulateNextEvent,
    demoIndex,
    stopActive,
    sourceMode,
    connection,
  } = useCommandCenter();
  const { t } = useLocale();

  if (notificationsStatus === "loading") {
    return (
      <div className="shell-content">
        <LoadingState label={t("notify.loading")} />
      </div>
    );
  }

  if (
    notificationsStatus === "error" ||
    shouldShowRouteFetchError(sourceMode, notificationsError, notifications.length > 0)
  ) {
    return (
      <div className="shell-content">
        <ErrorState
          label={t("notify.unavailable")}
          detail={routeFetchErrorDetail(sourceMode, connection, notificationsError, "Notification center")}
        />
      </div>
    );
  }

  const critical = notifications.filter((n) => n.severity === "CRITICAL").length;
  const resolved = notifications.filter((n) => n.state === "RESOLVED").length;

  return (
    <div className="shell-content">
      <SectionHeader
        title={t("notify.title")}
        description="Only meaningful JARVIS events appear here: verification failures, blocked missions, worker crashes and recoveries, provider degradation, memory conflicts, STOP, and promotion or rollback state. Every item links to the object that caused it."
        actions={
          <>
            {sourceMode === "SIMULATED" ? (
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => void simulateNextEvent()}
                disabled={stopActive}
                title={stopActive ? "Frozen during STOP simulation" : "Simulate the next demo event locally (verification.failed → repair → recovery → pass)"}
              >
                <Zap size={14} aria-hidden="true" />
                Simulate next event ({demoIndex % 4 === 0 ? "failed" : demoIndex % 4 === 1 ? "repair" : demoIndex % 4 === 2 ? "recovery" : "pass"})
              </button>
            ) : null}
            <Link to="/" className="btn btn-ghost">
              {t("notify.overview")}
              <ArrowUpRight size={14} aria-hidden="true" />
            </Link>
          </>
        }
      />

      <div className="fleet-stats systems-stats">
        <MetricCard label={t("notify.total")} value={notifications.length} tone="accent" />
        <MetricCard label={t("notify.unread")} value={unreadCount} tone={unreadCount ? "amber" : "default"} />
        <MetricCard label={t("notify.critical")} value={critical} tone={critical ? "error" : "default"} />
        <MetricCard label={t("notify.resolved")} value={resolved} tone="teal" />
      </div>

      <div className="panel panel-corner notify-page">
        {notifications.length === 0 ? (
          <EmptyState
            label={t("notify.empty")}
            detail={sourceMode === "SIMULATED" ? "Nothing is demanding owner attention in this simulated universe." : sourceMode === "LIVE" ? "No canonical runtime event currently demands owner attention." : `No alerts available: runtime source is ${sourceModeLabel(sourceMode)}.`}
          />
        ) : (
          <NotificationCenter
            notifications={notifications}
            onAcknowledge={(id) => void acknowledgeNotification(id)}
            onResolve={(id) => void resolveNotification(id)}
            onAcknowledgeAll={() => void acknowledgeAll()}
          />
        )}
      </div>
    </div>
  );
}
