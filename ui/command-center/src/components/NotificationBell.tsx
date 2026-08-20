import { useEffect, useRef } from "react";
import { Link } from "react-router-dom";
import { Bell, Zap } from "lucide-react";
import { useCommandCenter } from "../context/CommandCenterContext";
import { NotificationCenter } from "./NotificationCenter";

const DEMO_LABELS = ["verification.failed", "repair.created", "worker.recovered", "verification.passed"] as const;

export function NotificationBell() {
  const {
    notifications,
    unreadCount,
    notifyOpen,
    openNotify,
    closeNotify,
    simulateNextEvent,
    demoIndex,
    acknowledgeNotification,
    resolveNotification,
    stopActive,
    sourceMode,
  } = useCommandCenter();
  const wrapRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!notifyOpen) return;
    const onDown = (e: MouseEvent) => {
      if (!wrapRef.current?.contains(e.target as Node)) closeNotify();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") closeNotify();
    };
    document.addEventListener("mousedown", onDown);
    document.addEventListener("keydown", onKey, true);
    return () => {
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey, true);
    };
  }, [notifyOpen, closeNotify]);

  const nextEvent = DEMO_LABELS[demoIndex % DEMO_LABELS.length];

  return (
    <div className="notify-wrap" ref={wrapRef}>
      <button
        type="button"
        className="notify-bell"
        onClick={notifyOpen ? closeNotify : openNotify}
        aria-label={`Notifications, ${unreadCount} unread`}
        aria-expanded={notifyOpen}
      >
        <Bell size={15} aria-hidden="true" />
        {unreadCount > 0 ? (
          <span className="notify-badge" aria-hidden="true">
            {unreadCount > 9 ? "9+" : unreadCount}
          </span>
        ) : null}
      </button>
      {notifyOpen ? (
        <div className="notify-popover" role="dialog" aria-label="Notification center">
          <div className="notify-popover-head">
            <span className="t-label">Exceptions</span>
            <span className="t-mono text-muted">
              {unreadCount} unread / {notifications.length} total
            </span>
          </div>
          <NotificationCenter
            notifications={notifications.slice(0, 6)}
            onAcknowledge={acknowledgeNotification}
            onResolve={resolveNotification}
            compact
          />
          <div className="notify-popover-foot">
            {sourceMode === "SIMULATED" ? (
              <button
                type="button"
                className="btn btn-ghost"
                onClick={() => void simulateNextEvent()}
                disabled={stopActive}
                title={`Simulates the next demo event: ${nextEvent} (local only)`}
              >
                <Zap size={12} aria-hidden="true" />
                Simulate {nextEvent}
              </button>
            ) : null}
            <Link to="/notifications" className="text-link" onClick={closeNotify}>
              View all
            </Link>
          </div>
        </div>
      ) : null}
    </div>
  );
}
