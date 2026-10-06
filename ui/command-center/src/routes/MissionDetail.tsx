import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  AlertTriangle,
  ArrowLeft,
  Check,
  ClipboardCheck,
  Construction,
  Database,
  GitBranch,
  History,
  Link2,
  Route,
  SearchCheck,
  ShieldQuestion,
  Terminal,
  Timer,
  Users,
  Workflow,
} from "lucide-react";
import { useCommandCenter } from "../context/CommandCenterContext";
import type { EvidenceArtifact, LineageNode, Mission, MissionRun, MissionStep, RelatedEntities, Worker } from "../lib/contracts";
import { StatusBadge } from "../components/StatusBadge";
import { Dialog } from "../components/Dialog";
import { DetailRouteQueryGate } from "../components/DetailRouteQueryGate";
import { EntityRefLink, RefChipRow } from "../components/EntityRefLink";
import { Lineage } from "../components/Lineage";
import { useRouteQuery } from "../hooks/useRouteQuery";
import { sourceModeLabel } from "../lib/sourceMode";

const stageIcons: Record<string, typeof Check> = {
  observe: SearchCheck,
  plan: GitBranch,
  build: Construction,
  verify: ClipboardCheck,
  writeback: Database,
  next: Route,
};

type MissionDetailData = {
  mission: Mission;
  run: MissionRun;
  workers: Worker[];
  evidence: EvidenceArtifact[];
  lineage: LineageNode[];
  steps: MissionStep[];
  related: RelatedEntities;
  effectTruth: Record<string, unknown> | null;
};

type EffectReceipt = {
  kind: "provider_attempt" | "effect_lineage" | "effect";
  id: string;
  status: string;
  detail: string;
  sourceEventId?: string;
};

function records(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value)
    ? value.filter((row): row is Record<string, unknown> => Boolean(row) && typeof row === "object")
    : [];
}

function missionEffectReceipts(
  effectTruth: Record<string, unknown> | null,
  missionId: string,
): EffectReceipt[] {
  if (!effectTruth) return [];
  const attempts = records(effectTruth.attempts)
    .filter((row) => row.mission_id === missionId)
    .map((row): EffectReceipt => ({
      kind: "provider_attempt",
      id: String(row.attempt_id ?? row.source_event_id ?? "UNKNOWN"),
      status: String(row.outcome ?? "UNKNOWN"),
      detail: [
        row.connector_id ? `connector ${row.connector_id}` : null,
        row.provider_ref ? `provider receipt ${row.provider_ref}` : null,
        row.action_kind ? `action ${row.action_kind}` : null,
      ].filter(Boolean).join(" · ") || "Provider attempt detail unavailable",
      sourceEventId: typeof row.source_event_id === "string" ? row.source_event_id : undefined,
    }));
  const lineage = records(effectTruth.lineage)
    .filter((row) => row.mission_id === missionId)
    .map((row): EffectReceipt => ({
      kind: "effect_lineage",
      id: String(row.lineage_id ?? row.source_event_id ?? "UNKNOWN"),
      status: String(row.outcome ?? "UNKNOWN"),
      detail: [
        row.external_object_id ? `external object ${row.external_object_id}` : null,
        row.effect_ticket_id ? `effect ticket ${row.effect_ticket_id}` : null,
        row.ambiguity_reason ? `ambiguity ${row.ambiguity_reason}` : null,
      ].filter(Boolean).join(" · ") || "Effect lineage detail unavailable",
      sourceEventId: typeof row.source_event_id === "string" ? row.source_event_id : undefined,
    }));
  const actions = records(effectTruth.actions)
    .filter((row) => String(row.target_ref ?? "").includes(missionId))
    .map((row): EffectReceipt => ({
      kind: "effect",
      id: String(row.action_key ?? "UNKNOWN"),
      status: String(row.status ?? "UNKNOWN"),
      detail: [
        row.system_id ? `system ${row.system_id}` : null,
        row.kind ? `action ${row.kind}` : null,
        row.target_ref ? `target ${row.target_ref}` : null,
      ].filter(Boolean).join(" · ") || "External action detail unavailable",
      sourceEventId: typeof row.last_event_id === "string" ? row.last_event_id : undefined,
    }));
  return [...attempts, ...lineage, ...actions];
}

