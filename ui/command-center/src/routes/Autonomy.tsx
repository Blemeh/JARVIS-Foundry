import { useState } from "react";
import { Link } from "react-router-dom";
import {
  Activity, ArrowRight, BrainCircuit, Clock3, DatabaseZap,
  ChevronDown, FileSearch2, GitBranch, ShieldCheck, TimerReset,
} from "lucide-react";
import { useCommandCenter } from "../context/CommandCenterContext";
import { RouteQueryGate } from "../components/RouteQueryGate";
import { SectionHeader } from "../components/SectionHeader";
import { StatusBadge } from "../components/StatusBadge";
import { useRouteQuery } from "../hooks/useRouteQuery";
import type {
  AutonomyCandidate, AutonomyCandidateFilter, AutonomyFactor,
  AutonomyMetric, AutonomyProjection, AutonomyWorkField,
} from "../lib/contracts";

const FILTERS: AutonomyCandidateFilter[] = ["eligible", "rejected", "researched", "stale", "all"];
const FUNNEL: Array<[string, string]> = [
  ["observed", "Observed"], ["generated", "Generated"], ["deduped", "Deduped"],
  ["researched", "Researched"], ["rejected", "Rejected"], ["eligible", "Eligible"],
  ["executing", "Executing"], ["verified_outcome", "Outcome"],
];
const JUDGMENT_GROUPS: Array<[string, string]> = [
  ["owner_relevance", "Owner relevance"], ["expected_value", "Expected value"],
  ["confidence", "Confidence"], ["effort", "Effort"],
  ["capability_readiness", "Capability readiness"], ["opportunity_cost", "Opportunity cost"],
  ["duplicate_rejection_history", "Duplicate / rejection history"],
];

function instant(value: string | null | undefined): string {
  if (!value) return "Unavailable";
  const parsed = new Date(value);
  return Number.isNaN(parsed.getTime()) ? value : parsed.toLocaleString();
}

