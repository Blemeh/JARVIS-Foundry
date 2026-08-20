import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { AlertTriangle, ArrowLeftRight, Flag, Search, Terminal } from "lucide-react";
import { useCommandCenter } from "../context/CommandCenterContext";
import type { EntityRef, TimelineCategory, TimelineEvent, TimelineSeverity } from "../lib/contracts";
import { Drawer } from "../components/Drawer";
import { EmptyState, LoadingState } from "../components/states";
import { ContextChip, RefChipRow } from "../components/EntityRefLink";

const filters: { key: "ALL" | TimelineCategory; label: string }[] = [
  { key: "ALL", label: "All Events" },
  { key: "INCIDENT", label: "Incidents" },
  { key: "HANDOFF", label: "Handoffs" },
  { key: "MILESTONE", label: "Milestones" },
  { key: "COMMAND", label: "Commands" },
  { key: "SYSTEM", label: "System" },
];

const severityFilters: { key: "ALL" | TimelineSeverity; label: string }[] = [
  { key: "ALL", label: "Any severity" },
  { key: "INFO", label: "Info" },
  { key: "WARNING", label: "Warning" },
  { key: "CRITICAL", label: "Critical" },
];

const categoryIcon: Record<TimelineCategory, typeof Flag> = {
  INCIDENT: AlertTriangle,
  HANDOFF: ArrowLeftRight,
  MILESTONE: Flag,
  COMMAND: Terminal,
  SYSTEM: Terminal,
};

function eventRefs(e: TimelineEvent): EntityRef[] {
  const refs: EntityRef[] = [];
  if (e.missionId) refs.push({ kind: "mission", id: e.missionId, label: e.missionId });
  if (e.agentId) refs.push({ kind: "agent", id: e.agentId, label: e.agentId });
  if (e.systemId) refs.push({ kind: "system", id: e.systemId, label: e.systemId });
  if (e.evidenceId) refs.push({ kind: "evidence", id: e.evidenceId, label: e.evidenceId });
  if (e.verdictId) refs.push({ kind: "verdict", id: e.verdictId, label: e.verdictId });
  if (e.memoryId) refs.push({ kind: "memory", id: e.memoryId, label: e.memoryId });
  return refs;
}

