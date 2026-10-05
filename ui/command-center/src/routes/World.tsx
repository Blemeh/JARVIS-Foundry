import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { ArrowRight, CircleDot, Database, Globe2, Radar, ScanSearch } from "lucide-react";
import { Drawer } from "../components/Drawer";
import { RouteQueryGate } from "../components/RouteQueryGate";
import { useCommandCenter } from "../context/CommandCenterContext";
import { useRouteQuery } from "../hooks/useRouteQuery";
import { loadOverviewData, type OverviewData } from "../lib/overviewLoader";
import { sourceModeLabel } from "../lib/sourceMode";
import { summarizeListText } from "../lib/format";
import { useLocale } from "../i18n/LocaleContext";

type WorldSignal = {
  id: string;
  summary: string;
  source: string;
  at: string | null;
  importance: string;
  confidence: string;
  entity: string;
  candidate: string;
  acted: string;
  refs: string[];
  sourceEventIds: string[];
  evidenceRefs: string[];
  kind: string;
};

function record(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value) ? value as Record<string, unknown> : null;
}
function valueString(value: unknown, fallback = "UNKNOWN"): string {
  return typeof value === "string" && value.trim() ? value.trim() : fallback;
}
function valueNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}
function summarize(value: unknown, fallback: string): string {
  return summarizeListText(value, fallback, 140);
}

function ageLabel(at: string | null): string {
  if (!at) return "Age unknown";
  const stamp = new Date(at).getTime();
  if (!Number.isFinite(stamp)) return "Age unknown";
  const minutes = Math.max(0, Math.floor((Date.now() - stamp) / 60_000));
  if (minutes < 60) return `${minutes}m old`;
  if (minutes < 2880) return `${Math.floor(minutes / 60)}h old`;
  return `${Math.floor(minutes / 1440)}d old`;
}

function signalsFrom(data: OverviewData): { signals: WorldSignal[]; authority: string } {
  const operational = data.operationalIntelligence?.signals ?? [];
  if (operational.length) {
    return {
      authority: data.operationalIntelligence?.schema ?? "operational_intelligence",
      signals: operational.map((signal) => ({
        id: signal.signal_id,
        summary: signal.summary,
        source: signal.kind,
        at: data.operationalIntelligence?.generated_at ?? null,
        importance: signal.salience,
        confidence: signal.score == null ? "UNKNOWN" : `${Math.round(signal.score * (signal.score <= 1 ? 100 : 1))}% score`,
        entity: signal.entity_id ?? signal.entity_kind ?? "UNKNOWN",
        candidate: "UNKNOWN",
        acted: signal.state,
        refs: [...(signal.source_event_ids ?? []), ...(signal.evidence_refs ?? [])],
        sourceEventIds: signal.source_event_ids ?? [],
        evidenceRefs: signal.evidence_refs ?? [],
        kind: signal.kind,
      })),
    };
  }
  const rawSignals = Array.isArray(data.worldIntelligence?.signals) ? data.worldIntelligence.signals.map(record).filter(Boolean) as Record<string, unknown>[] : [];
  if (rawSignals.length) {
    return {
      authority: "world_intelligence",
      signals: rawSignals.map((signal, index) => {
        const score = valueNumber(signal.confidence ?? signal.score);
        const sourceEventIds = Array.isArray(signal.source_event_ids)
          ? signal.source_event_ids.filter((item): item is string => typeof item === "string")
          : [];
        const evidenceRefs = Array.isArray(signal.evidence_refs)
          ? signal.evidence_refs.filter((item): item is string => typeof item === "string")
          : [];
        return {
          id: valueString(signal.signal_id ?? signal.id, `world-signal-${index + 1}`),
          summary: valueString(signal.summary ?? signal.title ?? signal.claim, "Signal summary unavailable"),
          source: valueString(signal.source ?? signal.source_kind),
          at: typeof (signal.observed_at ?? signal.updated_at ?? signal.occurred_at) === "string" ? String(signal.observed_at ?? signal.updated_at ?? signal.occurred_at) : null,
          importance: valueString(signal.salience ?? signal.importance),
          confidence: score == null ? "UNKNOWN" : `${Math.round(score * (score <= 1 ? 100 : 1))}%`,
          entity: valueString(signal.entity_id ?? signal.entity),
          candidate: valueString(signal.candidate_id ?? signal.candidate, "NOT YET INTEGRATED"),
          acted: valueString(signal.action_state ?? signal.state, "UNKNOWN"),
          refs: [...sourceEventIds, ...evidenceRefs],
          sourceEventIds,
          evidenceRefs,
          kind: valueString(signal.kind, "world signal"),
        };
      }),
    };
  }
  return {
    authority: "timeline fallback — not a world-signal projection",
    signals: data.timeline.slice(0, 8).map((event) => ({
      id: event.id,
      summary: event.title,
      source: `timeline.${event.category.toLowerCase()}`,
      at: event.at,
      importance: event.severity ?? "UNKNOWN",
      confidence: "UNKNOWN",
      entity: event.missionId ?? event.systemId ?? "UNKNOWN",
      candidate: "NOT YET INTEGRATED",
      acted: "RECORDED EVENT",
      refs: event.meta.map((item) => item.value),
      sourceEventIds: [event.id],
      evidenceRefs: event.evidenceId ? [event.evidenceId] : [],
      kind: "activity context",
    })),
  };
}

