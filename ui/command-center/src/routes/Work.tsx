import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ArrowRight, CircleAlert, FolderKanban, GitBranch, ListChecks } from "lucide-react";
import { RouteQueryGate } from "../components/RouteQueryGate";
import { useCommandCenter } from "../context/CommandCenterContext";
import { useRouteQuery } from "../hooks/useRouteQuery";
import type {
  Mission,
  WorkCampaign,
  WorkHierarchy,
  WorkHierarchyLevelState,
  WorkMissionLink,
  WorkObjective,
} from "../lib/contracts";
import { sourceModeLabel } from "../lib/sourceMode";

type WorkFilter = "active" | "waiting" | "attention" | "completed" | "all";

type WorkData = {
  missions: Mission[];
  hierarchy: WorkHierarchy | null;
};

function matches(mission: Mission, filter: WorkFilter): boolean {
  if (filter === "active") return ["ACTIVE", "IN_PROGRESS", "INTERVENTION"].includes(mission.status);
  if (filter === "waiting") return ["STANDBY", "HALTED"].includes(mission.status);
  if (filter === "attention") return ["BLOCKED", "INTERVENTION", "HALTED"].includes(mission.status);
  if (filter === "completed") return mission.status === "COMPLETE";
  return true;
}

function levelLabel(state: WorkHierarchyLevelState | undefined): string {
  if (state === "AVAILABLE") return "Canonical projection";
  if (state === "EMPTY") return "No current records";
  if (state === "NOT_YET_INTEGRATED") return "NOT YET INTEGRATED";
  return "UNAVAILABLE";
}

function hierarchyState(
  hierarchy: WorkHierarchy | null,
  level: string,
): WorkHierarchyLevelState {
  if (hierarchy) return hierarchy.levels[level] ?? "UNAVAILABLE";
  if (level === "objectives" || level === "missions") return "AVAILABLE";
  return "NOT_YET_INTEGRATED";
}

function relationshipText(link: WorkMissionLink | undefined): string {
  if (!link) return "Hierarchy link unavailable";
  return [
    `${link.action_ids.length} action${link.action_ids.length === 1 ? "" : "s"}`,
    `${link.worker_ids.length} worker${link.worker_ids.length === 1 ? "" : "s"}`,
    `${link.evidence_refs.length} evidence`,
    `${link.verdict_ids.length} verdict${link.verdict_ids.length === 1 ? "" : "s"}`,
  ].join(" · ");
}

function MissionRows({
  rows,
  links,
}: {
  rows: Mission[];
  links: Map<string, WorkMissionLink>;
}) {
  return (
    <div className="rm-mission-rows">
      {rows.map((mission) => {
        const link = links.get(mission.id);
        return (
          <Link key={mission.id} to={`/missions/${mission.id}`} className={`rm-mission-row rm-mission-row--${mission.status.toLowerCase()}`}>
            <span className="rm-mission-status"><span />{mission.status.replace("_", " ")}</span>
            <div><strong>{mission.title}</strong><small>{mission.id} · {mission.owner}</small></div>
            <div className="rm-mission-phase"><span>{mission.stage || "UNKNOWN"}</span><small>{mission.nextAction || "Next action unknown"}</small></div>
            <div className="rm-mission-proof" aria-label={`Recorded proof links for ${mission.id}`}>
              <span>{relationshipText(link)}</span>
              <small>{link?.source_event_id ? `source ${link.source_event_id}` : "source event unavailable"}</small>
            </div>
            <ArrowRight size={16} aria-hidden="true" />
          </Link>
        );
      })}
    </div>
  );
}

function CampaignBlock({
  campaign,
  missions,
  links,
  params,
}: {
  campaign: WorkCampaign;
  missions: Mission[];
  links: Map<string, WorkMissionLink>;
  params: URLSearchParams;
}) {
  const next = new URLSearchParams(params);
  next.set("status", "all");
  next.set("campaign", campaign.campaign_id);
  const wait = campaign.wait_kinds.length
    ? ` · waiting: ${campaign.wait_kinds.join(", ")}`
    : "";
  return (
    <div className="rm-campaign-block" id={`campaign-${campaign.campaign_id}`}>
      <div className="rm-campaign-head">
        <div>
          <Link to={{ pathname: "/work", search: next.toString(), hash: `campaign-${campaign.campaign_id}` }}>
            {campaign.campaign_id}
          </Link>
          <small>{campaign.status}{wait}</small>
        </div>
        <span>{campaign.child_count} children · {campaign.pending_count} pending · {campaign.action_ids.length} actions</span>
      </div>
      {missions.length ? (
        <MissionRows rows={missions} links={links} />
      ) : (
        <p className="rm-campaign-empty">No mission in this campaign matches the selected status filter.</p>
      )}
    </div>
  );
}

