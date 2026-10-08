import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  CheckCircle2,
  ChevronRight,
  CircleAlert,
  Clock3,
  Crosshair,
  Eye,
  Focus,
  Lightbulb,
  Radio,
} from "lucide-react";
import { Dialog } from "../components/Dialog";
import { JarvisNucleus } from "../components/JarvisNucleus";
import { RouteQueryGate } from "../components/RouteQueryGate";
import { useCommandCenter } from "../context/CommandCenterContext";
import { useRouteQuery } from "../hooks/useRouteQuery";
import type { Mission } from "../lib/contracts";
import { summarizeListText as summarize } from "../lib/format";
import { resolveNucleusState, type JarvisNucleusState } from "../lib/nucleusState";
import {
  currentChildForGoal,
  goalChildren,
  ownerActionForMission,
  rankOwnerGoals,
  servingGoalLabel,
  type OwnerStandingGoal,
} from "../lib/ownerWork";
import { loadOverviewData, type OverviewData } from "../lib/overviewLoader";
import { sourceModeLabel } from "../lib/sourceMode";
import { useLocale } from "../i18n/LocaleContext";

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function compactAge(value: string | null | undefined): string {
  if (!value) return "start time unknown";
  const timestamp = new Date(value).getTime();
  if (!Number.isFinite(timestamp)) return "start time unknown";
  const minutes = Math.max(0, Math.floor((Date.now() - timestamp) / 60_000));
  if (minutes < 60) return `${minutes}m elapsed`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours}h ${minutes % 60}m elapsed`;
  return `${Math.floor(hours / 24)}d elapsed`;
}

function activeMission(missions: Mission[]): Mission | null {
  return missions.find((mission) => ["ACTIVE", "IN_PROGRESS", "INTERVENTION"].includes(mission.status)) ?? null;
}

function ownerGoalDirectiveId(goal: OwnerStandingGoal | null | undefined): string {
  if (!goal) return "";
  const id = goal.directive_id ?? goal.directiveId;
  return typeof id === "string" ? id.trim() : "";
}

/** Millis for a `startedAt` value; missing/unparseable sorts oldest. */
function startedAtTime(value: unknown): number {
  if (typeof value !== "string" || !value) return 0;
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : 0;
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
      return "Unknown";
  }
}

/** Primary-copy text: null for missing/blank/"UNKNOWN" sentinel values. */
function knownCopy(value: unknown): string | null {
  const normalized = text(value);
  return normalized && normalized !== "UNKNOWN" ? normalized : null;
}

const lifecycle = ["Observe", "Reason", "Prepare", "Act", "Verify"] as const;

function stageIndex(state: JarvisNucleusState, mission: Mission | null): number | null {
  if (state === "OBSERVING") return 0;
  if (state === "PLANNING" || state === "RESEARCHING") return 1;
  if (state === "EXECUTING") return 3;
  if (state === "VERIFYING") return 4;
  const stage = mission?.stage.toLowerCase();
  if (stage?.includes("observ")) return 0;
  if (stage?.includes("plan") || stage?.includes("research")) return 1;
  if (stage?.includes("build") || stage?.includes("execut")) return 3;
  if (stage?.includes("verif")) return 4;
  return null;
}

function metricValue(data: OverviewData, key: string): string {
  const metric = data.autonomy.funnel[key];
  return metric?.status === "AVAILABLE" && metric.value != null ? String(metric.value) : "—";
}

export function OverviewContent(data: OverviewData) {
  const { sourceMode, stopActive, scenario, streamConnected, connection } = useCommandCenter();
  const { t } = useLocale();
  const [focusOpen, setFocusOpen] = useState(false);
  const mission = activeMission(data.missions);
  const currentWork = data.autonomy.current_work;
  const candidateTitle = currentWork?.candidate?.title ?? null;
  // Ranked owner goals first: the active goal (e.g. the Hoowen DELEGATE) beats
  // historical failures. Missions carry the durable created_from directive id
  // on `owner`, so they pass straight into the selectors — no title matching.
  // Active semantics live in the Task 1 ranker (`rankOwnerGoals` sorts active
  // directives first per spec §2); the UI serves the top-ranked goal directly
  // so ranker-side changes flow through with no UI-tier mirror to diverge.
  const ownerGoals = (data.ownerSurface?.goals ?? []) as OwnerStandingGoal[];
  const rankedGoals = rankOwnerGoals(ownerGoals, data.missions);
  const topRankedGoal = rankedGoals[0] ?? null;
  // rankOwnerGoals sorts active-first but does not filter, so a non-empty all-inactive list still yields a [0]: serve it only when its own active/status markers read active, else fall through to the no-goal path.
  const activeOwnerGoal = topRankedGoal && (topRankedGoal.active === 1 || topRankedGoal.active === true || topRankedGoal.active === "1" || topRankedGoal.active === "true" || (typeof topRankedGoal.active === "string" && topRankedGoal.active.toUpperCase() === "ACTIVE") || ((topRankedGoal.active === undefined || topRankedGoal.active === null) && typeof topRankedGoal.status === "string" && topRankedGoal.status.toUpperCase() === "ACTIVE")) ? topRankedGoal : null;
  const activeGoalDirectiveId = ownerGoalDirectiveId(activeOwnerGoal);
  const goalMissions = activeGoalDirectiveId ? goalChildren(activeGoalDirectiveId, data.missions) : [];
  const currentGoalChild = activeGoalDirectiveId
    ? currentChildForGoal(activeGoalDirectiveId, data.missions)
    : null;
  const goalShortLabel = currentGoalChild
    ? servingGoalLabel(currentGoalChild, ownerGoals)
    : activeOwnerGoal
      ? (knownCopy(activeOwnerGoal.label) ??
        knownCopy(activeOwnerGoal.exact_text) ??
        knownCopy(activeOwnerGoal.objective))
      : null;
  const focusGoalLabel = goalShortLabel && goalShortLabel !== "UNKNOWN" ? goalShortLabel : null;
  const activeGoalCommandType = activeOwnerGoal
    ? (activeOwnerGoal.command_type ?? activeOwnerGoal.commandType)
    : null;
  const activeGoalKind = (() => {
    const raw = activeGoalCommandType;
    return typeof raw === "string" && raw.toUpperCase() === "DELEGATE"
      ? "Active strategic goal"
      : "Active owner goal";
  })();
  const focusTitle = mission?.title ?? candidateTitle ?? focusGoalLabel ?? t("overview.noActiveFocus");
  const goalExactText = activeOwnerGoal
    ? (knownCopy(activeOwnerGoal.exact_text) ??
      knownCopy(activeOwnerGoal.objective) ??
      focusGoalLabel)
    : null;
  const decisionReason = text(data.executive?.latest_decision?.reason)
    ?? text(data.autonomy.executive.wait_reason);
  const focusWhy = goalExactText && decisionReason
    ? `${goalExactText} — ${decisionReason}`
    : (goalExactText ?? decisionReason ?? mission?.objective
      ?? "JARVIS has not reported a durable reason for a current focus.");
  const executiveNextWake = text(data.autonomy.executive.next_wake);
  const nextAction =
    knownCopy(currentGoalChild?.nextAction) ??
    text(currentWork?.final_outcome?.next_action) ??
    mission?.nextAction ??
    data.autonomy.executive.wait_reason ??
    (executiveNextWake ? `Reconsider at ${executiveNextWake}.` : null) ??
    "No next action is recorded.";
  // Executing mission wins for detail views; otherwise the goal's current
  // child carries the card so history never stands in for live work.
  const detailMission = mission ?? currentGoalChild;
  const goalChildWorkers = currentGoalChild
    ? data.workers.filter((worker) => worker.status === "ACTIVE" && worker.missionIds?.includes(currentGoalChild.id))
    : [];
  const progressLine = currentGoalChild
    ? [
        knownCopy(currentGoalChild.objective)
          ?? (currentGoalChild.title && currentGoalChild.title !== currentGoalChild.id ? currentGoalChild.title : null)
          ?? "Current work is recorded without a summary.",
        plainMissionState(currentGoalChild.status),
        goalChildWorkers.length > 0
          ? `${goalChildWorkers.length} active worker${goalChildWorkers.length === 1 ? "" : "s"}`
          : null,
      ].filter((part): part is string => Boolean(part)).join(" · ")
    : null;
  const state = resolveNucleusState({
    stopActive,
    sourceMode,
    streamConnected,
    connectionStale: connection.stale,
    ownerAttention: data.ownerAttention.length > 0,
    scenario,
    executiveState: data.autonomy.executive.state,
    autonomyStatus: data.autonomy.status,
    activeMissionStatus: mission?.status ?? null,
  });
  const phase = data.autonomy.executive.stage ?? mission?.stage ?? null;
  const activeStage = stageIndex(state, mission);
  const lifecycleSuspended = state === "STOPPED";
  const lifecycleClass = (index: number) =>
    index === activeStage
      ? lifecycleSuspended ? "is-suspended" : "is-active"
      : activeStage != null && index < activeStage && data.autonomy.status !== "UNAVAILABLE"
        ? "is-complete"
        : "is-unknown";
  const lifecycleStatus = (index: number, focus = false) =>
    index === activeStage
      ? lifecycleSuspended ? "Suspended here" : focus ? "Current phase" : "Current"
      : activeStage != null && index < activeStage && data.autonomy.status !== "UNAVAILABLE"
        ? "Recorded"
        : focus ? "Not yet recorded" : "Unknown";
  // Genuine owner action only: an OPEN decision-queue record linked by
  // mission_id to the current child (or a blocked goal sibling). The first
  // global BLOCKED row — often history — never stands in. With no active
  // goal the goal join is empty by construction, so fall back to scanning
  // goal-unlinked BLOCKED/HALTED missions by mission_id join; only linked
  // open records surface, unlinked BLOCKED titles never do.
  const blockedGoalChild = goalMissions.find((item) => item.status === "BLOCKED" || item.status === "HALTED") ?? null;
  const noGoalBlockedMissions = activeGoalDirectiveId
    ? []
    : data.missions.filter((item) => item.status === "BLOCKED" || item.status === "HALTED");
  const needsYouCandidates = currentGoalChild
    ? [currentGoalChild, ...(blockedGoalChild && blockedGoalChild.id !== currentGoalChild.id ? [blockedGoalChild] : [])]
    : blockedGoalChild ? [blockedGoalChild] : noGoalBlockedMissions;
  let linkedOwnerAction: { mission: Mission; summary: string } | null = null;
  for (const candidate of needsYouCandidates) {
    const summary = ownerActionForMission(candidate, data.ownerAttention);
    if (summary) {
      linkedOwnerAction = { mission: candidate, summary };
      break;
    }
  }
  const completed = data.missions.filter((item) => item.status === "COMPLETE");
  const recentCompletionEvents = data.timeline.filter((event) => /complet|verified|accepted/i.test(`${event.title} ${event.detail}`)).slice(0, 3);
  // LATEST is the newest COMPLETE child of the active goal by `startedAt` —
  // never borrowed from outside the goal's subgraph, never the first row in
  // selector order. Without an active goal the legacy first-completed
  // fallback stays.
  const latestCompletedMission = (() => {
    let newest: (typeof goalMissions)[number] | null = null;
    for (const item of goalMissions) {
      if (item.status !== "COMPLETE") continue;
      if (!newest || startedAtTime(item.startedAt) > startedAtTime(newest.startedAt)) newest = item;
    }
    return newest;
  })() ?? (activeOwnerGoal ? null : completed[0]);
  const latestCompletionEvent = recentCompletionEvents[0];
  const missionEvidence = detailMission ? data.evidence.filter((item) => item.missionRef === detailMission.id) : [];
  const activeWorkers = data.workers.filter((worker) =>
    worker.status === "ACTIVE" && (!detailMission || worker.missionIds?.includes(detailMission.id)));
  const workerCount = detailMission?.agentsActive ?? activeWorkers.length;
  const blocker = linkedOwnerAction?.summary ?? null;
  const signals = data.operationalIntelligence?.signals?.slice(0, 3) ?? [];
  const candidates = data.autonomy.candidates.slice(0, 3);
  const liveConfidence = data.autonomy.candidates[0]?.confidence;
  const source = sourceModeLabel(sourceMode);
  const today = useMemo(() => [
    { label: "Observed", value: metricValue(data, "observed"), detail: data.autonomy.status === "UNAVAILABLE" ? "not integrated" : "signals" },
    { label: "Working", value: String(data.missions.filter((item) => ["ACTIVE", "IN_PROGRESS", "INTERVENTION"].includes(item.status)).length), detail: "missions" },
    { label: "Waiting", value: String(data.missions.filter((item) => ["STANDBY", "BLOCKED", "HALTED"].includes(item.status)).length), detail: "missions" },
    { label: "Completed", value: completed.length ? String(completed.length) : metricValue(data, "verified_outcome"), detail: completed.length ? "missions" : "outcomes" },
    { label: "Attention", value: data.ownerSurface ? String(data.ownerAttention.length) : "—", detail: data.ownerSurface ? "owner items" : "unavailable" },
  ], [data, completed.length]);

  return (
    <div className="jarvis-os rm-home">
      {sourceMode !== "LIVE" ? (
        <div className={`rm-source-banner rm-source-banner--${sourceMode.toLowerCase()}`} role="status">
          <Radio size={14} aria-hidden="true" />
          <strong>{source}</strong>
          <span>{
            sourceMode === "SIMULATED"
              ? "Preview fixture; no production state or effect is represented."
              : sourceMode === "DEGRADED"
                ? "Canonical runtime truth is connected with degraded health; ordinary owner effects remain gated."
                : "Canonical runtime state is unavailable; values fail closed."
          }</span>
        </div>
      ) : null}

      <section className="rm-focus-field" aria-labelledby="current-focus-heading">
        <div className="rm-focus-copy">
          <div className="rm-focus-state-line">
            <span className={`rm-state-mark rm-state-mark--${state.toLowerCase()}`} />
            <span>{state.toLowerCase().replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}</span>
            <span className="rm-focus-separator" />
            <span className="tech-id">{mission?.id ?? currentWork?.candidate?.candidate_id ?? "No active work"}</span>
          </div>
          {activeOwnerGoal && focusGoalLabel ? (
            <p className="rm-focus-goal">
              <strong>{focusGoalLabel}</strong>
              <span>{currentGoalChild ? `${activeGoalKind} · ${plainMissionState(currentGoalChild.status)}` : activeGoalKind}</span>
              {currentGoalChild ? (
                <Link to={`/missions/${currentGoalChild.id}`}>Open current work <ChevronRight size={15} aria-hidden="true" /></Link>
              ) : null}
            </p>
          ) : null}
          <h1 id="current-focus-heading">{focusTitle}</h1>
          <p className="rm-focus-why"><strong>{t("overview.whyNow")}</strong>{focusWhy}</p>
          {progressLine ? (
            <p className="rm-focus-progress"><strong>Progress</strong>{progressLine}</p>
          ) : null}
          <div className="rm-focus-meta">
            <span><Crosshair size={15} aria-hidden="true" />{lifecycleSuspended ? `Suspended at ${phase ?? "unknown phase"}` : phase ?? "Phase unknown"}</span>
            <span><Clock3 size={15} aria-hidden="true" />{compactAge(detailMission?.startedAt)}</span>
            <span><Eye size={15} aria-hidden="true" />Confidence {liveConfidence == null ? "UNKNOWN" : `${Math.round(liveConfidence * 100)}%`}</span>
          </div>
          <div className="rm-focus-next">
            <span>{t("overview.nextLabel")}</span><p>{nextAction}</p>
            <button type="button" className="rm-text-action" onClick={() => setFocusOpen(true)}>
              {t("overview.focusMode")} <Focus size={15} aria-hidden="true" />
            </button>
          </div>
          <div className="rm-focus-glance" aria-label="At a glance">
            <div className={linkedOwnerAction ? "is-attention" : ""}>
              <span>{t("overview.needsYou")}</span>
              <strong>{linkedOwnerAction?.summary ?? "Nothing needed from you."}</strong>
              <small>{linkedOwnerAction ? (knownCopy(linkedOwnerAction.mission.nextAction) ?? "Progress remains held until the recorded dependency is resolved.") : (data.ownerSurface ? "JARVIS can continue within current constraints." : "Owner queue unavailable from this source.")}</small>
            </div>
            <div>
              <span>{t("overview.latestVerified")}</span>
              {latestCompletedMission ? (
                <Link to={`/missions/${latestCompletedMission.id}`}>{latestCompletedMission.title}</Link>
              ) : latestCompletionEvent ? (
                <Link to={`/activity?event=${latestCompletionEvent.id}`}>{latestCompletionEvent.title}</Link>
              ) : (
                <strong>No completed outcome available</strong>
              )}
              <small>{latestCompletedMission?.nextAction ?? latestCompletionEvent?.detail ?? "No verified completion is present in this projection."}</small>
            </div>
          </div>
        </div>
        <div className="rm-focus-nucleus">
          <JarvisNucleus state={state} phase={phase} />
        </div>
      </section>

      <ol className="rm-lifecycle" aria-label="Autonomous work lifecycle">
        {lifecycle.map((label, index) => (
          <li key={label} className={lifecycleClass(index)}>
            <span>{String(index + 1).padStart(2, "0")}</span><strong>{label}</strong>
            <small>{lifecycleStatus(index)}</small>
          </li>
        ))}
      </ol>

      <section className={`rm-attention ${linkedOwnerAction ? "rm-attention--open" : ""}`} aria-labelledby="attention-title">
        <div className="rm-attention-icon">{linkedOwnerAction ? <CircleAlert size={19} aria-hidden="true" /> : <CheckCircle2 size={19} aria-hidden="true" />}</div>
        <div>
          <h2 id="attention-title">{linkedOwnerAction ? t("overview.attentionNeeded") : t("overview.noDecision")}</h2>
          <p>{linkedOwnerAction?.summary ?? (data.ownerSurface ? t("overview.canContinue") : "Owner decision queue is not available from this source.")}</p>
        </div>
        {linkedOwnerAction ? <Link to={`/missions/${linkedOwnerAction.mission.id}`}>{t("overview.review")} <ChevronRight size={15} /></Link> : <Link to="/you">{t("overview.ownerContext")} <ChevronRight size={15} /></Link>}
      </section>

      <section className="rm-today" aria-labelledby="today-title">
        <div className="rm-section-heading"><h2 id="today-title">{t("overview.today")}</h2><span>{streamConnected ? t("overview.updatesConnected") : t("overview.updatesPaused")}</span></div>
        <div className="rm-today-strip">
          {today.map((item) => <div key={item.label}><span>{item.label}</span><strong>{item.value}</strong><small>{item.detail}</small></div>)}
        </div>
      </section>

      <div className="rm-home-depth">
        <section className="rm-depth-section" aria-labelledby="completed-title">
          <div className="rm-section-heading"><h2 id="completed-title">{t("overview.recentlyCompleted")}</h2><Link to="/work?status=completed">{t("overview.allWork")} <ArrowRight size={14} /></Link></div>
          <div className="rm-record-list">
            {completed.slice(0, 3).map((item) => <Link key={item.id} to={`/missions/${item.id}`}><CheckCircle2 size={16} /><span><strong>{summarize(item.title, "Completed work — open for detail")}</strong><small className="tech-id">{item.id}</small><small>{summarize(item.nextAction, "Outcome recorded.")}</small></span></Link>)}
            {completed.length === 0 && recentCompletionEvents.map((event) => <Link key={event.id} to={`/activity?event=${event.id}`}><CheckCircle2 size={16} /><span><strong>{summarize(event.title, "Recorded activity — open for detail")}</strong><small>{summarize(event.detail, "Open the entry for detail.")}</small></span></Link>)}
            {completed.length === 0 && recentCompletionEvents.length === 0 ? <p className="rm-empty-line">{t("overview.noCompleted")}</p> : null}
          </div>
        </section>

        <section className="rm-depth-section" aria-labelledby="discovered-title">
          <div className="rm-section-heading"><h2 id="discovered-title">{t("overview.discovered")}</h2><Link to="/world">{t("overview.openWorld")} <ArrowRight size={14} /></Link></div>
          <div className="rm-record-list">
            {signals.map((signal) => <Link key={signal.signal_id} to="/world"><Lightbulb size={16} /><span><strong>{summarize(signal.summary, "Observed signal — open World for detail")}</strong><small>{signal.salience} · score {signal.score ?? "UNKNOWN"}</small></span></Link>)}
            {signals.length === 0 && candidates.map((candidate) => <Link key={candidate.candidate_id} to={`/executive?candidate=${candidate.candidate_id}`}><Lightbulb size={16} /><span><strong>{summarize(candidate.title, "Discovered candidate — open for detail")}</strong><small>{candidate.state} · {candidate.project ?? "project unknown"}</small></span></Link>)}
            {signals.length === 0 && candidates.length === 0 ? <p className="rm-empty-line">{t("overview.noDiscovered")}</p> : null}
          </div>
        </section>

        <section className="rm-depth-section rm-depth-section--next" aria-labelledby="next-title">
          <div className="rm-section-heading"><h2 id="next-title">{t("overview.next")}</h2><Link to="/activity">{t("overview.traceActivity")} <ArrowRight size={14} /></Link></div>
          <div className="rm-next-sequence">
            <span>01</span><p>{nextAction}</p>
            <span>02</span><p>{state === "VERIFYING" ? "Reconcile independent evidence before accepting an outcome." : "Verification remains required before any claimed outcome is trusted."}</p>
            <span>03</span><p>{data.autonomy.executive.next_wake ? `Reconsider at ${data.autonomy.executive.next_wake}.` : "The next wake boundary is not reported."}</p>
          </div>
        </section>
      </div>

      <Dialog open={focusOpen} title={t("overview.focusMode")} onClose={() => setFocusOpen(false)} variant="focus">
        <div className={`rm-focus-mode rm-focus-mode--${state.toLowerCase()}`}>
          <section className="rm-focus-mode-hero" aria-labelledby="focus-mode-objective">
            <div className="rm-focus-mode-copy">
              <div className="rm-focus-mode-state">
                <span className={`rm-state-mark rm-state-mark--${state.toLowerCase()}`} />
                <strong>{state.replace("_", " ")}</strong>
                <span className="tech-id">{mission?.id ?? currentWork?.candidate?.candidate_id ?? "NO ACTIVE ID"}</span>
              </div>
              <h2 id="focus-mode-objective">{focusTitle}</h2>
              <div className="rm-focus-mode-reason">
                <span>Reason</span>
                <p>{focusWhy}</p>
              </div>
              <div className="rm-focus-mode-primary">
                <div>
                  <span>{lifecycleSuspended ? "Suspended at" : "Current phase"}</span>
                  <strong>{phase ?? "UNKNOWN"}</strong>
                </div>
                <div>
                  <span>Progress evidence</span>
                  <strong>{missionEvidence.length ? `${missionEvidence.length} linked record${missionEvidence.length === 1 ? "" : "s"}` : "NOT REPORTED"}</strong>
                </div>
                <div>
                  <span>Active workers</span>
                  <strong>{workerCount}</strong>
                </div>
                <div>
                  <span>Source</span>
                  <strong>{source}</strong>
                </div>
              </div>
            </div>
            <div className="rm-focus-mode-nucleus">
              <JarvisNucleus state={state} phase={phase} />
            </div>
          </section>

          <ol className="rm-focus-mode-lifecycle" aria-label="Focus mode operational lifecycle">
            {lifecycle.map((label, index) => (
              <li key={label} className={lifecycleClass(index)}>
                <span>{String(index + 1).padStart(2, "0")}</span>
                <strong>{label}</strong>
                <small>{lifecycleStatus(index, true)}</small>
              </li>
            ))}
          </ol>

          <section className="rm-focus-mode-operations" aria-label="Operational focus details">
            <div>
              <span className="rm-focus-mode-label">Mission and workers</span>
              <strong>{detailMission?.title ?? "No active mission recorded"}</strong>
              <p>
                {activeWorkers.length > 0
                  ? activeWorkers.map((worker) => `${worker.name} · ${worker.role}`).join(" / ")
                  : workerCount > 0
                    ? `${workerCount} active worker${workerCount === 1 ? "" : "s"} reported; identities are not linked in this projection.`
                    : "No active worker is linked to this focus."}
              </p>
              {detailMission ? <Link to={`/missions/${detailMission.id}`}>Open mission <ArrowRight size={14} /></Link> : null}
            </div>
            <div>
              <span className="rm-focus-mode-label">Verification</span>
              <strong>{missionEvidence[0]?.verdict ?? "NO LINKED RECORD"}</strong>
              <p>{missionEvidence[0] ? `${missionEvidence.length} evidence artifact${missionEvidence.length === 1 ? "" : "s"} recorded for this focus.` : "No mission-linked verification artifact is present in this projection."}</p>
              {missionEvidence[0] ? <Link to={`/evidence/${missionEvidence[0].id}`}>Inspect evidence <ArrowRight size={14} /></Link> : <Link to="/evidence">Open evidence ledger <ArrowRight size={14} /></Link>}
            </div>
            <div className={blocker ? "is-blocked" : ""}>
              <span className="rm-focus-mode-label">Blocker / owner boundary</span>
              <strong>{blocker ?? "No blocker recorded"}</strong>
              <p>{linkedOwnerAction ? (knownCopy(linkedOwnerAction.mission.nextAction) ?? "Progress remains held until the recorded dependency is resolved.") : "JARVIS can continue within current constraints."}</p>
              <Link to={linkedOwnerAction ? `/missions/${linkedOwnerAction.mission.id}` : "/you"}>
                {blocker ? "Review boundary" : "Open owner context"} <ArrowRight size={14} />
              </Link>
            </div>
          </section>

          <section className="rm-focus-mode-next" aria-label="Next transition">
            <span>Next transition</span>
            <strong>{nextAction}</strong>
            <small>{data.autonomy.executive.next_wake ? `Next wake ${data.autonomy.executive.next_wake}` : "No later wake boundary is reported."}</small>
          </section>
        </div>
      </Dialog>
    </div>
  );
}

export function Overview() {
  const { client, version } = useCommandCenter();
  const query = useRouteQuery(() => loadOverviewData(client), [client, version], { keepPreviousWhileLoading: true });
  return (
    <RouteQueryGate query={query} loadingLabel="Loading JARVIS" errorLabel="JARVIS unavailable" surface="Executive projection">
      {(data) => <OverviewContent {...data} />}
    </RouteQueryGate>
  );
}
