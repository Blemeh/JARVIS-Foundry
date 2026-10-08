import { useMemo, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ArrowRight, Grid3X3, List, Plus, Search, Upload } from "lucide-react";
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
import type { Mission } from "../lib/contracts";
import { isHistoricalMission, missionDirectiveId, missionUpdatedAt } from "../lib/ownerWork";
import { sourceCopy, sourceModeLabel } from "../lib/sourceMode";
import { useLocale } from "../i18n/LocaleContext";

type Filter = "ALL" | "ACTIVE" | "STANDBY" | "BLOCKED" | "ARCHIVED";
type View = "grid" | "list";
type SortKey = "priority" | "status" | "title";

const PAGE_SIZE = 6;

const statusParam: Record<string, Filter> = {
  active: "ACTIVE",
  standby: "STANDBY",
  blocked: "BLOCKED",
};

const EXECUTING_MISSION_STATUSES = new Set(["ACTIVE", "IN_PROGRESS", "INTERVENTION"]);

/**
 * Recency gate for COMPLETE lineage lending (spec §2 7-day heuristic).
 *
 * The window is relative: a COMPLETE row lends only when its timestamp is
 * within 7 days of the NEWEST loaded mission's timestamp, so the check is
 * deterministic with no wall clock (no `Date.now()`).
 */
export const COMPLETE_LENDING_WINDOW_DAYS = 7;
const COMPLETE_LENDING_WINDOW_MS = COMPLETE_LENDING_WINDOW_DAYS * 24 * 60 * 60 * 1000;

/**
 * Historical flags for the Missions list (Task 5 fallback linkage).
 *
 * This route loads only `listMissions`: no owner-surface, decision-queue, or
 * work-hierarchy probes are added here by design, so active-goal ids are
 * unavailable. Sibling rows lend their directive lineages as the active-goal
 * set with a recency gate: EXECUTING rows (ACTIVE/IN_PROGRESS/INTERVENTION)
 * lend unconditionally, while a COMPLETE row lends ONLY when its
 * `missionUpdatedAt` value (`updated_at`, `startedAt` fallback — same
 * semantics as the sort) is within COMPLETE_LENDING_WINDOW_DAYS of the
 * newest loaded mission's `missionUpdatedAt`. A stale COMPLETE (e.g. 30+
 * days older than newest) therefore stops shielding a same-root BLOCKED,
 * which then correctly reads historical; without the gate any stale
 * COMPLETE carrying a short root would shield same-root BLOCKED rows
 * forever (substring `includes` matching amplifies the stale link). Rows
 * with an unparsable timestamp never lend as COMPLETE (fail closed).
 * Terminal-state + CI-marker rules delegate to Task 1
 * `isHistoricalMission`. A hierarchy-only link (no `created_from`/owner
 * lineage match) still reads historical here; that limitation is recorded
 * in the task report.
 */
export function historicalMissionsRowFlags(missions: readonly Mission[]): Map<string, boolean> {
  let newestLoadedAt = 0;
  for (const mission of missions) {
    const parsed = Date.parse(missionUpdatedAt(mission));
    if (Number.isFinite(parsed) && parsed > newestLoadedAt) newestLoadedAt = parsed;
  }
  const liveLineages = new Set<string>();
  for (const mission of missions) {
    if (EXECUTING_MISSION_STATUSES.has(mission.status)) {
      const lineage = missionDirectiveId(mission);
      if (lineage) liveLineages.add(lineage);
    } else if (mission.status === "COMPLETE") {
      const lineage = missionDirectiveId(mission);
      if (!lineage) continue;
      const parsed = Date.parse(missionUpdatedAt(mission));
      if (!Number.isFinite(parsed)) continue;
      if (newestLoadedAt - parsed <= COMPLETE_LENDING_WINDOW_MS) liveLineages.add(lineage);
    }
  }
  const activeGoalIds = [...liveLineages];
  return new Map(missions.map((mission) => [mission.id, isHistoricalMission(mission, activeGoalIds, [])]));
}