function WorkContent({ missions, hierarchy }: WorkData) {
  const { sourceMode } = useCommandCenter();
  const [params, setParams] = useSearchParams();
  const fromUrl = params.get("status") as WorkFilter | null;
  const [localFilter, setLocalFilter] = useState<WorkFilter>(fromUrl && ["active", "waiting", "attention", "completed", "all"].includes(fromUrl) ? fromUrl : "active");
  const filter = fromUrl && ["active", "waiting", "attention", "completed", "all"].includes(fromUrl) ? fromUrl : localFilter;
  const selectedObjective = params.get("objective");
  const selectedCampaign = params.get("campaign");
  const filtered = missions.filter((mission) => matches(mission, filter));
  const missionById = useMemo(() => new Map(missions.map((mission) => [mission.id, mission])), [missions]);
  const missionLinks = useMemo(
    () => new Map((hierarchy?.missions ?? []).map((mission) => [mission.mission_id, mission])),
    [hierarchy],
  );
  const campaigns = useMemo(
    () => new Map((hierarchy?.campaigns ?? []).map((campaign) => [campaign.campaign_id, campaign])),
    [hierarchy],
  );
  const goals = useMemo(
    () => new Map((hierarchy?.goals ?? []).map((goal) => [goal.goal_id, goal])),
    [hierarchy],
  );
  const projects = useMemo(
    () => new Map((hierarchy?.projects ?? []).map((project) => [project.project_id, project])),
    [hierarchy],
  );
  const objectives = useMemo(() => {
    const sourceObjectives = hierarchy?.objectives ?? [];
    if (sourceObjectives.length) return sourceObjectives;
    const grouped = new Map<string, Mission[]>();
    for (const mission of filtered) {
      const key = mission.objective?.trim() || "Objective not recorded";
      grouped.set(key, [...(grouped.get(key) ?? []), mission]);
    }
    return [...grouped.entries()].map(([label, rows], index): WorkObjective => ({
      objective_id: `mission-recorded-objective-${index + 1}`,
      label,
      status: "UNAVAILABLE",
      campaign_ids: [],
      mission_ids: rows.map((mission) => mission.id),
      plan_ids: [],
      action_ids: [],
      progress_evidence: { status: "UNAVAILABLE", evidence_refs: [] },
    }));
  }, [filtered, hierarchy]);
  const visibleObjectives = objectives.filter((objective) => (
    (!selectedObjective || objective.objective_id === selectedObjective)
    && (!selectedCampaign || objective.campaign_ids.includes(selectedCampaign))
  ));
  const assignedMissionIds = new Set((hierarchy?.missions ?? [])
    .filter((mission) => mission.objective_id)
    .map((mission) => mission.mission_id));
  const unlinkedMissions = hierarchy
    ? filtered.filter((mission) => !assignedMissionIds.has(mission.id))
    : [];
  const filters: Array<{ key: WorkFilter; label: string }> = [
    { key: "active", label: "Active" }, { key: "waiting", label: "Waiting" },
    { key: "attention", label: "Needs owner" }, { key: "completed", label: "Completed" }, { key: "all", label: "All" },
  ];

  return (
    <div className="shell-content rm-page rm-work">
      <header className="rm-page-head">
        <div><h1>Work</h1><p>Owner goals through evidence, joined only where canonical identities exist.</p></div>
        <div className="rm-page-source"><span>{sourceModeLabel(sourceMode)}</span><small>{missions.length} mission records · {hierarchy?.status ?? "UNAVAILABLE"} hierarchy</small></div>
      </header>

      <div className="rm-hierarchy-map" aria-label="Work hierarchy contract">
        {[
          ["goals", "Goal"],
          ["projects", "Project"],
          ["objectives", "Objective"],
          ["campaigns", "Campaign"],
          ["missions", "Mission"],
          ["actions", "Action / proof"],
        ].map(([key, label], index, all) => {
          const state = hierarchyState(hierarchy, key);
          return (
            <span className="rm-hierarchy-step" key={key}>
              <div className={state === "AVAILABLE" ? "is-available" : ""}><span>{label}</span><strong>{levelLabel(state)}</strong></div>
              {index < all.length - 1 ? <ArrowRight size={14} /> : null}
            </span>
          );
        })}
      </div>

      <div className="rm-filter-row" role="group" aria-label="Filter work">
        {filters.map((item) => (
          <button key={item.key} type="button" className={filter === item.key ? "is-active" : ""} aria-pressed={filter === item.key} onClick={() => {
            setLocalFilter(item.key);
            const next = new URLSearchParams(params); if (item.key === "active") next.delete("status"); else next.set("status", item.key); next.delete("objective"); next.delete("campaign"); setParams(next, { replace: true });
          }}>{item.label}<span>{missions.filter((mission) => matches(mission, item.key)).length}</span></button>
        ))}
      </div>

      {visibleObjectives.length ? (
        <div className="rm-objective-list">
          {visibleObjectives.map((objective, objectiveIndex) => {
            const objectiveMissions = objective.mission_ids
              .map((missionId) => missionById.get(missionId))
              .filter((mission): mission is Mission => Boolean(mission && matches(mission, filter)));
            const objectiveCampaigns = objective.campaign_ids
              .map((campaignId) => campaigns.get(campaignId))
              .filter((campaign): campaign is WorkCampaign => Boolean(campaign))
              .filter((campaign) => !selectedCampaign || campaign.campaign_id === selectedCampaign);
            const campaignMissionIds = new Set(objectiveCampaigns.flatMap((campaign) => campaign.mission_ids));
            const directMissions = objectiveMissions.filter((mission) => !campaignMissionIds.has(mission.id));
            const goal = objective.goal_id ? goals.get(objective.goal_id) : null;
            const project = objective.project_id ? projects.get(objective.project_id) : null;
            const next = new URLSearchParams(params);
            next.set("status", "all");
            next.set("objective", objective.objective_id);
            next.delete("campaign");
            return (
              <section key={objective.objective_id} className="rm-objective" aria-labelledby={`objective-${objectiveIndex}`}>
                <div className="rm-objective-head">
                  <span className="rm-objective-index">O{String(objectiveIndex + 1).padStart(2, "0")}</span>
                  <div>
                    <h2 id={`objective-${objectiveIndex}`}>
                      <Link to={{ pathname: "/work", search: next.toString() }}>{objective.label}</Link>
                    </h2>
                    <p>{objective.objective_id} · {objective.status}</p>
                    <div className="rm-objective-lineage">
                      <span>Goal: {goal?.label ?? "NOT YET INTEGRATED"}</span>
                      <span>Project: {project?.label ?? "NOT YET INTEGRATED"}</span>
                      <span>Progress evidence: {objective.progress_evidence.status}</span>
                    </div>
                  </div>
                </div>
                <div className="rm-objective-work">
                  {objectiveCampaigns.map((campaign) => (
                    <CampaignBlock
                      key={campaign.campaign_id}
                      campaign={campaign}
                      missions={campaign.mission_ids
                        .map((missionId) => missionById.get(missionId))
                        .filter((mission): mission is Mission => Boolean(mission && matches(mission, filter)))}
                      links={missionLinks}
                      params={params}
                    />
                  ))}
                  {directMissions.length ? (
                    <div className="rm-campaign-block">
                      <div className="rm-campaign-head"><div><strong>Direct missions</strong><small>No campaign identity recorded</small></div><span>{directMissions.length} mission{directMissions.length === 1 ? "" : "s"}</span></div>
                      <MissionRows rows={directMissions} links={missionLinks} />
                    </div>
                  ) : null}
                  {!objectiveCampaigns.length && !directMissions.length ? (
                    <p className="rm-campaign-empty">No mission under this objective matches the selected status filter.</p>
                  ) : null}
                </div>
              </section>
            );
          })}
          {unlinkedMissions.length && !selectedObjective && !selectedCampaign ? (
            <section className="rm-objective" aria-labelledby="objective-unlinked">
              <div className="rm-objective-head">
                <span className="rm-objective-index">UN</span>
                <div><h2 id="objective-unlinked">Unlinked mission records</h2><p>No explicit Executive objective or campaign identity is published.</p></div>
              </div>
              <MissionRows rows={unlinkedMissions} links={missionLinks} />
            </section>
          ) : null}
        </div>
      ) : (
        <div className="rm-page-empty"><FolderKanban size={22} /><h2>No work in this view</h2><p>{sourceMode === "LIVE" ? "No canonical work matches this filter or hierarchy selection." : "No simulated mission matches this filter."}</p></div>
      )}

      <aside className="rm-work-footnote">
        <GitBranch size={17} /><div><strong>Hierarchy truth boundary</strong><p>Missing programs, projects, or links stay unavailable. Mission detail carries action, worker, evidence, verifier, and repair drill-down.</p></div>
        <Link to="/missions">Mission forensics <ListChecks size={15} /></Link>
      </aside>
      {filtered.some((mission) => ["BLOCKED", "HALTED", "INTERVENTION"].includes(mission.status)) ? <span className="rm-sr-status"><CircleAlert size={14} />This view contains work needing attention.</span> : null}
    </div>
  );
}

export function Work() {
  const { client, version } = useCommandCenter();
  const query = useRouteQuery(async (): Promise<WorkData> => {
    const [missions, hierarchy] = await Promise.all([
      client.listMissions(),
      client.getWorkHierarchy?.() ?? Promise.resolve(null),
    ]);
    return { missions, hierarchy };
  }, [client, version], { keepPreviousWhileLoading: true });
  return <RouteQueryGate query={query} loadingLabel="Loading work" errorLabel="Work unavailable" surface="Work hierarchy projection">{(data) => <WorkContent {...data} />}</RouteQueryGate>;
}
