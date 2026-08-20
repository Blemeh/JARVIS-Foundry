import { ArrowUpRight, Zap } from "lucide-react";
import { Link } from "react-router-dom";
import { useCommandCenter } from "../context/CommandCenterContext";
import { NotificationCenter } from "../components/NotificationCenter";
import { MetricCard } from "../components/MetricCard";
import { SectionHeader } from "../components/SectionHeader";
import { EmptyState } from "../components/states";

export function Notifications() {
  const {
    notifications,
    unreadCount,
    acknowledgeNotification,
    resolveNotification,
    acknowledgeAll,
    simulateNextEvent,
    demoIndex,
    stopActive,
    sourceMode,
  } = useCommandCenter();

  const critical = notifications.filter((n) => n.severity === "CRITICAL").length;
  const resolved = notifications.filter((n) => n.state === "RESOLVED").length;

  return (
    <div className="shell-content">
      <SectionHeader
        title="Notification / Exception Center"
        description="Only meaningful JARVIS events appear here: verification failures, blocked missions, worker crashes and recoveries, provider degradation, memory conflicts, STOP, and promotion or rollback state. Every item links to the object that caused it."
        actions={
          <>
            {sourceMode === "SIMULATED" ? (
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => void simulateNextEvent()}
                disabled={stopActive}
                title="Simulate the next demo event locally (verification.failed → repair → recovery → pass)"
              >
                <Zap size={14} aria-hidden="true" />
                Simulate next event ({demoIndex % 4 === 0 ? "failed" : demoIndex % 4 === 1 ? "repair" : demoIndex % 4 === 2 ? "recovery" : "pass"})
              </button>
            ) : null}
            <Link to="/" className="btn btn-ghost">
              Overview
              <ArrowUpRight size={14} aria-hidden="true" />
            </Link>
          </>
        }
      />

      <div className="fleet-stats systems-stats">
        <MetricCard label="Total events" value={notifications.length} tone="accent" />
        <MetricCard label="Unread" value={unreadCount} tone={unreadCount ? "amber" : "default"} />
        <MetricCard label="Critical" value={critical} tone={critical ? "error" : "default"} />
        <MetricCard label="Resolved" value={resolved} tone="teal" />
      </div>

      <div className="panel panel-corner notify-page">
        {notifications.length === 0 ? (
          <EmptyState
            label="No alerts"
            detail={sourceMode === "SIMULATED" ? "Nothing is demanding owner attention in this simulated universe." : sourceMode === "LIVE" ? "No canonical runtime event currently demands owner attention." : `No alerts available: runtime source is ${sourceMode}.`}
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
