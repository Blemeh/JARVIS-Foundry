import { Link } from "react-router-dom";
import { ArrowRight, Ban, CircleUserRound, Compass, KeyRound, Scale, SlidersHorizontal } from "lucide-react";
import { ErrorState, LoadingState } from "../components/states";
import { useCommandCenter } from "../context/CommandCenterContext";
import { useRouteQuery } from "../hooks/useRouteQuery";
import type { OwnerControlSurface } from "../lib/contracts";
import { sourceModeLabel } from "../lib/sourceMode";
import { useLocale } from "../i18n/LocaleContext";

function field(record: Record<string, unknown>, names: string[]): string {
  for (const name of names) {
    const value = record[name];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return "Record detail unavailable";
}

function provenance(record: Record<string, unknown>): string {
  const value = record.provenance;
  if (!value || typeof value !== "object") return field(record, ["source", "directive_id"]);
  const source = value as Record<string, unknown>;
  return field(source, ["source_ref", "source_id", "authority"]);
}

function OwnerRecords({ title, rows, empty }: { title: string; rows: Record<string, unknown>[]; empty: string }) {
  return <section className="rm-owner-records"><h2>{title}</h2>{rows.length ? <div>{rows.slice(0, 8).map((row, index) => {
    const id = field(row, ["context_id", "directive_id", "event_id", "id"]);
    const sourceEvent = field(row, ["source_event_id", "event_id"]);
    return <article key={`${id}-${index}`}><span>{String(index + 1).padStart(2, "0")}</span><div><strong>{field(row, ["text", "exact_text", "objective", "reason", "summary", "command_type"])}</strong><small>{field(row, ["kind", "command_type", "state", "status"])} · {provenance(row)}</small>{sourceEvent !== "Record detail unavailable" ? <Link to={`/timeline?event=${encodeURIComponent(sourceEvent)}`}>Evidence <ArrowRight size={12} /></Link> : null}</div></article>;
  })}</div> : <p>{empty}</p>}</section>;
}

function YouContent({ surface }: { surface: OwnerControlSurface | null }) {
  const { sourceMode } = useCommandCenter();
  const { t } = useLocale();
  const waiting = surface?.decisionQueue.open ?? [];
  const context = surface?.ownerContext;
  const sections = context?.sections ?? {};
  const rows = (...names: string[]) => names.flatMap((name) => sections[name] ?? []);
  const preferences = rows("preferences");
  const permissions = rows("permissions");
  const decisions = rows("decisions");
  const contextAvailable = context?.status === "AVAILABLE" || context?.status === "EMPTY";
  return (
    <div className="shell-content rm-page rm-you">
      <header className="rm-page-head">
        <div><h1>{t("page.you")}</h1><p>{t("page.youSubtitle")}</p></div>
        <div className="rm-page-source"><span>{sourceModeLabel(sourceMode)}</span><small>{contextAvailable ? `${context?.contract} · ${context?.authority.model}` : surface ? `runtime ${surface.runtimeState}` : "owner projection unavailable"}</small></div>
      </header>

      <section className="rm-owner-relationship">
        <div className="rm-owner-avatar"><CircleUserRound size={42} aria-hidden="true" /></div>
        <div><h2>{t("you.authority")}</h2><p>JARVIS may observe, reason, prepare, act within granted authority, verify, recover, and learn. Goals, constraints, vetoes, pause, redirect, and STOP remain yours.</p></div>
        <div className="rm-owner-state"><span>{surface ? (surface.stopped ? "STOPPED" : surface.paused ? "PAUSED" : surface.runtimeState) : "UNKNOWN"}</span><small>{surface?.stateReason ?? "Canonical owner state not available"}</small></div>
      </section>

      {!surface ? <div className="rm-owner-unavailable"><Compass size={20} /><div><strong>{t("you.unavailable")}</strong><p>No simulated goals or preferences are substituted. Connect a canonical runtime projection to inspect durable directives.</p></div></div> : null}

      <div className="rm-owner-grid">
        <OwnerRecords title={t("you.goals")} rows={contextAvailable ? rows("goals", "priorities", "active_objectives") : surface?.goals ?? []} empty={contextAvailable ? "No current goal, priority, or objective is recorded." : surface ? "No active goal is recorded." : "UNAVAILABLE"} />
        <OwnerRecords title={t("you.projects")} rows={rows("programs", "projects", "standing_orders")} empty={contextAvailable ? "NOT YET INTEGRATED" : "UNAVAILABLE"} />
        <OwnerRecords title={t("you.constraints")} rows={contextAvailable ? rows("constraints", "permissions", "preferences") : surface?.constraints ?? []} empty={contextAvailable ? "No source-backed record is available." : surface ? "No standing constraint is recorded." : "UNAVAILABLE"} />
        <OwnerRecords title={t("you.decisions")} rows={contextAvailable ? rows("decisions", "vetoes") : [...(surface?.vetoes ?? []), ...(surface?.redirects ?? []), ...(surface?.reprioritizations ?? [])]} empty={contextAvailable ? "No current decision or veto is recorded." : "UNAVAILABLE"} />
      </div>

      <section className="rm-owner-waiting" aria-labelledby="owner-waiting-title">
        <div className="rm-section-heading"><h2 id="owner-waiting-title">{t("you.waiting")}</h2><span>{surface ? waiting.length : "—"}</span></div>
        {waiting.length ? waiting.map((item) => <article key={item.queue_id}>
          <Scale size={17} /><div><strong>{item.summary ?? item.classification}</strong><p>{item.consequence ?? "Consequence not recorded."}</p><small>{item.classification} · {item.opened_at ?? "time unknown"}</small></div>
          {item.mission_id ? <Link to={`/missions/${item.mission_id}`}>Review <ArrowRight size={14} /></Link> : <Link to="/owner">Inspect <ArrowRight size={14} /></Link>}
        </article>) : <p>{surface ? "No consequential owner decision is open." : "Decision queue unavailable."}</p>}
      </section>

      <div className="rm-owner-seams">
        <section><SlidersHorizontal size={17} /><div><h2>Preferences</h2><p>{preferences.length ? `${preferences.length} source-backed` : "NOT YET INTEGRATED"}</p><small>{preferences.length ? provenance(preferences[0]) : "No canonical preference event is present."}</small></div></section>
        <section><KeyRound size={17} /><div><h2>Permissions</h2><p>{permissions.length ? `${permissions.length} source-backed` : "NOT YET INTEGRATED"}</p><small>{permissions.length ? provenance(permissions[0]) : "Capability grants stay in their existing authority systems."}</small></div></section>
        <section><Ban size={17} /><div><h2>Decisions and veto history</h2><p>{surface ? `${context?.historyCount ?? surface.directiveHistory.length} durable records` : "UNAVAILABLE"}</p><small>{decisions.length ? `${decisions.length} current decision records; complete history stays forensic.` : "Open the forensic owner ledger for complete history."}</small></div><Link to="/owner">Open ledger <ArrowRight size={14} /></Link></section>
      </div>
    </div>
  );
}

export function You() {
  const { client, version, sourceMode, connection } = useCommandCenter();
  const query = useRouteQuery<OwnerControlSurface | null>(() => client.getOwnerControlSurface ? client.getOwnerControlSurface().catch(() => null) : Promise.resolve(null), [client, version]);
  if (query.status === "loading") return <div className="shell-content"><LoadingState label="Loading owner context" /></div>;
  if (query.status === "error") return <div className="shell-content"><ErrorState label="Owner context unavailable" detail={connection.error ?? `Source ${sourceModeLabel(sourceMode)} did not return owner context.`} onRetry={query.reload} /></div>;
  return <YouContent surface={query.data} />;
}
