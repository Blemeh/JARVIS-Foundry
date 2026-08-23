import { useMemo, useState } from "react";
import { CheckCheck, CircleCheck, CircleDot } from "lucide-react";
import type { Notification } from "../lib/contracts";
import { RefChipRow } from "./EntityRefLink";
import { relativeTime } from "../lib/format";

type Filter = "ALL" | "UNREAD" | "CRITICAL" | "RESOLVED";

const filters: { key: Filter; label: string }[] = [
  { key: "ALL", label: "All" },
  { key: "UNREAD", label: "Unread" },
  { key: "CRITICAL", label: "Critical" },
  { key: "RESOLVED", label: "Resolved" },
];

interface NotificationCenterProps {
  notifications: Notification[];
  onAcknowledge?: (id: string) => void;
  onResolve?: (id: string) => void;
  onAcknowledgeAll?: () => void;
  compact?: boolean;
}

export function NotificationCenter({
  notifications,
  onAcknowledge,
  onResolve,
  onAcknowledgeAll,
  compact = false,
}: NotificationCenterProps) {
  const [filter, setFilter] = useState<Filter>("ALL");

  const filtered = useMemo(() => {
    if (filter === "ALL") return notifications;
    if (filter === "UNREAD") return notifications.filter((n) => n.state === "UNREAD");
    if (filter === "CRITICAL") return notifications.filter((n) => n.severity === "CRITICAL");
    return notifications.filter((n) => n.state === "RESOLVED");
  }, [notifications, filter]);

  const unread = notifications.filter((n) => n.state === "UNREAD").length;

  return (
    <div className="notify-center">
      <div className="notify-filters" role="group" aria-label="Notification filters">
        {filters.map((f) => (
          <button
            key={f.key}
            type="button"
            className={`chip ${filter === f.key ? "chip-active" : ""}`}
            aria-pressed={filter === f.key}
            onClick={() => setFilter(f.key)}
          >
            {f.label}
          </button>
        ))}
        {onAcknowledgeAll ? (
          <button type="button" className="text-link notify-ack-all" onClick={onAcknowledgeAll} disabled={unread === 0}>
            <CheckCheck size={12} aria-hidden="true" />
            Acknowledge all
          </button>
        ) : null}
      </div>

      {filtered.length === 0 ? (
        <div className="state-block notify-empty">
          <CircleCheck size={18} className="text-muted" aria-hidden="true" />
          <span className="t-label text-muted">No matching events</span>
          <p className="prose">Only meaningful JARVIS exceptions appear here.</p>
        </div>
      ) : (
        <ul className="notify-list">
          {filtered.map((n) => (
            <li key={n.id} className={`notify-item notify-item-${n.severity.toLowerCase()} ${n.state === "UNREAD" ? "notify-item-unread" : ""}`}>
              <div className="notify-item-head">
                <span className="notify-item-title">
                  <span className={`lamp lamp-${n.severity === "CRITICAL" ? "error" : n.severity === "WARNING" ? "amber" : "teal"}`} aria-hidden="true" />
                  <span className="t-label">{n.category.replaceAll("_", " ")}</span>
                </span>
                <span className={`notify-item-state notify-item-state-${n.state.toLowerCase()}`}>
                  {n.state === "UNREAD" ? <CircleDot size={11} aria-hidden="true" /> : <CircleCheck size={11} aria-hidden="true" />}
                  {n.state}
                </span>
              </div>
              <h2 className="notify-item-title-text">{n.title}</h2>
              <p className="prose notify-item-detail">{n.detail}</p>
              <div className="notify-item-meta">
                <span className="t-mono text-muted notify-item-time">{relativeTime(n.createdAt)}</span>
                {!compact ? <RefChipRow refs={n.refs} /> : n.refs.length > 0 ? <RefChipRow refs={n.refs.slice(0, 3)} /> : null}
              </div>
              {!compact && (onAcknowledge || onResolve) ? (
                <div className="notify-item-actions">
                  {n.state === "UNREAD" && onAcknowledge ? (
                    <button type="button" className="btn btn-ghost" onClick={() => onAcknowledge(n.id)}>
                      Acknowledge
                    </button>
                  ) : null}
                  {n.state !== "RESOLVED" && onResolve ? (
                    <button type="button" className="btn" onClick={() => onResolve(n.id)}>
                      Resolve
                    </button>
                  ) : null}
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
