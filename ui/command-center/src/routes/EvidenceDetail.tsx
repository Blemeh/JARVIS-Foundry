import { useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { ArrowLeft, FileCode2, Fingerprint, Link2 } from "lucide-react";
import { useCommandCenter } from "../context/CommandCenterContext";
import type { EvidenceArtifact, LineageNode, TimelineEvent } from "../lib/contracts";
import { StatusBadge } from "../components/StatusBadge";
import { LoadingState, ErrorState } from "../components/states";
import { EntityRefLink } from "../components/EntityRefLink";
import { Lineage } from "../components/Lineage";
import { formatHash } from "../lib/format";

export function EvidenceDetail() {
  const { evidenceId } = useParams();
  const { client, version } = useCommandCenter();
  const [item, setItem] = useState<EvidenceArtifact | null>(null);
  const [lineage, setLineage] = useState<LineageNode[]>([]);
  const [relatedEvents, setRelatedEvents] = useState<TimelineEvent[]>([]);
  const [notFound, setNotFound] = useState(false);

  useEffect(() => {
    if (!evidenceId) return;
    let cancelled = false;
    client.getEvidence(evidenceId).then((e) => {
      if (cancelled) return;
      if (!e) {
        setNotFound(true);
        return;
      }
      setItem(e);
      void Promise.all([client.getLineage(e.missionRef), client.listTimeline()]).then(([lin, tl]) => {
        if (cancelled) return;
        setLineage(lin);
        setRelatedEvents(tl.filter((x) => x.evidenceId === e.id || (e.relatedEventId && x.id === e.relatedEventId)));
      });
    });
    return () => {
      cancelled = true;
    };
  }, [evidenceId, client, version]);

  if (notFound) {
    return (
      <div className="shell-content">
        <ErrorState label="Evidence not found" detail="No simulated artifact exists at this identifier." />
      </div>
    );
  }

  if (!item) {
    return (
      <div className="shell-content">
        <LoadingState label="Loading evidence record" />
      </div>
    );
  }

  const chain = [
    { label: "Observation", value: item.source },
    { label: "Evidence", value: `${item.id} · ${item.typeLabel}` },
    ...(item.claim ? [{ label: "Claim", value: item.claim }] : []),
    ...(item.decision ? [{ label: "Decision", value: item.decision }] : []),
    ...(item.outcome ? [{ label: "Outcome", value: item.outcome }] : []),
  ];

  return (
    <div className="shell-content">
      <div className="md-breadcrumb">
        <Link to="/evidence" className="md-back">
          <ArrowLeft size={14} aria-hidden="true" />
          <span className="t-label">Evidence</span>
        </Link>
        <span className="t-mono text-muted md-crumb-id">{item.id}</span>
      </div>

      <div className="md-head">
        <div className="md-head-main">
          <div className="md-head-badges">
            <StatusBadge status={item.verdict} />
            <span className="t-mono text-muted">{item.capturedAt}</span>
          </div>
          <h1 className="t-display md-title">{item.typeLabel}</h1>
          <p className="prose md-desc">{item.note}</p>
        </div>
        <div className="md-actions">
          <span className="badge badge-muted">
            <Fingerprint size={11} aria-hidden="true" />
            {formatHash(item.hash, 16)}
          </span>
        </div>
      </div>

      <div className="detail-grid">
        <section className="panel panel-corner detail-panel detail-panel-full">
          <div className="panel-header">
            <h2 className="t-label panel-title text-muted">
              <Link2 size={13} className="panel-icon" />
              Provenance chain
            </h2>
          </div>
          <ol className="provenance-chain">
            {chain.map((c, i) => (
              <li key={c.label} className="provenance-step">
                <span className="provenance-step-index">{i + 1}</span>
                <div>
                  <span className="t-label text-muted">{c.label}</span>
                  <span className="t-mono provenance-step-value">{c.value}</span>
                </div>
              </li>
            ))}
          </ol>
        </section>
      </div>

      <div className="detail-grid">
        <section className="panel panel-corner detail-panel">
          <div className="panel-header">
            <h2 className="t-label panel-title text-muted">Record fields</h2>
          </div>
          <dl className="record-fields panel-body">
            <div>
              <dt className="t-label text-muted">Source</dt>
              <dd className="t-mono">{item.source}</dd>
            </div>
            <div>
              <dt className="t-label text-muted">Source URI</dt>
              <dd className="t-mono record-hash">{item.sourceUri}</dd>
            </div>
            <div>
              <dt className="t-label text-muted">SHA-256</dt>
              <dd className="t-mono record-hash">{item.hash}</dd>
            </div>
            <div>
              <dt className="t-label text-muted">Mission ref</dt>
              <dd>
                <EntityRefLink entity={{ kind: "mission", id: item.missionRef, label: item.missionRef }} />
              </dd>
            </div>
            {item.stepId ? (
              <div>
                <dt className="t-label text-muted">Step ref</dt>
                <dd className="t-mono">{item.stepId}</dd>
              </div>
            ) : null}
            {item.workerId ? (
              <div>
                <dt className="t-label text-muted">Producing worker</dt>
                <dd>
                  <EntityRefLink entity={{ kind: "agent", id: item.workerId, label: item.workerId }} />
                </dd>
              </div>
            ) : null}
            {item.verdictId ? (
              <div>
                <dt className="t-label text-muted">Verifier verdict</dt>
                <dd>
                  <EntityRefLink entity={{ kind: "verdict", id: item.verdictId, label: item.verdictId }} />
                </dd>
              </div>
            ) : null}
          </dl>
        </section>

        <section className="panel panel-corner detail-panel">
          <div className="panel-header">
            <h2 className="t-label panel-title text-muted">Related timeline events</h2>
            <span className="t-mono text-muted">{relatedEvents.length} events</span>
          </div>
          <div className="panel-body">
            {relatedEvents.length === 0 ? (
              <span className="t-mono text-dim">No timeline events reference this artifact.</span>
            ) : (
              <ul className="detail-event-list">
                {relatedEvents.map((e) => (
                  <li key={e.id}>
                    <span className="t-mono detail-event-title">{e.title}</span>
                    <EntityRefLink entity={{ kind: "timeline", id: e.id, label: e.id }} />
                  </li>
                ))}
              </ul>
            )}
          </div>
        </section>
      </div>

      <section className="panel panel-corner detail-panel detail-panel-full">
        <div className="panel-header">
          <h2 className="t-label panel-title text-muted">
            <FileCode2 size={13} className="panel-icon" />
            Position in the verification lineage
          </h2>
          <span className="t-mono text-muted">mission {item.missionRef}</span>
        </div>
        <div className="panel-body">
          {lineage.length > 0 ? <Lineage nodes={lineage} /> : <span className="t-mono text-dim">No lineage recorded.</span>}
        </div>
      </section>

      <p className="t-mono text-muted">SIMULATED RECORD - the runtime adapter will replace this with canonical ledger evidence.</p>
    </div>
  );
}
