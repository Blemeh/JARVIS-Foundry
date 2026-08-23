import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  Activity,
  CircleSlash,
  Clock,
  FlaskConical,
  GitBranch,
  MinusCircle,
  Radar,
  ScrollText,
  Target,
} from "lucide-react";
import { useCommandCenter } from "../context/CommandCenterContext";
import { SectionHeader } from "../components/SectionHeader";
import { StatusBadge } from "../components/StatusBadge";
import { EmptyState, LoadingState } from "../components/states";
import { sourceCopy, sourceModeLabel } from "../lib/sourceMode";
import type {
  V3Candidate,
  V3CandidateStatus,
  V3CycleOutcome,
  V3CycleOutcomeOrUnknown,
  V3DiscoveryView,
} from "../lib/v3Models";
import { V3_DIMENSION_LABELS, V3_NEGATIVE_DIMENSIONS } from "../lib/v3Models";

function num(value: number | null | undefined): string {
  return typeof value === "number" ? value.toFixed(2) : "—";
}

const STATUS_BADGE: Record<V3CandidateStatus, Parameters<typeof StatusBadge>[0]["status"]> = {
  NEW: "PENDING",
  SCORED: "EVALUATING",
  ELIGIBLE: "READY",
  SELECTED: "ACTIVE",
  MISSION_LINKED: "IN_PROGRESS",
  EXECUTED: "RUNNING",
  MEASURING: "WARN",
  CLOSED: "COMPLETE",
  APPROVED: "PASS",
  REJECTED: "FAILED",
  DEFERRED: "STANDBY",
  SUPERSEDED: "NOT_READY",
};

const CYCLE_BADGE: Record<V3CycleOutcome, Parameters<typeof StatusBadge>[0]["status"]> = {
  COMPILED: "COMPLETE",
  WAITING: "STANDBY",
  STOPPED: "HALTED",
  BLOCKED: "BLOCKED",
};

function cycleBadge(outcome: V3CycleOutcomeOrUnknown | null): Parameters<typeof StatusBadge>[0]["status"] | null {
  if (outcome === null) return null;
  return (CYCLE_BADGE as Record<string, Parameters<typeof StatusBadge>[0]["status"]>)[outcome] ?? "PENDING";
}

/** Lifecycle order for the inline stage tracker on each candidate card. */
const LIFECYCLE: V3CandidateStatus[] = [
  "NEW",
  "SCORED",
  "ELIGIBLE",
  "SELECTED",
  "MISSION_LINKED",
  "EXECUTED",
  "MEASURING",
  "CLOSED",
];

function LifecycleTrack({ status }: { status: V3CandidateStatus }) {
  if (status === "REJECTED" || status === "DEFERRED" || status === "SUPERSEDED") {
    return (
      <div className="v3-lifecycle-track v3-lifecycle-track-off">
        <span className="t-mono">{status}</span>
      </div>
    );
  }
  const at = LIFECYCLE.indexOf(status);
  return (
    <div className="v3-lifecycle-track" aria-label={`Lifecycle stage ${status}`}>
      {LIFECYCLE.map((stage, i) => (
        <span
          key={stage}
          className={`v3-stage${i <= at ? " v3-stage-on" : ""}${i === at ? " v3-stage-now" : ""}`}
          title={stage}
        />
      ))}
    </div>
  );
}

function DimensionBar({ label, value, negative }: { label: string; value: number; negative: boolean }) {
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  return (
    <div className="v3-dim-row">
      <span className="t-label text-muted v3-dim-label">{label}</span>
      <span className="v3-dim-meter">
        <span
          className={`meter-fill ${negative ? "meter-fill-amber" : ""}`}
          style={{ width: `${pct}%` }}
        />
      </span>
      <span className="t-mono v3-dim-value">{value.toFixed(2)}</span>
    </div>
  );
}

