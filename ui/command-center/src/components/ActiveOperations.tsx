import { Link } from "react-router-dom";
import type { Mission, Worker } from "../lib/contracts";

function toneForStatus(status: string): "nominal" | "caution" | "critical" {
  const s = (status ?? "UNKNOWN").toUpperCase();
  if (["BLOCKED", "HALTED", "FAILED", "CRITICAL", "STOPPED"].includes(s)) return "critical";
  if (["DEGRADED", "UNKNOWN", "STANDBY", "WAITING", "INTERVENTION"].includes(s)) return "caution";
  return "nominal";
}

function statusGlyph(tone: "nominal" | "caution" | "critical", status: string): string {
  const s = status.toUpperCase();
  if (s === "COMPLETE" || s === "HEALTHY") return "✓";
  if (tone === "caution") return "○";
  if (tone === "critical") return "●";
  return "●";
}

type ActiveOperationsProps = {
  missions: Mission[];
  workers: Worker[];
};

export function ActiveOperations({ missions, workers }: ActiveOperationsProps) {
  const threads = missions.slice(0, 6);

  return (
    <section className="active-ops" aria-label="Active operations">
      <header className="zone-header">
        <h2>Active operations</h2>
      </header>
      <div className="active-ops-list">
        {threads.length === 0 ? (
          <p className="active-ops-empty">No durable missions recorded.</p>
        ) : (
          threads.map((m) => {
            const tone = toneForStatus(m.status);
            const worker = workers.find((w) => w.missionIds?.includes(m.id));
            return (
              <Link key={m.id} to={`/missions/${m.id}`} className={`op-thread op-thread--${tone}`}>
                <div className="op-thread-head">
                  <span className="op-thread-code">{m.code ?? m.id}</span>
                  <span className="op-thread-status">
                    <span aria-hidden="true">{statusGlyph(tone, m.status)}</span>
                    {m.status}
                  </span>
                </div>
                <p className="op-thread-title">{m.title}</p>
                <div className="op-thread-meta">
                  <span>{worker?.name ?? m.nextAction ?? m.stage ?? "UNKNOWN"}</span>
                  {m.startedAt ? <time dateTime={m.startedAt}>{formatElapsed(m.startedAt)}</time> : null}
                </div>
              </Link>
            );
          })
        )}
      </div>
    </section>
  );
}

function formatElapsed(iso: string): string {
  const start = Date.parse(iso);
  if (!Number.isFinite(start)) return "UNKNOWN";
  const mins = Math.max(0, Math.floor((Date.now() - start) / 60000));
  if (mins < 60) return `${mins}m`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 48) return `${hrs}h`;
  return `${Math.floor(hrs / 24)}d`;
}
