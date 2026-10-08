import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import {
  AlertTriangle,
  ArrowLeft,
  ClipboardCheck,
  Database,
  History,
  Link2,
  Terminal,
  Timer,
  Users,
  Workflow,
} from "lucide-react";
import { useCommandCenter } from "../context/CommandCenterContext";
import type { AutonomyLifecycleRecord, EvidenceArtifact, LineageNode, Mission, MissionRun, MissionStep, Notification, OwnerControlSurface, RelatedEntities, TrustSummary, Worker } from "../lib/contracts";
import { StatusBadge } from "../components/StatusBadge";
import { Dialog } from "../components/Dialog";
import { DetailRouteQueryGate } from "../components/DetailRouteQueryGate";
import { EntityRefLink, RefChipRow } from "../components/EntityRefLink";
import { Lineage } from "../components/Lineage";
import { useRouteQuery } from "../hooks/useRouteQuery";
import { sourceModeLabel, type ResolvedSourceMode } from "../lib/sourceMode";
import {
  isHistoricalMission,
  missionDirectiveId,
  ownerActionForMission,
  plainSummary,
  servingGoalLabel,
  type OwnerStandingGoal,
} from "../lib/ownerWork";

type MissionDetailData = {
  mission: Mission;
  run: MissionRun;
  workers: Worker[];
  evidence: EvidenceArtifact[];
  lineage: LineageNode[];
  steps: MissionStep[];
  related: RelatedEntities;
  effectTruth: Record<string, unknown> | null;
  ownerSurface: OwnerControlSurface | null;
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

// --- Task 4 owner-experience helpers (pure, exported for tests) ---

const EXECUTING_MISSION_STATES: ReadonlySet<string> = new Set(["ACTIVE", "IN_PROGRESS", "INTERVENTION"]);

export function isExecutingMissionStatus(status: Mission["status"]): boolean {
  return EXECUTING_MISSION_STATES.has(status);
}

/** HALT shows only while the mission is currently executing AND mutations are enabled. */
export function shouldShowHaltButton(status: Mission["status"], mutationsEnabled: boolean): boolean {
  return isExecutingMissionStatus(status) && mutationsEnabled;
}

/**
 * The live client reports gate re-evaluation UNAVAILABLE
 * (`accepted:false`), so the gate button is hidden in LIVE and retained in
 * SIMULATED. Other disconnected/degraded sources fail closed (hidden).
 */
export function shouldShowGateButton(sourceMode: ResolvedSourceMode): boolean {
  return sourceMode === "SIMULATED";
}

function plainMissionState(status: Mission["status"]): string {
  switch (status) {
    case "ACTIVE":
    case "IN_PROGRESS":
    case "INTERVENTION":
      return "Executing";
    case "COMPLETE":
      return "Complete";
    case "BLOCKED":
    case "HALTED":
      return "Blocked";
    case "CANCELLED":
      return "Cancelled";
    case "STANDBY":
      return "Waiting";
    default:
      return String(status) === "FAILED" ? "Failed" : "Unknown";
  }
}

function sentenceCaseTitle(mission: Mission): string {
  const raw = (mission.objective || mission.title || "").trim();
  if (!raw) return "Untitled mission";
  return raw.charAt(0).toUpperCase() + raw.slice(1);
}

function ownerGoalsOf(surface: OwnerControlSurface | null): OwnerStandingGoal[] {
  return ((surface?.goals ?? []) as OwnerStandingGoal[]).filter(
    (goal): goal is OwnerStandingGoal => Boolean(goal) && typeof goal === "object",
  );
}

/**
 * Task 2 precedent: the served goal comes from the Task 1 ranker-side
 * judgment (`servingGoalLabel`), guarded by the record's own active/status
 * markers — no second active mechanism. Here the guard only matters for the
 * Hoowen anchor and the historical active-goal ids below.
 */
function isActiveStandingGoal(goal: OwnerStandingGoal): boolean {
  if (goal.active === 1 || goal.active === true || goal.active === "1" || goal.active === "true") return true;
  if (typeof goal.active === "string" && goal.active.toUpperCase() === "ACTIVE") return true;
  if ((goal.active === undefined || goal.active === null) && typeof goal.status === "string" && goal.status.toUpperCase() === "ACTIVE") return true;
  return false;
}

function goalDirectiveId(goal: OwnerStandingGoal): string {
  const id = goal.directive_id ?? goal.directiveId;
  return typeof id === "string" ? id.trim() : "";
}

function goalText(goal: OwnerStandingGoal): string {
  const label = typeof goal.label === "string" ? goal.label : "";
  const exact = typeof goal.exact_text === "string"
    ? goal.exact_text
    : typeof goal.exactText === "string"
      ? goal.exactText
      : "";
  return `${label} ${exact}`;
}

function activeGoalDirectiveIds(surface: OwnerControlSurface | null): string[] {
  return ownerGoalsOf(surface).filter(isActiveStandingGoal).map(goalDirectiveId).filter(Boolean);
}

/** Most-specific directive id behind the serves label, for the Work link. Null when unlinked. */
function servingDirectiveId(mission: Mission, goals: OwnerStandingGoal[]): string | null {
  const lineage = missionDirectiveId(mission);
  if (!lineage) return null;
  let best: string | null = null;
  for (const goal of goals) {
    const directiveId = goalDirectiveId(goal);
    if (!directiveId || !lineage.includes(directiveId)) continue;
    if (!best || directiveId.length > best.length) best = directiveId;
  }
  return best;
}

function lifecycleByKey(lifecycle: AutonomyLifecycleRecord[] | undefined, key: string): AutonomyLifecycleRecord | null {
  return (lifecycle ?? []).find((record) => record.key === key) ?? null;
}

export type CompactTrackerState = "COMPLETE" | "ACTIVE" | "PENDING" | "FAILED" | "UNKNOWN";

export interface CompactTrackerStage {
  key: "goal" | "plan" | "build" | "verify" | "outcome";
  label: "Goal" | "Plan" | "Build" | "Verify" | "Outcome";
  state: CompactTrackerState;
  detail: string;
}

function normalizeTrackerState(state: AutonomyLifecycleRecord["state"] | undefined): CompactTrackerState {
  return state === "COMPLETE" || state === "ACTIVE" || state === "PENDING" || state === "FAILED"
    ? state
    : "UNKNOWN";
}

function combineTrackerStates(states: Array<AutonomyLifecycleRecord["state"] | undefined>): CompactTrackerState {
  const normalized = states.map(normalizeTrackerState);
  if (normalized.includes("FAILED")) return "FAILED";
  if (normalized.includes("ACTIVE")) return "ACTIVE";
  if (normalized.length > 0 && normalized.every((state) => state === "COMPLETE")) return "COMPLETE";
  if (normalized.includes("PENDING")) return "PENDING";
  if (normalized.includes("COMPLETE")) return "PENDING";
  return "UNKNOWN";
}

function knownDetail(record: AutonomyLifecycleRecord | null): string | null {
  const detail = record?.detail?.trim() ?? "";
  return detail && detail !== "UNKNOWN" ? detail : null;
}

/**
 * Compact Goal → Plan → Build → Verify → Outcome tracker mapped from the
 * canonical lifecycle records: owner_goal → Goal, plan → Plan,
 * worker + action_artifact → Build, evidence + verifier → Verify,
 * owner_briefing (+ terminal mission state) → Outcome.
 */
export function compactMissionTracker(
  lifecycle: AutonomyLifecycleRecord[] | undefined,
  missionStatus: Mission["status"],
): CompactTrackerStage[] {
  const goal = lifecycleByKey(lifecycle, "owner_goal");
  const plan = lifecycleByKey(lifecycle, "plan");
  const worker = lifecycleByKey(lifecycle, "worker");
  const actionArtifact = lifecycleByKey(lifecycle, "action_artifact");
  const evidence = lifecycleByKey(lifecycle, "evidence");
  const verifier = lifecycleByKey(lifecycle, "verifier");
  const briefing = lifecycleByKey(lifecycle, "owner_briefing");

  const buildState = combineTrackerStates([worker?.state, actionArtifact?.state]);
  const buildDetail = knownDetail(buildState === "FAILED" && verifier?.state === "FAILED" ? verifier : worker)
    ?? knownDetail(actionArtifact)
    ?? knownDetail(worker)
    ?? "Build state is not reported.";
  const verifyState = combineTrackerStates([evidence?.state, verifier?.state]);
  const failedVerifyDetail = [evidence, verifier].find((record) => record?.state === "FAILED");
  const activeVerifyDetail = [evidence, verifier].find((record) => record?.state === "ACTIVE");
  const verifyDetail = knownDetail(verifyState === "FAILED" ? failedVerifyDetail ?? null : verifyState === "ACTIVE" ? activeVerifyDetail ?? null : null)
    ?? knownDetail(evidence)
    ?? knownDetail(verifier)
    ?? "Verification state is not reported.";

  const briefingState = normalizeTrackerState(briefing?.state);
  const outcomeState: CompactTrackerState = briefingState === "COMPLETE" || missionStatus === "COMPLETE"
    ? "COMPLETE"
    : briefingState;
  const outcomeDetail = missionStatus === "COMPLETE" && outcomeState === "COMPLETE" && !knownDetail(briefing)
    ? "Mission complete."
    : knownDetail(briefing) ?? "No outcome is recorded yet.";

  return [
    { key: "goal", label: "Goal", state: normalizeTrackerState(goal?.state), detail: knownDetail(goal) ?? "Goal state is not reported." },
    { key: "plan", label: "Plan", state: normalizeTrackerState(plan?.state), detail: knownDetail(plan) ?? "Plan state is not reported." },
    { key: "build", label: "Build", state: buildState, detail: buildDetail },
    { key: "verify", label: "Verify", state: verifyState, detail: verifyDetail },
    { key: "outcome", label: "Outcome", state: outcomeState, detail: outcomeDetail },
  ];
}

function trackerStateWord(state: CompactTrackerState): string {
  switch (state) {
    case "COMPLETE": return "Complete";
    case "ACTIVE": return "Active";
    case "PENDING": return "Pending";
    case "FAILED": return "Failed";
    default: return "Unknown";
  }
}

/** Builder/verifier state line in plain words — never raw IDs, hashes, or counts. */
function currentResultText(run: MissionRun): string {
  const lifecycle = run.lifecycle ?? [];
  if (lifecycle.length > 0) {
    const word = (state: AutonomyLifecycleRecord["state"] | undefined): string => {
      switch (state) {
        case "COMPLETE": return "complete";
        case "ACTIVE": return "active";
        case "PENDING": return "pending";
        case "FAILED": return "failed";
        default: return "unknown";
      }
    };
    const worker = lifecycleByKey(lifecycle, "worker")?.state;
    const actionArtifact = lifecycleByKey(lifecycle, "action_artifact")?.state;
    const evidence = lifecycleByKey(lifecycle, "evidence")?.state;
    const verifier = lifecycleByKey(lifecycle, "verifier")?.state;
    return `Builder ${word(combineTrackerStates([worker, actionArtifact]) === "UNKNOWN" ? undefined : combineTrackerStates([worker, actionArtifact]))} · Verifier ${word(combineTrackerStates([evidence, verifier]) === "UNKNOWN" ? undefined : combineTrackerStates([evidence, verifier]))}.`;
  }
  const stageWord = (state: string | undefined): string => {
    switch (state) {
      case "COMPLETE": return "complete";
      case "ACTIVE": return "active";
      case "PENDING": return "pending";
      case "FAILED": return "failed";
      default: return "unknown";
    }
  };
  const build = run.pipeline.find((stage) => stage.key === "build")?.state;
  const verify = run.pipeline.find((stage) => stage.key === "verify")?.state;
  const latestVerdict = run.verdicts.find((verdict) => verdict.state === "EVALUATING" || verdict.state === "FAILED")
    ?? run.verdicts[0];
  const verifierWord = latestVerdict
    ? latestVerdict.state === "PASSED" ? "passed" : latestVerdict.state === "FAILED" ? "failed" : latestVerdict.state === "WARN_ACCEPT" ? "accepted with warnings" : "evaluating"
    : stageWord(verify);
  return `Builder ${stageWord(build)} · Verifier ${verifierWord}.`;
}

/**
 * Historical banner for `mission-4646c7023b05`-class missions. Renders iff
 * `isHistoricalMission` holds; the "Not affecting current Hoowen work" clause
 * is appended only when the loaded snapshot shows no shared refs with the
 * Hoowen subgraph (computed from loaded workers/evidence/decisionQueue/
 * related mission_id refs — never hardcoded) AND a Hoowen-family active goal
 * exists in the loaded owner surface. When linkage cannot be computed from
 * loaded data, the Hoowen clause is omitted rather than guessed.
 */
export function historicalBannerForMission(
  mission: Mission,
  ownerSurface: OwnerControlSurface | null,
  loaded: { workers: Worker[]; evidence: EvidenceArtifact[]; related: RelatedEntities },
): string | null {
  const decisionQueue = ownerSurface?.decisionQueue ?? null;
  if (!isHistoricalMission(mission, activeGoalDirectiveIds(ownerSurface), decisionQueue)) return null;
  const lineage = missionDirectiveId(mission);
  const ciMarked = /pw-dupkey|ci_failure|ci-failure/i.test(lineage);
  const middle = ciMarked ? "Blocked on CI verification" : plainMissionState(mission.status);
  const headline = `Historical mission · ${middle}`;
  if (!ownerSurface) return headline;
  const hoowenGoal = ownerGoalsOf(ownerSurface)
    .filter(isActiveStandingGoal)
    .find((goal) => /hoowen/i.test(goalText(goal)));
  if (!hoowenGoal) return headline;
  const hoowenId = goalDirectiveId(hoowenGoal);
  if (!hoowenId) return headline;
  if (lineage.includes(hoowenId)) return headline;
  const sharedRef = (value: unknown): boolean =>
    typeof value === "string" && value.length > 0 && value.includes(hoowenId);
  const workerRefs = loaded.workers.flatMap((worker) => [worker.id, ...(worker.missionIds ?? [])]);
  const evidenceRefs = loaded.evidence.flatMap((item) => [item.id, item.missionRef, item.workerId ?? ""]);
  const queueRecords = [...(decisionQueue?.open ?? []), ...(decisionQueue?.history ?? [])];
  const queueRefs = queueRecords.flatMap((record) => [record.mission_id ?? "", record.entity_id ?? "", record.queue_id ?? ""]);
  const relatedRefs = [
    ...loaded.related.missions, ...loaded.related.agents, ...loaded.related.evidence,
    ...loaded.related.systems, ...loaded.related.memory, ...loaded.related.timeline,
    ...loaded.related.notifications,
  ].map((ref) => ref.id);
  const linked = [...workerRefs, ...evidenceRefs, ...queueRefs, ...relatedRefs].some(sharedRef);
  if (linked) return headline;
  return `${headline} · Not affecting current Hoowen work`;
}

export interface MissionDetailContentProps {
  mission: Mission;
  run: MissionRun;
  workers: Worker[];
  evidence: EvidenceArtifact[];
  lineage: LineageNode[];
  steps: MissionStep[];
  related: RelatedEntities;
  ownerSurface: OwnerControlSurface | null;
  sourceMode: ResolvedSourceMode;
  mutationsEnabled: boolean;
  stopActive: boolean;
  notifications?: Notification[];
  trustSummary?: TrustSummary | null;
  onOpenTrustDialog?: () => void;
}

/**
 * Presentation-only mission view per spec §5: primary order
 * title/status/serves/summary/current-result/next/owner-action + compact
 * Goal → Plan → Build → Verify → Outcome tracker, with technical sections
 * behind progressive disclosure and state-aware buttons.
 */
export function MissionDetailContent({
  mission,
  run,
  workers,
  evidence,
  lineage,
  steps,
  related,
  ownerSurface,
  sourceMode,
  mutationsEnabled,
  stopActive,
  notifications = [],
  trustSummary = null,
  onOpenTrustDialog,
}: MissionDetailContentProps) {
  const [dialog, setDialog] = useState<"halt" | "gate" | null>(null);
  const [halted, setHalted] = useState(false);
  const [gateRequested, setGateRequested] = useState(false);

  const goals = ownerGoalsOf(ownerSurface);
  const decisionQueue = ownerSurface?.decisionQueue ?? null;
  const historical = isHistoricalMission(mission, activeGoalDirectiveIds(ownerSurface), decisionQueue);
  const banner = historicalBannerForMission(mission, ownerSurface, { workers, evidence, related });
  const servesLabel = servingGoalLabel(mission, goals);
  const servesDirectiveId = servingLabelKnown(servesLabel) ? servingDirectiveId(mission, goals) : null;
  const summarySource = mission.objective?.trim() ? mission.objective : mission.description;
  const summary = plainSummary(summarySource, 3);
  const terminal = mission.status === "COMPLETE" || mission.status === "BLOCKED" || mission.status === "HALTED" || mission.status === "CANCELLED";
  const ownerAction = ownerActionForMission(mission, decisionQueue);
  const nextAction = mission.nextAction?.trim() ?? "";
  const tracker = compactMissionTracker(run.lifecycle, mission.status);
  const currentResult = currentResultText(run);
  const showHalt = shouldShowHaltButton(mission.status, mutationsEnabled) && !stopActive;
  const showGate = shouldShowGateButton(sourceMode) && !stopActive;

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
  const effectReceipts = missionEffectReceipts(ownerSurface?.effectTruth ?? null, mission.id);
  const lifecycle = run.lifecycle ?? [];
  const lifecycleKnown = lifecycle.filter((record) => record.state !== "UNKNOWN" && record.state !== "UNAVAILABLE");

  return (
    <div className="shell-content">
      <div className="md-breadcrumb">
        <Link to="/missions" className="md-back">
          <ArrowLeft size={14} aria-hidden="true" />
          <span className="t-label">Missions</span>
        </Link>
        <span className="t-mono text-muted md-crumb-id tech-id">{mission.code}</span>
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

      {banner ? (
        <div className="md-historical-banner" role="status">
          <History size={15} aria-hidden="true" />
          <span className="t-label">{banner}</span>
        </div>
      ) : null}

      <section className="md-primary" aria-label="Mission summary">
        <h1 className="t-display md-title md-primary-title">{sentenceCaseTitle(mission)}</h1>
        <span className="t-mono text-muted tech-id">{mission.id}</span>
        <div className="md-head-badges md-primary-status">
          <StatusBadge status={mission.status} pulse={isExecutingMissionStatus(mission.status)} />
          {historical ? <span className="badge badge-muted">Historical</span> : null}
        </div>
        <p className="md-primary-serves">
          <span className="t-label text-muted">Serves: </span>
          {servesDirectiveId ? (
            <Link to="/work?status=all" className="text-link">{servesLabel}</Link>
          ) : (
            <span>{servesLabel}</span>
          )}
        </p>
        <p className="prose md-desc md-primary-summary">
          <span>{summary}</span> <span className="md-state-line">Status: {plainMissionState(mission.status)}.</span>
        </p>
        <p className="md-primary-current">
          <span className="t-label text-muted">{terminal ? "Result: " : "Current: "}</span>
          <span>{currentResult}</span>
        </p>
        <p className="md-primary-next">
          <span className="t-label text-muted">Next: </span>
          {nextAction ? <span>{mission.nextAction}</span> : <span>No next action recorded</span>}
        </p>
        <div className="md-primary-action">
          <span className="t-label text-muted">Needs you: </span>
          {ownerAction ? (
            <span>
              <span>{ownerAction}</span> <Link to="/owner" className="text-link">Review the decision</Link>
            </span>
          ) : (
            <span>Nothing needed from you.</span>
          )}
        </div>

        <div className="md-compact-progress">
          <h2 className="t-label panel-title text-muted">
            <Workflow size={13} aria-hidden="true" />
            Progress
          </h2>
          <ol className="md-compact-tracker" aria-label="Mission progress">
            {tracker.map((stage) => (
              <li key={stage.key} className={`md-compact-stage md-compact-stage-${stage.state.toLowerCase()}`}>
                <span className="t-label">{stage.label}</span>
                <small className="t-mono text-muted">{trackerStateWord(stage.state)}</small>
              </li>
            ))}
          </ol>
        </div>

        {showHalt || showGate ? (
          <div className="md-actions">
            {showHalt ? (
              <button
                type="button"
                className="btn"
                onClick={() => setDialog("halt")}
                disabled={halted || stopActive || !mutationsEnabled}
                aria-label={sourceMode === "SIMULATED" ? "Request halt of this mission execution (simulated)" : `Request halt of this mission execution (${sourceModeLabel(sourceMode).toLowerCase()})`}
              >
                {halted ? "Halt requested" : "Halt Execution"}
              </button>
            ) : null}
            {showGate ? (
              <button
                type="button"
                className="btn btn-primary"
                onClick={() => setDialog("gate")}
                disabled={gateRequested || stopActive || !mutationsEnabled}
                aria-label={sourceMode === "SIMULATED" ? "Request fresh verifier evaluation of the current gate (simulated)" : `Gate re-evaluation unavailable in ${sourceModeLabel(sourceMode)} source mode`}
              >
                {gateRequested ? "Re-evaluation requested" : "Re-evaluate Gate"}
              </button>
            ) : null}
          </div>
        ) : null}
      </section>

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
              <span className="t-mono tech-id">{mission.id}</span>
            </Link>
          ))}
        </div>
      </section>

      <details className="panel panel-corner detail-panel detail-panel-full md-disclosure" aria-label="Technical details">
        <summary className="t-label panel-title text-muted">Technical details</summary>
        <div className="panel-body">
          <p className="t-mono text-muted">
            <span className="tech-id">{mission.id}</span> · <span className="tech-id">{mission.code}</span> · Stage {mission.stage} · Started {mission.startedAt}
          </p>
          {lifecycleKnown.length > 0 ? (
            <ul className="detail-event-list">
              {lifecycle.map((record) => (
                <li key={record.key}>
                  <span className="t-label">{record.label}</span>
                  <span className="t-mono text-muted">{record.state} · {record.eventIds.length} event{record.eventIds.length === 1 ? "" : "s"}</span>
                  {record.eventIds.length > 0 ? (
                    <span className="t-mono text-muted tech-id" title={record.eventIds.join(", ")}>
                      latest {record.eventIds[record.eventIds.length - 1].slice(0, 8)}…
                    </span>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : (
            <span className="t-mono text-dim">No canonical lifecycle records are projected for this mission.</span>
          )}
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
          <section className="panel panel-corner md-streams" aria-label="Active data streams">
            <div className="panel-header">
              <h3 className="t-label panel-title text-muted">Active Data Streams</h3>
              <span className="t-mono text-muted md-stream-count">{run.streams.length} streams</span>
            </div>
            <div className="md-stream-grid">
              {run.streams.map((s) => (
                <div key={s.id} className="md-stream">
                  <div className="md-stream-meta">
                    <span className="t-label md-stream-name">{s.name}</span>
                    <span className="t-mono md-stream-status">{s.status}</span>
                  </div>
                </div>
              ))}
            </div>
          </section>
          <section className="panel panel-corner md-hub" aria-label="Assigned workers">
            <div className="panel-header">
              <h3 className="t-label panel-title text-muted">
                <Users size={13} className="panel-icon" />
                Assigned Workers
              </h3>
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
          {steps.length > 0 ? (
            <ul className="detail-event-list">
              {steps.map((step) => (
                <li key={step.id}>
                  <span className="t-label">{step.label}</span>
                  <span className="t-mono text-muted">{step.state}</span>
                  <span className="t-mono text-muted tech-id">{step.id}</span>
                </li>
              ))}
            </ul>
          ) : null}
          <section className="panel panel-corner detail-panel detail-panel-full md-lineage" aria-label="Verification and repair lineage">
            <div className="panel-header">
              <h3 className="t-label panel-title text-muted">
                <History size={13} className="panel-icon" />
                Verification / Repair Lineage
              </h3>
              <span className="t-mono text-muted">presentation only - verifier outcomes come from evidence</span>
            </div>
            <div className="panel-body">
              <Lineage nodes={lineage} />
            </div>
          </section>
          <section className="panel panel-corner detail-panel detail-panel-full" aria-label="Effects and provider receipts">
            <div className="panel-header">
              <h3 className="t-label panel-title text-muted">
                <Link2 size={13} className="panel-icon" />
                Effects / Provider Receipts
              </h3>
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
          {trustSummary ? (
            <div className="md-trust-row">
              <span className="t-label text-muted">Trust state</span>
              <span className="t-mono md-trust-value">{trustSummary.state} · gate {trustSummary.gateStatus}</span>
              {onOpenTrustDialog ? (
                <button type="button" className="text-link" onClick={onOpenTrustDialog}>
                  Explain
                </button>
              ) : null}
            </div>
          ) : null}
        </div>
      </details>

      <details className="panel panel-corner detail-panel detail-panel-full md-disclosure" aria-label="Evidence">
        <summary className="t-label panel-title text-muted">Evidence</summary>
        <div className="panel-body">
          <section className="panel panel-corner md-hub" aria-label="Evidence bundle">
            <div className="panel-header">
              <h3 className="t-label panel-title text-muted">
                <Database size={13} className="panel-icon" />
                Evidence Bundle
              </h3>
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
          <section className="panel panel-corner md-verdicts" aria-label="Verifier verdicts">
            <div className="panel-header">
              <h3 className="t-label panel-title text-muted">
                <ClipboardCheck size={13} className="panel-icon" />
                Verifier Verdicts
              </h3>
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
        </div>
      </details>

      <details className="panel panel-corner detail-panel detail-panel-full md-disclosure" aria-label="Timeline">
        <summary className="t-label panel-title text-muted">Timeline</summary>
        <div className="panel-body">
          <section className="panel panel-corner md-log-panel" aria-label="Agent telemetry log">
            <div className="panel-header">
              <h3 className="t-label panel-title text-muted">
                <Terminal size={13} className="panel-icon" />
                Agent Telemetry Log
              </h3>
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
          <section className="panel panel-corner detail-panel detail-panel-full md-related" aria-label="Related records">
            <div className="panel-header">
              <h3 className="t-label panel-title text-muted">
                <Link2 size={13} className="panel-icon" />
                Related Records
              </h3>
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
        </div>
      </details>

      <Dialog
        open={dialog === "halt"}
        title={sourceMode === "SIMULATED" ? "Halt Execution - Simulated" : `Halt Execution - ${sourceModeLabel(sourceMode)}`}
        onClose={() => setDialog(null)}
        labelledBy="halt-dialog-title"
        footer={
          halted ? (
            <>
              <span className="t-mono text-muted" style={{ marginRight: "auto" }}>
                {sourceMode === "SIMULATED" ? "HALT REQUEST SIMULATED - NO RUNTIME EFFECT" : `HALT REQUEST ${sourceModeLabel(sourceMode)} - CONFIRMATION FOLLOWS RUNTIME EVIDENCE`}
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
            {sourceMode === "SIMULATED" ? "This candidate records the halt request locally. A future runtime adapter will submit it as an authenticated owner directive to the Supervisor." : `Live halt submits STOP_MISSION as an authenticated owner directive. Confirming here records the request; canonical confirmation follows ${sourceModeLabel(sourceMode)} runtime evidence.`}
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
}

function servingLabelKnown(label: string): boolean {
  return label !== "UNKNOWN" && label.trim().length > 0;
}

export function MissionDetail() {
  const { missionId } = useParams();
  const { client, version, notifications, stopActive, sourceMode, trustSummary, mode, mutationsEnabled, openTrustDialog } = useCommandCenter();
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
        ownerSurface,
      } satisfies MissionDetailData;
    },
    [client, version, missionIdResolved],
  );

  return (
    <DetailRouteQueryGate
      query={detailQuery}
      loadingLabel="Loading mission detail"
      notFoundLabel="Mission not found"
      notFoundDetail={sourceMode === "SIMULATED" ? "No simulated mission exists at this identifier." : sourceMode === "LIVE" ? "No canonical mission exists at this identifier." : `Mission lookup unavailable: runtime source is ${sourceModeLabel(sourceMode)}.`}
      errorLabel="Mission detail unavailable"
      surface="Mission detail"
    >
      {({ mission, run, workers, evidence, lineage, steps, related, ownerSurface }) => (
        <MissionDetailContent
          mission={mission}
          run={run}
          workers={workers}
          evidence={evidence}
          lineage={lineage}
          steps={steps}
          related={related}
          ownerSurface={ownerSurface}
          sourceMode={sourceMode}
          mutationsEnabled={mutationsEnabled}
          stopActive={stopActive}
          notifications={notifications}
          trustSummary={trustSummary}
          onOpenTrustDialog={openTrustDialog}
        />
      )}
    </DetailRouteQueryGate>
  );
}
