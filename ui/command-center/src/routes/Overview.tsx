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
import { resolveNucleusState, type JarvisNucleusState } from "../lib/nucleusState";
import { loadOverviewData, type OverviewData } from "../lib/overviewLoader";
import { sourceModeLabel } from "../lib/sourceMode";

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

function OverviewContent(data: OverviewData) {
  const { sourceMode, stopActive, scenario, streamConnected, connection } = useCommandCenter();
  const [focusOpen, setFocusOpen] = useState(false);
  const mission = activeMission(data.missions);
  const currentWork = data.autonomy.current_work;
  const candidateTitle = currentWork?.candidate?.title ?? null;
  const ownerGoal = data.ownerSurface?.goals[0];
  const durableGoal = text(ownerGoal?.exact_text) ?? text(ownerGoal?.objective);
  const focusTitle = candidateTitle ?? mission?.title ?? durableGoal ?? "No active focus is recorded";
  const focusWhy =
    data.executive?.latest_decision?.reason ??
    data.autonomy.executive.wait_reason ??
    mission?.objective ??
    durableGoal ??
    "JARVIS has not reported a durable reason for a current focus.";
  const nextAction =
    text(currentWork?.final_outcome?.next_action) ??
    mission?.nextAction ??
    data.autonomy.executive.wait_reason ??
    "No next action is recorded.";
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
  const attention = data.ownerAttention[0];
  const blockedMission = data.missions.find((item) => item.status === "BLOCKED" || item.status === "HALTED");
  const completed = data.missions.filter((item) => item.status === "COMPLETE");
  const recentCompletionEvents = data.timeline.filter((event) => /complet|verified|accepted/i.test(`${event.title} ${event.detail}`)).slice(0, 3);
  const latestCompletedMission = completed[0];
  const latestCompletionEvent = recentCompletionEvents[0];
  const missionEvidence = mission ? data.evidence.filter((item) => item.missionRef === mission.id) : [];
  const activeWorkers = data.workers.filter((worker) =>
    worker.status === "ACTIVE" && (!mission || worker.missionIds?.includes(mission.id)));
  const workerCount = mission?.agentsActive ?? activeWorkers.length;
  const blocker = attention?.summary ?? blockedMission?.nextAction ?? null;
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
            <span>{state.replace("_", " ")}</span>
            <span className="rm-focus-separator" />
            <span>{mission?.id ?? currentWork?.candidate?.candidate_id ?? "NO ACTIVE ID"}</span>
          </div>
          <h1 id="current-focus-heading">{focusTitle}</h1>
          <p className="rm-focus-why"><strong>Why now</strong>{focusWhy}</p>
          <div className="rm-focus-meta">
            <span><Crosshair size={15} aria-hidden="true" />{lifecycleSuspended ? `Suspended at ${phase ?? "unknown phase"}` : phase ?? "Phase unknown"}</span>
            <span><Clock3 size={15} aria-hidden="true" />{compactAge(mission?.startedAt)}</span>
            <span><Eye size={15} aria-hidden="true" />Confidence {liveConfidence == null ? "UNKNOWN" : `${Math.round(liveConfidence * 100)}%`}</span>
          </div>
          <div className="rm-focus-next">
            <span>Next</span><p>{nextAction}</p>
            <button type="button" className="rm-text-action" onClick={() => setFocusOpen(true)}>
              Focus mode <Focus size={15} aria-hidden="true" />
            </button>
          </div>
          <div className="rm-focus-glance" aria-label="At a glance">
            <div className={attention || blockedMission ? "is-attention" : ""}>
              <span>Needs you</span>
              <strong>{attention?.summary ?? blockedMission?.title ?? "No owner attention reported"}</strong>
              <small>{attention?.consequence ?? blockedMission?.nextAction ?? (data.ownerSurface ? "JARVIS can continue within current constraints." : "Owner queue unavailable from this source.")}</small>
            </div>
            <div>
              <span>Latest verified</span>
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

      <section className={`rm-attention ${attention || blockedMission ? "rm-attention--open" : ""}`} aria-labelledby="attention-title">
        <div className="rm-attention-icon">{attention || blockedMission ? <CircleAlert size={19} aria-hidden="true" /> : <CheckCircle2 size={19} aria-hidden="true" />}</div>
        <div>
          <h2 id="attention-title">{attention ? "Your attention is needed" : blockedMission ? "A blocked mission needs review" : "No owner decision is waiting"}</h2>
          <p>{attention?.summary ?? attention?.consequence ?? blockedMission?.nextAction ?? (data.ownerSurface ? "JARVIS can continue within current constraints." : "Owner decision queue is not available from this source.")}</p>
        </div>
        {attention?.mission_id ? <Link to={`/missions/${attention.mission_id}`}>Review <ChevronRight size={15} /></Link> : blockedMission ? <Link to={`/missions/${blockedMission.id}`}>Inspect <ChevronRight size={15} /></Link> : <Link to="/you">Owner context <ChevronRight size={15} /></Link>}
      </section>

      <section className="rm-today" aria-labelledby="today-title">
        <div className="rm-section-heading"><h2 id="today-title">Today</h2><span>{streamConnected ? "Updates connected" : "Updates paused"}</span></div>
        <div className="rm-today-strip">
          {today.map((item) => <div key={item.label}><span>{item.label}</span><strong>{item.value}</strong><small>{item.detail}</small></div>)}
        </div>
      </section>

      <div className="rm-home-depth">
        <section className="rm-depth-section" aria-labelledby="completed-title">
          <div className="rm-section-heading"><h2 id="completed-title">Recently completed</h2><Link to="/work?status=completed">All work <ArrowRight size={14} /></Link></div>
          <div className="rm-record-list">
            {completed.slice(0, 3).map((item) => <Link key={item.id} to={`/missions/${item.id}`}><CheckCircle2 size={16} /><span><strong>{item.title}</strong><small>{item.id} · {item.nextAction}</small></span></Link>)}
            {completed.length === 0 && recentCompletionEvents.map((event) => <Link key={event.id} to={`/activity?event=${event.id}`}><CheckCircle2 size={16} /><span><strong>{event.title}</strong><small>{event.detail}</small></span></Link>)}
            {completed.length === 0 && recentCompletionEvents.length === 0 ? <p className="rm-empty-line">No completed outcome is present in this projection.</p> : null}
          </div>
        </section>

        <section className="rm-depth-section" aria-labelledby="discovered-title">
          <div className="rm-section-heading"><h2 id="discovered-title">Discovered</h2><Link to="/world">Open world <ArrowRight size={14} /></Link></div>
          <div className="rm-record-list">
            {signals.map((signal) => <Link key={signal.signal_id} to="/world"><Lightbulb size={16} /><span><strong>{signal.summary}</strong><small>{signal.salience} · score {signal.score ?? "UNKNOWN"}</small></span></Link>)}
            {signals.length === 0 && candidates.map((candidate) => <Link key={candidate.candidate_id} to={`/executive?candidate=${candidate.candidate_id}`}><Lightbulb size={16} /><span><strong>{candidate.title}</strong><small>{candidate.state} · {candidate.project ?? "project unknown"}</small></span></Link>)}
            {signals.length === 0 && candidates.length === 0 ? <p className="rm-empty-line">No durable discovered signal is available from this source.</p> : null}
          </div>
        </section>

        <section className="rm-depth-section rm-depth-section--next" aria-labelledby="next-title">
          <div className="rm-section-heading"><h2 id="next-title">What happens next</h2><Link to="/activity">Trace activity <ArrowRight size={14} /></Link></div>
          <div className="rm-next-sequence">
            <span>01</span><p>{nextAction}</p>
            <span>02</span><p>{state === "VERIFYING" ? "Reconcile independent evidence before accepting an outcome." : "Verification remains required before any claimed outcome is trusted."}</p>
            <span>03</span><p>{data.autonomy.executive.next_wake ? `Reconsider at ${data.autonomy.executive.next_wake}.` : "The next wake boundary is not reported."}</p>
          </div>
        </section>
      </div>

      <Dialog open={focusOpen} title="Focus mode" onClose={() => setFocusOpen(false)} variant="focus">
        <div className={`rm-focus-mode rm-focus-mode--${state.toLowerCase()}`}>
          <section className="rm-focus-mode-hero" aria-labelledby="focus-mode-objective">
            <div className="rm-focus-mode-copy">
              <div className="rm-focus-mode-state">
                <span className={`rm-state-mark rm-state-mark--${state.toLowerCase()}`} />
                <strong>{state.replace("_", " ")}</strong>
                <span>{mission?.id ?? currentWork?.candidate?.candidate_id ?? "NO ACTIVE ID"}</span>
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
              <strong>{mission?.title ?? "No active mission recorded"}</strong>
              <p>
                {activeWorkers.length > 0
                  ? activeWorkers.map((worker) => `${worker.name} · ${worker.role}`).join(" / ")
                  : workerCount > 0
                    ? `${workerCount} active worker${workerCount === 1 ? "" : "s"} reported; identities are not linked in this projection.`
                    : "No active worker is linked to this focus."}
              </p>
              {mission ? <Link to={`/missions/${mission.id}`}>Open mission <ArrowRight size={14} /></Link> : null}
            </div>
            <div>
              <span className="rm-focus-mode-label">Verification</span>
              <strong>{missionEvidence[0]?.verdict ?? "NO LINKED RECORD"}</strong>
              <p>{missionEvidence[0] ? `${missionEvidence.length} evidence artifact${missionEvidence.length === 1 ? "" : "s"} linked to ${mission?.id}.` : "No mission-linked verification artifact is present in this projection."}</p>
              {missionEvidence[0] ? <Link to={`/evidence/${missionEvidence[0].id}`}>Inspect evidence <ArrowRight size={14} /></Link> : <Link to="/evidence">Open evidence ledger <ArrowRight size={14} /></Link>}
            </div>
            <div className={blocker ? "is-blocked" : ""}>
              <span className="rm-focus-mode-label">Blocker / owner boundary</span>
              <strong>{blocker ?? "No blocker recorded"}</strong>
              <p>{attention?.consequence ?? (blockedMission ? "Progress remains held until the recorded dependency is resolved." : "JARVIS can continue within current constraints.")}</p>
              <Link to={attention?.mission_id ? `/missions/${attention.mission_id}` : blockedMission ? `/missions/${blockedMission.id}` : "/you"}>
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
