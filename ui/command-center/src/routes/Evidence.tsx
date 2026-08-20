import { useEffect, useMemo, useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Download, FileCode2, Fingerprint, Lock, Search, ShieldCheck } from "lucide-react";
import { useCommandCenter } from "../context/CommandCenterContext";
import type { EvidenceArtifact } from "../lib/contracts";
import { SectionHeader } from "../components/SectionHeader";
import { StatusBadge } from "../components/StatusBadge";
import { MetricCard } from "../components/MetricCard";
import { Pagination } from "../components/Pagination";
import { EmptyState, LoadingState } from "../components/states";
import { ContextChip } from "../components/EntityRefLink";
import { formatHash } from "../lib/format";
import { sourceCopy, sourceModeLabel } from "../lib/sourceMode";

const PAGE_SIZE = 6;
const sources = ["ALL", "VERIFIER", "WORKER", "LEDGER", "MEMORY"] as const;
type SourceFilter = (typeof sources)[number];

export function Evidence() {
  const { client, version, sourceMode } = useCommandCenter();
  const navigate = useNavigate();
  const [searchParams, setSearchParams] = useSearchParams();
  const [items, setItems] = useState<EvidenceArtifact[] | null>(null);
  const [query, setQuery] = useState("");
  const [source, setSource] = useState<SourceFilter>("ALL");
  const [page, setPage] = useState(1);

  const missionFilter = searchParams.get("mission");
  const verdictFilter = searchParams.get("verdict");

  useEffect(() => {
    let cancelled = false;
    client.listEvidence().then((e) => {
      if (!cancelled) setItems(e);
    });
    return () => {
      cancelled = true;
    };
  }, [client, version]);

  const filtered = useMemo(() => {
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
  }, [items, query, source, missionFilter, verdictFilter]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount);
  const rows = filtered.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);

  if (!items) {
    return (
      <div className="shell-content">
        <LoadingState label="Loading evidence ledger" />
      </div>
    );
  }

  const verified = items.filter((i) => i.verdict === "VERIFIED").length;
  const flagged = items.filter((i) => i.verdict === "TAMPERED" || i.verdict === "FAILED" || i.verdict === "WARN").length;
  const verificationRate = items.length === 0 ? 0 : Math.round((verified / items.length) * 1000) / 10;

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
            <span className="badge badge-teal">
              <ShieldCheck size={11} aria-hidden="true" />
              Integrity node online
            </span>
            <button type="button" className="btn" aria-label={`Export ledger (${sourceModeLabel(sourceMode).toLowerCase()})`} disabled={sourceMode !== "SIMULATED"}>
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
        <MetricCard label="Verification rate" value={`${verificationRate}%`} delta={`${flagged} flagged`} />
        <div className="panel panel-corner evidence-directive">
          <div className="evidence-directive-main">
            <span className="t-label text-muted">Active ingestion directive</span>
            <span className="t-mono evidence-directive-target">VERIFIER_RUN_1142</span>
            <div className="meter">
              <div className="meter-fill" style={{ width: "65%" }} />
            </div>
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
          <div className="evidence-table" role="table" aria-label="Evidence artifacts">
            <div className="evidence-row evidence-row-head" role="row">
              <span className="t-label text-muted">Artifact ID / Type</span>
              <span className="t-label text-muted">Provenance (source)</span>
              <span className="t-label text-muted">Integrity hash (SHA-256)</span>
              <span className="t-label text-muted evidence-verdict-head">Verdict</span>
            </div>
            {rows.map((it) => (
              <button
                key={it.id}
                type="button"
                className={`evidence-row evidence-row-data ${it.verdict === "TAMPERED" ? "evidence-row-tampered" : ""}`}
                role="row"
                onClick={() => navigate(`/evidence/${it.id}`)}
                aria-label={`Open evidence ${it.id}`}
              >
                <span className="evidence-cell-id">
                  <FileCode2 size={15} className={it.verdict === "TAMPERED" ? "text-error" : "text-muted"} aria-hidden="true" />
                  <span>
                    <span className="t-mono evidence-artifact-id">{it.id}</span>
                    <span className="t-mono evidence-artifact-kind">{it.typeLabel}</span>
                  </span>
                </span>
                <span className="evidence-cell-source">
                  <span className="evidence-source-name">{it.source}</span>
                  <span className="t-mono evidence-source-uri">{it.sourceUri}</span>
                </span>
                <span className="evidence-cell-hash">
                  <Lock size={12} className={it.verdict === "TAMPERED" ? "text-error" : "text-muted"} aria-hidden="true" />
                  <span className={`t-mono ${it.verdict === "TAMPERED" ? "text-error" : ""}`}>{formatHash(it.hash)}</span>
                </span>
                <span className="evidence-cell-verdict">
                  <StatusBadge status={it.verdict} />
                </span>
              </button>
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
}
