import { Link, useParams } from "react-router-dom";
import { ArrowLeft, Cpu, HeartPulse, History, ShieldAlert } from "lucide-react";
import { useCommandCenter } from "../context/CommandCenterContext";
import type { SystemComponent, TimelineEvent } from "../lib/contracts";
import { StatusBadge } from "../components/StatusBadge";
import { DetailRouteQueryGate } from "../components/DetailRouteQueryGate";
import { EntityRefLink, RefChipRow } from "../components/EntityRefLink";
import { useRouteQuery } from "../hooks/useRouteQuery";
import { relativeTime } from "../lib/format";
import { sourceModeLabel } from "../lib/sourceMode";

type SystemDetailData = {
  system: SystemComponent;
  events: TimelineEvent[];
};

export function SystemDetail() {
  const { systemId } = useParams();
  const { client, version, stopActive, sourceMode } = useCommandCenter();
  const detailQuery = useRouteQuery(
    async () => {
      if (!systemId) return null;
      const [system, timeline] = await Promise.all([
        client.getSystem(systemId),
        client.listTimeline(),
      ]);
      if (!system) return null;
      return {
        system,
        events: timeline.filter((e) => e.systemId === systemId),
      } satisfies SystemDetailData;
    },
    [client, version, systemId],
  );

  return (
    <DetailRouteQueryGate
      query={detailQuery}
      loadingLabel="Loading system detail"
      notFoundLabel="System not found"
      notFoundDetail={sourceMode === "SIMULATED" ? "No simulated component exists at this identifier." : sourceMode === "LIVE" ? "No canonical component exists at this identifier." : `System lookup unavailable: runtime source is ${sourceModeLabel(sourceMode)}.`}
      errorLabel="System detail unavailable"
      surface="System detail"
    >
      {({ system, events }) => {
  const depRefs = system.dependencies.map((id) => ({ kind: "system" as const, id, label: id }));
  const missionRefs = (system.missionIds ?? []).map((id) => ({ kind: "mission" as const, id, label: id }));

  return (
    <div className="shell-content">
      <div className="md-breadcrumb">
        <Link to="/systems" className="md-back">
          <ArrowLeft size={14} aria-hidden="true" />
          <span className="t-label">Systems</span>
        </Link>
        <span className="t-mono text-muted md-crumb-id">{system.id}</span>
      </div>

      <div className="md-head">
        <div className="md-head-main">
          <div className="md-head-badges">
            <StatusBadge status={system.health} />
            <StatusBadge status={system.state} />
            <StatusBadge status={system.readiness} />
            {system.id === "SYS-STOP" && stopActive ? <StatusBadge status="ACTIVE" /> : null}
          </div>
          <h1 className="t-display md-title">{system.name}</h1>
          <p className="prose md-desc">{system.description}</p>
        </div>
        <div className="md-actions">
          <span className="badge badge-muted">
            <HeartPulse size={11} aria-hidden="true" />
            Heartbeat {relativeTime(system.lastHeartbeat)}
          </span>
          <span className="t-mono text-muted">v{system.version}</span>
        </div>
      </div>

      <div className="detail-grid">
        <section className="panel panel-corner detail-panel">
          <div className="panel-header">
            <h2 className="t-label panel-title text-muted">
              <Cpu size={13} className="panel-icon" />
              Relationships
            </h2>
          </div>
          <div className="panel-body detail-sections">
            <div className="detail-section-label">
              <span className="t-label text-muted">Dependencies</span>
              <RefChipRow refs={depRefs} emptyLabel="No dependencies" />
            </div>
            <div className="detail-section-label">
              <span className="t-label text-muted">Affected missions</span>
              <RefChipRow refs={missionRefs} emptyLabel="No missions touch this component" />
            </div>
          </div>
        </section>

        <section className="panel panel-corner detail-panel">
          <div className="panel-header">
            <h2 className="t-label panel-title text-muted">Operational state</h2>
          </div>
          <dl className="record-fields panel-body">
            <div>
              <dt className="t-label text-muted">Recovery state</dt>
              <dd className="t-mono">{system.recovery}</dd>
            </div>
            <div>
              <dt className="t-label text-muted">Version / build</dt>
              <dd className="t-mono">{system.version}</dd>
            </div>
            <div>
              <dt className="t-label text-muted">Last heartbeat</dt>
              <dd className="t-mono">{system.lastHeartbeat}</dd>
            </div>
            <div>
              <dt className="t-label text-muted">STOP status</dt>
              <dd className="t-mono">
                {system.id === "SYS-STOP" && stopActive ? sourceMode === "SIMULATED" ? "ACTIVE (SIMULATED - UI CANDIDATE)" : sourceMode === "LIVE" ? "ACTIVE (LIVE CANONICAL DIRECTIVE)" : `ACTIVE (${sourceModeLabel(sourceMode)} SOURCE)` : system.id === "SYS-STOP" ? "ARMED" : "Not applicable"}
              </dd>
            </div>
          </dl>
        </section>
      </div>

      <section className="panel panel-corner detail-panel detail-panel-full">
        <div className="panel-header">
          <h2 className="t-label panel-title text-muted">
            <History size={13} className="panel-icon" />
            Recent incidents
          </h2>
          <span className="t-mono text-muted">{events.length} events</span>
        </div>
        <div className="panel-body">
          {events.length === 0 ? (
            <span className="t-mono text-dim">No incidents recorded for this component.</span>
          ) : (
            <ul className="detail-event-list">
              {events.map((e) => (
                <li key={e.id}>
                  <span className={`lamp lamp-${e.severity === "CRITICAL" ? "error" : e.severity === "WARNING" ? "amber" : "teal"}`} aria-hidden="true" />
                  <span className="t-mono detail-event-title">{e.title}</span>
                  <EntityRefLink entity={{ kind: "timeline", id: e.id, label: e.id }} />
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      <section className="panel panel-corner detail-panel detail-panel-full">
        <div className="panel-header">
          <h2 className="t-label panel-title text-muted">
            <ShieldAlert size={13} className="panel-icon" />
            Checks
          </h2>
        </div>
        <ul className="systems-checks panel-body">
          {system.checks.map((ch) => (
            <li key={ch.label} className="systems-check">
              <span className="systems-check-head">
                <span className="t-mono">{ch.label}</span>
                <StatusBadge status={ch.state} />
              </span>
              <span className="t-mono text-muted">{ch.detail}</span>
            </li>
          ))}
        </ul>
      </section>

      <p className="t-mono text-muted">{sourceMode === "SIMULATED" ? "SIMULATED COMPONENT STATE - no live component is being read or written." : sourceMode === "LIVE" ? "LIVE COMPONENT STATE - read from the canonical runtime projection; no component is written." : `COMPONENT STATE ${sourceModeLabel(sourceMode)} - canonical runtime state is unavailable.`}</p>
    </div>
  );
      }}
    </DetailRouteQueryGate>
  );
}