/**
 * Owner-priority band for the default (sort=priority) order: executing (0) →
 * attention (1) → recent completed (2) → other parked states (3) →
 * historical last (4). Band 3 holds non-historical parked rows matched by no
 * earlier band (e.g. STANDBY, or a linked CANCELLED that escapes history via
 * lineage) — the brief's shorthand "recent completed → historical" folds this
 * band in, but the code orders it explicitly between them. Historical rows
 * never change status; the band only orders them last so current work wins.
 */
export function missionOwnerBand(mission: Mission, historical: boolean): number {
  if (historical) return 4;
  if (EXECUTING_MISSION_STATUSES.has(mission.status)) return 0;
  if (mission.status === "BLOCKED" || mission.status === "HALTED") return 1;
  if (mission.status === "COMPLETE") return 2;
  return 3;
}

function missionRecencyValue(mission: Mission): number {
  const parsed = Date.parse(missionUpdatedAt(mission));
  return Number.isFinite(parsed) ? parsed : 0;
}

/**
 * Default-sort comparator: owner-priority band first, then `updated_at`
 * (`startedAt` fallback) DESC, then id for determinism. Explicit
 * sort=title/status comparators are untouched.
 */
export function compareMissionsByOwnerPriority(
  a: Mission,
  b: Mission,
  historicalOf: (mission: Mission) => boolean,
): number {
  const band = missionOwnerBand(a, historicalOf(a)) - missionOwnerBand(b, historicalOf(b));
  if (band !== 0) return band;
  const recency = missionRecencyValue(b) - missionRecencyValue(a);
  if (recency !== 0) return recency;
  return a.id.localeCompare(b.id);
}

