import { Link } from "react-router-dom";
import { FlaskConical, ShieldQuestion } from "lucide-react";
import { useCommandCenter } from "../context/CommandCenterContext";
import type { OwnerControlSurface } from "../lib/contracts";
import { SectionHeader } from "../components/SectionHeader";
import { StatusBadge } from "../components/StatusBadge";
import { EmptyState } from "../components/states";
import { RouteQueryGate } from "../components/RouteQueryGate";
import { useRouteQuery } from "../hooks/useRouteQuery";
import { sourceCopy, sourceModeLabel } from "../lib/sourceMode";
import { useLocale } from "../i18n/LocaleContext";

function text(value: unknown): string {
  if (typeof value === "string" && value.trim()) return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return "UNKNOWN";
}

function objectValue(value: unknown, key: string): unknown {
  return value && typeof value === "object" && !Array.isArray(value) ? (value as Record<string, unknown>)[key] : undefined;
}

function badge(value: string): "READY" | "NOT_TESTED" | "DEGRADED" | "FAIL" | "UNKNOWN" {
  const normalized = value.toUpperCase();
  if (["PASS", "AVAILABLE", "ELIGIBLE", "TRUSTED_AUTONOMOUS"].includes(normalized)) return "READY";
  if (["FAIL", "FAILED", "REJECTED"].includes(normalized)) return "FAIL";
  if (["DEGRADED", "BLOCKED", "PROVING"].includes(normalized)) return "DEGRADED";
  if (["EMPTY", "NOT_TESTED", "NOT_RUN"].includes(normalized)) return "NOT_TESTED";
  return "UNKNOWN";
}

function EvolutionContent({ surface }: { surface: OwnerControlSurface | null }) {
  const { sourceMode } = useCommandCenter();
  const { t } = useLocale();
  const truth = surface?.evolutionTruth ?? null;
  const records = truth && Array.isArray(truth.records)
    ? truth.records.filter((record): record is Record<string, unknown> => Boolean(record) && typeof record === "object" && !Array.isArray(record))
    : [];
  const summary = truth ? objectValue(truth, "summary") : null;
  const byStatus = objectValue(summary, "by_status");
  return (
    <div className="shell-content">
      <SectionHeader
        kicker="Evidence gates"
        title={t("page.evolution")}
        description={sourceCopy(sourceMode, {
          live: "Authoritative evolution evidence and independent-gate projection. This page cannot promote a candidate or change runtime authority.",
          simulated: "Evolution projection is unavailable in simulated mode; no fixture gate is presented as production truth.",
          unavailable: `Evolution projection ${sourceModeLabel(sourceMode)}: canonical gate evidence is unavailable and no fixture fallback is shown.`,
        })}
        actions={<Link to="/owner" className="btn btn-ghost">Owner controls</Link>}
      />
      <section className="panel panel-corner p-4" aria-label="Evolution truth status">
        <div className="flex items-start justify-between gap-4">
          <div className="flex items-start gap-3">
            <FlaskConical size={19} className="mt-0.5 text-teal-300" aria-hidden="true" />
            <div>
              <div className="flex flex-wrap items-center gap-2"><span className="t-label">Canonical evolution projection</span><StatusBadge status={badge(text(truth?.status))} /></div>
              <p className="mt-2 text-sm">Source: {text(truth?.source)} · authority: {text(truth?.ui_authority)}</p>
            </div>
          </div>
          <ShieldQuestion size={17} className="opacity-60" aria-hidden="true" />
        </div>
        <div className="mt-4 grid gap-3 sm:grid-cols-3">
          <div><span className="t-label text-muted">Records</span><p className="mt-1 t-mono">{text(objectValue(summary, "records"))}</p></div>
          <div><span className="t-label text-muted">Eligible decisions</span><p className="mt-1 t-mono">{text(objectValue(summary, "eligible_decisions"))}</p></div>
          <div><span className="t-label text-muted">Status counts</span><p className="mt-1 t-mono">{byStatus && typeof byStatus === "object" ? Object.entries(byStatus as Record<string, unknown>).map(([key, value]) => `${key} ${text(value)}`).join(" · ") || "UNKNOWN" : "UNKNOWN"}</p></div>
        </div>
      </section>
      {records.length === 0 ? (
        <EmptyState label="No evolution evidence recorded" detail={truth ? "The canonical projection is empty. No eligibility or promotion claim is made." : "Evolution truth is unavailable from this source."} />
      ) : (
        <section className="panel panel-corner mt-4" aria-label="Evolution evidence records">
          <div className="evidence-toolbar"><span className="t-label text-muted">EVENT-BACKED RECORDS</span><span className="t-mono text-muted">{records.length}</span></div>
          <div className="space-y-3 p-4">
            {records.map((record, index) => (
              <article key={text(record.event_id) !== "UNKNOWN" ? text(record.event_id) : `${index}`} className="border-t border-white/10 pt-3 first:border-0 first:pt-0">
                <div className="flex flex-wrap items-center justify-between gap-2"><span className="t-mono">{text(record.event_type)}</span><StatusBadge status={badge(text(record.status))} /></div>
                <p className="mt-1 text-sm">event {text(record.event_id)} · sequence {text(record.sequence)} · observed {text(record.occurred_at)}</p>
                <p className="mt-1 text-xs opacity-60">improvement {text(record.improvement_id)} · candidate {text(record.candidate_release_id)} · verification {text(record.verification_id)}</p>
              </article>
            ))}
          </div>
        </section>
      )}
    </div>
  );
}

export function Evolution() {
  const { client, version } = useCommandCenter();
  const query = useRouteQuery<OwnerControlSurface | null>(
    () => client.getOwnerControlSurface ? client.getOwnerControlSurface() : Promise.resolve(null),
    [client, version],
  );
  return (
    <RouteQueryGate query={query} loadingLabel="Loading evolution truth" errorLabel="Evolution truth unavailable" surface="Evolution evidence">
      {(surface) => <EvolutionContent surface={surface} />}
    </RouteQueryGate>
  );
}
