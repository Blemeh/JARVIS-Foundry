import { ArrowUpRight, CircleAlert, Clock3, ExternalLink, ShieldCheck } from "lucide-react";
import { Link } from "react-router-dom";
import { useCommandCenter } from "../context/CommandCenterContext";
import { EmptyState, ErrorState, LoadingState } from "../components/states";
import { MetricCard } from "../components/MetricCard";
import { SectionHeader } from "../components/SectionHeader";
import { StatusBadge } from "../components/StatusBadge";
import { useRouteQuery } from "../hooks/useRouteQuery";
import type { OwnerAttentionRecord, OwnerControlSurface } from "../lib/contracts";
import { routeFetchErrorDetail, shouldShowRouteFetchError } from "../lib/routeResilience";

function value(record: Record<string, unknown>, ...keys: string[]): string {
  for (const key of keys) {
    const candidate = record[key];
    if (candidate !== undefined && candidate !== null && String(candidate).trim()) return String(candidate);
  }
  return "—";
}

function runtimeBadge(state: string): "RUNNING" | "STOPPED" | "DEGRADED" | "BLOCKED" | "UNKNOWN" {
  const normalized = state.toUpperCase();
  if (normalized === "STOPPED" || normalized === "FROZEN" || normalized === "SUSPENDED") return "STOPPED";
  if (normalized === "DEGRADED") return "DEGRADED";
  if (normalized === "BLOCKED" || normalized === "PAUSED") return "BLOCKED";
  if (normalized === "RUNNING") return "RUNNING";
  return "UNKNOWN";
}

function attentionBadge(item: OwnerAttentionRecord): "ACTIVE" | "BLOCKED" | "STOPPED" | "UNKNOWN" | "COMPLETE" {
  if (item.status === "RESOLVED") return "COMPLETE";
  if (item.status === "SUPERSEDED") return "UNKNOWN";
  if (item.classification === "BLOCKED_EXTERNAL_DEPENDENCY") return "BLOCKED";
  if (item.classification === "OWNER_VETO_ACTIVE") return "STOPPED";
  if (item.classification === "UNKNOWN") return "UNKNOWN";
  return "ACTIVE";
}

function directiveLabel(record: Record<string, unknown>): string {
  return value(record, "exact_text", "objective", "constraint", "reason", "command_type", "directive_id");
}

function objectValue(record: Record<string, unknown>, key: string): Record<string, unknown> {
  const candidate = record[key];
  return candidate && typeof candidate === "object" && !Array.isArray(candidate)
    ? candidate as Record<string, unknown>
    : {};
}

function projectionBadge(status: string): "COMPLETE" | "PENDING" | "UNKNOWN" {
  if (status === "EVIDENCED") return "COMPLETE";
  if (status === "DERIVED") return "PENDING";
  return "UNKNOWN";
}

function truthBadge(status: string): "READY" | "NOT_TESTED" | "DEGRADED" | "FAIL" | "UNKNOWN" {
  const normalized = status.toUpperCase();
  if (normalized === "AVAILABLE" || normalized === "PASS" || normalized === "ELIGIBLE") return "READY";
  if (normalized === "FAIL" || normalized === "FAILED" || normalized === "REJECTED") return "FAIL";
  if (normalized === "DEGRADED" || normalized === "BLOCKED") return "DEGRADED";
  if (normalized === "EMPTY" || normalized === "NOT_TESTED") return "NOT_TESTED";
  return "UNKNOWN";
}

