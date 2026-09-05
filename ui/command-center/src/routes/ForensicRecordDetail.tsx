import { ArrowLeft, FileSearch, History, Link2 } from "lucide-react";
import { Link, useParams } from "react-router-dom";
import { DetailRouteQueryGate } from "../components/DetailRouteQueryGate";
import { useCommandCenter } from "../context/CommandCenterContext";
import { useRouteQuery } from "../hooks/useRouteQuery";
import { sourceModeLabel } from "../lib/sourceMode";

function display(value: unknown): string {
  if (value === null || value === undefined || value === "") return "UNKNOWN";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return JSON.stringify(value);
}

function relatedLinks(record: Record<string, unknown>) {
  const links: Array<{ label: string; to: string }> = [];
  if (typeof record.mission_id === "string" && record.mission_id) {
    links.push({ label: `Mission ${record.mission_id}`, to: `/missions/${encodeURIComponent(record.mission_id)}` });
  }
  const eventId = typeof record.source_event_id === "string"
    ? record.source_event_id
    : typeof record.last_event_id === "string"
      ? record.last_event_id
      : null;
  if (eventId) links.push({ label: `Event ${eventId}`, to: `/activity?event=${encodeURIComponent(eventId)}` });
  const evidence = Array.isArray(record.evidence_refs)
    ? record.evidence_refs.filter((value): value is string => typeof value === "string")
    : [];
  for (const id of evidence.slice(0, 8)) {
    links.push({ label: `Evidence ${id}`, to: `/evidence/${encodeURIComponent(id)}` });
  }
  return links;
}

export function ForensicRecordDetail() {
  const { recordKind, recordId } = useParams();
  const { client, version, sourceMode } = useCommandCenter();
  const query = useRouteQuery(async () => {
    if (!recordKind || !recordId || !client.getForensicRecord) return null;
    return client.getForensicRecord(recordKind, recordId);
  }, [client, version, recordKind, recordId]);

  return (
    <DetailRouteQueryGate
      query={query}
      loadingLabel="Loading forensic projection"
      notFoundLabel="Projection record not found"
      notFoundDetail={`No bounded ${recordKind ?? "forensic"} projection exists at this identifier.`}
      errorLabel="Forensic projection unavailable"
      surface="Forensic record"
    >
      {(result) => {
        const entries = Object.entries(result.record);
        const links = relatedLinks(result.record);
        return (
          <div className="shell-content">
            <div className="md-breadcrumb">
              <Link to="/activity" className="md-back">
                <ArrowLeft size={14} aria-hidden="true" />
                <span className="t-label">Activity</span>
              </Link>
              <span className="t-mono text-muted md-crumb-id">{recordKind}:{recordId}</span>
            </div>

            <div className="md-head">
              <div className="md-head-main">
                <div className="md-head-badges">
                  <span className="badge badge-muted">READ-ONLY PROJECTION</span>
                  <span className="t-mono text-muted">{sourceModeLabel(sourceMode)}</span>
                </div>
                <h1 className="t-display md-title">{recordKind?.replaceAll("_", " ")} record</h1>
                <p className="prose md-desc">Bounded canonical readback. This surface has no execution or database-write authority.</p>
              </div>
            </div>

            {links.length ? (
              <section className="panel panel-corner md-hub" aria-label="Related projection links">
                <div className="panel-header">
                  <h2 className="t-label panel-title text-muted"><Link2 size={13} className="panel-icon" />Related records</h2>
                </div>
                <div className="md-hub-body">
                  {links.map((link) => <Link key={`${link.to}:${link.label}`} to={link.to} className="md-hub-link"><span className="t-label">{link.label}</span></Link>)}
                </div>
              </section>
            ) : null}

            <div className="detail-grid">
              <section className="panel panel-corner detail-panel">
                <div className="panel-header">
                  <h2 className="t-label panel-title text-muted"><FileSearch size={13} className="panel-icon" />Record fields</h2>
                </div>
                <dl className="record-fields panel-body">
                  {entries.map(([key, value]) => (
                    <div key={key}>
                      <dt className="t-label text-muted">{key.replaceAll("_", " ")}</dt>
                      <dd className="t-mono record-hash">{display(value)}</dd>
                    </div>
                  ))}
                </dl>
              </section>

              <section className="panel panel-corner detail-panel">
                <div className="panel-header">
                  <h2 className="t-label panel-title text-muted"><History size={13} className="panel-icon" />Projection envelope</h2>
                </div>
                <pre className="forensic-json">{JSON.stringify(result, null, 2)}</pre>
              </section>
            </div>
          </div>
        );
      }}
    </DetailRouteQueryGate>
  );
}