function ScoreBlock({ candidate }: { candidate: V3Candidate }) {
  if (!candidate.score) {
    return (
      <p className="v3-note">Not scored yet — no discovery.candidate_scored event.</p>
    );
  }
  const { score } = candidate;
  const thresholdPct = Math.round(score.thresholds.min_total_score * 100);
  return (
    <section className="v3-mt-2" aria-label="Score breakdown">
      <div className="v3-row-between">
        <h4 className="t-label v3-icon-label">
          <Target size={12} aria-hidden /> Score
        </h4>
        <span className="t-mono v3-sub">
          total <span className="v3-strong">{score.total.toFixed(2)}</span> · pass ≥ {thresholdPct}%
          <span className="v3-faint">
            {" "}· calibration {score.calibrationAdjust >= 0 ? "+" : ""}
            {score.calibrationAdjust.toFixed(2)}
          </span>
        </span>
      </div>
      <div className="v3-dim-grid v3-mt-1">
        {(Object.keys(score.dimensions) as (keyof typeof score.dimensions)[]).map((dim) => (
          <DimensionBar
            key={dim}
            label={V3_DIMENSION_LABELS[dim]}
            value={score.dimensions[dim]}
            negative={V3_NEGATIVE_DIMENSIONS.has(dim)}
          />
        ))}
      </div>
      <p className="t-label text-muted v3-mt-1">policy {score.policyVersion}</p>
    </section>
  );
}

function CandidateCard({ c }: { c: V3Candidate }) {
  return (
    <article className="panel panel-corner v3-candidate-card">
      <LifecycleTrack status={c.status} />
      <div className="v3-row-between v3-mt-2">
        <div className="v3-grow">
          <span className="t-mono v3-sub v3-faint">{c.id}</span>
          <h3 className="mission-card-title">{c.objective}</h3>
        </div>
        <StatusBadge status={STATUS_BADGE[c.status]} pulse={c.status === "SELECTED"} />
      </div>
      <p className="t-label text-muted v3-mt-1">
        {c.type} · fingerprint {c.fingerprint.slice(0, 8)} · evidence ×{c.evidenceRefCount}
      </p>
      {c.expectedOutcome && <p className="prose v3-mt-1">{c.expectedOutcome}</p>}
      {c.rejection && (
        <p className="v3-row v3-note" role="note">
          <CircleSlash size={13} aria-hidden />
          <span className="t-mono">{c.rejection.code}</span>
          <span>— {c.rejection.detail}</span>
        </p>
      )}
      {c.deferredUntil && (
        <p className="v3-note">
          Deferred until {c.deferredUntil} — re-eligible after cooldown.
        </p>
      )}
      {c.supersededBy && (
        <p className="v3-note t-mono">superseded_by {c.supersededBy}</p>
      )}
      <ScoreBlock candidate={c} />
      {c.missionId && (
        <p className="v3-row v3-note v3-mt-2">
          <GitBranch size={13} aria-hidden />
          Lineage:{" "}
          <Link className="ref-link" to={`/missions/${c.missionId}`}>
            {c.missionId}
          </Link>
        </p>
      )}
      {c.maintenanceExpectation && (
        <p className="v3-note">Maintenance expectation: {c.maintenanceExpectation}</p>
      )}
    </article>
  );
}

function FlagBanner({ enabled }: { enabled: boolean }) {
  return (
    <div className={`panel panel-corner v3-flag-banner${enabled ? " v3-flag-banner-on" : ""}`} role="note">
      <FlaskConical size={14} aria-hidden />
      <span className="badge badge-amber">EXPERIMENTAL</span>
      <span className="t-mono">discovery.cycle_enabled = {enabled ? "TRUE" : "FALSE"}</span>
      <span className="v3-sub">
        {enabled
          ? "Unified V3 cycle is active in lab only. Production keeps the WAIT fallback."
          : "OFF — the unified V3 cycle is disabled. Selection runs through the executive path only."}
      </span>
    </div>
  );
}