function WorldContent(data: OverviewData) {
  const { sourceMode } = useCommandCenter();
  const { t } = useLocale();
  const [selected, setSelected] = useState<WorldSignal | null>(null);
  const projection = useMemo(() => signalsFrom(data), [data]);
  const counts = record(data.worldIntelligence?.counts);
  const entityCount = counts ? valueNumber(counts.entities) : null;
  const predictionCount = counts ? valueNumber(counts.predictions) : null;

  return (
    <div className="shell-content rm-page rm-world">
      <header className="rm-page-head">
        <div><h1>{t("page.world")}</h1><p>{t("page.worldSubtitle")}</p></div>
        <div className="rm-page-source"><span>{sourceModeLabel(sourceMode)}</span><small>{projection.authority}</small></div>
      </header>

      <div className="rm-world-radar">
        <div className="rm-world-radar-visual" aria-hidden="true"><Radar size={74} /><span /></div>
        <div><strong>{projection.signals.length}</strong><span>visible signals</span></div>
        <div><strong>{entityCount ?? "—"}</strong><span>durable entities</span></div>
        <div><strong>{predictionCount ?? "—"}</strong><span>predictions</span></div>
        <p>{projection.authority.startsWith("timeline fallback") ? "World Intelligence is not available in this source. Recent timeline records are shown as context and are not promoted to signals." : "Signals are read-only projections. The mission engine remains the sole work authority."}</p>
      </div>

      <section className="rm-world-feed" aria-labelledby="world-feed-title">
        <div className="rm-section-heading"><h2 id="world-feed-title">Recent observations</h2><span>{projection.signals.length} signals</span></div>
        {projection.signals.length ? projection.signals.map((signal) => (
          <button key={signal.id} type="button" className="rm-world-signal" onClick={() => setSelected(signal)}>
            <span className="rm-world-signal-mark"><CircleDot size={16} aria-hidden="true" /></span>
            <span className="rm-world-signal-main"><strong>{summarize(signal.summary, "Observed signal — open for detail")}</strong><small>{ageLabel(signal.at)} · {signal.importance}</small></span>
            <span className="rm-world-signal-meta"><small className="tech-id">{signal.id}</small></span>
            <ArrowRight size={15} aria-hidden="true" />
          </button>
        )) : <div className="rm-page-empty"><Globe2 size={22} /><h2>Nothing observed yet</h2><p>Jarvis hasn’t recorded anything worth surfacing. New signals will appear here.</p></div>}
      </section>

      <aside className="rm-world-boundary"><Database size={17} /><div><strong>Authority boundary</strong><p>World is advisory context. A signal, entity, or prediction cannot create work or authorize effects on its own.</p></div><Link to="/activity">Raw activity <ArrowRight size={14} /></Link></aside>

      <Drawer
        open={selected !== null}
        title="Signal evidence"
        onClose={() => setSelected(null)}
        footer={selected?.sourceEventIds[0] ? (
          <Link className="btn" to={`/activity?event=${encodeURIComponent(selected.sourceEventIds[0])}`}>
            Open source event
          </Link>
        ) : null}
      >
        {selected ? <div className="rm-signal-detail">
          <ScanSearch size={22} /><h2>{selected.summary}</h2>
          <dl>
            <div><dt>Identity</dt><dd>{selected.id}</dd></div><div><dt>Source</dt><dd>{selected.source}</dd></div>
            <div><dt>Observed</dt><dd>{selected.at ?? "UNKNOWN"}</dd></div><div><dt>Importance</dt><dd>{selected.importance}</dd></div>
            <div><dt>Confidence</dt><dd>{selected.confidence}</dd></div><div><dt>Entity</dt><dd>{selected.entity}</dd></div>
            <div><dt>Candidate</dt><dd>{selected.candidate}</dd></div><div><dt>Acted on</dt><dd>{selected.acted}</dd></div>
          </dl>
          <h3>Evidence references</h3>{selected.refs.length ? <ul>{selected.refs.map((ref) => (
            <li key={ref}>
              {selected.sourceEventIds.includes(ref)
                ? <Link to={`/activity?event=${encodeURIComponent(ref)}`}>Event {ref}</Link>
                : selected.evidenceRefs.includes(ref)
                  ? <Link to={`/evidence/${encodeURIComponent(ref)}`}>Evidence {ref}</Link>
                  : ref}
            </li>
          ))}</ul> : <p>No evidence references were published with this projection.</p>}
        </div> : null}
      </Drawer>
    </div>
  );
}

export function World() {
  const { client, version } = useCommandCenter();
  const query = useRouteQuery(() => loadOverviewData(client), [client, version], { keepPreviousWhileLoading: true });
  return <RouteQueryGate query={query} loadingLabel="Loading world" errorLabel="World unavailable" surface="World Intelligence projection">{(data) => <WorldContent {...data} />}</RouteQueryGate>;
}