export function MissionsContent({ missions, onReload }: { missions: Mission[]; onReload?: () => void }) {
  const { client, mode, mutationsEnabled, sourceMode } = useCommandCenter();
  const { t } = useLocale();
  const [searchParams, setSearchParams] = useSearchParams();
  const statusFromUrl = statusParam[searchParams.get("status") ?? ""] ?? "ALL";
  const [filter, setFilter] = useState<Filter>(statusFromUrl);
  const [view, setView] = useState<View>((searchParams.get("view") as View) === "list" ? "list" : "grid");
  const [page, setPage] = useState(1);
  const [query, setQuery] = useState(searchParams.get("q") ?? "");
  const [sort, setSort] = useState<SortKey>((searchParams.get("sort") as SortKey) || "priority");
  const [dialog, setDialog] = useState<"new" | "import" | null>(null);
  const [submitted, setSubmitted] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const live = mode === "live";
  const activeFilter = searchParams.get("status") ? statusFromUrl : filter;

  const historicalFlags = useMemo(() => historicalMissionsRowFlags(missions), [missions]);

  const filtered = useMemo(() => {
    const candidateFilter = searchParams.get("candidate")?.toLowerCase();
    const q = query.trim().toLowerCase();
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
    if (q) {
      rows = rows.filter(
        (m) =>
          `${m.id} ${m.code} ${m.title} ${m.stage} ${m.status} ${m.nextAction ?? ""} ${m.owner ?? ""}`
            .toLowerCase()
            .includes(q),
      );
    }
    if (activeFilter === "ACTIVE") rows = rows.filter((m) => m.status === "ACTIVE" || m.status === "IN_PROGRESS" || m.status === "INTERVENTION");
    else if (activeFilter === "STANDBY") rows = rows.filter((m) => m.status === "STANDBY");
    else if (activeFilter === "BLOCKED") rows = rows.filter((m) => m.status === "BLOCKED" || m.status === "HALTED");
    else if (activeFilter === "ARCHIVED") rows = rows.filter((m) => m.status === "COMPLETE");
    else if (activeFilter !== "ALL") rows = [];

    return [...rows].sort((a, b) => {
      if (sort === "title") return a.title.localeCompare(b.title);
      if (sort === "status") return a.status.localeCompare(b.status);
      return compareMissionsByOwnerPriority(a, b, (mission) => historicalFlags.get(mission.id) ?? false);
    });
  }, [missions, historicalFlags, activeFilter, searchParams, query, sort]);

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
        onReload?.();
      } else {
        setSubmitError(result.error ?? "owner goal rejected");
      }
      return;
    }
    setSubmitted(true);
  };

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
    <div className="shell-content cinematic-surface">
      <SectionHeader
        kicker="Mission Vectors"
        title={t("page.missions")}
        activeMissionCount={missions.filter((m) => m.status === "ACTIVE" || m.status === "IN_PROGRESS").length}
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

      {statusValue || candidateValue || query ? (
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
          {query ? (
            <ContextChip
              label={`Search "${query}"`}
              onClear={() => {
                setQuery("");
                const next = new URLSearchParams(searchParams);
                next.delete("q");
                setSearchParams(next, { replace: true });
                setPage(1);
              }}
            />
          ) : null}
        </div>
      ) : null}

      <div className="missions-toolbar missions-toolbar-extended">
        <div className="missions-search">
          <Search size={14} className="text-muted" aria-hidden="true" />
          <input
            className="field missions-search-input"
            placeholder="Search missions by id, title, stage, owner..."
            value={query}
            onChange={(e) => {
              const value = e.target.value;
              setQuery(value);
              setPage(1);
              const next = new URLSearchParams(searchParams);
              if (value.trim()) next.set("q", value);
              else next.delete("q");
              setSearchParams(next, { replace: true });
            }}
            aria-label="Search missions"
          />
        </div>
        <label className="missions-sort">
          <span className="t-label text-muted">Sort</span>
          <select
            className="field"
            value={sort}
            onChange={(e) => {
              const value = e.target.value as SortKey;
              setSort(value);
              const next = new URLSearchParams(searchParams);
              next.set("sort", value);
              setSearchParams(next, { replace: true });
            }}
            aria-label="Sort missions"
          >
            <option value="priority">Priority</option>
            <option value="status">Status</option>
            <option value="title">Title</option>
          </select>
        </label>
        <span className="t-mono text-muted missions-result-count">
          {filtered.length} of {missions.length} missions
        </span>
      </div>

      <div className="missions-toolbar">
        <div className="segmented" role="group" aria-label="Mission filters">
          {filters.map((f) => (
            <button
              key={f.key}
              type="button"
              className={`segmented-item ${activeFilter === f.key ? "segmented-item-active" : ""}`}
              aria-pressed={activeFilter === f.key}
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
            onClick={() => {
              setView("grid");
              const next = new URLSearchParams(searchParams);
              next.delete("view");
              setSearchParams(next, { replace: true });
            }}
          >
            <Grid3X3 size={15} />
          </button>
          <button
            type="button"
            className={`icon-btn ${view === "list" ? "view-toggle-active" : ""}`}
            aria-label="List view"
            aria-pressed={view === "list"}
            onClick={() => {
              setView("list");
              const next = new URLSearchParams(searchParams);
              next.set("view", "list");
              setSearchParams(next, { replace: true });
            }}
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
                {historicalFlags.get(m.id) ? <span className="badge badge-muted">Historical</span> : null}
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
                  <span className="t-mono">{m.agentsActive < 0 ? "UNKNOWN" : m.agentsActive} active</span>
                </div>
                <div className="mission-card-stat">
                  <span className="t-label text-muted">Stage</span>
                  <span className="t-mono">{m.stage}</span>
                </div>
              </div>
              <div className="mission-card-progress">
                <div className="mission-card-progress-head">
                  <span className="t-label text-muted">Completion</span>
                  <span className="t-mono mission-card-progress-value">{m.completion < 0 ? "UNKNOWN" : `${m.completion}%`}</span>
                </div>
                <div className="meter">
                  {m.completion >= 0 ? <div className={`meter-fill ${m.status === "HALTED" || m.status === "BLOCKED" || m.status === "CANCELLED" ? "meter-fill-amber" : ""}`} style={{ width: `${m.completion}%` }} /> : null}
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
}

export function Missions() {
  const { client, version } = useCommandCenter();
  const missionsQuery = useRouteQuery(
    () => client.listMissions(),
    [client, version],
  );

  return (
    <RouteQueryGate
      query={missionsQuery}
      loadingLabel="Loading missions"
      errorLabel="Missions unavailable"
      surface="Mission vectors"
    >
      {(missions) => <MissionsContent missions={missions} onReload={missionsQuery.reload} />}
    </RouteQueryGate>
  );
}
