import { useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { AlertTriangle, ArrowLeftRight, Flag, Search, Terminal } from "lucide-react";
import { useCommandCenter } from "../context/CommandCenterContext";
import type { EntityRef, TimelineCategory, TimelineEvent, TimelineSeverity } from "../lib/contracts";
import { Drawer } from "../components/Drawer";
import { EmptyState } from "../components/states";
import { RouteQueryGate } from "../components/RouteQueryGate";
import { ContextChip, RefChipRow } from "../components/EntityRefLink";
import { useRouteQuery } from "../hooks/useRouteQuery";
import { metaLabelToRef, refsFromTimelineEvent } from "../lib/entityRefs";
import { isCanonicalSource, sourceModeLabel } from "../lib/sourceMode";

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
  return refsFromTimelineEvent(e);
}

function TimelineContent({ events }: { events: TimelineEvent[] }) {
  const { sourceMode } = useCommandCenter();
  const [searchParams, setSearchParams] = useSearchParams();
  const [query, setQuery] = useState("");
  const [manualSelected, setManualSelected] = useState<TimelineEvent | null>(null);

  const missionFilter = searchParams.get("mission");
  const agentFilter = searchParams.get("agent");
  const systemFilter = searchParams.get("system");
  const severityFilter = searchParams.get("severity");
  const categoryFilter = searchParams.get("category");
  const eventParam = searchParams.get("event");

  const selected = useMemo(() => {
    if (manualSelected) return manualSelected;
    if (!eventParam) return null;
    return events.find((x) => x.id === eventParam) ?? null;
  }, [manualSelected, eventParam, events]);

  const filtered = useMemo(() => {
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

  return (
    <div className="shell-content">
      <div className="timeline-head">
        <div>
          <p className="t-label text-muted page-kicker">Activity</p>
          <h1 className="t-display">Activity</h1>
          <p className="prose page-desc">
            {sourceMode === "SIMULATED" ? "What Jarvis did, in plain order. Open any entry for technical detail." : isCanonicalSource(sourceMode) ? "What Jarvis did, in plain order. Open any entry for technical detail." : `Timeline source ${sourceModeLabel(sourceMode)}: activity is unavailable and no fallback is shown.`}
          </p>
        </div>
        <span className="t-mono text-muted timeline-head-hash tech-id">{sourceModeLabel(sourceMode)}</span>
      </div>

      {missionFilter || agentFilter || systemFilter || severityFilter || categoryFilter ? (
        <div className="context-row">
          {missionFilter ? (
            <ContextChip
              label={`Mission ${missionFilter}`}
              entity={{ kind: "mission", id: missionFilter, label: missionFilter }}
              onClear={() => clearParam("mission")}
            />
          ) : null}
          {agentFilter ? (
            <ContextChip
              label={`Agent ${agentFilter}`}
              entity={{ kind: "agent", id: agentFilter, label: agentFilter }}
              onClear={() => clearParam("agent")}
            />
          ) : null}
          {systemFilter ? (
            <ContextChip
              label={`System ${systemFilter}`}
              entity={{ kind: "system", id: systemFilter, label: systemFilter }}
              onClear={() => clearParam("system")}
            />
          ) : null}
          {severityFilter ? <ContextChip label={`Severity ${severityFilter}`} onClear={() => clearParam("severity")} /> : null}
          {categoryFilter ? <ContextChip label={`Category ${categoryFilter}`} onClear={() => clearParam("category")} /> : null}
        </div>
      ) : null}

      <div className="timeline-filters">
        <span className="t-mono text-muted timeline-result-count" aria-live="polite">
          {filtered.length} of {events.length} events
        </span>
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
          <EmptyState label="Empty timeline" detail={sourceMode === "SIMULATED" ? "No events match the current filters in this simulated ledger." : isCanonicalSource(sourceMode) ? "No canonical events match the current filters." : `Timeline data is unavailable: runtime source is ${sourceMode}.`} />
        </div>
      ) : (
        <div className="timeline">
          {groups.map((g) => (
            <div key={g.group} className="timeline-group">
              <h2 className="timeline-group-marker">
                <span className="t-mono">{g.group}</span>
              </h2>
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
                        setManualSelected(e);
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
            <span className="text-dim">End of activity</span>
          </div>
        </div>
      )}

      <Drawer
        open={selected !== null}
        title="Timeline Event"
        onClose={() => {
          setManualSelected(null);
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
              {selected.meta.map((m) => {
                const ref = metaLabelToRef(m.label, m.value);
                return (
                  <div key={m.label}>
                    <dt className="t-label text-muted">{m.label}</dt>
                    <dd className="t-mono">{ref ? <RefChipRow refs={[ref]} /> : m.value}</dd>
                  </div>
                );
              })}
            </dl>
            <div className="detail-section-label">
              <span className="t-label text-muted">Referenced records</span>
              <RefChipRow refs={eventRefs(selected)} />
            </div>
            <div className="record-foot">
              <span className="t-mono text-muted">{sourceModeLabel(sourceMode)} EVENT</span>
            </div>
          </div>
        ) : null}
      </Drawer>
    </div>
  );
}

export function Timeline() {
  const { client, version } = useCommandCenter();
  const eventsQuery = useRouteQuery(
    () => client.listTimeline(),
    [client, version],
    { keepPreviousWhileLoading: true },
  );

  return (
    <RouteQueryGate
      query={eventsQuery}
      loadingLabel="Loading timeline"
      errorLabel="Timeline unavailable"
      surface="Evidence spine"
    >
      {(events) => <TimelineContent events={events} />}
    </RouteQueryGate>
  );
}
