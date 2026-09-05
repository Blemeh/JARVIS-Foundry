import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ArrowRight, CircleAlert, FolderKanban, GitBranch, ListChecks } from "lucide-react";
import { RouteQueryGate } from "../components/RouteQueryGate";
import { useCommandCenter } from "../context/CommandCenterContext";
import { useRouteQuery } from "../hooks/useRouteQuery";
import type { Mission } from "../lib/contracts";
import { sourceModeLabel } from "../lib/sourceMode";

type WorkFilter = "active" | "waiting" | "attention" | "completed" | "all";

function matches(mission: Mission, filter: WorkFilter): boolean {
  if (filter === "active") return ["ACTIVE", "IN_PROGRESS", "INTERVENTION"].includes(mission.status);
  if (filter === "waiting") return ["STANDBY", "HALTED"].includes(mission.status);
  if (filter === "attention") return ["BLOCKED", "INTERVENTION", "HALTED"].includes(mission.status);
  if (filter === "completed") return mission.status === "COMPLETE";
  return true;
}

function WorkContent({ missions }: { missions: Mission[] }) {
  const { sourceMode } = useCommandCenter();
  const [params, setParams] = useSearchParams();
  const fromUrl = params.get("status") as WorkFilter | null;
  const [localFilter, setLocalFilter] = useState<WorkFilter>(fromUrl && ["active", "waiting", "attention", "completed", "all"].includes(fromUrl) ? fromUrl : "active");
  const filter = fromUrl && ["active", "waiting", "attention", "completed", "all"].includes(fromUrl) ? fromUrl : localFilter;
  const filtered = missions.filter((mission) => matches(mission, filter));
  const objectives = useMemo(() => {
    const grouped = new Map<string, Mission[]>();
    for (const mission of filtered) {
      const key = mission.objective?.trim() || "Objective not recorded";
      grouped.set(key, [...(grouped.get(key) ?? []), mission]);
    }
    return [...grouped.entries()];
  }, [filtered]);
  const filters: Array<{ key: WorkFilter; label: string }> = [
    { key: "active", label: "Active" }, { key: "waiting", label: "Waiting" },
    { key: "attention", label: "Needs owner" }, { key: "completed", label: "Completed" }, { key: "all", label: "All" },
  ];

  return (
    <div className="shell-content rm-page rm-work">
      <header className="rm-page-head">
        <div><h1>Work</h1><p>Durable missions organized by their recorded objective. Missing goal and project links stay visibly unresolved.</p></div>
        <div className="rm-page-source"><span>{sourceModeLabel(sourceMode)}</span><small>{missions.length} mission records</small></div>
      </header>

      <div className="rm-hierarchy-map" aria-label="Work hierarchy contract">
        <div><span>Goal</span><strong>NOT YET INTEGRATED</strong></div><ArrowRight size={14} />
        <div><span>Project</span><strong>NOT YET INTEGRATED</strong></div><ArrowRight size={14} />
        <div className="is-available"><span>Objective</span><strong>Recorded on mission</strong></div><ArrowRight size={14} />
        <div className="is-available"><span>Mission</span><strong>Canonical projection</strong></div><ArrowRight size={14} />
        <div><span>Task</span><strong>Open mission detail</strong></div>
      </div>

      <div className="rm-filter-row" role="group" aria-label="Filter work">
        {filters.map((item) => (
          <button key={item.key} type="button" className={filter === item.key ? "is-active" : ""} aria-pressed={filter === item.key} onClick={() => {
            setLocalFilter(item.key);
            const next = new URLSearchParams(params); if (item.key === "active") next.delete("status"); else next.set("status", item.key); setParams(next, { replace: true });
          }}>{item.label}<span>{missions.filter((mission) => matches(mission, item.key)).length}</span></button>
        ))}
      </div>

      {objectives.length ? (
        <div className="rm-objective-list">
          {objectives.map(([objective, rows], objectiveIndex) => (
            <section key={objective} className="rm-objective" aria-labelledby={`objective-${objectiveIndex}`}>
              <div className="rm-objective-head">
                <span className="rm-objective-index">O{String(objectiveIndex + 1).padStart(2, "0")}</span>
                <div><h2 id={`objective-${objectiveIndex}`}>{objective}</h2><p>{rows.length} mission{rows.length === 1 ? "" : "s"} · source field: mission.objective</p></div>
              </div>
              <div className="rm-mission-rows">
                {rows.map((mission) => (
                  <Link key={mission.id} to={`/missions/${mission.id}`} className={`rm-mission-row rm-mission-row--${mission.status.toLowerCase()}`}>
                    <span className="rm-mission-status"><span />{mission.status.replace("_", " ")}</span>
                    <div><strong>{mission.title}</strong><small>{mission.id} · {mission.owner}</small></div>
                    <div className="rm-mission-phase"><span>{mission.stage || "UNKNOWN"}</span><small>{mission.nextAction || "Next action unknown"}</small></div>
                    <div className="rm-mission-progress" aria-label={`${mission.completion}% reported completion`}><span style={{ width: `${Math.max(0, Math.min(100, mission.completion))}%` }} /><small>{mission.completion}%</small></div>
                    <ArrowRight size={16} aria-hidden="true" />
                  </Link>
                ))}
              </div>
            </section>
          ))}
        </div>
      ) : (
        <div className="rm-page-empty"><FolderKanban size={22} /><h2>No work in this view</h2><p>{sourceMode === "LIVE" ? "No canonical mission matches this filter." : "No simulated mission matches this filter."}</p></div>
      )}

      <aside className="rm-work-footnote">
        <GitBranch size={17} /><div><strong>Hierarchy truth boundary</strong><p>Command Center does not infer goals or projects from titles. Those levels remain unavailable until the canonical portfolio projection publishes explicit links.</p></div>
        <Link to="/missions">Mission forensics <ListChecks size={15} /></Link>
      </aside>
      {filtered.some((mission) => ["BLOCKED", "HALTED", "INTERVENTION"].includes(mission.status)) ? <span className="rm-sr-status"><CircleAlert size={14} />This view contains work needing attention.</span> : null}
    </div>
  );
}

export function Work() {
  const { client, version } = useCommandCenter();
  const query = useRouteQuery(() => client.listMissions(), [client, version], { keepPreviousWhileLoading: true });
  return <RouteQueryGate query={query} loadingLabel="Loading work" errorLabel="Work unavailable" surface="Mission projection">{(missions) => <WorkContent missions={missions} />}</RouteQueryGate>;
}
