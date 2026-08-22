import { Link } from "react-router-dom";
import { BrainCircuit, CircleSlash, Clock, MinusCircle } from "lucide-react";
import { useCommandCenter } from "../context/CommandCenterContext";
import type { FoundryExecutiveDecision } from "../lib/snapshotTypes";
import { SectionHeader } from "../components/SectionHeader";
import { StatusBadge } from "../components/StatusBadge";
import { EmptyState } from "../components/states";
import { RouteQueryGate } from "../components/RouteQueryGate";
import { useRouteQuery } from "../hooks/useRouteQuery";

function num(value: unknown): string {
  return typeof value === "number" ? value.toFixed(3) : "—";
}

function DecisionCard({ d }: { d: FoundryExecutiveDecision }) {
  const kind = (d.decision ?? "WAIT").toUpperCase();
  return (
    <article className="panel">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2 min-w-0">
          <BrainCircuit size={16} aria-hidden />
          <span className="t-label truncate">{d.decision_id}</span>
          <StatusBadge status={kind === "SELECT" ? "ACTIVE" : "STANDBY"} />
        </div>
        <span className="text-xs opacity-70">{d.occurred_at ?? ""}</span>
      </div>
      <p className="mt-1 t-label">{kind}</p>

      {d.candidate_id && (
        <p className="mt-2 text-sm">
          Selected candidate{" "}
          <Link className="underline" to={`/missions?candidate=${encodeURIComponent(d.candidate_id)}`}>
            {d.candidate_id}
          </Link>
          {d.selected_score?.composite !== undefined && (
            <> · composite {num(d.selected_score.composite)}</>
          )}
          {d.selected_score?.goal_fit !== undefined && (
            <> · goal fit {num(d.selected_score.goal_fit)}</>
          )}
        </p>
      )}

      {d.reason && <p className="mt-1 text-sm opacity-80">{d.reason}</p>}

      {d.wait && (
        <p className="mt-1 text-sm">
          Waiting ({d.wait.wait_kind ?? "UNKNOWN"}): {d.wait.reason}
          {d.wait.reconsider_condition ? ` — reconsider when ${d.wait.reconsider_condition}` : ""}
        </p>
      )}

      {(d.active_goals?.length ?? 0) > 0 && (
        <p className="mt-1 text-xs opacity-70">Active goals: {d.active_goals!.join(", ")}</p>
      )}

      {d.prediction?.prediction && (
        <dl className="mt-2 grid grid-cols-2 gap-x-4 gap-y-1 text-xs">
          <dt className="opacity-60">Predicted success</dt>
          <dd>{num(d.prediction.prediction["expected_success"])}</dd>
          <dt className="opacity-60">Expected risk</dt>
          <dd>{String(d.prediction.prediction["expected_risk"] ?? "UNKNOWN")}</dd>
          <dt className="opacity-60">Confidence</dt>
          <dd>{num(d.prediction.prediction["confidence"])}</dd>
          <dt className="opacity-60">Unknown fields</dt>
          <dd>{((d.prediction.prediction["unknown_fields"] as string[]) ?? []).join(", ") || "none"}</dd>
        </dl>
      )}

      {d.calibration && (
        <p className="mt-2 flex items-center gap-2 text-sm">
          Actual outcome:
          <StatusBadge status={d.calibration.outcome === "PASSED" ? "COMPLETE" : "BLOCKED"} />
          <span className="opacity-70">repairs {d.calibration.repair_count ?? 0}</span>
        </p>
      )}

      {(d.rejected?.length ?? 0) > 0 && (
        <section className="mt-2">
          <h4 className="t-label flex items-center gap-1"><CircleSlash size={12} aria-hidden /> Rejected alternatives</h4>
          <ul className="mt-1 space-y-1 text-xs">
            {d.rejected!.map((r) => (
              <li key={r.candidate_id}>
                <span className="opacity-70">{r.candidate_id}</span> — {r.reason}
                {r.composite !== undefined ? ` (${num(r.composite)})` : ""}
              </li>
            ))}
          </ul>
        </section>
      )}

      {(d.deferred?.length ?? 0) > 0 && (
        <section className="mt-2">
          <h4 className="t-label flex items-center gap-1"><MinusCircle size={12} aria-hidden /> Deferred</h4>
          <ul className="mt-1 space-y-1 text-xs">
            {d.deferred!.map((r) => (
              <li key={r.candidate_id}>{r.candidate_id} — {r.reason}</li>
            ))}
          </ul>
        </section>
      )}
    </article>
  );
}

type ExecutiveData = {
  decisions: FoundryExecutiveDecision[];
  epoch: string | null;
  authority: string;
};

export function Executive() {
  const { client, version } = useCommandCenter();
  const executiveQuery = useRouteQuery(
    () =>
      client.getExecutive().then((e) => ({
        decisions: e.latest_decision ? e.recent_decisions : [],
        epoch: e.activation_epoch,
        authority: e.admission_authority,
      } satisfies ExecutiveData)),
    [client, version],
  );

  return (
    <RouteQueryGate
      query={executiveQuery}
      loadingLabel="Loading executive decisions"
      errorLabel="Executive decisions unavailable"
      surface="Executive decision spine"
      shellClass="space-y-4"
    >
      {({ decisions, epoch, authority }) => (
    <div className="space-y-4">
      <SectionHeader
        title="Executive"
        description={
          "Decision spine projection from the durable ledger. " +
          `Activation epoch: ${epoch ?? "none"} · admission authority: ${authority}.`
        }
      />
      {decisions.length === 0 ? (
        <EmptyState
          label="No executive decisions recorded"
          detail="The Executive has not yet selected a mission in this ledger. This is an honest empty state, not an error."
        />
      ) : (
        <div className="space-y-3">
          {decisions.map((d) => <DecisionCard key={d.decision_id} d={d} />)}
        </div>
      )}
      <p className="text-xs opacity-50 flex items-center gap-1">
        <Clock size={12} aria-hidden /> Bounded projection of executive.* ledger events — the UI is not a second authority.
      </p>
    </div>
      )}
    </RouteQueryGate>
  );
}
