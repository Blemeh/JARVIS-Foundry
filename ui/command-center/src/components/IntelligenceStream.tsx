import { Link } from "react-router-dom";
import type { TimelineEvent } from "../lib/contracts";

type IntelligenceStreamProps = {
  events: TimelineEvent[];
};

function formatTime(at: string | undefined): string {
  if (!at) return "--:--:--";
  const slice = String(at).slice(11, 19);
  return slice || "--:--:--";
}

export function IntelligenceStream({ events }: IntelligenceStreamProps) {
  const stream = events.slice(0, 8);

  return (
    <section className="intel-stream" aria-label="Intelligence stream">
      <header className="zone-header">
        <h2>Intelligence stream</h2>
        <Link to="/timeline" className="zone-header-link">
          Full timeline
        </Link>
      </header>
      <ol className="intel-stream-list">
        {stream.length === 0 ? (
          <li className="intel-stream-item intel-stream-item--empty">
            <span className="intel-stream-time">--:--:--</span>
            <div>
              <strong>No events</strong>
              <p>No durable ledger signals yet.</p>
            </div>
          </li>
        ) : (
          stream.map((e) => (
            <li key={e.id} className="intel-stream-item">
              <time className="intel-stream-time" dateTime={e.at}>
                {formatTime(e.at)}
              </time>
              <div>
                <Link to={`/timeline?event=${e.id}`} className="intel-stream-title">
                  {e.title ?? e.id}
                </Link>
                <p>{e.detail ?? e.id}</p>
              </div>
            </li>
          ))
        )}
      </ol>
    </section>
  );
}