function CycleCard({ view }: { view: Extract<V3DiscoveryView, { available: true }> }) {
  const current = view.cycles[0];
  if (!current) {
    return (
      <article className="panel panel-corner">
        <h3 className="t-label v3-icon-label"><Radar size={14} aria-hidden /> Discovery cycles</h3>
        <EmptyState label="No recorded cycles" detail="No discovery.cycle_started events exist yet." />
      </article>
    );
  }
  return (
    <article className="panel panel-corner">
      <div className="v3-row-between">
        <h3 className="t-label v3-icon-label"><Radar size={14} aria-hidden /> Current cycle</h3>
        {current.outcome ? <StatusBadge status={cycleBadge(current.outcome) ?? "EVALUATING"} /> : <StatusBadge status="EVALUATING" />}
      </div>
      <p className="t-mono v3-sub v3-faint v3-mt-1">{current.loopId}</p>
      <dl className="detail-stats-grid v3-mt-2">
        <div className="detail-stat"><dt className="t-label text-muted">Observed</dt><dd className="t-mono">{current.observed}</dd></div>
        <div className="detail-stat"><dt className="t-label text-muted">Candidates</dt><dd className="t-mono">{current.candidatesCreated}</dd></div>
        <div className="detail-stat"><dt className="t-label text-muted">Scored</dt><dd className="t-mono">{current.scored}</dd></div>
        <div className="detail-stat"><dt className="t-label text-muted">Eligible</dt><dd className="t-mono">{current.eligible}</dd></div>
        <div className="detail-stat"><dt className="t-label text-muted">Rejected</dt><dd className="t-mono">{current.rejected}</dd></div>
        <div className="detail-stat"><dt className="t-label text-muted">Degraded sources</dt><dd className="t-mono">{current.degradedSources.length}</dd></div>
      </dl>
      {current.degradedSources.length > 0 && (
        <p className="t-mono v3-note v3-faint">degraded: {current.degradedSources.join(", ")}</p>
      )}
      {current.reason && (
        <p className="v3-row v3-row-top v3-note" role="note">
          <Clock size={13} aria-hidden />
          <span>{current.reason}{current.waitKind ? ` (${current.waitKind})` : ""}</span>
        </p>
      )}
      {view.cycles.length > 1 && (
        <section className="v3-mt-2" aria-label="Recent cycles">
          <h4 className="t-label text-muted">Recent cycles</h4>
          <ul className="v3-list">
            {view.cycles.slice(1).map((cycle) => (
              <li key={cycle.loopId} className="v3-row">
                <span className="t-mono v3-faint">{cycle.loopId}</span>
                <StatusBadge status={cycleBadge(cycle.outcome) ?? "PENDING"} />
                {cycle.reason ? <span className="v3-faint v3-ellipsis">{cycle.reason}</span> : null}
              </li>
            ))}
          </ul>
        </section>
      )}
    </article>
  );
}

function SelectionCard({ view }: { view: Extract<V3DiscoveryView, { available: true }> }) {
  const s = view.selected;
  return (
    <article className="panel panel-corner">
      <h3 className="t-label v3-icon-label"><ScrollText size={14} aria-hidden /> Executive selection</h3>
      {!s ? (
        <EmptyState label="No decision recorded" detail="No executive.decision_recorded event exists yet." />
      ) : (
        <>
          <p className="t-mono v3-sub v3-faint v3-mt-1">{s.decisionId}</p>
          <p className="v3-note">
            Selected <span className="t-mono v3-strong">{s.candidateId}</span> at {s.totalScore.toFixed(2)}
          </p>
          {s.comparativeRationale && <p className="prose v3-mt-1">{s.comparativeRationale}</p>}
          {s.missionId && (
            <p className="v3-note">
              Mission: <Link className="ref-link" to={`/missions/${s.missionId}`}>{s.missionId}</Link>
            </p>
          )}
          {s.blockReason && <p className="v3-note">Blocked: {s.blockReason}</p>}
          {s.rejectedAlternatives.length > 0 && (
            <section className="v3-mt-2">
              <h4 className="t-label v3-icon-label"><CircleSlash size={12} aria-hidden /> Rejected alternatives</h4>
              <ul className="v3-list">
                {s.rejectedAlternatives.map((r) => (
                  <li key={r.candidateId}>
                    <span className="t-mono v3-faint">{r.candidateId}</span> — {r.reason}
                    {r.totalScore !== null ? ` (${num(r.totalScore)})` : ""}
                  </li>
                ))}
              </ul>
            </section>
          )}
          {s.deferredAlternatives.length > 0 && (
            <section className="v3-mt-2">
              <h4 className="t-label v3-icon-label"><MinusCircle size={12} aria-hidden /> Deferred</h4>
              <ul className="v3-list">
                {s.deferredAlternatives.map((d) => (
                  <li key={d.candidateId}>
                    <span className="t-mono v3-faint">{d.candidateId}</span> — {d.reason}
                  </li>
                ))}
              </ul>
            </section>
          )}
        </>
      )}
    </article>
  );
}

