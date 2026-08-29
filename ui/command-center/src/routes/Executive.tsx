import { Link } from "react-router-dom";
import { BrainCircuit, CircleSlash, Clock, MinusCircle } from "lucide-react";
import { useCommandCenter } from "../context/CommandCenterContext";
import type { FoundryExecutive, FoundryExecutiveAutonomy, FoundryExecutiveDecision } from "../lib/snapshotTypes";
import type { OwnerControlSurface } from "../lib/contracts";
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
                <Link className="underline" to={`/missions?candidate=${encodeURIComponent(r.candidate_id)}`}>
                  {r.candidate_id}
                </Link>
                {" — "}
                {r.reason}
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
              <li key={r.candidate_id}>
                <Link className="underline" to={`/missions?candidate=${encodeURIComponent(r.candidate_id)}`}>
                  {r.candidate_id}
                </Link>
                {" — "}
                {r.reason}
              </li>
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
  ownerSurface: OwnerControlSurface | null;
  autonomy: FoundryExecutiveAutonomy | null;
};

function autonomyText(value: unknown, fallback = "UNKNOWN"): string {
  if (typeof value === "string" && value.trim()) return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return fallback;
}

function AutonomySurface({ autonomy }: { autonomy: FoundryExecutiveAutonomy | null }) {
  const objectives = Object.values(autonomy?.objectives ?? {});
  const priorities = autonomy?.priorities ?? [];
  const proposals = Object.values(autonomy?.operational_followup_proposals ?? {});
  const cycle = autonomy?.latest_cycle;
  const briefing = autonomy?.briefing;
  return (
    <section className="panel" aria-label="V8 executive autonomy">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="t-label">Autonomous objectives</h2>
          <p className="mt-1 text-xs opacity-60">Durable objectives and operational proposals projected from Supervisor-owned ledger state.</p>
        </div>
        <StatusBadge status={autonomy ? autonomyText(autonomy.status, objectives.length ? "READY" : "EMPTY_LEDGER") : "UNKNOWN"} />
      </div>
      {objectives.length === 0 ? (
        <p className="mt-3 text-sm opacity-60">No durable objective is recorded. Operational discoveries remain proposals until owner or policy authority admits them.</p>
      ) : (
        <div className="mt-3 grid gap-2 md:grid-cols-2">
          {objectives.slice(0, 8).map((objective) => {
            const id = autonomyText(objective.objective_id);
            const status = autonomyText(objective.status);
            const progress = typeof objective.progress === "number" && Number.isFinite(objective.progress)
              ? `${Math.round(objective.progress * 100)}%`
              : "UNKNOWN";
            const priority = typeof objective.priority === "number" && Number.isFinite(objective.priority)
              ? objective.priority.toFixed(2)
              : "UNKNOWN";
            return (
              <article key={id} className="rounded border border-white/10 p-3">
                <div className="flex items-center justify-between gap-2">
                  <span className="t-label truncate">{id}</span>
                  <StatusBadge status={status} />
                </div>
                <p className="mt-2 text-sm">{autonomyText(objective.objective)}</p>
                <p className="mt-1 text-xs opacity-60">priority {priority} · progress {progress}</p>
              </article>
            );
          })}
        </div>
      )}
      <div className="mt-3 grid gap-2 text-xs opacity-75 md:grid-cols-3">
        <span>Ranked {priorities.length || "UNKNOWN"}</span>
        <span>Operational proposals {proposals.length || "NONE"}</span>
        <span>Last cycle {autonomyText(cycle?.cycle_id, "NONE")}</span>
      </div>
      {proposals.length > 0 && (
        <p className="mt-2 text-xs opacity-60">Proposal state is non-executable: owner/policy authority is required before objective or mission creation.</p>
      )}
      {briefing && <p className="mt-2 text-xs opacity-50">Briefing {autonomyText(briefing.briefing_id)} · evidence-backed next-work selection remains Supervisor-owned.</p>}
    </section>
  );
}

function recordText(value: unknown): string {
  if (typeof value === "string" && value.trim()) return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "UNKNOWN";
}

function packetItems(packet: Record<string, unknown> | null, key: string): Record<string, unknown>[] {
  const value = packet?.[key];
  return Array.isArray(value) ? value.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object" && !Array.isArray(item)) : [];
}

function compactValue(value: unknown): string {
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.map(compactValue).filter(Boolean).join("; ");
  return "";
}

