import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Activity, ArrowLeft, FileCheck2, HeartPulse, History, Layers, Server } from "lucide-react";
import { useCommandCenter } from "../context/CommandCenterContext";
import type { AgentRun, SystemComponent, TimelineEvent, Worker } from "../lib/contracts";
import { StatusBadge } from "../components/StatusBadge";
import { LoadingState, ErrorState } from "../components/states";
import { EntityRefLink, RefChipRow } from "../components/EntityRefLink";
import { relativeTime } from "../lib/format";
import { sourceModeLabel } from "../lib/sourceMode";

export function AgentDetail() {
  const { agentId } = useParams();
  const { client, version, stopActive, sourceMode } = useCommandCenter();
  const [agent, setAgent] = useState<Worker | null>(null);
  const [runs, setRuns] = useState<AgentRun[] | null>(null);
  const [system, setSystem] = useState<SystemComponent | null>(null);
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    if (!agentId) return;
    let cancelled = false;
    Promise.all([client.getAgent(agentId), client.getAgentRuns(agentId), client.listTimeline()]).then(
      ([a, r, tl]) => {
        if (cancelled) return;
        if (!a) {
          setNotFound(true);
          return;
        }
        setAgent(a);
        setRuns(r);
        setEvents(tl.filter((e) => e.agentId === agentId));
        if (a.systemId) {
          void client.getSystem(a.systemId).then((s) => {
            if (!cancelled) setSystem(s);
          });
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [agentId, client, version]);

  if (notFound) {
    return (
      <div className="shell-content">
        <ErrorState label="Agent not found" detail={sourceMode === "SIMULATED" ? "No simulated worker exists at this identifier." : sourceMode === "LIVE" ? "No canonical worker exists at this identifier." : `Worker lookup unavailable: runtime source is ${sourceModeLabel(sourceMode)}.`} />
      </div>
    );
  }

  if (!agent || !runs) {
    return (
      <div className="shell-content">
        <LoadingState label="Loading agent detail" />
      </div>
    );
  }

  const missionRefs = (agent.missionIds ?? []).map((id) => ({ kind: "mission" as const, id, label: id }));
  const evidenceRefs = (agent.evidenceIds ?? []).map((id) => ({ kind: "evidence" as const, id, label: id }));

  return (
    <div className="shell-content">
      <div className="md-breadcrumb">
        <Link to="/agents" className="md-back">
          <ArrowLeft size={14} aria-hidden="true" />
          <span className="t-label">Agents</span>
        </Link>
        <span className="t-mono text-muted md-crumb-id">{agent.id}</span>
      </div>

      <div className="md-head">
        <div className="md-head-main">
          <div className="md-head-badges">
            <StatusBadge status={agent.status} pulse={agent.status === "ACTIVE"} />
            <span className="t-mono text-muted">Provider {sourceMode === "SIMULATED" ? "fixture" : sourceMode === "LIVE" ? "runtime" : sourceModeLabel(sourceMode)}: {agent.provider}</span>
          </div>
          <h1 className="t-display md-title">{agent.name}</h1>
          <p className="prose md-desc">{agent.role}. {sourceMode === "SIMULATED" ? "Simulated worker state - never a live dispatch claim." : sourceMode === "LIVE" ? "Canonical worker state read from the runtime projection." : `Worker state source is ${sourceModeLabel(sourceMode)}; canonical dispatch status is unavailable.`}</p>
        </div>
        <div className="md-actions">
          <span className="badge badge-muted">
            <HeartPulse size={11} aria-hidden="true" />
            Last heartbeat {agent.lastHeartbeat ? relativeTime(agent.lastHeartbeat) : "UNKNOWN"}
          </span>
        </div>
      </div>

      <div className="detail-grid">
        <section className="panel panel-corner detail-panel">
          <div className="panel-header">
            <h2 className="t-label panel-title text-muted">
              <Layers size={13} className="panel-icon" />
              Current assignment
            </h2>
          </div>
          <div className="panel-body">
            <p className="prose detail-task">{agent.currentTask}</p>
            <div className="detail-section-label">
              <span className="t-label text-muted">Missions</span>
              <RefChipRow refs={missionRefs} />
            </div>
            <div className="detail-section-label">
              <span className="t-label text-muted">Runtime dependency</span>
              {system ? <EntityRefLink entity={{ kind: "system", id: system.id, label: system.name }} /> : <span className="t-mono text-dim">UNKNOWN</span>}
            </div>
          </div>
        </section>

        <section className="panel panel-corner detail-panel">
          <div className="panel-header">
            <h2 className="t-label panel-title text-muted">
              <Activity size={13} className="panel-icon" />
              History
            </h2>
          </div>
          <div className="detail-stats-grid">
            <div className="detail-stat">
              <span className="t-label text-muted">Failures</span>
              <span className="t-mono detail-stat-value">{agent.failures ?? 0}</span>
            </div>
            <div className="detail-stat">
              <span className="t-label text-muted">Recoveries</span>
              <span className="t-mono detail-stat-value">{agent.recoveries ?? 0}</span>
            </div>
            <div className="detail-stat">
              <span className="t-label text-muted">Uptime</span>
              <span className="t-mono detail-stat-value">{agent.uptime}</span>
            </div>
            <div className="detail-stat">
              <span className="t-label text-muted">Latency</span>
              <span className="t-mono detail-stat-value">{agent.latencyMs === null ? "N/A" : `${agent.latencyMs}ms`}</span>
            </div>
          </div>
        </section>
      </div>

      <section className="panel panel-corner detail-panel detail-panel-full">
        <div className="panel-header">
          <h2 className="t-label panel-title text-muted">
            <FileCheck2 size={13} className="panel-icon" />
            Evidence produced
          </h2>
          <span className="t-mono text-muted">{evidenceRefs.length} attached</span>
        </div>
        <div className="panel-body">
          <RefChipRow refs={evidenceRefs} emptyLabel={sourceMode === "SIMULATED" ? "No evidence attached in this fixture." : sourceMode === "LIVE" ? "No evidence attached to this worker." : `Evidence unavailable: runtime source is ${sourceModeLabel(sourceMode)}.`} />
        </div>
      </section>

      <section className="panel panel-corner detail-panel detail-panel-full">
        <div className="panel-header">
          <h2 className="t-label panel-title text-muted">
            <Server size={13} className="panel-icon" />
            Worker runs
          </h2>
          <span className="t-mono text-muted">{runs.length} runs</span>
        </div>
        <div className="runs-table" role="table" aria-label="Worker runs">
          <div className="runs-row runs-row-head" role="row">
            <span className="t-label text-muted">Run</span>
            <span className="t-label text-muted">Mission</span>
            <span className="t-label text-muted">State</span>
            <span className="t-label text-muted">Summary</span>
            <span className="t-label text-muted">Evidence / verdict</span>
          </div>
          {runs.map((r) => (
            <div key={r.id} className="runs-row" role="row">
              <span className="t-mono runs-run-id">{r.id}</span>
              <EntityRefLink entity={{ kind: "mission", id: r.missionId, label: r.missionId }} />
              <StatusBadge status={r.state} />
              <span className="prose runs-summary">{r.summary}</span>
              <div className="runs-refs">
                {r.evidenceIds.map((eid) => (
                  <EntityRefLink key={eid} entity={{ kind: "evidence", id: eid, label: eid }} />
                ))}
                {r.verdictId ? <EntityRefLink entity={{ kind: "verdict", id: r.verdictId, label: r.verdictId }} /> : null}
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="panel panel-corner detail-panel detail-panel-full">
        <div className="panel-header">
          <h2 className="t-label panel-title text-muted">
            <History size={13} className="panel-icon" />
            Failure / recovery events
          </h2>
          <span className="t-mono text-muted">{events.length} events</span>
        </div>
        <div className="panel-body">
          {events.length === 0 ? (
            <span className="t-mono text-dim">{sourceMode === "SIMULATED" ? "No failure or recovery events in this fixture." : sourceMode === "LIVE" ? "No failure or recovery events recorded for this worker." : `Worker events unavailable: runtime source is ${sourceModeLabel(sourceMode)}.`}</span>
          ) : (
            <ul className="detail-event-list">
              {events.map((e) => (
                <li key={e.id}>
                  <span className={`lamp lamp-${e.severity === "CRITICAL" ? "error" : e.severity === "WARNING" ? "amber" : "teal"}`} aria-hidden="true" />
                  <span className="t-mono detail-event-time">{e.at}</span>
                  <span className="t-mono detail-event-title">{e.title}</span>
                  <EntityRefLink entity={{ kind: "timeline", id: e.id, label: e.id }} />
                </li>
              ))}
            </ul>
          )}
        </div>
      </section>

      {stopActive ? (
        <p className="t-mono text-muted">{sourceMode === "SIMULATED" ? "SIMULATED STOP ACTIVE - this worker's dispatch is frozen in the UI candidate." : sourceMode === "LIVE" ? "LIVE STOP ACTIVE - this worker's dispatch is frozen by the canonical directive." : `STOP ACTIVE, but runtime source is ${sourceModeLabel(sourceMode)}.`}</p>
      ) : null}
    </div>
  );
}
