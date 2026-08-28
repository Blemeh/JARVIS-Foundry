import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { Download, FileCode2, Fingerprint, Lock, Search, ShieldCheck } from "lucide-react";
import { useCommandCenter } from "../context/CommandCenterContext";
import { SectionHeader } from "../components/SectionHeader";
import { StatusBadge } from "../components/StatusBadge";
import { MetricCard } from "../components/MetricCard";
import { Pagination } from "../components/Pagination";
import { EmptyState } from "../components/states";
import { RouteQueryGate } from "../components/RouteQueryGate";
import { ContextChip, EntityRefLink } from "../components/EntityRefLink";
import { useRouteQuery } from "../hooks/useRouteQuery";
import { formatHash } from "../lib/format";
import { sourceCopy, sourceModeLabel } from "../lib/sourceMode";
import { NOT_REPORTED_NUMERIC, formatCoveragePercent } from "../lib/truthFormat";

const PAGE_SIZE = 6;
const sources = ["ALL", "VERIFIER", "WORKER", "LEDGER", "MEMORY"] as const;
type SourceFilter = (typeof sources)[number];

export function Evidence() {
  const { client, version, sourceMode } = useCommandCenter();
  const [searchParams, setSearchParams] = useSearchParams();
  const itemsQuery = useRouteQuery(
    () => client.listEvidence(),
    [client, version],
  );

  const [query, setQuery] = useState("");
  const [source, setSource] = useState<SourceFilter>("ALL");
  const [page, setPage] = useState(1);

  const missionFilter = searchParams.get("mission");
  const verdictFilter = searchParams.get("verdict");

  const filtered = useMemo(() => {
    const items = itemsQuery.data;
    if (!items) return [];
    const q = query.trim().toLowerCase();
    return items.filter((it) => {
      const sourceOk = source === "ALL" || it.source.toLowerCase().includes(source.toLowerCase());
      const qOk = !q || `${it.id} ${it.kind} ${it.source} ${it.hash} ${it.verdict}`.toLowerCase().includes(q);
      const missionOk = !missionFilter || it.missionRef === missionFilter;
      const verdictOk =
        !verdictFilter ||
        (verdictFilter === "FAILED" ? it.verdict === "FAILED" || it.verdict === "TAMPERED" : it.verdict === verdictFilter);
      return sourceOk && qOk && missionOk && verdictOk;
    });
  }, [itemsQuery.data, query, source, missionFilter, verdictFilter]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount);
  const rows = filtered.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);

  return (
    <RouteQueryGate
      query={itemsQuery}
      loadingLabel="Loading evidence ledger"
      errorLabel="Evidence ledger unavailable"
      surface="Evidence ledger"
    >
      {(items) => {
        const verified = items.filter((i) => i.verdict === "VERIFIED").length;
        const flagged = items.filter((i) => i.verdict === "TAMPERED" || i.verdict === "FAILED" || i.verdict === "WARN").length;
        const verificationRate = items.length === 0 ? NOT_REPORTED_NUMERIC : Math.round((verified / items.length) * 1000) / 10;
        const activeEvidence = items.find((item) => item.verdict === "EVALUATING") ?? items[0] ?? null;

        return (
    <div className="shell-content">
      <SectionHeader
        kicker="Evidence"
        title="Evidence Ledger"
        description={sourceCopy(sourceMode, {
          live: "Canonical evidence artifacts collected by JARVIS workers and verifiers, read from the Foundry ledger.",
          simulated: "Simulated artifacts collected by JARVIS workers and verifiers for tests and demos. Every row is fixture data with a plausible hash; no canonical ledger is touched.",
          unavailable: `Evidence source ${sourceModeLabel(sourceMode)}: canonical artifacts are unavailable and no fixture fallback is shown.`,
        })}
        actions={
          <>
            <span className={`badge ${sourceMode === "SIMULATED" ? "badge-amber" : sourceMode === "LIVE" ? "badge-teal" : "badge-muted"}`} title={sourceMode === "SIMULATED" ? "Simulated integrity checks only; not a live verifier node." : sourceMode === "LIVE" ? "Verifier health follows canonical projection factors." : "Integrity status unavailable while disconnected."}>
              <ShieldCheck size={11} aria-hidden="true" />
              {sourceMode === "SIMULATED" ? "Integrity SIMULATED" : sourceMode === "LIVE" ? "Integrity from projection" : "Integrity UNAVAILABLE"}
            </span>
            <button type="button" className="btn" aria-label={`Export ledger (${sourceModeLabel(sourceMode).toLowerCase()})`} disabled={sourceMode !== "SIMULATED"} title={sourceMode !== "SIMULATED" ? `Export unavailable in ${sourceModeLabel(sourceMode)} source mode` : "Export simulated ledger fixture locally"}>
              <Download size={14} />
              Export Ledger
            </button>
          </>
        }
      />

      {missionFilter || verdictFilter ? (
        <div className="context-row">
          {missionFilter ? (
            <ContextChip
              label={`Evidence for mission ${missionFilter}`}
              entity={{ kind: "mission", id: missionFilter, label: missionFilter }}
              onClear={() => {
                const next = new URLSearchParams(searchParams);
                next.delete("mission");
                setSearchParams(next, { replace: true });
                setPage(1);
              }}
            />
          ) : null}
          {verdictFilter ? (
            <ContextChip
              label={`Verdict ${verdictFilter}`}
              onClear={() => {
                const next = new URLSearchParams(searchParams);
                next.delete("verdict");
                setSearchParams(next, { replace: true });
                setPage(1);
              }}
            />
          ) : null}
        </div>
      ) : null}

      <div className="evidence-stats">
        <MetricCard label="Total artifacts" value={items.length.toLocaleString()} delta={`${verified} verified`} tone="accent" />
        <MetricCard label="Verification rate" value={formatCoveragePercent(verificationRate)} delta={`${flagged} flagged`} />
        <div className="panel panel-corner evidence-directive">
          <div className="evidence-directive-main">
            <span className="t-label text-muted">Latest verifier record</span>
            <span className="t-mono evidence-directive-target">{activeEvidence?.verdictId ?? activeEvidence?.id ?? "UNKNOWN"}</span>
            <span className="t-mono text-muted">{activeEvidence?.verdict ?? "UNKNOWN"} · no progress estimate recorded</span>
          </div>
          <Fingerprint size={34} className="evidence-directive-icon text-muted" aria-hidden="true" />
        </div>
      </div>

      <div className="panel panel-corner evidence-table-wrap">
        <div className="evidence-toolbar">
          <div className="evidence-search">
            <Search size={14} className="text-muted" aria-hidden="true" />
            <input
              className="field evidence-search-input"
              placeholder="Filter artifacts by hash, source, or verdict..."
              value={query}
              onChange={(e) => {
                setQuery(e.target.value);
                setPage(1);
              }}
              aria-label="Filter artifacts"
            />
          </div>
          <div className="evidence-filters" role="group" aria-label="Evidence source filters">
            {sources.map((s) => (
              <button
                key={s}
                type="button"
                className={`chip ${source === s ? "chip-active" : ""}`}
                aria-pressed={source === s}
                onClick={() => {
                  setSource(s);
                  setPage(1);
                }}
              >
                {s}
              </button>
            ))}
          </div>
        </div>

        {rows.length === 0 ? (
          <EmptyState label="No evidence matches" detail={sourceMode === "SIMULATED" ? "Adjust filters or clear context to inspect more artifacts in this simulated universe." : sourceMode === "LIVE" ? "Adjust filters or clear context to inspect canonical artifacts." : `Evidence data is unavailable: runtime source is ${sourceMode}.`} />
        ) : (
          <div className="evidence-table" aria-label="Evidence artifacts">
            <div className="evidence-row evidence-row-head">
              <span className="t-label text-muted">Artifact ID / Type</span>
              <span className="t-label text-muted">Mission</span>
              <span className="t-label text-muted">Provenance (source)</span>
              <span className="t-label text-muted">Integrity hash (SHA-256)</span>
              <span className="t-label text-muted evidence-verdict-head">Verdict</span>
            </div>
            {rows.map((it) => (
              <div
                key={it.id}
                className={`evidence-row evidence-row-data ${it.verdict === "TAMPERED" ? "evidence-row-tampered" : ""}`}
              >
                <div className="evidence-cell-id">
                  <FileCode2 size={15} className={it.verdict === "TAMPERED" ? "text-error" : "text-muted"} aria-hidden="true" />
                  <span>
                    <Link to={`/evidence/${it.id}`} className="evidence-artifact-link t-mono evidence-artifact-id">
                      {it.id}
                    </Link>
                    <span className="t-mono evidence-artifact-kind">{it.typeLabel}</span>
                  </span>
                </div>
                <span className="evidence-cell-mission">
                  <EntityRefLink entity={{ kind: "mission", id: it.missionRef, label: it.missionRef }} />
                </span>
                <Link to={`/evidence/${it.id}`} className="evidence-cell-source evidence-row-link">
                  <span className="evidence-source-name">{it.source}</span>
                  <span className="t-mono evidence-source-uri">{it.sourceUri}</span>
                </Link>
                <Link to={`/evidence/${it.id}`} className="evidence-cell-hash evidence-row-link">
                  <Lock size={12} className={it.verdict === "TAMPERED" ? "text-error" : "text-muted"} aria-hidden="true" />
                  <span className={`t-mono ${it.verdict === "TAMPERED" ? "text-error" : ""}`}>{formatHash(it.hash)}</span>
                </Link>
                <Link to={`/evidence/${it.id}`} className="evidence-cell-verdict evidence-row-link">
                  <StatusBadge status={it.verdict} />
                </Link>
              </div>
            ))}
          </div>
        )}

        <Pagination
          page={safePage}
          pageCount={pageCount}
          onPage={setPage}
          showingLabel={`Showing ${(safePage - 1) * PAGE_SIZE + 1}-${Math.min(safePage * PAGE_SIZE, filtered.length)} of ${filtered.length} artifacts`}
        />
      </div>
    </div>
        );
      }}
    </RouteQueryGate>
  );
}
