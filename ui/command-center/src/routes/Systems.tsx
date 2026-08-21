import { useEffect, useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ArrowUpRight, Cpu, HeartPulse, Server, ShieldAlert } from "lucide-react";
import { useCommandCenter } from "../context/CommandCenterContext";
import type { SystemComponent, SystemOverview } from "../lib/contracts";
import { SectionHeader } from "../components/SectionHeader";
import { StatusBadge } from "../components/StatusBadge";
import { MetricCard } from "../components/MetricCard";
import { Drawer } from "../components/Drawer";
import { EmptyState, LoadingState } from "../components/states";
import { ContextChip } from "../components/EntityRefLink";
import { sourceCopy, sourceModeLabel } from "../lib/sourceMode";

export function Systems() {
  const { client, version, stopActive, sourceMode } = useCommandCenter();
  const [searchParams, setSearchParams] = useSearchParams();
  const [components, setComponents] = useState<SystemComponent[] | null>(null);
  const [overview, setOverview] = useState<SystemOverview | null>(null);
  const [selected, setSelected] = useState<SystemComponent | null>(null);

  const healthFilter = searchParams.get("health");
  const missionFilter = searchParams.get("mission");

  useEffect(() => {
    let cancelled = false;
    Promise.all([client.listSystemComponents(), client.getSystemOverview()]).then(([c, o]) => {
      if (!cancelled) {
        setComponents(c);
        setOverview(o);
      }
    });
    return () => {
      cancelled = true;
    };
  }, [client, version]);

  const grouped = useMemo(() => {
    if (!components) return [];
    const order: SystemComponent["health"][] = ["HEALTHY", "DEGRADED", "CRITICAL", "UNKNOWN"];
    return [...components]
      .filter((c) => {
        const healthOk = !healthFilter || c.health.toLowerCase() === healthFilter;
        const missionOk = !missionFilter || (c.missionIds ?? []).includes(missionFilter);
        return healthOk && missionOk;
      })
      .sort((a, b) => order.indexOf(a.health) - order.indexOf(b.health) || a.name.localeCompare(b.name));
  }, [components, healthFilter, missionFilter]);

  if (!components || !overview) {
    return (
      <div className="shell-content">
        <LoadingState label="Loading systems" />
      </div>
    );
  }

  const healthy = components.filter((c) => c.health === "HEALTHY").length;
  const degraded = components.filter((c) => c.health === "DEGRADED").length;
  const stopped = components.filter((c) => c.state === "STOPPED").length;

  return (
    <div className="shell-content">
      <SectionHeader
        kicker="Telemetry"
        title="Telemetry"
        description={sourceCopy(sourceMode, {
          live: "Canonical projection of JARVIS components: supervisor, gateway, durable state, hybrid memory, workers, routing, STOP, ledger, verification, and resource health.",
          simulated: "Simulated projection of JARVIS components for tests and demos: supervisor, gateway, durable state, hybrid memory, workers, routing, STOP, ledger, verification, and resource health.",
          unavailable: `System projection unavailable: runtime source is ${sourceModeLabel(sourceMode)}; no fixture fallback is shown.`,
        })}
        actions={
          <span className="badge badge-amber">
            <ShieldAlert size={11} aria-hidden="true" />
            {overview.releaseState}
          </span>
        }
      />

      {healthFilter || missionFilter ? (
        <div className="context-row">
          {healthFilter ? (
            <ContextChip
              label={`Health ${healthFilter.toUpperCase()}`}
              onClear={() => {
                const next = new URLSearchParams(searchParams);
                next.delete("health");
                setSearchParams(next, { replace: true });
              }}
            />
          ) : null}
          {missionFilter ? (
            <ContextChip
              label={`Systems touched by ${missionFilter}`}
              onClear={() => {
                const next = new URLSearchParams(searchParams);
                next.delete("mission");
                setSearchParams(next, { replace: true });
              }}
            />
          ) : null}
        </div>
      ) : null}

      <div className="fleet-stats systems-stats">
        <MetricCard label="Components" value={components.length} tone="accent" />
        <MetricCard label="Healthy" value={healthy} tone="teal" />
        <MetricCard label="Degraded" value={degraded} tone={degraded ? "amber" : "default"} />
        <MetricCard label="Stopped" value={stopped} tone={stopped ? "error" : "default"} />
      </div>

      <div className="systems-overview panel panel-corner">
        <div className="panel-header">
          <h2 className="t-label panel-title text-muted">
            <Server size={13} className="panel-icon" />
            Foundry Resource Profile
          </h2>
          <span className="t-mono text-muted">v{overview.version} / {overview.build}</span>
        </div>
        <div className="systems-resources">
          {overview.resourceUsage.map((r) => (
            <div key={r.label} className="resource-bar">
              <div className="resource-bar-head">
                <span className="t-label text-muted">{r.label}</span>
                <span className="t-mono resource-bar-value">{r.value}{r.unit}</span>
              </div>
              <div className="meter">
                <div className={`meter-fill ${r.value > 70 ? "meter-fill-amber" : "meter-fill-teal"}`} style={{ width: `${r.value}%` }} />
              </div>
            </div>
          ))}
        </div>
      </div>

      <div className="systems-table panel panel-corner">
        <div className="evidence-toolbar">
          <span className="t-mono text-muted">COMPONENTS - CLICK A ROW TO INSPECT</span>
          <span className="t-mono text-muted">{sourceMode === "SIMULATED" ? "HEARTBEATS ARE FIXTURE VALUES" : sourceMode === "LIVE" ? "HEARTBEATS ARE CANONICAL VALUES" : `HEARTBEATS ${sourceModeLabel(sourceMode)}`}</span>
        </div>
        {grouped.length === 0 ? (
          <EmptyState label="No systems match" detail="Clear the context filter to see the full component set." />
        ) : (
          <>
            <div className="systems-grid systems-grid-head" role="row">
              <span className="t-label text-muted">Component</span>
              <span className="t-label text-muted">State</span>
              <span className="t-label text-muted">Health</span>
              <span className="t-label text-muted">Readiness</span>
              <span className="t-label text-muted">Dependencies</span>
              <span className="t-label text-muted">Last heartbeat</span>
              <span className="t-label text-muted">Recovery state</span>
              <span className="t-label text-muted">Version / Build</span>
            </div>
            {grouped.map((c) => (
              <button
                key={c.id}
                type="button"
                className={`systems-grid systems-grid-row ${c.health === "DEGRADED" ? "systems-row-warn" : ""}`}
                role="row"
                onClick={() => setSelected(c)}
                aria-label={`Inspect ${c.name}`}
              >
                <span className="systems-cell-name">
                  <span className={`systems-cell-icon ${c.health === "DEGRADED" ? "systems-cell-icon-warn" : ""}`} aria-hidden="true">
                    {c.id === "SYS-STOP" ? <ShieldAlert size={15} /> : c.id === "SYS-SUPERVISOR" ? <Cpu size={15} /> : <HeartPulse size={15} />}
                  </span>
                  <span className="t-mono">{c.name}</span>
                </span>
                <span className="systems-cell"><StatusBadge status={c.state} /></span>
                <span className="systems-cell"><StatusBadge status={c.health} /></span>
                <span className="systems-cell"><StatusBadge status={c.readiness} /></span>
                <span className="t-mono text-muted systems-cell-deps">{c.dependencies.length === 0 ? "none" : c.dependencies.join(", ")}</span>
                <span className="t-mono systems-cell-hb">{c.lastHeartbeat.slice(11, 19)}Z</span>
                <span className="t-mono systems-cell-recovery">{c.recovery}</span>
                <span className="t-mono text-muted">{c.version}</span>
              </button>
            ))}
          </>
        )}
      </div>

      <Drawer open={selected !== null} title="Component Inspector" onClose={() => setSelected(null)}>
        {selected ? (
          <div className="record-detail">
            <div className="record-detail-head">
              <StatusBadge status={selected.health} />
              <span className="t-mono text-muted">v{selected.version}</span>
            </div>
            <h3 className="t-title">{selected.name}</h3>
            <p className="prose">{selected.description}</p>
            <dl className="record-fields">
              <div>
                <dt className="t-label text-muted">State</dt>
                <dd className="t-mono">{selected.state}</dd>
              </div>
              <div>
                <dt className="t-label text-muted">Readiness</dt>
                <dd className="t-mono">{selected.readiness}</dd>
              </div>
              <div>
                <dt className="t-label text-muted">Dependencies</dt>
                <dd className="t-mono">{selected.dependencies.length === 0 ? "none" : selected.dependencies.join(", ")}</dd>
              </div>
              <div>
                <dt className="t-label text-muted">Last heartbeat</dt>
                <dd className="t-mono">{selected.lastHeartbeat}</dd>
              </div>
              <div>
                <dt className="t-label text-muted">Recovery state</dt>
                <dd className="t-mono">{selected.recovery}</dd>
              </div>
              <div>
                <dt className="t-label text-muted">Affected missions</dt>
                <dd className="t-mono">{(selected.missionIds ?? []).length === 0 ? "none" : (selected.missionIds ?? []).join(", ")}</dd>
              </div>
            </dl>
            <h4 className="t-label text-muted record-section-title">Checks</h4>
            <ul className="systems-checks">
              {selected.checks.map((ch) => (
                <li key={ch.label} className="systems-check">
                  <span className="systems-check-head">
                    <span className="t-mono">{ch.label}</span>
                    <StatusBadge status={ch.state} />
                  </span>
                  <span className="t-mono text-muted">{ch.detail}</span>
                </li>
              ))}
            </ul>
            <div className="record-foot">
              <Link to={`/systems/${selected.id}`} className="btn btn-primary" onClick={() => setSelected(null)}>
                Open system detail
                <ArrowUpRight size={13} aria-hidden="true" />
              </Link>
              <span className="t-mono text-muted">{sourceModeLabel(sourceMode)} COMPONENT STATE{stopActive && selected.id === "SYS-STOP" ? ` · STOP ACTIVE (${sourceModeLabel(sourceMode)})` : ""}</span>
            </div>
          </div>
        ) : null}
      </Drawer>
    </div>
  );
}