function TruthSignals({
  effectTruth,
  evolutionTruth,
}: {
  effectTruth?: Record<string, unknown>;
  evolutionTruth?: Record<string, unknown>;
}) {
  const effectSummary = objectValue(effectTruth ?? {}, "summary");
  const evolutionSummary = objectValue(evolutionTruth ?? {}, "summary");
  const evolutionCounts = objectValue(evolutionSummary, "by_status");
  return (
    <section className="panel">
      <div className="flex items-center justify-between gap-3">
        <div>
          <h2 className="t-label">Cross-system truth</h2>
          <p className="mt-1 text-xs opacity-60">V5 effects and V6 evolution gates reduced from durable evidence.</p>
        </div>
        <span className="t-mono text-muted">READ ONLY</span>
      </div>
      <div className="mt-4 grid gap-3 md:grid-cols-2">
        <article className="rounded border border-white/10 p-3">
          <div className="flex items-center justify-between gap-2">
            <span className="t-label">Effect / reconciliation</span>
            <StatusBadge status={truthBadge(value(effectTruth ?? {}, "status"))} />
          </div>
          <p className="mt-2 text-sm">Attempts {value(effectSummary, "attempts", "lineage")} · unknown {value(effectSummary, "unknown")} · pending {value(effectSummary, "pending")}</p>
          <p className="mt-1 text-xs opacity-60">External payloads: {value(effectTruth ?? {}, "external_payloads", "source")}</p>
        </article>
        <article className="rounded border border-white/10 p-3">
          <div className="flex items-center justify-between gap-2">
            <span className="t-label">Self-evolution gates</span>
            <StatusBadge status={truthBadge(value(evolutionTruth ?? {}, "status"))} />
          </div>
          <p className="mt-2 text-sm">Records {value(evolutionSummary, "records")} · eligible {value(evolutionSummary, "eligible_decisions")} · failed {value(evolutionCounts, "FAIL", "FAILED")}</p>
          <p className="mt-1 text-xs opacity-60">Authority: {value(evolutionTruth ?? {}, "ui_authority", "source")}</p>
        </article>
      </div>
    </section>
  );
}