const TEAM_BADGE: Record<string, Parameters<typeof StatusBadge>[0]["status"]> = {
  ACTIVE: "ACTIVE",
  FAILED: "FAILED",
  COMPLETE: "COMPLETE",
  STANDBY: "STANDBY",
};

const VERDICT_BADGE: Record<string, Parameters<typeof StatusBadge>[0]["status"]> = {
  PASS: "PASS",
  FAIL: "FAIL",
  BLOCKED: "BLOCKED",
  NOT_TESTED: "NOT_TESTED",
};

function TeamVerifierCard({ view }: { view: Extract<V3DiscoveryView, { available: true }> }) {
  const v = view.verifierState;
  return (
    <article className="panel panel-corner">
      <h3 className="t-label v3-icon-label"><Activity size={14} aria-hidden /> Team &amp; verifier</h3>
      {view.activeTeam.length === 0 && !v ? (
        <EmptyState label="No active team" detail="No mission is dispatching roles right now." />
      ) : (
        <>
          <ul className="v3-list v3-mt-1">
            {view.activeTeam.map((m) => (
              <li key={`${m.role}-${m.missionId ?? "-"}`} className="v3-row">
                <StatusBadge status={TEAM_BADGE[m.status] ?? "STANDBY"} />
                <span className="t-mono">{m.role}</span>
                <span className="v3-faint">{m.provider}</span>
                {m.missionId ? <Link className="ref-link v3-sub" to={`/missions/${m.missionId}`}>{m.missionId}</Link> : null}
              </li>
            ))}
          </ul>
          {v && (
            <section className="v3-divider-top v3-mt-2" aria-label="Verifier state">
              <p className="v3-row-wrap">
                <span className="t-mono">verifier</span>
                {v.verdict ? (
                  <StatusBadge status={VERDICT_BADGE[v.verdict] ?? "NOT_TESTED"} />
                ) : (
                  <StatusBadge status="EVALUATING" />
                )}
                <span className="v3-faint">{v.provider}</span>
              </p>
              <p className="t-label text-muted v3-mt-1">
                fresh session {v.freshSession ? "yes" : "no"} · independence {v.independence} · repairs{" "}
                {v.repairRoundsUsed}/{v.repairBudget}
              </p>
            </section>
          )}
        </>
      )}
    </article>
  );
}

function OutcomesCard({ view }: { view: Extract<V3DiscoveryView, { available: true }> }) {
  const fmt = (d: Record<string, string>) =>
    Object.entries(d)
      .map(([k, v]) => `${k} ${v}`)
      .join(" · ") || "—";
  return (
    <article className="panel panel-corner">
      <h3 className="t-label">Predicted vs actual</h3>
      {view.outcomeMeasurements.length === 0 ? (
        <EmptyState label="No outcome measurements" detail="No outcome window has closed yet." />
      ) : (
        <ul className="v3-list v3-mt-1">
          {view.outcomeMeasurements.map((o) => (
            <li key={`${o.candidateId}:${o.window}`}>
              <p className="v3-row">
                <span className="t-mono v3-sub">{o.candidateId}</span>
                <StatusBadge status={o.status === "RECORDED" ? "COMPLETE" : "PENDING"} />
                <span className="t-label text-muted">{o.window} window</span>
              </p>
              <p className="v3-note">
                Predicted <span className="t-mono">{fmt(o.predicted)}</span>
                {" · "}Actual{" "}
                {o.actual ? (
                  <span className="t-mono">{fmt(o.actual)}</span>
                ) : (
                  <span className="v3-faint">UNKNOWN — measurement pending</span>
                )}
                {o.delta !== null ? ` · delta ${o.delta >= 0 ? "+" : ""}${o.delta.toFixed(2)}` : ""}
              </p>
              <p className="v3-note v3-faint">
                Mission <Link className="ref-link" to={`/missions/${o.missionId}`}>{o.missionId}</Link>
              </p>
            </li>
          ))}
        </ul>
      )}
    </article>
  );
}