function duration(seconds: number | null): string {
  if (seconds === null) return "Unavailable";
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}m`;
  if (seconds < 86_400) return `${Math.floor(seconds / 3600)}h`;
  return `${Math.floor(seconds / 86_400)}d`;
}

function numeric(value: number | null, digits = 2): string {
  return value === null ? "—" : value.toFixed(digits);
}

function metricValue(metric: AutonomyMetric | undefined): string {
  return metric?.status === "AVAILABLE" && metric.value !== null ? String(metric.value) : (metric?.status ?? "UNAVAILABLE");
}

function fieldIdentity(field: AutonomyWorkField, keys: string[]): string {
  for (const key of keys) {
    const value = field[key];
    if (typeof value === "string" && value.trim()) return value;
  }
  return field.status === "PENDING" ? "Pending bridge" : "Unavailable";
}

function FactorRows({ factors }: { factors: AutonomyFactor[] }) {
  if (!factors.length) return <p className="autonomy-muted">No stored factors in this group.</p>;
  return (
    <dl className="judgment-factors">
      {factors.map((factor) => (
        <div key={factor.factor}>
          <dt>{factor.factor.replaceAll("_", " ")}</dt>
          <dd>
            <span>{factor.value === null ? "—" : String(factor.value)}</span>
            <span>{factor.contribution === null ? "no contribution" : `${factor.contribution >= 0 ? "+" : ""}${factor.contribution}`}</span>
            <span>{factor.kind}</span>
          </dd>
        </div>
      ))}
    </dl>
  );
}

function CandidateCard({ candidate }: { candidate: AutonomyCandidate }) {
  const source = candidate.sources[0];
  return (
    <details className="candidate-record" open={candidate.state === "ELIGIBLE"}>
      <summary>
        <span className="candidate-rank"><ChevronDown size={13} aria-hidden />#{candidate.rank ?? "—"}</span>
        <span className="candidate-title">
          <strong>{candidate.title}</strong>
          <small>{candidate.project ?? source?.identity ?? "Project unavailable"}</small>
        </span>
        <span className="candidate-score">
          <strong>{numeric(candidate.score, 3)}</strong>
          <small>score</small>
        </span>
        <span className="candidate-state">
          <StatusBadge status={candidate.state} />
          {candidate.stale && <StatusBadge status="STALE" />}
        </span>
      </summary>
      <div className="candidate-detail">
        <dl className="candidate-facts">
          <div><dt>Expected outcome</dt><dd>{candidate.expected_outcome ?? "Unavailable"}</dd></div>
          <div className="candidate-provenance"><dt>Provenance</dt><dd>
            {candidate.sources.length === 0 ? "Unavailable" : (
              <ul>{candidate.sources.map((item, index) => (
                <li key={`${item.kind}:${item.identity}:${item.ref}:${index}`}>
                  <span>{item.kind ?? "UNKNOWN"} · {item.ref ?? item.identity ?? "no reference"}</span>
                  {item.event_id && <Link to={`/timeline?event=${encodeURIComponent(item.event_id)}`}>source event</Link>}
                </li>
              ))}</ul>
            )}
          </dd></div>
          <div><dt>Confidence</dt><dd>{numeric(candidate.confidence, 3)}</dd></div>
          <div><dt>Score / rank</dt><dd>{numeric(candidate.score, 3)} / #{candidate.rank ?? "—"}</dd></div>
          <div><dt>Research</dt><dd>{candidate.research.status}{candidate.research.source_ref ? ` · ${candidate.research.source_ref}` : ""}</dd></div>
          <div><dt>Age / TTL</dt><dd>{duration(candidate.age_seconds)} / {candidate.stale ? "STALE" : duration(candidate.ttl_seconds)}</dd></div>
          <div><dt>Policy</dt><dd>{candidate.judgment.policy_version ?? "Unavailable"}</dd></div>
          <div className="durable-ids"><dt>Durable IDs</dt><dd>
            <code>{candidate.candidate_id}</code>
            {candidate.judgment.event_id && <Link to={`/timeline?event=${encodeURIComponent(candidate.judgment.event_id)}`}>judgment event</Link>}
            {candidate.research.event_id && <Link to={`/timeline?event=${encodeURIComponent(candidate.research.event_id)}`}>research event</Link>}
          </dd></div>
        </dl>
        {(candidate.rejection || candidate.judgment.reason) && (
          <div className={`judgment-reason ${candidate.state === "REJECTED" ? "is-rejected" : ""}`}>
            <span>{candidate.rejection?.reason_code ?? candidate.judgment.reason_code ?? candidate.state}</span>
            <p>{candidate.rejection?.reason ?? candidate.judgment.reason ?? "No stored reason detail."}</p>
          </div>
        )}
        <div className="judgment-grid" aria-label={`Stored judgment evidence for ${candidate.title}`}>
          {JUDGMENT_GROUPS.map(([key, label]) => (
            <section key={key}>
              <h4>{label}</h4>
              <FactorRows factors={(candidate.judgment.evidence[key] as AutonomyFactor[] | undefined) ?? []} />
            </section>
          ))}
        </div>
      </div>
    </details>
  );
}

function CurrentWork({ projection }: { projection: AutonomyProjection }) {
  const work = projection.current_work;
  const stages = work ? [
    ["Candidate", work.candidate?.title ?? "Unavailable", work.candidate?.state ?? "UNAVAILABLE"],
    ["Objective", fieldIdentity(work.objective, ["objective_id"]), work.objective.status],
    ["Mission", fieldIdentity(work.mission, ["objective", "mission_id"]), work.mission.status],
    ["Worktree", fieldIdentity(work.worktree, ["path", "reason"]), work.worktree.status],
    ["Current step", fieldIdentity(work.current_step, ["capability", "step_id"]), work.current_step.status],
    ["Verifier", fieldIdentity(work.verifier, ["verdict_id", "run_id"]), work.verifier.status],
    ["Final outcome", work.final_outcome.state, work.final_outcome.state],
  ] : [];
  return (
    <section className="autonomy-section current-work" aria-labelledby="current-work-title">
      <header><div><span className="section-kicker">A3 bridge</span><h2 id="current-work-title">Current autonomous work</h2></div><StatusBadge status={work ? work.final_outcome.state : "UNAVAILABLE"} /></header>
      {!work ? <p className="honest-empty">No eligible candidate or durable A3 mission bridge is present.</p> : (
        <ol className="work-chain">
          {stages.map(([label, value, status], index) => (
            <li key={label}>
              <span className="work-index">{String(index + 1).padStart(2, "0")}</span>
              <div><small>{label}</small><strong>{value}</strong></div>
              <StatusBadge status={status} />
              {index < stages.length - 1 && <ArrowRight size={14} aria-hidden />}
            </li>
          ))}
        </ol>
      )}
      {work && (
        <div className="outcome-band">
          <div><small>Verification</small><strong>{work.final_outcome.verification.status}</strong><span>{work.final_outcome.verification.acceptance}</span></div>
          <div><small>Repair attempts</small><strong>{work.final_outcome.repair_attempts ?? "Unavailable"}</strong></div>
          <div><small>Learned / calibration</small><strong>{work.final_outcome.learned_calibration ? "RECORDED" : "UNAVAILABLE"}</strong></div>
          <div><small>Next action</small><strong>{work.final_outcome.next_action ?? "Unavailable"}</strong></div>
        </div>
      )}
      {work?.final_outcome.state === "PRELIMINARY_PASS" && (
        <p className="preliminary-note"><ShieldCheck size={14} aria-hidden /> Verification passed, but no durable AcceptanceGate receipt exists. This is not final success.</p>
      )}
    </section>
  );
}

export function AutonomyView({
  projection, filter, onFilter,
}: { projection: AutonomyProjection; filter: AutonomyCandidateFilter; onFilter: (filter: AutonomyCandidateFilter) => void }) {
  const unavailable = projection.status === "UNAVAILABLE";
  return (
    <div className="shell-content autonomy-page">
      <SectionHeader
        kicker="Autonomy control"
        title="Autonomous Executive"
        description="A bounded read-only evidence chain: what JARVIS observed, judged, selected, verified, and learned."
        cinematic={false}
      />

      <section className="autonomy-command" aria-label="Autonomy overview">
        <div className="autonomy-state">
          <span className="section-kicker">Executive state</span>
          <div className="state-word"><Activity size={19} aria-hidden /><strong>{projection.executive.state}</strong></div>
          <p>{projection.executive.wait_boundary ?? projection.executive.wait_reason ?? (unavailable ? projection.reason : "No durable WAIT reason recorded.")}</p>
          {projection.executive.wait_reason_code && <span className="wait-code">{projection.executive.wait_reason_code}</span>}
          {projection.executive.wait_boundary && projection.executive.wait_reason && <small className="cycle-decision">Cycle decision: {projection.executive.wait_reason}</small>}
          <small className="owner-action">Owner action: {projection.executive.owner_action?.status ?? "UNAVAILABLE"} — no durable owner requirement is recorded.</small>
        </div>
        <dl className="autonomy-clock">
          <div><dt><Clock3 size={13} aria-hidden /> Last observation cycle</dt><dd>{instant(projection.executive.last_observation_cycle)}</dd></div>
          <div><dt><TimerReset size={13} aria-hidden /> Next wake</dt><dd>{instant(projection.executive.next_wake)}</dd></div>
          <div><dt><GitBranch size={13} aria-hidden /> Active autonomous WIP</dt><dd>{metricValue(projection.executive.active_wip)}</dd></div>
        </dl>
      </section>

      <section className="funnel-ribbon" aria-label="Candidate funnel">
        {FUNNEL.map(([key, label], index) => (
          <div key={key} className={projection.funnel[key]?.status !== "AVAILABLE" ? "is-unavailable" : ""}>
            <span>{label}</span><strong>{metricValue(projection.funnel[key])}</strong>
            {index < FUNNEL.length - 1 && <ArrowRight size={13} aria-hidden />}
          </div>
        ))}
      </section>

      <div className="autonomy-main-grid">
        <section className="autonomy-section candidate-feed" aria-labelledby="candidate-feed-title">
          <header>
            <div><span className="section-kicker">Possibilities → judgment</span><h2 id="candidate-feed-title">Candidate feed</h2></div>
            <FileSearch2 size={17} aria-hidden />
          </header>
          <div className="candidate-filters" role="group" aria-label="Filter candidates">
            {FILTERS.map((value) => <button key={value} type="button" className={filter === value ? "is-active" : ""} aria-pressed={filter === value} onClick={() => onFilter(value)}>{value}</button>)}
          </div>
          {projection.candidates.length === 0 ? <p className="honest-empty">No candidates match this durable projection.</p> : projection.candidates.map((candidate) => <CandidateCard key={candidate.candidate_id} candidate={candidate} />)}
        </section>

        <section className="autonomy-section timeline-stream" aria-labelledby="autonomy-timeline-title">
          <header><div><span className="section-kicker">Actual stages only</span><h2 id="autonomy-timeline-title">Autonomous activity</h2></div><BrainCircuit size={17} aria-hidden /></header>
          {projection.timeline.length === 0 ? <p className="honest-empty">No material autonomy events are recorded.</p> : (
            <ol>
              {projection.timeline.slice().reverse().map((event) => (
                <li key={event.event_id}>
                  <span className="timeline-node" aria-hidden />
                  <div><span>{event.stage}</span><time>{instant(event.occurred_at)}</time></div>
                  <p>{event.summary}</p>
                  <small>
                    <Link to={`/timeline?event=${encodeURIComponent(event.event_id)}`}>{event.event_id}</Link>
                    {event.candidate_id ? ` · ${event.candidate_id}` : event.mission_id ? ` · ${event.mission_id}` : ""}
                  </small>
                </li>
              ))}
            </ol>
          )}
        </section>
      </div>

      <CurrentWork projection={projection} />
      <section className="source-strip" aria-labelledby="source-health-title">
        <header><div><span className="section-kicker">Observation fabric</span><h2 id="source-health-title">Source health</h2></div><span>{projection.sources.length} durable source records</span></header>
        {projection.sources.length === 0 ? <p className="honest-empty">No durable source checks are recorded.</p> : (
          <div className="source-grid">
            {projection.sources.map((source) => (
              <article key={`${source.source_kind}:${source.source_identity}`}>
                <div><DatabaseZap size={14} aria-hidden /><strong>{source.source_kind}</strong><StatusBadge status={source.readiness} /></div>
                <p>{source.source_identity}</p>
                <dl>
                  <div><dt>Checked</dt><dd>{instant(source.last_checked)}</dd></div>
                  <div><dt>Changed</dt><dd>{instant(source.last_meaningful_change)}</dd></div>
                  <div><dt>Next due</dt><dd>{instant(source.next_due)}</dd></div>
                </dl>
                {source.last_error && <p className="source-error">{source.last_error}</p>}
                {source.event_id && <Link className="source-event-link" to={`/timeline?event=${encodeURIComponent(source.event_id)}`}>event {source.event_id}</Link>}
              </article>
            ))}
          </div>
        )}
      </section>
      <footer className="projection-footnote">
        <span>Projection {projection.schema}</span><span>Generated {instant(projection.generated_at)}</span><span>Candidate cap {projection.limits.candidate_limit ?? "—"}</span><span>Timeline cap {projection.limits.timeline_limit ?? "—"}</span>
      </footer>
    </div>
  );
}

export function Autonomy() {
  const { client, version } = useCommandCenter();
  const [filter, setFilter] = useState<AutonomyCandidateFilter>("all");
  const query = useRouteQuery(() => client.getAutonomy(filter), [client, version, filter], { keepPreviousWhileLoading: true });
  return (
    <RouteQueryGate query={query} loadingLabel="Loading autonomous evidence" errorLabel="Autonomy projection unavailable" surface="Autonomous Executive Command Center" shellClass="autonomy-page">
      {(projection) => <AutonomyView projection={projection} filter={filter} onFilter={setFilter} />}
    </RouteQueryGate>
  );
}