function DirectiveList({
  title,
  records,
  empty,
}: {
  title: string;
  records: Record<string, unknown>[];
  empty: string;
}) {
  return (
    <section className="panel">
      <div className="flex items-center justify-between gap-3">
        <h2 className="t-label">{title}</h2>
        <span className="t-mono text-muted">{records.length}</span>
      </div>
      {records.length === 0 ? (
        <p className="mt-3 text-sm opacity-60">{empty}</p>
      ) : (
        <ul className="mt-3 space-y-3">
          {records.map((record, index) => (
            <li key={value(record, "directive_id", "event_id", "id") !== "—" ? value(record, "directive_id", "event_id", "id") : `${title}-${index}`} className="border-t border-white/10 pt-3 first:border-0 first:pt-0">
              <div className="flex items-start justify-between gap-3">
                <p className="text-sm">{directiveLabel(record)}</p>
                <StatusBadge status={value(record, "status", "lifecycle", "command_type").toUpperCase() === "ACTIVE" ? "ACTIVE" : "UNKNOWN"} />
              </div>
              <p className="mt-1 text-xs opacity-60">
                {value(record, "target", "resource_id", "scope")} · {value(record, "directive_id", "event_id", "source_event_id")}
              </p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function AttentionCard({ item }: { item: OwnerAttentionRecord }) {
  return (
    <article className="border-t border-white/10 pt-4 first:border-0 first:pt-0">
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 items-start gap-2">
          <CircleAlert size={15} className="mt-0.5 shrink-0 opacity-70" aria-hidden="true" />
          <div className="min-w-0">
            <p className="text-sm font-medium">{item.summary ?? "Owner attention item"}</p>
            <p className="mt-1 text-xs opacity-60">{item.classification} · {item.queue_id}</p>
          </div>
        </div>
        <StatusBadge status={attentionBadge(item)} />
      </div>
      {item.consequence ? <p className="mt-2 text-sm opacity-80">Consequence: {item.consequence}</p> : null}
      {(item.options?.length ?? 0) > 0 ? (
        <ul className="mt-2 list-disc space-y-1 pl-5 text-xs opacity-80">
          {item.options!.map((option, index) => <li key={`${item.queue_id}-option-${index}`}>{String(option)}</li>)}
        </ul>
      ) : null}
      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1 text-xs opacity-60">
        {item.source_event_id ? <span>event {item.source_event_id}</span> : null}
        {item.entity_id ? <span>{item.entity_kind ?? "entity"} {item.entity_id}</span> : null}
        {(item.evidence_refs?.length ?? 0) > 0 ? <span>evidence {item.evidence_refs!.join(", ")}</span> : null}
      </div>
    </article>
  );
}

function BriefingHistory({ records }: { records: Record<string, unknown>[] }) {
  return (
    <section className="panel">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center gap-2"><Clock3 size={15} aria-hidden="true" /><h2 className="t-label">Briefing history</h2></div>
        <span className="t-mono text-muted">{records.length}</span>
      </div>
      {records.length === 0 ? (
        <EmptyState label="No briefings recorded" detail="The local ledger has not produced an owner briefing yet." />
      ) : (
        <ul className="mt-3 space-y-3">
          {records.map((record, index) => (
            <li key={value(record, "briefing_id", "id") !== "—" ? value(record, "briefing_id", "id") : `briefing-${index}`} className="border-t border-white/10 pt-3 first:border-0 first:pt-0">
              <div className="flex items-start justify-between gap-3">
                <p className="text-sm">{value(record, "kind", "briefing_kind", "type")}</p>
                <StatusBadge status={value(record, "status", "delivery_status").toUpperCase() === "DELIVERED" ? "COMPLETE" : "PENDING"} />
              </div>
              <p className="mt-1 text-xs opacity-60">
                {value(record, "period_start")} → {value(record, "period_end")} · items {Array.isArray(record.items) ? record.items.length : value(record, "item_count")}
              </p>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}

function ResultTraceability({
  results,
  count,
}: {
  results: Record<string, unknown>[];
  count: number;
}) {
  return (
    <section className="panel">
      <div className="flex items-center justify-between gap-3">
        <div><h2 className="t-label">Result traceability</h2><p className="mt-1 text-xs opacity-60">Decision → evidence → execution → outcome, with unknowns left visible.</p></div>
        <span className="t-mono text-muted">{count}</span>
      </div>
      {results.length === 0 ? <EmptyState label="No result lineage recorded" detail="A representative result cannot be traced until the ledger records an outcome." /> : (
        <ul className="mt-4 space-y-4">
          {results.slice(0, 5).map((result, index) => {
            const candidate = objectValue(result, "candidate");
            const mission = objectValue(result, "mission");
            const outcome = objectValue(result, "actual_outcome");
            const decision = objectValue(result, "decision");
            const next = objectValue(result, "next_action");
            const missionId = value(mission, "mission_id");
            return (
              <li key={value(result, "trace_id") !== "—" ? value(result, "trace_id") : `trace-${index}`} className="border-t border-white/10 pt-3 first:border-0 first:pt-0">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <p className="text-sm">{value(candidate, "candidate_id")} → {missionId !== "—" ? <Link className="underline" to={`/missions/${encodeURIComponent(missionId)}`}>{missionId}</Link> : "mission unknown"}</p>
                  <StatusBadge status={projectionBadge(value(outcome, "status").toUpperCase())} />
                </div>
                <p className="mt-1 text-xs opacity-70">Decision {value(decision, "decision_id", "decision")} · next {value(next, "value")}</p>
                <p className="mt-1 text-xs opacity-50">Refs: {Array.isArray(result.evidence_refs) ? result.evidence_refs.join(", ") || "none" : "unknown"}</p>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

export function OwnerManagement() {
  const { client, version, sourceMode, connection } = useCommandCenter();
  const query = useRouteQuery<OwnerControlSurface | null>(
    () => client.getOwnerControlSurface ? client.getOwnerControlSurface() : Promise.resolve(null),
    [client, version],
  );

  if (query.status === "loading") {
    return <div className="shell-content"><LoadingState label="Loading owner controls" /></div>;
  }
  if (query.status === "error" || shouldShowRouteFetchError(sourceMode, query.error, query.data !== null)) {
    return <div className="shell-content"><ErrorState label="Owner controls unavailable" detail={routeFetchErrorDetail(sourceMode, connection, query.error, "Owner management projection")} onRetry={query.reload} /></div>;
  }
  if (!query.data) {
    return <div className="shell-content"><EmptyState label="Owner management projection unavailable" detail="The simulated client does not expose a canonical owner ledger. Connect the live runtime to inspect authenticated controls and their evidence." /></div>;
  }

  const surface = query.data;
  const queue = surface.decisionQueue;
  const openItems = queue.open;
  const state = surface.runtimeState || "UNKNOWN";

  return (
    <div className="shell-content">
      <SectionHeader
        kicker="Control plane / ledger projection"
        title="Owner control / management by exception"
        description="A read-only view of authenticated owner directives, runtime state, briefings, and unresolved decisions. This surface reports canonical evidence; it is not a second authority."
        actions={<Link to="/notifications" className="btn btn-ghost">Exception center <ArrowUpRight size={14} aria-hidden="true" /></Link>}
      />

      <div className="panel panel-corner p-4">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="flex items-start gap-3">
            <ShieldCheck size={20} className="mt-0.5 text-teal-300" aria-hidden="true" />
            <div>
              <div className="flex flex-wrap items-center gap-2"><span className="t-label">Canonical runtime state</span><StatusBadge status={runtimeBadge(state)} /></div>
              <p className="mt-2 text-sm">{surface.stateReason}</p>
              <p className="mt-1 text-xs opacity-60">Source: {surface.stateSource} · projection: {surface.source}</p>
            </div>
          </div>
          <div className="text-right text-xs opacity-70">
            <p>{surface.paused ? "Owner pause is active" : "No owner pause projected"}</p>
            <p>{surface.resumeAvailable ? "Resume available" : "Resume not available"}</p>
          </div>
        </div>
      </div>

      <div className="fleet-stats systems-stats mt-4">
        <MetricCard label="Open attention" value={queue.open_count} tone={queue.open_count ? "amber" : "teal"} />
        <MetricCard label="Active goals" value={surface.goals.length} tone="accent" />
        <MetricCard label="Constraints / vetoes" value={`${surface.constraints.length} / ${surface.vetoes.length}`} tone={surface.vetoes.length ? "error" : "default"} />
        <MetricCard label="Briefing records" value={surface.briefings.length} tone="teal" />
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-[1.35fr_1fr]">
        <section className="panel">
          <div className="flex items-center justify-between gap-3">
            <div><h2 className="t-label">Decision queue</h2><p className="mt-1 text-xs opacity-60">Items that need owner awareness, choice, or explicit reconciliation.</p></div>
            <span className="t-mono text-muted">{queue.open_count} open</span>
          </div>
          {openItems.length === 0 ? <EmptyState label="No unresolved owner decisions" detail="The ledger currently projects no open exception or decision-required item." /> : <div className="mt-4 space-y-4">{openItems.map((item) => <AttentionCard key={item.queue_id} item={item} />)}</div>}
          {queue.history_count > 0 ? <p className="mt-4 flex items-center gap-1 text-xs opacity-50"><ExternalLink size={12} aria-hidden="true" /> {queue.history_count} resolved or superseded item(s) remain in history.</p> : null}
        </section>

        <div className="space-y-4">
          <DirectiveList title="Authenticated goals" records={surface.goals} empty="No active owner goals are projected." />
          <DirectiveList title="Constraints" records={surface.constraints} empty="No active owner constraints are projected." />
          <DirectiveList title="Vetoes" records={surface.vetoes} empty="No active owner vetoes are projected." />
        </div>
      </div>

      <div className="mt-4 grid gap-4 xl:grid-cols-2">
        <BriefingHistory records={surface.briefings} />
        <section className="panel">
          <h2 className="t-label">Directive history</h2>
          {surface.directiveHistory.length === 0 ? <p className="mt-3 text-sm opacity-60">No owner directive history is available.</p> : <p className="mt-3 text-sm">{surface.directiveHistory.length} directive record(s) are available through the canonical snapshot.</p>}
          <p className="mt-3 text-xs opacity-50">Redirects: {surface.redirects.length} · Reprioritizations: {surface.reprioritizations.length}</p>
        </section>
      </div>

      <div className="mt-4">
        <ResultTraceability results={surface.resultTraceability.results} count={surface.resultTraceability.count} />
      </div>

      <div className="mt-4">
        <TruthSignals effectTruth={surface.effectTruth} evolutionTruth={surface.evolutionTruth} />
      </div>

      <p className="mt-4 flex items-center gap-1 text-xs opacity-50"><ExternalLink size={12} aria-hidden="true" /> Evidence and event identifiers link this projection back to the local ledger. No UI action here mutates owner authority.</p>
    </div>
  );
}