export function Timeline() {
  const { client, version } = useCommandCenter();
  const [searchParams, setSearchParams] = useSearchParams();
  const [events, setEvents] = useState<TimelineEvent[] | null>(null);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<TimelineEvent | null>(null);

  const missionFilter = searchParams.get("mission");
  const agentFilter = searchParams.get("agent");
  const systemFilter = searchParams.get("system");
  const severityFilter = searchParams.get("severity");
  const categoryFilter = searchParams.get("category");
  const eventParam = searchParams.get("event");

  useEffect(() => {
    let cancelled = false;
    client.listTimeline().then((e) => {
      if (!cancelled) {
        setEvents(e);
        if (eventParam) {
          const found = e.find((x) => x.id === eventParam);
          setSelected(found ?? null);
        }
      }
    });
    return () => {
      cancelled = true;
    };
  }, [client, version, eventParam]);

  const filtered = useMemo(() => {
    if (!events) return [];
    const q = query.trim().toLowerCase();
    return events.filter((e) => {
      const catOk = !categoryFilter || e.category === categoryFilter;
      const sevOk = !severityFilter || e.severity === severityFilter;
      const missionOk = !missionFilter || e.missionId === missionFilter;
      const agentOk = !agentFilter || e.agentId === agentFilter;
      const systemOk = !systemFilter || e.systemId === systemFilter;
      const qOk = !q || `${e.title} ${e.detail} ${e.id}`.toLowerCase().includes(q);
      return catOk && sevOk && missionOk && agentOk && systemOk && qOk;
    });
  }, [events, query, missionFilter, agentFilter, systemFilter, severityFilter, categoryFilter]);

  const groups = useMemo(() => {
    const order = ["NOW", "T-1 HOUR", "T-6 HOURS", "T-24 HOURS"];
    const map = new Map<string, TimelineEvent[]>();
    filtered.forEach((e) => {
      const list = map.get(e.group) ?? [];
      list.push(e);
      map.set(e.group, list);
    });
    return order.filter((g) => map.has(g)).map((g) => ({ group: g, events: map.get(g)! }));
  }, [filtered]);

  const clearParam = (key: string) => {
    const next = new URLSearchParams(searchParams);
    next.delete(key);
    setSearchParams(next, { replace: true });
  };

  if (!events) {
    return (
      <div className="shell-content">
        <LoadingState label="Loading timeline" />
      </div>
    );
  }

  return (
    <div className="shell-content">
      <div className="timeline-head">
        <div>
          <p className="t-label text-muted page-kicker">Event Ledger</p>
          <h1 className="t-display">Timeline</h1>
          <p className="prose page-desc">
            Simulated ledger sequence for missions, handoffs, verifier gates, recovery, and STOP drills. Detail views
            are local only and every reference is navigable.
          </p>
        </div>
        <span className="t-mono text-muted timeline-head-hash">SYSTEM AUDIT HASH: 8F9A.22C (FIXTURE)</span>
      </div>

      {missionFilter || agentFilter || systemFilter || severityFilter || categoryFilter ? (
        <div className="context-row">
          {missionFilter ? <ContextChip label={`Mission ${missionFilter}`} onClear={() => clearParam("mission")} /> : null}
          {agentFilter ? <ContextChip label={`Agent ${agentFilter}`} onClear={() => clearParam("agent")} /> : null}
          {systemFilter ? <ContextChip label={`System ${systemFilter}`} onClear={() => clearParam("system")} /> : null}
          {severityFilter ? <ContextChip label={`Severity ${severityFilter}`} onClear={() => clearParam("severity")} /> : null}
          {categoryFilter ? <ContextChip label={`Category ${categoryFilter}`} onClear={() => clearParam("category")} /> : null}
        </div>
      ) : null}

      <div className="timeline-filters">
        {filters.map((f) => (
          <button
            key={f.key}
            type="button"
            className={`chip ${categoryFilter === f.key || (f.key === "ALL" && !categoryFilter) ? "chip-active" : ""}`}
            aria-pressed={categoryFilter === f.key || (f.key === "ALL" && !categoryFilter)}
            onClick={() => {
              const next = new URLSearchParams(searchParams);
              if (f.key === "ALL") next.delete("category");
              else next.set("category", f.key);
              setSearchParams(next, { replace: true });
            }}
          >
            {f.label}
          </button>
        ))}
        <span className="timeline-filter-divider" aria-hidden="true" />
        {severityFilters.map((f) => (
          <button
            key={f.key}
            type="button"
            className={`chip ${severityFilter === f.key || (f.key === "ALL" && !severityFilter) ? "chip-active" : ""}`}
            aria-pressed={severityFilter === f.key || (f.key === "ALL" && !severityFilter)}
            onClick={() => {
              const next = new URLSearchParams(searchParams);
              if (f.key === "ALL") next.delete("severity");
              else next.set("severity", f.key);
              setSearchParams(next, { replace: true });
            }}
          >
            {f.label}
          </button>
        ))}
        <div className="timeline-search">
          <Search size={13} className="text-muted" aria-hidden="true" />
          <input
            className="timeline-search-input"
            placeholder="Search events..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label="Search timeline events"
          />
        </div>
      </div>

      {groups.length === 0 ? (
        <div className="panel panel-corner">
          <EmptyState label="Empty timeline" detail="No events match the current filters in this simulated ledger." />
        </div>
      ) : (
        <div className="timeline">
          {groups.map((g) => (
            <div key={g.group} className="timeline-group">
              <div className="timeline-group-marker">
                <span className="t-mono">{g.group}</span>
              </div>
              {g.events.map((e) => {
                const Icon = categoryIcon[e.category];
                const refs = eventRefs(e);
                return (
                  <article key={e.id} className={`tl-event tl-event-${e.category.toLowerCase()}`}>
                    <span className="tl-node" aria-hidden="true" />
                    <button
                      type="button"
                      className="tl-card panel panel-corner"
                      onClick={() => {
                        setSelected(e);
                        const next = new URLSearchParams(searchParams);
                        next.set("event", e.id);
                        setSearchParams(next, { replace: true });
                      }}
                      aria-label={`Open timeline event ${e.title}`}
                    >
                      <div className="tl-card-head">
                        <span className="tl-card-cat">
                          <Icon size={15} aria-hidden="true" />
                          <span className="t-label">{e.category}</span>
                          {e.severity ? <span className={`tl-severity tl-severity-${e.severity.toLowerCase()}`} aria-label={`Severity ${e.severity}`} /> : null}
                        </span>
                        <span className="t-mono text-muted">{e.at}</span>
                      </div>
                      <h3 className="tl-card-title">{e.title}</h3>
                      <p className="prose tl-card-detail">{e.detail}</p>
                      <div className="tl-card-meta">
                        {e.meta.map((m) => (
                          <span key={m.label} className="tl-meta">
                            <span className="t-label text-muted">{m.label}:</span>
                            <span className="t-mono">{m.value}</span>
                          </span>
                        ))}
                      </div>
                    </button>
                    {refs.length > 0 ? (
                      <div className="tl-refs-row">
                        <RefChipRow refs={refs} />
                      </div>
                    ) : null}
                  </article>
                );
              })}
            </div>
          ))}
          <div className="timeline-end">
            <span className="t-mono text-dim">--- END OF SIMULATED LEDGER ---</span>
          </div>
        </div>
      )}

      <Drawer
        open={selected !== null}
        title="Timeline Event"
        onClose={() => {
          setSelected(null);
          const next = new URLSearchParams(searchParams);
          next.delete("event");
          setSearchParams(next, { replace: true });
        }}
      >
        {selected ? (
          <div className="record-detail">
            <div className="record-detail-head">
              <span className="t-label tl-drawer-category">{selected.category}</span>
              <span className="t-mono text-muted">{selected.at}</span>
            </div>
            <h3 className="t-title tl-drawer-title">{selected.title}</h3>
            <p className="prose">{selected.detail}</p>
            <dl className="record-fields">
              {selected.meta.map((m) => (
                <div key={m.label}>
                  <dt className="t-label text-muted">{m.label}</dt>
                  <dd className="t-mono">{m.value}</dd>
                </div>
              ))}
            </dl>
            <div className="detail-section-label">
              <span className="t-label text-muted">Referenced records</span>
              <RefChipRow refs={eventRefs(selected)} />
            </div>
            <div className="record-foot">
              <span className="t-mono text-muted">SIMULATED EVENT</span>
            </div>
          </div>
        ) : null}
      </Drawer>
    </div>
  );
}