export function Discovery() {
  const { client, version, sourceMode } = useCommandCenter();
  const [view, setView] = useState<V3DiscoveryView | null>(null);

  useEffect(() => {
    let cancelled = false;
    client.getDiscovery().then((v) => {
      if (!cancelled) setView(v);
    });
    return () => {
      cancelled = true;
    };
  }, [client, version]);

  if (!view) {
    return (
      <div className="shell-content">
        <LoadingState label="Loading discovery projection" />
      </div>
    );
  }

  if (!view.available) {
    return (
      <div className="shell-content">
        <SectionHeader
          kicker="V3 Discovery"
          title="Discovery"
          description={sourceCopy(sourceMode, {
            live: "The connected runtime does not expose the V3 discovery projection yet.",
            simulated: "Simulated mode has no discovery projection.",
            unavailable: `Source ${sourceModeLabel(sourceMode)}: discovery state is unavailable.`,
          })}
        />
        <div className="panel panel-corner">
          <EmptyState
            label="Projection not available"
            detail="The backend contract (candidate/v3-integration @701aa74) ships no discovery section in its snapshot. This surface activates when the additive projection lands."
          />
        </div>
      </div>
    );
  }

  return (
    <div className="shell-content">
      <SectionHeader
        kicker="V3 Discovery"
        title="Discovery"
        description={sourceCopy(sourceMode, {
          live: "Live V3 discovery projection: candidates, scoring under a versioned policy, portfolio state, and honest idle reasons from the canonical ledger.",
          simulated: "Simulated V3 discovery following the docs/v3 worked examples: candidates, policy-scored dimensions, portfolio, and cycle reports.",
          unavailable: `Discovery source ${sourceModeLabel(sourceMode)}: projection is unavailable.`,
        })}
      />

      <FlagBanner enabled={view.flag.enabled} />

      <div className="v3-summary-grid">
        <div className="panel panel-corner v3-kpi">
          <span className="t-label text-muted">Portfolio</span>
          <span className="t-mono v3-kpi-value">
            {view.portfolioSummary.eligible} eligible · {view.portfolioSummary.deferred} deferred ·{" "}
            {view.portfolioSummary.rejected} rejected
          </span>
        </div>
        <div className="panel panel-corner v3-kpi">
          <span className="t-label text-muted">Policy</span>
          <span className="t-mono v3-kpi-value">{view.policyVersion}</span>
        </div>
        <div className="panel panel-corner v3-kpi">
          <span className="t-label text-muted">Next selected action</span>
          <span className="v3-kpi-value">{view.nextAction ?? "—"}</span>
        </div>
      </div>

      <div className="v3-top-grid">
        <CycleCard view={view} />
        <SelectionCard view={view} />
      </div>

      <div className="v3-top-grid">
        <TeamVerifierCard view={view} />
        <OutcomesCard view={view} />
      </div>

      <section aria-label="Recent candidates">
        <h2 className="t-display v3-section-title">Recent candidates</h2>
        {view.candidatesRecent.length === 0 ? (
          <div className="panel panel-corner">
            <EmptyState label="No candidates" detail="No discovery.candidate_generated events exist yet." />
          </div>
        ) : (
          <div className="v3-candidate-grid">
            {view.candidatesRecent.map((c) => (
              <CandidateCard key={c.id} c={c} />
            ))}
          </div>
        )}
      </section>

      {view.maintenanceDue && (
        <p className="panel panel-corner v3-maintenance-note" role="note">
          Maintenance / continuation: {view.maintenanceDue}
        </p>
      )}
    </div>
  );
}