export function MissionDetail() {
  const { missionId } = useParams();
  const { client, version, notifications, stopActive, openTrustDialog, sourceMode, trustSummary, mode } = useCommandCenter();
  const missionIdResolved = missionId ?? (mode === "simulated" ? "M-HYB-014" : "");
  const detailQuery = useRouteQuery(
    async () => {
      const mission = await client.getMission(missionIdResolved);
      if (!mission) return null;
      const [run, workers, evidence, lineage, steps, related, ownerSurface] = await Promise.all([
        client.getMissionRun(mission.id),
        client.listWorkers(),
        client.listEvidence(),
        client.getLineage(mission.id),
        client.getMissionSteps(mission.id),
        client.getRelatedEntities("mission", mission.id),
        client.getOwnerControlSurface?.() ?? Promise.resolve(null),
      ]);
      return {
        mission,
        run,
        workers: workers.filter((x) => (x.missionIds ?? []).includes(mission.id)),
        evidence: evidence.filter((x) => x.missionRef === mission.id),
        lineage,
        steps,
        related,
        effectTruth: ownerSurface?.effectTruth ?? null,
      } satisfies MissionDetailData;
    },
    [client, version, missionIdResolved],
  );
  const [dialog, setDialog] = useState<"halt" | "gate" | null>(null);
  const [halted, setHalted] = useState(false);
  const [gateRequested, setGateRequested] = useState(false);

  return (
    <DetailRouteQueryGate
      query={detailQuery}
      loadingLabel="Loading mission detail"
      notFoundLabel="Mission not found"
      notFoundDetail={sourceMode === "SIMULATED" ? "No simulated mission exists at this identifier." : sourceMode === "LIVE" ? "No canonical mission exists at this identifier." : `Mission lookup unavailable: runtime source is ${sourceModeLabel(sourceMode)}.`}
      errorLabel="Mission detail unavailable"
      surface="Mission detail"
    >
      {({ mission, run, workers, evidence, lineage, steps, related, effectTruth }) => {
  const stepByKey = Object.fromEntries(steps.map((s) => [s.key, s]));
  const lifecycle = run.lifecycle ?? [];
  const lifecycleKnown = lifecycle.filter((record) => record.state !== "UNKNOWN" && record.state !== "UNAVAILABLE" && record.state !== "NOT REQUIRED");
  const lifecycleComplete = lifecycleKnown.filter((record) => record.state === "COMPLETE").length;
  const lifecycleProgress = lifecycleKnown.length > 0 ? Math.round((lifecycleComplete / lifecycleKnown.length) * 100) : null;
  const blocking = notifications.find((n) => n.refs.some((r) => r.kind === "mission" && r.id === mission.id) && n.state !== "RESOLVED");
  const blockingEventRef = blocking?.refs.find((r) => r.kind === "timeline");
  const contextLinks = [
    { label: "Timeline", to: `/timeline?mission=${mission.id}` },
    { label: "Evidence", to: `/evidence?mission=${mission.id}` },
    { label: "Agents", to: `/agents?mission=${mission.id}` },
    { label: "Memory", to: `/memory?mission=${mission.id}` },
    { label: "Systems", to: `/systems?mission=${mission.id}` },
  ];
  const relatedGroups = [
    { label: "Workers", refs: related.agents },
    { label: "Evidence", refs: related.evidence },
    { label: "Systems", refs: related.systems },
    { label: "Timeline", refs: related.timeline },
    { label: "Memory", refs: related.memory },
    { label: "Notifications", refs: related.notifications },
  ];
  const hasRelated = relatedGroups.some((group) => group.refs.length > 0);
  const effectReceipts = missionEffectReceipts(effectTruth, mission.id);

  return (
    <div className="shell-content">
      <div className="md-breadcrumb">
        <Link to="/missions" className="md-back">
          <ArrowLeft size={14} aria-hidden="true" />
          <span className="t-label">Missions</span>
        </Link>
        <span className="t-mono text-muted md-crumb-id">{mission.code}</span>
      </div>

      {blocking ? (
        <div className="md-blocked-banner" role="alert">
          <AlertTriangle size={15} aria-hidden="true" />
          <span className="t-label">Blocking issue</span>
          <span className="t-mono md-blocked-detail">{blocking.title} - {blocking.detail}</span>
          {blockingEventRef ? <EntityRefLink entity={blockingEventRef} /> : null}
        </div>
      ) : null}

      {stopActive ? (
        <div className="md-blocked-banner md-blocked-banner-stop" role="alert">
          <AlertTriangle size={15} aria-hidden="true" />
          <span className="t-label">{sourceMode === "SIMULATED" ? "SIMULATED FROZEN" : sourceMode === "LIVE" ? "LIVE FROZEN" : `${sourceModeLabel(sourceMode)} FROZEN`}</span>
          <span className="t-mono md-blocked-detail">{sourceMode === "SIMULATED" ? "STOP active in the UI candidate. Execution pipeline is frozen; no advancement events fire." : sourceMode === "LIVE" ? "Canonical STOP is active. Execution pipeline is frozen; no advancement events fire." : `STOP is active but runtime source is ${sourceModeLabel(sourceMode)}.`}</span>
        </div>
      ) : null}

      <div className="md-head">
        <div className="md-head-main">
          <div className="md-head-badges">
            <StatusBadge status={mission.status} pulse={mission.status === "ACTIVE"} />
            <span className="t-mono text-muted">Stage: {mission.stage}</span>
          </div>
          <h1 className="t-display md-title">{mission.title}</h1>
          <p className="prose md-desc">{mission.description}</p>
        </div>
        <div className="md-actions">
          <button
            type="button"
            className="btn"
            onClick={() => setDialog("halt")}
            disabled={halted || stopActive || sourceMode !== "SIMULATED"}
            aria-label={sourceMode === "SIMULATED" ? "Request halt of this mission execution (simulated)" : `Mission halt unavailable in ${sourceModeLabel(sourceMode)} source mode`}
          >
            {halted ? "Halt requested" : "Halt Execution"}
          </button>
          <button
            type="button"
            className="btn btn-primary"
            onClick={() => setDialog("gate")}
            disabled={gateRequested || stopActive || sourceMode !== "SIMULATED"}
            aria-label={sourceMode === "SIMULATED" ? "Request fresh verifier evaluation of the current gate (simulated)" : `Gate re-evaluation unavailable in ${sourceModeLabel(sourceMode)} source mode`}
          >
            {gateRequested ? "Re-evaluation requested" : "Re-evaluate Gate"}
          </button>
        </div>
      </div>

      <div className="md-pipeline panel panel-corner">
        <h2 className="t-label panel-title text-muted md-pipeline-title">
          <Workflow size={13} aria-hidden="true" />
          Execution Pipeline
        </h2>
        <div className="md-stages">
          <div className="md-track" aria-hidden="true">
            {lifecycleProgress !== null ? <span className="md-track-fill" style={{ width: `${lifecycleProgress}%` }} /> : null}
          </div>
          {(lifecycle.length > 0 ? lifecycle : run.pipeline.map((stage) => ({ ...stage, refs: [], eventIds: [] }))).map((stage) => {
            const Icon = stageIcons[stage.key] ?? Check;
            const step = stepByKey[stage.key];
            const compactRefs = stage.refs.slice(0, 2).map((ref) => ({
              ...ref,
              label: ref.label.length > 16 ? `${ref.label.slice(0, 13)}…` : ref.label,
            }));
            return (
              <div key={stage.key} className={`md-stage md-stage-${stage.state.toLowerCase()}`}>
                <div className="md-stage-dot">
                  <Icon size={16} aria-hidden="true" />
                </div>
                <span className="t-label md-stage-label">{stage.label}</span>
                <span className="t-mono md-stage-detail">{stage.detail}</span>
                <StatusBadge status={stage.state} />
                {step?.workerId ? (
                  <EntityRefLink entity={{ kind: "agent", id: step.workerId, label: step.workerId }} />
                ) : null}
                {step?.evidenceIds && step.evidenceIds.length > 0 ? (
                  <RefChipRow refs={step.evidenceIds.map((id) => ({ kind: "evidence" as const, id, label: id }))} />
                ) : null}
                {compactRefs.length > 0 ? <RefChipRow refs={compactRefs} /> : null}
                {stage.refs.length > compactRefs.length ? <span className="t-mono text-muted md-stage-ref-count">+{stage.refs.length - compactRefs.length} linked refs</span> : null}
                {stage.eventIds.length > 0 ? (
                  <span className="t-mono text-muted md-stage-events" title={stage.eventIds.join(", ")}>
                    {stage.eventIds.length} event{stage.eventIds.length === 1 ? "" : "s"} · latest {stage.eventIds[stage.eventIds.length - 1].slice(0, 8)}…
                  </span>
                ) : null}
              </div>
            );
          })}
        </div>
      </div>

      <section className="panel panel-corner md-hub">
        <div className="panel-header">
          <h2 className="t-label panel-title text-muted">
            <Link2 size={13} className="panel-icon" />
            Operational Links
          </h2>
          <span className="t-mono text-muted">Follow this mission across every surface</span>
        </div>
        <div className="md-hub-body">
          {contextLinks.map((l) => (
            <Link key={l.label} to={l.to} className="md-hub-link">
              <span className="t-label">{l.label}</span>
              <span className="t-mono">{mission.id}</span>
            </Link>
          ))}
        </div>
      </section>

      <section className="panel panel-corner detail-panel detail-panel-full" aria-label="Effects and provider receipts">
        <div className="panel-header">
          <h2 className="t-label panel-title text-muted">
            <Link2 size={13} className="panel-icon" />
            Effects / Provider Receipts
          </h2>
          <span className="t-mono text-muted">{effectReceipts.length} source-backed records</span>
        </div>
        <div className="panel-body">
          {effectReceipts.length ? (
            <ul className="detail-event-list">
              {effectReceipts.map((receipt) => (
                <li key={`${receipt.kind}:${receipt.id}`}>
                  <div>
                    <Link className="t-mono detail-event-title" to={`/records/${receipt.kind}/${encodeURIComponent(receipt.id)}`}>{receipt.id}</Link>
                    <span className="t-mono text-muted">{receipt.status} · {receipt.detail}</span>
                  </div>
                  {receipt.sourceEventId ? <EntityRefLink entity={{ kind: "timeline", id: receipt.sourceEventId, label: receipt.sourceEventId }} /> : null}
                </li>
              ))}
            </ul>
          ) : (
            <span className="t-mono text-dim">No canonical external-effect attempt, receipt, or lineage record is linked to this mission.</span>
          )}
        </div>
      </section>

      <div className="md-grid">
        <div className="md-left">
          <section className="panel panel-corner md-log-panel" aria-label="Agent telemetry log">
            <div className="panel-header">
              <h2 className="t-label panel-title text-muted">
                <Terminal size={13} className="panel-icon" />
                Agent Telemetry Log
              </h2>
              <span className="t-mono text-muted md-log-toggle">Auto-scroll: ON</span>
            </div>
            <div className="md-log" aria-live="polite">
              {run.telemetry.map((entry, i) => (
                <div key={i} className="logline md-logline">
                  <time>{entry.at}</time>
                  <span className={`md-log-source md-log-source-${entry.level.toLowerCase()}`}>[{entry.source}]</span>
                  <span className="md-log-text">{entry.text}</span>
                </div>
              ))}
            </div>
          </section>

          <section className="panel panel-corner md-streams" aria-label="Active data streams">
            <div className="panel-header">
              <h2 className="t-label panel-title text-muted">Active Data Streams</h2>
              <span className="t-mono text-muted md-stream-count">{run.streams.length} streams</span>
            </div>
            <div className="md-stream-grid">
              {run.streams.map((s) => (
                <div key={s.id} className="md-stream">
                  <span className={`md-stream-icon md-stream-icon-${s.state.toLowerCase()}`} aria-hidden="true">
                    {s.icon === "database" ? <Database size={18} /> : s.icon === "memory" ? <Database size={18} /> : s.icon === "api" ? <GitBranch size={18} /> : <Route size={18} />}
                  </span>
                  <div className="md-stream-meta">
                    <span className="t-label md-stream-name">{s.name}</span>
                    <span className="t-mono md-stream-status">{s.status}</span>
                  </div>
                </div>
              ))}
            </div>
          </section>
        </div>

        <div className="md-right">
          <section className="panel panel-corner md-verdicts" aria-label="Verifier verdicts">
            <div className="panel-header">
              <h2 className="t-label panel-title text-muted">
                <ClipboardCheck size={13} className="panel-icon" />
                Verifier Verdicts
              </h2>
            </div>
            <div className="md-verdict-list">
              {run.verdicts.map((v) => (
                <div key={v.id} className={`md-verdict md-verdict-${v.state.toLowerCase()}`}>
                  <div className="md-verdict-head">
                    <span className="t-mono md-verdict-title">
                      <EntityRefLink entity={{ kind: "verdict", id: v.id, label: v.id }} />
                      <span className="md-verdict-title-text">{v.title}</span>
                    </span>
                    <StatusBadge status={v.state} />
                  </div>
                  <p className="prose md-verdict-detail">{v.detail}</p>
                  <div className="md-verdict-refs">
                    {v.workerId ? (
                      <EntityRefLink entity={{ kind: "agent", id: v.workerId, label: v.workerId }} />
                    ) : null}
                    {v.evidenceId ? (
                      <EntityRefLink entity={{ kind: "evidence", id: v.evidenceId, label: v.evidenceId }} />
                    ) : null}
                  </div>
                </div>
              ))}
            </div>
          </section>

          <section className="panel panel-corner md-stats" aria-label="Mission statistics">
            <div className="md-stats-grid">
              <div className="md-stat">
                <span className="t-label text-muted">Elapsed</span>
                <span className="t-mono md-stat-value">
                  <Timer size={12} aria-hidden="true" /> {run.stats.elapsed}
                </span>
              </div>
              <div className="md-stat">
                <span className="t-label text-muted">Compute cost</span>
                <span className="t-mono md-stat-value">{run.stats.computeCost}</span>
              </div>
              <div className="md-stat">
                <span className="t-label text-muted">Agents active</span>
                <Link to={`/agents?mission=${mission.id}`} className="t-mono md-stat-value md-stat-link">
                  {run.stats.agentsActive}
                </Link>
              </div>
              <div className="md-stat">
                <span className="t-label text-muted">Evidence artifacts</span>
                <Link to={`/evidence?mission=${mission.id}`} className="t-mono md-stat-value md-stat-link">
                  {run.stats.evidenceArtifacts}
                </Link>
              </div>
            </div>
          </section>

          <section className="panel panel-corner md-trust" aria-label="Trust state">
            <div className="md-trust-row">
              <ShieldQuestion size={14} className="text-muted" aria-hidden="true" />
              <span className="t-label text-muted">Trust state</span>
              <span className="t-mono md-trust-value">{trustSummary?.state ?? "UNKNOWN"} · gate {trustSummary?.gateStatus ?? "UNKNOWN"}</span>
              <button type="button" className="text-link" onClick={openTrustDialog}>
                Explain
              </button>
            </div>
            <p className="prose md-trust-note">
              {trustSummary?.factors?.find((factor) => factor.id === "coverage")?.detail ?? "Trust factors are UNKNOWN until the canonical projection reports them."}
            </p>
          </section>
        </div>
      </div>

      <div className="md-grid">
        <section className="panel panel-corner md-hub">
          <div className="panel-header">
            <h2 className="t-label panel-title text-muted">
              <Users size={13} className="panel-icon" />
              Assigned Workers
            </h2>
            <span className="t-mono text-muted">{workers.length} workers</span>
          </div>
          <div className="md-hub-body">
            {workers.length === 0 ? (
              <span className="t-mono text-dim">{sourceMode === "SIMULATED" ? "No workers assigned in this fixture." : sourceMode === "LIVE" ? "No canonical workers assigned." : `Worker assignment unavailable: runtime source is ${sourceModeLabel(sourceMode)}.`}</span>
            ) : (
              <RefChipRow refs={workers.map((w) => ({ kind: "agent" as const, id: w.id, label: w.name }))} />
            )}
          </div>
        </section>

        <section className="panel panel-corner md-hub">
          <div className="panel-header">
            <h2 className="t-label panel-title text-muted">
              <Database size={13} className="panel-icon" />
              Evidence Bundle
            </h2>
            <span className="t-mono text-muted">{evidence.length} attached</span>
          </div>
          <div className="md-hub-body">
            {evidence.length === 0 ? (
              <span className="t-mono text-dim">{sourceMode === "SIMULATED" ? "No evidence attached in this fixture." : sourceMode === "LIVE" ? "No canonical evidence attached." : `Evidence unavailable: runtime source is ${sourceModeLabel(sourceMode)}.`}</span>
            ) : (
              <RefChipRow refs={evidence.map((e) => ({ kind: "evidence" as const, id: e.id, label: `${e.id} · ${e.verdict}` }))} />
            )}
          </div>
        </section>
      </div>

      <section className="panel panel-corner detail-panel detail-panel-full md-related" aria-label="Related records">
        <div className="panel-header">
          <h2 className="t-label panel-title text-muted">
            <Link2 size={13} className="panel-icon" />
            Related Records
          </h2>
        </div>
        <div className="md-related-body">
          {!hasRelated ? (
            <span className="t-mono text-dim">No related records indexed for this mission.</span>
          ) : (
            relatedGroups
              .filter((group) => group.refs.length > 0)
              .map((group) => (
                <div key={group.label} className="md-related-group">
                  <span className="t-label text-muted">{group.label}</span>
                  <RefChipRow refs={group.refs} />
                </div>
              ))
          )}
        </div>
      </section>

      <section className="panel panel-corner detail-panel detail-panel-full md-lineage">
        <div className="panel-header">
          <h2 className="t-label panel-title text-muted">
            <History size={13} className="panel-icon" />
            Verification / Repair Lineage
          </h2>
          <span className="t-mono text-muted">presentation only - verifier outcomes come from evidence</span>
        </div>
        <div className="panel-body">
          <Lineage nodes={lineage} />
        </div>
      </section>

      <Dialog
        open={dialog === "halt"}
        title={sourceMode === "SIMULATED" ? "Halt Execution - Simulated" : `Halt Execution - ${sourceModeLabel(sourceMode)}`}
        onClose={() => setDialog(null)}
        labelledBy="halt-dialog-title"
        footer={
          halted ? (
            <>
              <span className="t-mono text-muted" style={{ marginRight: "auto" }}>
                {sourceMode === "SIMULATED" ? "HALT REQUEST SIMULATED - NO RUNTIME EFFECT" : `HALT REQUEST ${sourceModeLabel(sourceMode)} - NOT AVAILABLE`}
              </span>
              <button type="button" className="btn" onClick={() => setDialog(null)}>
                Close
              </button>
            </>
          ) : (
            <>
              <button type="button" className="btn" onClick={() => setDialog(null)}>
                Cancel
              </button>
              <button type="button" className="btn btn-danger" onClick={() => setHalted(true)}>
                <AlertTriangle size={14} />
                Request halt
              </button>
            </>
          )
        }
      >
        <div className="state-block">
          <AlertTriangle size={22} aria-hidden="true" />
          <span className="t-label">{sourceMode === "SIMULATED" ? "Simulated halt request" : `Halt request (${sourceModeLabel(sourceMode)})`}</span>
          <p className="prose">
            {sourceMode === "SIMULATED" ? "This candidate records the halt request locally. A future runtime adapter will submit it as an authenticated owner directive to the Supervisor." : `Mission halt is not invoked in this read-only proof. The ${sourceModeLabel(sourceMode)} source does not expose a confirmed halt path.`}
          </p>
        </div>
      </Dialog>

      <Dialog
        open={dialog === "gate"}
        title={sourceMode === "SIMULATED" ? "Re-evaluate Gate - Simulated" : `Re-evaluate Gate - ${sourceModeLabel(sourceMode)}`}
        onClose={() => setDialog(null)}
        labelledBy="gate-dialog-title"
        footer={
          gateRequested ? (
            <>
              <span className="t-mono text-muted" style={{ marginRight: "auto" }}>
                RE-EVALUATION REQUESTED - OUTCOME DEPENDS ON RUNTIME EVIDENCE
              </span>
              <button type="button" className="btn" onClick={() => setDialog(null)}>
                Close
              </button>
            </>
          ) : (
            <>
              <button type="button" className="btn" onClick={() => setDialog(null)}>
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => setGateRequested(true)}
              >
                <ClipboardCheck size={14} />
                Request advance
              </button>
            </>
          )
        }
      >
        <div className="state-block">
          <ClipboardCheck size={22} aria-hidden="true" />
          <span className="t-label">Advancement stays conditional</span>
          <p className="prose">
            This button requests a fresh verifier evaluation of the current gate. It cannot skip the verifier,
            override the gate, or mark anything PASS. Actual advancement is decided by canonical runtime evidence.
          </p>
        </div>
      </Dialog>
    </div>
  );
      }}
    </DetailRouteQueryGate>
  );
}
