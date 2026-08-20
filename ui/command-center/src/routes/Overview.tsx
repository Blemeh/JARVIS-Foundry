import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  Activity,
  ArrowRight,
  BellRing,
  Braces,
  ChevronRight,
  Cpu,
  HardDrive,
  History,
  MemoryStick,
  ShieldQuestion,
} from "lucide-react";
import { useCommandCenter } from "../context/CommandCenterContext";
import type { EvidenceArtifact, Mission, SystemComponent, SystemOverview, TimelineEvent, TrustSummary, Worker } from "../lib/contracts";
import { Panel } from "../components/Panel";
import { StatusBadge } from "../components/StatusBadge";
import { SectionHeader } from "../components/SectionHeader";
import { LoadingState } from "../components/states";

export function Overview() {
  const { client, version, openTrustDialog, notifications, unreadCount } = useCommandCenter();
  const [missions, setMissions] = useState<Mission[] | null>(null);
  const [overview, setOverview] = useState<SystemOverview | null>(null);
  const [trust, setTrust] = useState<TrustSummary | null>(null);
  const [workers, setWorkers] = useState<Worker[] | null>(null);
  const [evidence, setEvidence] = useState<EvidenceArtifact[] | null>(null);
  const [timeline, setTimeline] = useState<TimelineEvent[] | null>(null);
  const [systems, setSystems] = useState<SystemComponent[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      client.listMissions(),
      client.getSystemOverview(),
      client.getTrustSummary(),
      client.listWorkers(),
      client.listEvidence(),
      client.listTimeline(),
      client.listSystemComponents(),
    ]).then(([m, o, t, w, e, tl, s]) => {
      if (cancelled) return;
      setMissions(m);
      setOverview(o);
      setTrust(t);
      setWorkers(w);
      setEvidence(e);
      setTimeline(tl);
      setSystems(s);
    });
    return () => {
      cancelled = true;
    };
  }, [client, version]);

  if (!missions || !overview || !trust || !workers || !evidence || !timeline || !systems) {
    return (
      <div className="shell-content">
        <LoadingState label="Loading command center" />
      </div>
    );
  }

  const active = missions.filter((m) => m.status === "ACTIVE" || m.status === "IN_PROGRESS" || m.status === "INTERVENTION");
  const degradedSystems = systems.filter((s) => s.health === "DEGRADED" || s.health === "CRITICAL");
  const failedVerification = evidence.filter((e) => e.verdict === "FAILED" || e.verdict === "TAMPERED");
  const activeWorkers = workers.filter((w) => w.status === "ACTIVE");
  const atRiskWorkers = workers.filter((w) => w.status === "FAILED" || w.status === "RECOVERING" || w.status === "RATE_LIMITED");
  const memoryConflict = notifications.find((n) => n.category === "MEMORY_CONFLICT" && n.state !== "RESOLVED");

  const resourceBars = [
    { label: "Supervisor core", value: overview.resourceUsage[0].value, tone: "accent" as const },
    { label: "Hybrid memory", value: overview.resourceUsage[1].value, tone: "teal" as const },
    { label: "Provider routing", value: overview.resourceUsage[3].value, tone: "amber" as const },
    { label: "Worker pool", value: 58, tone: "accent" as const },
  ];

  return (
    <div className="shell-content">
      <SectionHeader
        kicker="Foundry"
        title="Command Center"
        description="Operational projection of the JARVIS autonomy runtime. Every displayed value is simulated fixture data; the runtime adapter will replace it with ledger-backed measurements."
        actions={
          <>
            <Link to="/missions" className="btn btn-ghost">
              View all missions
              <ArrowRight size={14} />
            </Link>
          </>
        }
      />

      <div className="overview-core" aria-hidden="true">
        <div className="overview-ring overview-ring-outer" />
        <div className="overview-ring overview-ring-inner" />
        <div className="overview-core-glyph">
          <Cpu size={26} />
        </div>
      </div>

      <div className="overview-grid">
        <Panel
          title="Active Missions"
          icon={<Braces size={13} className="panel-icon" />}
          actions={
            <Link to="/missions?status=active" className="overview-panel-count">
              <span className="lamp lamp-accent" aria-hidden="true" />
              <span className="t-mono">{active.length} live</span>
            </Link>
          }
        >
          <div className="mission-mini-list">
            {active.slice(0, 4).map((m) => (
              <Link key={m.id} to={`/missions/${m.id}`} className="mission-mini">
                <div className="mission-mini-main">
                  <span className="t-mono mission-mini-code">{m.code}</span>
                  <span className="mission-mini-title">{m.title}</span>
                </div>
                <span className="mission-mini-status">
                  <StatusBadge status={m.status} pulse={m.status === "ACTIVE"} />
                  <ChevronRight size={13} className="text-muted" />
                </span>
              </Link>
            ))}
          </div>
          <div className="overview-panel-foot">
            <Link to="/missions?status=active" className="text-link">
              View active
            </Link>
            <span className="t-mono text-muted">{failedVerification.length} failed verification</span>
          </div>
        </Panel>

        <Panel
          title="System Integrity"
          icon={<Cpu size={13} className="panel-icon" />}
          actions={<span className="t-mono overview-panel-count">{overview.version}</span>}
        >
          <div className="resource-bars">
            {resourceBars.map((r) => (
              <div key={r.label} className="resource-bar">
                <div className="resource-bar-head">
                  <span className="t-label text-muted">{r.label}</span>
                  <span className="t-mono resource-bar-value">{r.value}%</span>
                </div>
                <div className="meter">
                  <div className={`meter-fill meter-fill-${r.tone}`} style={{ width: `${r.value}%` }} />
                </div>
              </div>
            ))}
          </div>
          <div className="overview-panel-foot">
            <Link to="/systems" className="text-link">
              Inspect systems
            </Link>
            <span className="t-mono text-muted">Uptime {overview.uptime}</span>
          </div>
          {degradedSystems.length > 0 ? (
            <div className="overview-degraded">
              <span className="t-label text-muted">Degraded</span>
              <div className="ref-row">
                {degradedSystems.map((s) => (
                  <Link key={s.id} to={`/systems/${s.id}`} className="ref-link">
                    <span>{s.name}</span>
                  </Link>
                ))}
              </div>
            </div>
          ) : null}
        </Panel>

        <Panel
          title="Trust State"
          icon={<ShieldQuestion size={13} className="panel-icon" />}
          actions={
            <Link to="/notifications" className="overview-panel-count">
              <span className="lamp lamp-amber" aria-hidden="true" />
              <span className="t-mono">{trust.unresolvedFindings} open</span>
            </Link>
          }
        >
          <div className="trust-compact">
            <div className="trust-compact-head">
              <StatusBadge status={trust.state} />
              <span className="t-mono text-muted">SCORE: NOT_AVAILABLE</span>
            </div>
            <div className="trust-compact-rows">
              <div className="trust-compact-row">
                <span className="t-label text-muted">Gate</span>
                <span className="t-mono">{trust.gateStatus}</span>
              </div>
              <div className="trust-compact-row">
                <span className="t-label text-muted">Evidence coverage</span>
                <span className="t-mono">{trust.evidenceCoverage}%</span>
              </div>
              <div className="trust-compact-row">
                <span className="t-label text-muted">Health</span>
                <span className="t-mono">{trust.health}</span>
              </div>
            </div>
          </div>
          <div className="overview-panel-foot">
            <button type="button" className="text-link" onClick={openTrustDialog}>
              Explain trust
            </button>
            <Link to="/notifications" className="text-link">
              Exceptions
            </Link>
          </div>
        </Panel>

        <Panel
          title="Agent Health"
          icon={<Activity size={13} className="panel-icon" />}
          actions={<Link to="/agents" className="text-link">Fleet</Link>}
        >
          <div className="agent-health-grid">
            <Link to="/agents" className="agent-health-tile">
              <span className="t-mono agent-health-num agent-health-num-accent">{activeWorkers.length}</span>
              <span className="t-label text-muted">Online</span>
            </Link>
            <Link to="/agents" className="agent-health-tile">
              <span className="t-mono agent-health-num">{workers.filter((w) => w.status === "STANDBY").length}</span>
              <span className="t-label text-muted">Standby</span>
            </Link>
            <Link to="/agents" className="agent-health-tile agent-health-tile-warn">
              <span className="t-mono agent-health-num">{workers.filter((w) => w.status === "DEGRADED").length}</span>
              <span className="t-label text-muted">Degraded</span>
            </Link>
            <Link to="/agents" className="agent-health-tile">
              <span className="t-mono agent-health-num agent-health-num-atrisk">{atRiskWorkers.length}</span>
              <span className="t-label text-muted">At risk</span>
            </Link>
          </div>
          <div className="overview-panel-foot">
            <span className="t-mono text-muted">{failedVerification.length} failed verification</span>
            <Link to="/evidence?verdict=FAILED" className="text-link">
              Evidence
            </Link>
          </div>
        </Panel>

        <Panel
          title="Resources"
          icon={<HardDrive size={13} className="panel-icon" />}
          actions={<span className="t-mono text-muted">{overview.releaseState}</span>}
        >
          <div className="resource-row-list">
            {overview.resourceUsage.map((r) => (
              <div key={r.label} className="resource-row">
                <span className="t-label text-muted">{r.label}</span>
                <div className="resource-row-meter">
                  <div className="meter">
                    <div className="meter-fill meter-fill-teal" style={{ width: `${r.value}%` }} />
                  </div>
                  <span className="t-mono">{r.value}{r.unit}</span>
                </div>
              </div>
            ))}
          </div>
          <div className="overview-panel-foot">
            <span className="t-mono text-muted">
              <MemoryStick size={12} aria-hidden="true" /> build {overview.build}
            </span>
          </div>
        </Panel>

        <Panel
          title="Recent Events"
          icon={<History size={13} className="panel-icon" />}
          actions={<Link to="/timeline" className="text-link">Timeline</Link>}
        >
          <div className="event-mini-list">
            {timeline.slice(0, 4).map((e) => (
              <Link key={e.id} to={`/timeline?event=${e.id}`} className="event-mini">
                <span className="t-num event-mini-time">{e.at}</span>
                <span className="event-mini-text">{e.title}</span>
              </Link>
            ))}
          </div>
          <div className="overview-panel-foot">
            <Link to="/notifications" className="text-link">
              <BellRing size={12} aria-hidden="true" />
              {unreadCount} unread alerts
            </Link>
            {memoryConflict ? (
              <Link to={`/memory/${memoryConflict.refs.find((r) => r.kind === "memory")?.id ?? "n-reconcile"}`} className="text-link">
                Memory conflict
              </Link>
            ) : (
              <span className="t-mono text-muted">{activeWorkers.length} workers active</span>
            )}
          </div>
        </Panel>
      </div>
    </div>
  );
}
