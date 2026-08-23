import { useMemo, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ArrowRight, Grid3X3, List, Plus, Upload } from "lucide-react";
import { useCommandCenter } from "../context/CommandCenterContext";
import { SectionHeader } from "../components/SectionHeader";
import { StatusBadge } from "../components/StatusBadge";
import { Pagination } from "../components/Pagination";
import { Dialog } from "../components/Dialog";
import { EmptyState } from "../components/states";
import { RouteQueryGate } from "../components/RouteQueryGate";
import { Tooltip } from "../components/Tooltip";
import { ContextChip } from "../components/EntityRefLink";
import { useRouteQuery } from "../hooks/useRouteQuery";
import { formatArchivedMissionLabel } from "../lib/routeResilience";
import { sourceCopy, sourceModeLabel } from "../lib/sourceMode";

type Filter = "ALL" | "ACTIVE" | "STANDBY" | "BLOCKED" | "ARCHIVED";
type View = "grid" | "list";

const PAGE_SIZE = 6;

const statusParam: Record<string, Filter> = {
  active: "ACTIVE",
  standby: "STANDBY",
  blocked: "BLOCKED",
};

export function Missions() {
  const { client, version, mode, mutationsEnabled, sourceMode } = useCommandCenter();
  const [searchParams, setSearchParams] = useSearchParams();
  const [filter, setFilter] = useState<Filter>(statusParam[searchParams.get("status") ?? ""] ?? "ALL");
  const [view, setView] = useState<View>("grid");
  const [page, setPage] = useState(1);
  const [dialog, setDialog] = useState<"new" | "import" | null>(null);
  const [submitted, setSubmitted] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const live = mode === "live";

  const missionsQuery = useRouteQuery(
    () => client.listMissions(),
    [client, version],
  );

  const filtered = useMemo(() => {
    const missions = missionsQuery.data ?? [];
    const candidateFilter = searchParams.get("candidate")?.toLowerCase();
    let rows = missions;
    if (candidateFilter) {
      rows = rows.filter(
        (m) =>
          m.id.toLowerCase().includes(candidateFilter) ||
          m.code.toLowerCase().includes(candidateFilter) ||
          m.title.toLowerCase().includes(candidateFilter) ||
          (m.objective ?? "").toLowerCase().includes(candidateFilter),
      );
    }
    if (filter === "ALL") return rows;
    if (filter === "ACTIVE") return rows.filter((m) => m.status === "ACTIVE" || m.status === "IN_PROGRESS" || m.status === "INTERVENTION");
    if (filter === "STANDBY") return rows.filter((m) => m.status === "STANDBY");
    if (filter === "BLOCKED") return rows.filter((m) => m.status === "BLOCKED" || m.status === "HALTED");
    if (filter === "ARCHIVED") return rows.filter((m) => m.status === "COMPLETE");
    return [];
  }, [missionsQuery.data, filter, searchParams]);

  const pageCount = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const safePage = Math.min(page, pageCount);
  const rows = filtered.slice((safePage - 1) * PAGE_SIZE, safePage * PAGE_SIZE);

  const submitMission = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    const objective = String(form.get("objective") ?? "").trim();
    if (!objective) return;
    if (live) {
      const result = await client.submitOwnerGoal(objective);
      if (result.accepted) {
        setSubmitted(true);
        setSubmitError(null);
        missionsQuery.reload();
      } else {
        setSubmitError(result.error ?? "owner goal rejected");
      }
      return;
    }
    setSubmitted(true);
  };

  return (
    <RouteQueryGate
      query={missionsQuery}
      loadingLabel="Loading missions"
      errorLabel="Missions unavailable"
      surface="Mission vectors"
    >
      {(missions) => {
        const archivedCount = missions.filter((m) => m.status === "COMPLETE").length;
        const filters: { key: Filter; label: string }[] = [
          { key: "ALL", label: "All" },
          { key: "ACTIVE", label: `Active (${missions.filter((m) => ["ACTIVE", "IN_PROGRESS", "INTERVENTION"].includes(m.status)).length})` },
          { key: "STANDBY", label: `Standby (${missions.filter((m) => m.status === "STANDBY").length})` },
          { key: "BLOCKED", label: `Blocked (${missions.filter((m) => m.status === "BLOCKED" || m.status === "HALTED").length})` },
          { key: "ARCHIVED", label: formatArchivedMissionLabel(archivedCount) },
        ];
        const statusValue = searchParams.get("status");
        const candidateValue = searchParams.get("candidate");

        return (
    <div className="shell-content">
      <SectionHeader
        kicker="Mission Vectors"
        title="Mission Vectors"
        description={
          sourceCopy(sourceMode, {
            live: "Live missions from the canonical append-only ledger projection. New missions enter as authenticated owner GOAL directives.",
            simulated: "Simulated JARVIS missions for tests and demos across verification cycles, hybrid workers, memory reconciliation, recovery drills, and evidence gates.",
            unavailable: `Mission source ${sourceModeLabel(sourceMode)}: canonical mission state is unavailable and no fixture fallback is shown.`,
          })
        }
        actions={
          <>
            <button type="button" className="btn btn-primary" onClick={() => setDialog("new")} disabled={!mutationsEnabled} title={!mutationsEnabled ? "Unsafe while STOP active or disconnected" : undefined}>
              <Plus size={14} />
              New Mission
            </button>
            <button type="button" className="btn" onClick={() => setDialog("import")} disabled={!mutationsEnabled || live} title={live ? "Import not available in live mode" : !mutationsEnabled ? "Frozen during STOP or disconnect" : undefined}>
              <Upload size={14} />
              Import Directive
            </button>
          </>
        }
      />

      {statusValue || candidateValue ? (
        <div className="context-row">
          {statusValue ? (
            <ContextChip
              label={`Mission status: ${statusValue.toUpperCase()}`}
              onClear={() => {
                const next = new URLSearchParams(searchParams);
                next.delete("status");
                setSearchParams(next, { replace: true });
                setFilter("ALL");
              }}
            />
          ) : null}
          {candidateValue ? (
            <ContextChip
              label={`Candidate ${candidateValue}`}
              onClear={() => {
                const next = new URLSearchParams(searchParams);
                next.delete("candidate");
                setSearchParams(next, { replace: true });
                setPage(1);
              }}
            />
          ) : null}
        </div>
      ) : null}

      <div className="missions-toolbar">
        <div className="segmented" role="group" aria-label="Mission filters">
          {filters.map((f) => (
            <button
              key={f.key}
              type="button"
              className={`segmented-item ${filter === f.key ? "segmented-item-active" : ""}`}
              aria-pressed={filter === f.key}
              onClick={() => {
                setFilter(f.key);
                setPage(1);
                if (statusParam[searchParams.get("status") ?? ""] === f.key) {
                  setSearchParams({}, { replace: true });
                }
              }}
            >
              <span className="t-label">{f.label}</span>
            </button>
          ))}
        </div>
        <div className="view-toggle" role="group" aria-label="View mode">
          <button
            type="button"
            className={`icon-btn ${view === "grid" ? "view-toggle-active" : ""}`}
            aria-label="Grid view"
            aria-pressed={view === "grid"}
            onClick={() => setView("grid")}
          >
            <Grid3X3 size={15} />
          </button>
          <button
            type="button"
            className={`icon-btn ${view === "list" ? "view-toggle-active" : ""}`}
            aria-label="List view"
            aria-pressed={view === "list"}
            onClick={() => setView("list")}
          >
            <List size={15} />
          </button>
        </div>
      </div>

      {rows.length === 0 ? (
        <div className="panel panel-corner">
          <EmptyState label="No missions" detail={sourceMode === "SIMULATED" ? "No simulated missions match this view. Reset the filter to return to the full universe." : sourceMode === "LIVE" ? "No canonical missions match this view. Reset the filter to return to the full ledger projection." : `Mission data is unavailable: runtime source is ${sourceMode}.`} />
        </div>
      ) : (
        <div className={view === "grid" ? "mission-grid" : "mission-list"}>
          {rows.map((m) => (
            <Link
              key={m.id}
              to={`/missions/${m.id}`}
              className="mission-card panel panel-corner"
              aria-describedby={`tt-open-${m.id}`}
            >
              <div className="mission-card-head">
                <div>
                  <span className="t-mono mission-card-code">{m.code}</span>
                  <h2 className="mission-card-title">{m.title}</h2>
                </div>
                <StatusBadge status={m.status} pulse={m.status === "ACTIVE"} />
              </div>
              <div className="mission-card-stats">
                <div className="mission-card-stat">
                  <span className="t-label text-muted">Priority</span>
                  <span className={`t-mono ${m.priority === "CRITICAL" || m.priority === "HIGH" ? "mission-priority-high" : ""}`}>
                    {m.priority}
                  </span>
                </div>
                <div className="mission-card-stat">
                  <span className="t-label text-muted">Agents</span>
                  <span className="t-mono">{m.agentsActive} active</span>
                </div>
                <div className="mission-card-stat">
                  <span className="t-label text-muted">Stage</span>
                  <span className="t-mono">{m.stage}</span>
                </div>
              </div>
              <div className="mission-card-progress">
                <div className="mission-card-progress-head">
                  <span className="t-label text-muted">Completion</span>
                  <span className="t-mono mission-card-progress-value">{m.completion}%</span>
                </div>
                <div className="meter">
                  <div className={`meter-fill ${m.status === "HALTED" || m.status === "BLOCKED" ? "meter-fill-amber" : ""}`} style={{ width: `${m.completion}%` }} />
                </div>
              </div>
              <div className="mission-card-action">
                <span className="t-label text-muted">Next action</span>
                <div className="mission-card-action-row">
                  <span className="mission-card-next">{m.nextAction}</span>
                  <Tooltip label={`Open mission ${m.code}`} id={`tt-open-${m.id}`}>
                    <span className="mission-open-arrow" aria-hidden="true">
                      <ArrowRight size={14} />
                    </span>
                  </Tooltip>
                </div>
              </div>
            </Link>
          ))}
        </div>
      )}

      <Pagination
        page={safePage}
        pageCount={pageCount}
        onPage={setPage}
        showingLabel={`Showing ${(safePage - 1) * PAGE_SIZE + 1}-${Math.min(safePage * PAGE_SIZE, filtered.length)} of ${filtered.length} missions`}
      />

      <Dialog
        open={dialog === "new"}
        title={live ? sourceMode === "LIVE" ? "New Mission - Owner Goal" : `New Mission - ${sourceModeLabel(sourceMode)}` : "New Mission - Simulated"}
        onClose={() => {
          setDialog(null);
          setSubmitted(false);
          setSubmitError(null);
        }}
        footer={
          submitted ? (
            <span className="t-mono text-muted">
              {live ? "OWNER GOAL RECORDED ON CANONICAL LEDGER" : "MISSION PACKET PREPARED LOCALLY - NO RUNTIME EFFECT"}
            </span>
          ) : (
            <button type="submit" form="new-mission-form" className="btn btn-primary" disabled={!mutationsEnabled && live}>
              {live ? "Submit owner goal" : "Prepare mission"}
            </button>
          )
        }
      >
        {submitted ? (
          <div className="state-block">
            <span className="t-label">{live ? "Owner goal recorded" : "Simulated mission prepared"}</span>
            <p className="prose">
              {live
                ? "The GOAL directive entered the authenticated owner directive ledger. Mission creation is owned by the Supervisor executive path."
                : "This candidate does not create real missions. Live mode submits through the authenticated directive system."}
            </p>
          </div>
        ) : (
          <form id="new-mission-form" className="sim-form" onSubmit={submitMission}>
            <label className="sim-field">
              <span className="t-label text-muted">Objective</span>
              <input className="field" name="objective" placeholder="e.g. Verification cycle for candidate X" required />
            </label>
            {!live ? (
              <label className="sim-field">
                <span className="t-label text-muted">Priority</span>
                <select className="field" name="priority" defaultValue="NORMAL">
                  <option>NORMAL</option>
                  <option>ELEVATED</option>
                  <option>HIGH</option>
                  <option>CRITICAL</option>
                </select>
              </label>
            ) : null}
            {submitError ? <p className="prose sim-note" role="alert">{submitError}</p> : null}
            <p className="prose sim-note">
              {live ? sourceMode === "LIVE" ? "Submits GOAL through /api/owner-command on localhost." : `Owner goal submission unavailable while source is ${sourceModeLabel(sourceMode)}.` : "Simulated form. No mission is created in any runtime store."}
            </p>
          </form>
        )}
      </Dialog>

      <Dialog
        open={dialog === "import"}
        title="Import Directive - Simulated"
        onClose={() => {
          setDialog(null);
          setSubmitted(false);
        }}
        footer={
          submitted ? (
            <span className="t-mono text-muted">DIRECTIVE QUEUED LOCALLY - NO RUNTIME EFFECT</span>
          ) : (
            <button type="button" className="btn" onClick={() => setSubmitted(true)}>
              Queue import
            </button>
          )
        }
      >
        {submitted ? (
          <div className="state-block">
            <span className="t-label">Directive captured locally</span>
            <p className="prose">The future runtime adapter will map imported directives to the authenticated directive system.</p>
          </div>
        ) : (
          <div className="import-zone">
            <Upload size={26} aria-hidden="true" />
            <p className="prose">Drop a directive packet or paste its reference here. Local capture only.</p>
          </div>
        )}
      </Dialog>
    </div>
        );
      }}
    </RouteQueryGate>
  );
}