function compactSummary(value: unknown): string {
  const direct = compactValue(value);
  if (!direct) return "UNKNOWN";
  let parsedEnvelope = false;
  try {
    const parsed: unknown = JSON.parse(direct);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      parsedEnvelope = true;
      const record = parsed as Record<string, unknown>;
      for (const key of ["state", "status", "outcome", "verdict", "reason", "objective", "findings", "repair_requirements", "capability", "next_action"]) {
        const candidate = compactValue(record[key]);
        if (candidate) return candidate;
      }
    }
  } catch {
    // Plain-text summaries are already canonical; only JSON envelopes need
    // compacting here.
  }
  if (parsedEnvelope) return "canonical payload recorded";
  return direct.length > 180 ? `${direct.slice(0, 177)}…` : direct;
}

function happenedRows(packet: Record<string, unknown> | null): string[] {
  const salient = packetItems(packet, "items")
    .filter((item) => /mission|verification|evidence|memory|briefing|repair|fallback|directive/i.test(String(item.source_event_type ?? "")))
    .sort((a, b) => Number(a.source_sequence ?? 0) - Number(b.source_sequence ?? 0));
  return salient.slice(-6).reverse().map((item) => `${recordText(item.source_event_type)} · ${compactSummary(item.summary ?? item.detail ?? item.item_id)}`);
}

function BriefingPacket({ packet }: { packet: Record<string, unknown> | null }) {
  const happened = happenedRows(packet);
  const decisionItems = packetItems(packet, "decision_required");
  const classifiedMatters = decisionItems.filter((item) => {
    const classification = String(item.classification ?? "UNKNOWN").toUpperCase();
    return classification !== "UNKNOWN" && classification !== "INFORM";
  });
  const unknownMatterCount = decisionItems.length - classifiedMatters.length;
  const matters = classifiedMatters.map((item) => `${recordText(item.classification)} · ${compactSummary(item.summary ?? item.consequence ?? item.item_id)}`);
  const actions = classifiedMatters.map((item) => compactSummary(item.consequence ?? item.summary ?? item.item_id));
  const sections = [
    {
      key: "happened",
      title: "What happened",
      rows: happened,
      empty: "No briefing items are recorded.",
    },
    {
      key: "matters",
      title: "What matters",
      rows: matters.length > 0 ? matters : unknownMatterCount > 0 ? [`${unknownMatterCount} owner-attention record${unknownMatterCount === 1 ? "" : "s"} remain unclassified.`] : [],
      empty: packet ? "No decision-required items are recorded." : "Briefing packet unavailable.",
    },
    {
      key: "actions",
      title: "Owner action",
      rows: actions.length > 0 ? actions : unknownMatterCount > 0 ? ["Action UNKNOWN until the canonical queue classifies the open records."] : [],
      empty: packet ? "No owner action is recorded." : "Briefing packet unavailable.",
    },
  ];
  return (
    <section className="panel" aria-label="Executive briefing packet">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="t-label">Executive briefing</h2>
          <p className="mt-1 text-xs opacity-60">Read-only packet from the canonical briefing projection. Empty and unknown values stay visible.</p>
        </div>
        <StatusBadge status={packet ? "READY" : "UNKNOWN"} />
      </div>
      <div className="mt-4 grid gap-3 md:grid-cols-3">
        {sections.map((section) => (
          <article key={section.key} className="rounded border border-white/10 p-3">
            <h3 className="t-label">{section.title}</h3>
            {section.rows.length === 0 ? <p className="mt-2 text-sm opacity-60">{section.empty}</p> : <ul className="mt-2 space-y-2 text-sm">{section.rows.slice(0, 5).map((row, index) => <li key={`${section.key}-${index}`}>{row}</li>)}</ul>}
          </article>
        ))}
      </div>
      {packet ? <p className="mt-3 text-xs opacity-50">Packet {recordText(packet.packet_id)} · generated {recordText((packet.window as Record<string, unknown> | undefined)?.generated_at)}</p> : null}
    </section>
  );
}

export function Executive() {
  const { client, version } = useCommandCenter();
  const executiveQuery = useRouteQuery(
    () =>
      Promise.all([
        client.getExecutive(),
        client.getOwnerControlSurface ? client.getOwnerControlSurface() : Promise.resolve(null),
      ]).then(([e, ownerSurface]) => ({
        decisions: e.latest_decision ? e.recent_decisions : [],
        epoch: e.activation_epoch,
        authority: e.admission_authority,
        ownerSurface,
        autonomy: (e as FoundryExecutive).autonomy ?? null,
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
      {({ decisions, epoch, authority, ownerSurface, autonomy }) => (
    <div className="space-y-4">
      <SectionHeader
        title="Executive"
        description={
          "Decision spine projection from the durable ledger. " +
          `Activation epoch: ${epoch ?? "none"} · admission authority: ${authority}.`
        }
      />
      <BriefingPacket packet={ownerSurface?.briefingPacket ?? null} />
      <AutonomySurface autonomy={autonomy} />
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
