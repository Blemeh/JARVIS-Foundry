import { useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Activity, Bolt, FileSearch, Plus, Search } from "lucide-react";
import { useCommandCenter } from "../context/CommandCenterContext";
import { SectionHeader } from "../components/SectionHeader";
import { StatusBadge } from "../components/StatusBadge";
import { MetricCard } from "../components/MetricCard";
import { Dialog } from "../components/Dialog";
import { EmptyState } from "../components/states";
import { RouteQueryGate } from "../components/RouteQueryGate";
import { ContextChip } from "../components/EntityRefLink";
import { useRouteQuery } from "../hooks/useRouteQuery";
import { sourceCopy, sourceModeLabel } from "../lib/sourceMode";

export function Agents() {
  const { client, version, stopActive, sourceMode } = useCommandCenter();
  const [searchParams, setSearchParams] = useSearchParams();
  const workersQuery = useRouteQuery(
    () => client.listWorkers(),
    [client, version],
  );

  const missionFilter = searchParams.get("mission");
  const statusFilter = searchParams.get("status");

  const [query, setQuery] = useState("");
  const [deployOpen, setDeployOpen] = useState(false);
  const [deployed, setDeployed] = useState(false);

  const filtered = (workersQuery.data ?? []).filter((w) => {
    const q = query.trim().toLowerCase();
    if (q && !`${w.name} ${w.role} ${w.provider} ${w.id}`.toLowerCase().includes(q)) return false;
    if (missionFilter && !(w.missionIds ?? []).includes(missionFilter)) return false;
    if (statusFilter === "active" && w.status !== "ACTIVE") return false;
    return true;
  });

  const submitDeploy = (e: FormEvent) => {
    e.preventDefault();
    setDeployed(true);
  };

  return (
    <RouteQueryGate
      query={workersQuery}
      loadingLabel="Loading agent fleet"
      errorLabel="Agent fleet unavailable"
      surface="Specialist fleet"
    >
      {(workers) => {
        const counts = {
          active: workers.filter((w) => w.status === "ACTIVE").length,
          standby: workers.filter((w) => w.status === "STANDBY").length,
          degraded: workers.filter((w) => w.status === "DEGRADED" || w.status === "RATE_LIMITED").length,
          atRisk: workers.filter((w) => w.status === "FAILED" || w.status === "RECOVERING" || w.status === "RATE_LIMITED").length,
        }
        return (
    <div className="shell-content">
      <SectionHeader
        kicker="Specialist Fleet"
        title="Specialist Fleet"
        description={sourceCopy(sourceMode, {
          live: "Canonical JARVIS workers: orchestrators, build executors, verifiers, memory reconcilers, routing, and the deterministic STOP gatekeeper.",
          simulated: "Simulated JARVIS workers for tests and demos: orchestrators, build executors, verifiers, memory reconcilers, routing, and the deterministic STOP gatekeeper.",
          unavailable: `Worker fleet unavailable: runtime source is ${sourceModeLabel(sourceMode)}; no fixture fallback is shown.`,
        })}
        actions={
          <>
            <button type="button" className="btn" onClick={() => setDeployOpen(true)} disabled={stopActive || sourceMode !== "SIMULATED"} title={stopActive ? `Frozen during ${sourceModeLabel(sourceMode)} STOP` : sourceMode !== "SIMULATED" ? `Worker deployment unavailable in ${sourceModeLabel(sourceMode)} source mode` : undefined}>
              <Plus size={14} />
              Deploy Agent
            </button>
          </>
        }
      />

      {missionFilter || statusFilter ? (
        <div className="context-row">
          {missionFilter ? (
            <ContextChip
              label={`Workers for mission ${missionFilter}`}
              onClear={() => setSearchParams({}, { replace: true })}
            />
          ) : null}
          {statusFilter ? (
            <ContextChip
              label={`Workers ${statusFilter.toUpperCase()}`}
              onClear={() => setSearchParams({}, { replace: true })}
            />
          ) : null}
        </div>
      ) : null}

      <div className="fleet-stats">
        <MetricCard label="Active" value={counts.active} tone="accent" />
        <MetricCard label="Standby" value={counts.standby} tone="teal" />
        <MetricCard label="Degraded" value={counts.degraded} tone={counts.degraded ? "amber" : "default"} />
        <MetricCard label="At risk" value={counts.atRisk} tone={counts.atRisk ? "error" : "default"} />
      </div>

      <div className="agents-toolbar">
        <div className="agents-search">
          <Search size={14} className="text-muted" aria-hidden="true" />
          <input
            className="field agents-search-input"
            placeholder="Filter workers by name, role, provider..."
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            aria-label="Filter workers"
          />
        </div>
        <span className="t-mono text-muted">
          {filtered.length} of {workers.length} workers
        </span>
      </div>

      {filtered.length === 0 ? (
        <div className="panel panel-corner">
          <EmptyState label="No workers match" detail={sourceMode === "SIMULATED" ? "Adjust the search or context filter to see workers in this simulated universe." : sourceMode === "LIVE" ? "No canonical workers match the current filters." : `Worker data is unavailable: runtime source is ${sourceMode}.`} />
        </div>
      ) : (
        <div className="agent-grid">
          {filtered.map((w) => (
            <article key={w.id} className={`agent-card panel panel-corner ${w.status === "DEGRADED" ? "agent-card-warn" : ""}`}>
              <div className="agent-card-head">
                <div className="agent-card-identity">
                  <span className={`agent-card-icon agent-card-icon-${w.status.toLowerCase()}`} aria-hidden="true">
                    {w.role.includes("Verif") ? <FileSearch size={18} /> : <Activity size={18} />}
                  </span>
                  <div>
                    <Link to={`/agents/${w.id}`} className="agent-card-name-link">
                      <h2 className="agent-card-name">{w.name}</h2>
                    </Link>
                    <p className="t-mono agent-card-id">
                      <Link to={`/agents/${w.id}`}>{w.id}</Link> | {w.provider}
                    </p>
                  </div>
                </div>
                <StatusBadge status={w.status} pulse={w.status === "ACTIVE"} />
              </div>
              <div className="agent-card-body">
                <p className="t-label text-muted agent-task-label">Current task</p>
                <p className="agent-card-task">{w.currentTask}</p>
                <div className="agent-card-log" aria-label={`${w.name} recent log`}>
                  {w.log.map((line, i) => (
                    <div key={i} className={`agent-log-line ${line.includes("_") ? "agent-log-line-pulse" : ""}`}>
                      {line}
                    </div>
                  ))}
                </div>
              </div>
              <div className="agent-card-foot">
                <span className="t-mono text-muted">UPTIME: {w.uptime}</span>
                <span className="t-mono text-muted">{w.latencyMs === null ? "LATENCY: N/A" : `LATENCY: ${w.latencyMs}ms`}</span>
                <Link to={`/agents/${w.id}`} className="text-link" aria-label={`Open detail for ${w.name}`}>
                  <Bolt size={12} aria-hidden="true" />
                  DETAIL
                </Link>
              </div>
            </article>
          ))}
        </div>
      )}

      <Dialog
        open={deployOpen}
        title={sourceMode === "SIMULATED" ? "Deploy Agent - Simulated" : `Deploy Agent - ${sourceModeLabel(sourceMode)}`}
        onClose={() => {
          setDeployOpen(false);
          setDeployed(false);
        }}
        footer={
          deployed ? (
            <span className="t-mono text-muted">DEPLOYMENT STAGED LOCALLY - NO WORKER DISPATCHED</span>
          ) : (
            <button type="submit" form="deploy-form" className="btn btn-primary">
              Stage deployment
            </button>
          )
        }
      >
        {deployed ? (
          <div className="state-block">
            <span className="t-label">Deployment staged</span>
            <p className="prose">
              This candidate does not dispatch workers. The future runtime adapter will route staged deployments
              through the Supervisor's worker subsystem.
            </p>
          </div>
        ) : (
          <form id="deploy-form" className="sim-form" onSubmit={submitDeploy}>
            <label className="sim-field">
              <span className="t-label text-muted">Worker role</span>
              <select className="field" defaultValue="Verifier">
                <option>Verifier</option>
                <option>Builder</option>
                <option>Memory Reconciler</option>
                <option>Evidence Collector</option>
              </select>
            </label>
            <label className="sim-field">
              <span className="t-label text-muted">Provider</span>
              <select className="field" defaultValue="opencode-go/deepseek-v4-flash">
                <option>opencode-go/deepseek-v4-flash</option>
                <option>opencode-go/deepseek-v4-pro</option>
                <option>internal deterministic loop</option>
              </select>
            </label>
            <p className="prose sim-note">{sourceMode === "SIMULATED" ? "Simulated form. No worker is dispatched to any runtime." : `Worker deployment is unavailable in ${sourceModeLabel(sourceMode)} source mode.`}</p>
          </form>
        )}
      </Dialog>
    </div>
        )
      }}
    </RouteQueryGate>
  );
}
