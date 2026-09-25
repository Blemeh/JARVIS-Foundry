import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import {
  Activity, ArrowRight, BrainCircuit, Clock3, DatabaseZap,
  ChevronDown, FileSearch2, GitBranch, ShieldCheck, TimerReset,
  BookOpenCheck, CircleAlert, Layers3, Lightbulb, ListChecks,
} from "lucide-react";
import { useCommandCenter } from "../context/CommandCenterContext";
import { useLocale } from "../i18n/LocaleContext";
import { RouteQueryGate } from "../components/RouteQueryGate";
import { SectionHeader } from "../components/SectionHeader";
import { StatusBadge } from "../components/StatusBadge";
import { Drawer } from "../components/Drawer";
import { useRouteQuery } from "../hooks/useRouteQuery";
import type {
  AutonomyCandidate, AutonomyCandidateFilter, AutonomyFactor,
  AutonomyContractProjection, AutonomyMetric, AutonomyOwnerAction,
  AutonomyProjection, AutonomyWorkField,
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

function textValue(value: unknown, fallback = "Unavailable"): string {
  if (value === null || value === undefined || value === "") return fallback;
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return fallback;
}

function listValue(value: unknown): unknown[] {
  return Array.isArray(value) ? value : [];
}

function contractState(contract: AutonomyContractProjection | undefined): string {
  return contract?.status ?? "UNAVAILABLE";
}

function contractReason(contract: AutonomyContractProjection | undefined): string {
  return textValue(contract?.reason, "No durable record is available for this contract.");
}

function ContractList({ value, empty = "Unavailable" }: { value: unknown; empty?: string }) {
  const items = listValue(value);
  if (!items.length) return <p className="autonomy-muted">{empty}</p>;
  return (
    <ul className="contract-list">
      {items.map((item, index) => (
        <li key={`${String(item)}:${index}`}>{typeof item === "object" ? textValue((item as Record<string, unknown>).statement ?? (item as Record<string, unknown>).name ?? (item as Record<string, unknown>).id) : textValue(item)}</li>
      ))}
    </ul>
  );
}

function ContractPanel({
  icon, title, contract, children,
}: { icon: ReactNode; title: string; contract?: AutonomyContractProjection; children: ReactNode }) {
  return (
    <section className="drawer-contract">
      <header><div className="drawer-contract-title">{icon}<h3>{title}</h3></div><StatusBadge status={contractState(contract)} /></header>
      {contract?.contract ? <small className="contract-id">{contract.contract}</small> : null}
      {children}
      {contract?.status === "UNAVAILABLE" ? <p className="contract-reason">{contractReason(contract)}</p> : null}
    </section>
  );
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

function CandidateCard({ candidate, onOpen }: { candidate: AutonomyCandidate; onOpen: () => void }) {
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
        <button type="button" className="candidate-inspect" aria-label={`Open candidate detail — inspect complete candidate brief for ${candidate.title}`} onClick={onOpen}>
          Inspect complete candidate brief <ArrowRight size={13} aria-hidden />
        </button>
      </div>
    </details>
  );
}

function CandidateDetailDrawer({
  candidate, open, onClose,
}: { candidate: AutonomyCandidate | null; open: boolean; onClose: () => void }) {
  if (!candidate) return null;
  const dossier = candidate.research.dossier;
  const capabilities = candidate.capability_readiness;
  const context = candidate.project_context;
  const plan = candidate.verification_plan ?? candidate.current_work.verification_plan;
  const outcome = candidate.current_work.final_outcome;
  const verification = outcome.verification;
  return (
    <Drawer open={open} title="Candidate evidence brief" onClose={onClose}>
      <div className="drawer-candidate-heading">
        <div>
          <span className="section-kicker">{candidate.candidate_id}</span>
          <h2>{candidate.title}</h2>
          <p>{candidate.expected_outcome ?? "Expected outcome unavailable."}</p>
        </div>
        <StatusBadge status={candidate.state} />
      </div>

      <section className="drawer-summary-grid" aria-label="Candidate summary">
        <div><small>Rank</small><strong>#{candidate.rank ?? "—"}</strong></div>
        <div><small>Score</small><strong>{numeric(candidate.score, 3)}</strong></div>
        <div><small>Confidence</small><strong>{numeric(candidate.confidence, 3)}</strong></div>
        <div><small>Age / TTL</small><strong>{duration(candidate.age_seconds)} / {candidate.stale ? "STALE" : duration(candidate.ttl_seconds)}</strong></div>
      </section>

      <section className="drawer-contract drawer-observation">
        <header><div className="drawer-contract-title"><DatabaseZap size={14} aria-hidden /><h3>Observed sources</h3></div><StatusBadge status={candidate.sources.length ? "AVAILABLE" : "UNAVAILABLE"} /></header>
        {candidate.sources.length ? <ul className="contract-list">
          {candidate.sources.map((source, index) => <li key={`${source.ref}:${index}`}>
            <span>{textValue(source.kind, "UNKNOWN")} · {textValue(source.ref ?? source.identity)}</span>
            {source.event_id ? <Link to={`/timeline?event=${encodeURIComponent(source.event_id)}`}>source event</Link> : null}
          </li>)}
        </ul> : <p className="contract-reason">No durable source link is available.</p>}
      </section>

      <ContractPanel icon={<BookOpenCheck size={14} aria-hidden />} title="Research dossier" contract={dossier}>
        <p className="contract-summary">{textValue(dossier?.human_summary, "No durable research summary is recorded.")}</p>
        <div className="evidence-columns">
          <div><small>Supporting evidence</small><ContractList value={dossier?.supporting_evidence} /></div>
          <div><small>Contradicting evidence</small><ContractList value={dossier?.contradicting_evidence} empty="No stored contradictions." /></div>
        </div>
        <div className="contract-subsection"><small>Contradiction records</small><ContractList value={dossier?.contradictions} empty="No contradiction record is available." /></div>
        <div className="contract-subsection"><small>Unresolved unknowns</small><ContractList value={dossier?.unresolved_unknowns} empty="No unresolved unknowns are recorded." /></div>
        {dossier?.final_research_conclusion ? <p className="contract-note">Conclusion: {textValue((dossier.final_research_conclusion as Record<string, unknown>).summary ?? (dossier.final_research_conclusion as Record<string, unknown>).verdict)}</p> : null}
      </ContractPanel>

      <ContractPanel icon={<Layers3 size={14} aria-hidden />} title="Capability readiness" contract={capabilities}>
        <div className="evidence-columns">
          <div><small>Required capabilities</small><ContractList value={capabilities?.required_capability_ids} /></div>
          <div><small>Missing / blocked</small><ContractList value={[...listValue(capabilities?.missing_capabilities), ...listValue(capabilities?.blocked_capabilities)]} empty="No durable missing capability list." /></div>
        </div>
        <p className="contract-note">Decision: {textValue(capabilities?.decision)} · Executable: {textValue(capabilities?.executable)}</p>
      </ContractPanel>

      <ContractPanel icon={<GitBranch size={14} aria-hidden />} title="Project profile / context" contract={context}>
        <p className="contract-summary">{textValue((context?.context as Record<string, unknown> | undefined)?.purpose ?? (context?.profile as Record<string, unknown> | undefined)?.name, "No durable project context summary is recorded.")}</p>
        <div className="evidence-columns">
          <div><small>Stack</small><p className="contract-value">{textValue(JSON.stringify(context?.stack), "Unavailable")}</p></div>
          <div><small>Repository state</small><p className="contract-value">{textValue(JSON.stringify(context?.repository_state), "Unavailable")}</p></div>
        </div>
        <div className="contract-subsection"><small>Context gaps / constraints</small><ContractList value={[...listValue(context?.context_gaps), ...listValue(context?.known_constraints)]} empty="No durable context gaps are recorded." /></div>
      </ContractPanel>

      <ContractPanel icon={<ListChecks size={14} aria-hidden />} title="Verification plan" contract={plan}>
        <p className="contract-summary">{textValue(plan?.objective, "Verification objective unavailable.")}</p>
        <div className="evidence-columns">
          <div><small>Acceptance criteria</small><ContractList value={plan?.acceptance_criteria ?? plan?.checks_to_run} /></div>
          <div><small>Expected artifacts</small><ContractList value={plan?.expected_artifacts} /></div>
        </div>
        <div className="contract-subsection"><small>Forbidden paths / side effects</small><ContractList value={[...listValue(plan?.forbidden_paths), ...listValue(plan?.forbidden_side_effects)]} empty="No durable forbidden-effect scope is recorded." /></div>
      </ContractPanel>

      <ContractPanel icon={<CircleAlert size={14} aria-hidden />} title="Outcome and learning" contract={{ status: outcome.state, contract: "verification.accepted / candidate outcome" }}>
        <div className="outcome-detail-grid">
          <div><small>Verification</small><strong>{verification.status}</strong><span>{verification.acceptance}</span></div>
          <div><small>Acceptance receipt</small><strong>{textValue(verification.acceptance_event_id)}</strong></div>
          <div><small>Repair attempts</small><strong>{textValue(outcome.repair_attempts)}</strong></div>
          <div><small>Next action</small><strong>{textValue(outcome.next_action)}</strong></div>
          <div><small>Measured actual</small><strong>{textValue(outcome.measurement_status)}</strong><span>{textValue(JSON.stringify(outcome.actual), "No durable actual value")}</span></div>
        </div>
        <p className="contract-note"><Lightbulb size={13} aria-hidden /> Learning: {outcome.learned_calibration ? "durable calibration recorded" : "no durable calibration record"}</p>
        {outcome.state === "PRELIMINARY_PASS" ? <p className="preliminary-note"><ShieldCheck size={14} aria-hidden /> PASS is preliminary until the durable AcceptanceGate receipt is present.</p> : null}
      </ContractPanel>
    </Drawer>
  );
}

function OverviewPanel({ projection }: { projection: AutonomyProjection }) {
  const rejected = projection.candidates.find((item) => item.state === "REJECTED");
  const chosen = projection.overview?.chosen as Record<string, unknown> | undefined;
  const active = projection.current_work;
  const learning = projection.learning;
  const wait = projection.wait;
  const rows: Array<[string, string, string]> = [
    ["Observed", metricValue(projection.funnel.observed), "Continuous source digest"],
    ["Opportunities", metricValue(projection.funnel.generated), "Candidate funnel"],
    ["Rejected + why", metricValue(projection.funnel.rejected), `Reason on first rejection: ${textValue(rejected?.rejection?.reason, "No durable rejection reason recorded.")}`],
    ["Researched", metricValue(projection.funnel.researched), "Evidence and contradiction review"],
    ["Chosen", textValue(chosen?.title ?? active?.candidate?.title), "Selection remains subordinate to durable judgment"],
    ["Doing", textValue(active?.mission.objective, projection.executive.state), textValue(active?.current_step.capability, "No active build step recorded.")],
    ["Verification", textValue(active?.final_outcome.state, textValue((projection.overview?.verification as Record<string, unknown> | undefined)?.status)), textValue(active?.final_outcome.verification.acceptance, "Acceptance unavailable")],
    ["Learned", textValue(learning?.status), textValue((learning?.latest as Record<string, unknown> | undefined)?.summary, "No durable calibration summary recorded.")],
    ["Waiting / continuing", textValue(wait?.status, projection.executive.state), `Durable wake reason: ${textValue(wait?.continuation, projection.executive.wait_reason ?? "No durable wake reason recorded.")}`],
  ];
  return (
    <section className="autonomy-section overview-panel" aria-labelledby="overview-title">
      <header><div><span className="section-kicker">Owner brief</span><h2 id="overview-title">Executive overview</h2></div><StatusBadge status={projection.overview?.status ?? projection.status} /></header>
      <div className="overview-grid">
        {rows.map(([label, value, detail]) => <article key={label}><small>{label}</small><strong>{value}</strong><p>{detail}</p></article>)}
      </div>
    </section>
  );
}

function SourceDigestPanel({ projection }: { projection: AutonomyProjection }) {
  const digest = projection.source_digest;
  const changes = listValue(digest?.meaningful_changes);
  const signals = listValue(digest?.candidate_worthy_signals);
  return (
    <section className="autonomy-section source-digest" aria-labelledby="source-digest-title">
      <header><div><span className="section-kicker">Meaningful change intelligence</span><h2 id="source-digest-title">Source digest</h2></div><StatusBadge status={contractState(digest)} /></header>
      {digest?.status === "UNAVAILABLE" || !digest ? <p className="honest-empty">{contractReason(digest)}</p> : <>
        <div className="digest-metrics">
          <div><small>Sources checked</small><strong>{listValue(digest.sources_checked).length || projection.sources.length}</strong></div>
          <div><small>Meaningful changes</small><strong>{changes.length}</strong></div>
          <div><small>Candidate-worthy signals</small><strong>{signals.length}</strong></div>
          <div><small>Noise suppressed</small><strong>{textValue(digest.noise_suppressed)}</strong></div>
        </div>
        {changes.length ? <ul className="change-list">
          {changes.map((change, index) => {
            const item = (typeof change === "object" && change !== null) ? change as Record<string, unknown> : {};
            const eventId = textValue(item.event_id, "");
            return <li key={`${eventId}:${index}`}><div><strong>{textValue(item.change_type, "MEANINGFUL CHANGE")}</strong><span>{textValue(item.source_identity ?? item.source_ref)}</span></div><p>{textValue(item.statement ?? item.summary ?? item.changed_fields, "Durable source change recorded.")}</p>{eventId ? <Link to={`/timeline?event=${encodeURIComponent(eventId)}`}>event {eventId}</Link> : null}</li>;
          })}
        </ul> : <p className="honest-empty">No meaningful source changes are recorded for this cycle.</p>}
      </>}
    </section>
  );
}

function LearningWaitPanels({ projection }: { projection: AutonomyProjection }) {
  const learning = projection.learning;
  const wait = projection.wait;
  return (
    <div className="learning-wait-grid">
      <section className="autonomy-section learning-panel" aria-labelledby="learning-title">
        <header><div><span className="section-kicker">Calibration / outcomes</span><h2 id="learning-title">What JARVIS learned</h2></div><StatusBadge status={contractState(learning)} /></header>
        {learning?.status === "UNAVAILABLE" || !learning ? <p className="honest-empty">{contractReason(learning)}</p> : <>
          <p className="contract-summary">{textValue((learning.latest as Record<string, unknown> | undefined)?.summary ?? (learning.latest as Record<string, unknown> | undefined)?.reason, "Durable learning is recorded; no narrative summary was supplied.")}</p>
          <div className="learning-meta"><span>{listValue(learning.history).length} durable learning records</span><span>{textValue((learning.latest as Record<string, unknown> | undefined)?.event_id)}</span></div>
        </>}
      </section>
      <section className="autonomy-section wait-panel" aria-labelledby="wait-title">
        <header><div><span className="section-kicker">Control boundary</span><h2 id="wait-title">WAIT / next wake</h2></div><StatusBadge status={textValue(wait?.status, "UNAVAILABLE")} /></header>
        <dl className="wait-facts">
          <div><dt>Reason</dt><dd>Recorded: {textValue(wait?.reason ?? projection.executive.wait_reason)}</dd></div>
          <div><dt>Boundary</dt><dd>Recorded: {textValue(wait?.boundary ?? projection.executive.wait_boundary)}</dd></div>
          <div><dt>Next wake</dt><dd>{instant(typeof wait?.next_wake === "string" ? wait.next_wake : projection.executive.next_wake)}</dd></div>
          <div><dt>Continuation</dt><dd>{textValue(wait?.continuation, "No durable continuation instruction is recorded.")}</dd></div>
        </dl>
      </section>
    </div>
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
      {work && (
        <div className="work-detail-grid" aria-label="Durable execution context">
          <div><small>Mission / objective</small><strong>{textValue(work.mission.mission_id)} · {textValue(work.objective.objective_id)}</strong><span>{textValue(work.mission.objective)}</span></div>
          <div><small>Worktree</small><strong>{textValue(work.worktree.path)}</strong><span>{textValue(work.worktree.resource_id)}</span></div>
          <div><small>Build step / worker</small><strong>{textValue(work.current_step.step_id)} · {textValue(work.worker.run_id)}</strong><span>{textValue(work.worker.provider)} / {textValue(work.worker.model)}</span></div>
          <div><small>Verifier evidence</small><strong>{textValue(work.verifier.verdict_id)}</strong><span>{textValue(work.verifier.reason, "No durable verifier reason recorded.")}</span></div>
          <div><small>Verification plan</small><strong>{contractState(work.verification_plan)}</strong><span>{textValue(work.verification_plan?.contract)}</span></div>
          <div><small>Acceptance receipt</small><strong>{textValue(work.final_outcome.verification.acceptance_event_id)}</strong><span>{textValue(work.final_outcome.verification.reason, "No durable acceptance reason recorded.")}</span></div>
        </div>
      )}
      {work?.final_outcome.state === "PRELIMINARY_PASS" && (
        <p className="preliminary-note"><ShieldCheck size={14} aria-hidden /> Verification passed, but no durable AcceptanceGate receipt exists. This is not final success.</p>
      )}
    </section>
  );
}

function OwnerActions({ projection }: { projection: AutonomyProjection }) {
  const ownerAction = projection.executive.owner_action;
  const items = ownerAction?.items ?? [];
  const badgeStatus = ownerAction?.status === "PARTIAL"
    ? "PARTIAL"
    : ownerAction?.status === "AVAILABLE"
      ? items.length > 0 ? "BLOCKED" : "AVAILABLE"
      : "UNAVAILABLE";
  const emptyMessage = ownerAction?.status === "PARTIAL"
    ? ownerAction.scan_truncated
      ? "No open escalation appears in the latest scan; older records may remain open."
      : "Owner actions are only partially available in this projection."
    : ownerAction?.status === "AVAILABLE"
      ? "No open Executive escalation needs owner direction."
      : "Owner action state is unavailable in this projection.";

  return (
    <section className="autonomy-section owner-actions" aria-labelledby="owner-actions-title">
      <header>
        <div>
          <span className="section-kicker">Executive exception channel</span>
          <h2 id="owner-actions-title">Owner direction</h2>
        </div>
        <StatusBadge status={badgeStatus} />
      </header>
      {items.length === 0 ? <p className="honest-empty">{emptyMessage}</p> : (
        <ol className="owner-action-list">
          {items.map((item: AutonomyOwnerAction) => (
            <li key={item.escalation_id}>
              <div className="owner-action-identity">
                <small>Objective {item.objective_id ?? "unavailable"}</small>
                <strong>{item.decision}</strong>
                <small>{item.escalation_id}</small>
              </div>
              <div className="owner-action-details">
                {item.why && <p>{item.why}</p>}
                {item.recommendation && (
                  <div className="owner-action-guidance">
                    <small>Executive recommendation</small>
                    <p>{item.recommendation}</p>
                  </div>
                )}
                {item.options.length > 0 && (
                  <div className="owner-action-options">
                    <small>Available paths</small>
                    <ul>{item.options.map((option) => <li key={option}>{option}</li>)}</ul>
                  </div>
                )}
                {item.delay_consequence && (
                  <p className="owner-action-consequence">
                    If deferred: {item.delay_consequence}
                  </p>
                )}
                {item.event_id && (
                  <Link className="owner-action-link" to={`/timeline?event=${encodeURIComponent(item.event_id)}`}>
                    View escalation record
                  </Link>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}
      {ownerAction?.items_truncated && (
        <p className="owner-action-truncation">
          Showing the latest {ownerAction.count} of {ownerAction.open_count} open escalations in the scanned records.
        </p>
      )}
      {ownerAction?.scan_truncated && (
        <p className="owner-action-truncation">
          Scanned the latest {ownerAction.scan_limit} escalation events. Older open escalations may also need attention.
        </p>
      )}
    </section>
  );
}

export function AutonomyView({
  projection, filter, onFilter,
}: { projection: AutonomyProjection; filter: AutonomyCandidateFilter; onFilter: (filter: AutonomyCandidateFilter) => void }) {
  const { t } = useLocale();
  const unavailable = projection.status === "UNAVAILABLE";
  const [selectedCandidate, setSelectedCandidate] = useState<AutonomyCandidate | null>(null);
  return (
    <div className="shell-content autonomy-page">
      <SectionHeader
        kicker="Autonomy control"
        title={t("page.autonomy")}
        description="A bounded read-only evidence chain: what JARVIS observed, judged, selected, verified, and learned."
        cinematic={false}
      />

      <OverviewPanel projection={projection} />

      <section className="autonomy-command" aria-label="Autonomy overview">
        <div className="autonomy-state">
          <span className="section-kicker">Executive state</span>
          <div className="state-word"><Activity size={19} aria-hidden /><strong>{projection.executive.state}</strong></div>
          <p>{projection.executive.wait_boundary ?? projection.executive.wait_reason ?? (unavailable ? projection.reason : "No durable WAIT reason recorded.")}</p>
          {projection.executive.wait_reason_code && <span className="wait-code">{projection.executive.wait_reason_code}</span>}
          {projection.executive.wait_boundary && projection.executive.wait_reason && <small className="cycle-decision">Cycle decision: {projection.executive.wait_reason}</small>}
        </div>
        <dl className="autonomy-clock">
          <div><dt><Clock3 size={13} aria-hidden /> Last observation cycle</dt><dd>{instant(projection.executive.last_observation_cycle)}</dd></div>
          <div><dt><TimerReset size={13} aria-hidden /> Next wake</dt><dd>{instant(projection.executive.next_wake)}</dd></div>
          <div><dt><GitBranch size={13} aria-hidden /> Active autonomous WIP</dt><dd>{metricValue(projection.executive.active_wip)}</dd></div>
        </dl>
      </section>

      <OwnerActions projection={projection} />

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
          {projection.candidates.length === 0 ? <p className="honest-empty">No candidates match this durable projection.</p> : projection.candidates.map((candidate) => <CandidateCard key={candidate.candidate_id} candidate={candidate} onOpen={() => setSelectedCandidate(candidate)} />)}
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
      <SourceDigestPanel projection={projection} />
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
      <LearningWaitPanels projection={projection} />
      <CandidateDetailDrawer candidate={selectedCandidate} open={selectedCandidate !== null} onClose={() => setSelectedCandidate(null)} />
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
