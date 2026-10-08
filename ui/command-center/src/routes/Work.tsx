import { useMemo, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { ArrowRight, CircleAlert, FolderKanban, GitBranch, ListChecks } from "lucide-react";
import { RouteQueryGate } from "../components/RouteQueryGate";
import { useCommandCenter } from "../context/CommandCenterContext";
import { useRouteQuery } from "../hooks/useRouteQuery";
import type {
  Mission,
  WorkCampaign,
  WorkGoal,
  WorkHierarchy,
  WorkHierarchyLevelState,
  WorkMissionLink,
  WorkObjective,
  WorkProject,
} from "../lib/contracts";
import {
  currentChildForGoal,
  goalChildren,
  rankOwnerGoals,
  type OwnerStandingGoal,
} from "../lib/ownerWork";
import { sourceModeLabel } from "../lib/sourceMode";
import { useLocale } from "../i18n/LocaleContext";

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

function statusLabel(status: string): string {
  const lower = status.toLowerCase().replace(/_/g, " ");
  return lower.charAt(0).toUpperCase() + lower.slice(1);
}

function ownerGoalDirectiveId(goal: OwnerStandingGoal | null | undefined): string {
  if (!goal) return "";
  const id = goal.directive_id ?? goal.directiveId;
  return typeof id === "string" ? id.trim() : "";
}

function hierarchyGoalId(goal: OwnerStandingGoal | null | undefined): string {
  if (!goal) return "";
  const id = goal.goal_id ?? goal.goalId;
  return typeof id === "string" ? id.trim() : "";
}

/** Goal drill-down match: the `goal` param carries a directive id or a goal id. */
function objectiveMatchesGoal(objective: WorkObjective, selectedGoal: string, goals: WorkGoal[] | undefined): boolean {
  if (objective.goal_id === selectedGoal) return true;
  const goal = (goals ?? []).find((candidate) => candidate.directive_id === selectedGoal);
  return Boolean(goal && objective.goal_id === goal.goal_id);
}

function goalHeadingLabel(goal: OwnerStandingGoal): string {
  const label = typeof goal.label === "string" ? goal.label.trim() : "";
  return label && label !== "UNKNOWN" ? label : "Owner goal";
}

function goalStatusText(goal: OwnerStandingGoal): string {
  const status = typeof goal.status === "string" ? goal.status.trim() : "";
  return status || "UNKNOWN";
}

type ObjectiveSplit = {
  visibleCampaigns: WorkCampaign[];
  directMissions: Mission[];
};

function splitObjectiveMissions(
  objective: WorkObjective,
  missionById: Map<string, Mission>,
  campaigns: Map<string, WorkCampaign>,
  filter: WorkFilter,
  selectedCampaign: string | null,
): ObjectiveSplit {
  const objectiveMissions = objective.mission_ids
    .map((missionId) => missionById.get(missionId))
    .filter((mission): mission is Mission => Boolean(mission && matches(mission, filter)));
  const objectiveCampaigns = objective.campaign_ids
    .map((campaignId) => campaigns.get(campaignId))
    .filter((campaign): campaign is WorkCampaign => Boolean(campaign))
    .filter((campaign) => !selectedCampaign || campaign.campaign_id === selectedCampaign);
  const campaignMissionIds = new Set(objectiveCampaigns.flatMap((campaign) => campaign.mission_ids));
  const directMissions = objectiveMissions.filter((mission) => !campaignMissionIds.has(mission.id));
  const visibleCampaigns = objectiveCampaigns.filter((campaign) =>
    campaign.mission_ids.some((id) => { const m = missionById.get(id); return m && matches(m, filter); }),
  );
  return { visibleCampaigns, directMissions };
}

type GoalSection = {
  goal: OwnerStandingGoal;
  directiveId: string;
  goalId: string;
  label: string;
  status: string;
  isServed: boolean;
  children: Mission[];
  current: Mission | null;
  latestComplete: Mission | null;
  splits: Array<{ objective: WorkObjective; split: ObjectiveSplit }>;
  orphans: Mission[];
  indexBase: number;
};

function MissionRows({
  rows,
}: {
  rows: Mission[];
  links: Map<string, WorkMissionLink>;
}) {
  return (
    <div className="rm-mission-rows">
      {rows.map((mission) => {
        const needsAttention = ["BLOCKED", "HALTED", "INTERVENTION"].includes(mission.status);
        return (
          <Link key={mission.id} to={`/missions/${mission.id}`} className={`rm-mission-row rm-mission-row--${mission.status.toLowerCase()}`}>
            <span className="rm-mission-status"><span />{statusLabel(mission.status)}</span>
            <div>
              <strong>{mission.title}</strong>
              <small className="tech-id">{mission.id}</small>
              <small>Now: {mission.stage && mission.stage !== "UNKNOWN" ? mission.stage : "Current step not reported"}</small>
              <small>Next: {mission.nextAction && mission.nextAction !== "UNKNOWN" ? mission.nextAction : "Next action not recorded"}</small>
              {mission.completion >= 0 ? <small>{mission.completion}% complete · {mission.agentsActive} active</small> : null}
              {needsAttention ? <small>Needs you: {mission.nextAction}</small> : null}
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

function ObjectiveBlock({
  objective,
  index,
  split,
  missionById,
  goals,
  projects,
  missionLinks,
  params,
  filter,
}: {
  objective: WorkObjective;
  index: number;
  split: ObjectiveSplit;
  missionById: Map<string, Mission>;
  goals: Map<string, WorkGoal>;
  projects: Map<string, WorkProject>;
  missionLinks: Map<string, WorkMissionLink>;
  params: URLSearchParams;
  filter: WorkFilter;
}) {
  const goal = objective.goal_id ? goals.get(objective.goal_id) : null;
  const project = objective.project_id ? projects.get(objective.project_id) : null;
  const next = new URLSearchParams(params);
  next.set("status", "all");
  next.set("objective", objective.objective_id);
  next.delete("campaign");
  return (
    <section key={objective.objective_id} className="rm-objective" aria-labelledby={`objective-${index}`}>
      <div className="rm-objective-head">
        <span className="rm-objective-index">O{String(index + 1).padStart(2, "0")}</span>
        <div>
          <h2 id={`objective-${index}`}>
            <Link to={{ pathname: "/work", search: next.toString() }}>{objective.label}</Link>
          </h2>
          <p className="tech-id">{objective.objective_id} · {objective.status}</p>
          {(goal || project) && (goal?.label || project?.label) ? (
            <div className="rm-objective-lineage">
              {goal?.label ? <span>Goal: {goal.label}</span> : null}
              {project?.label ? <span>Project: {project.label}</span> : null}
            </div>
          ) : null}
        </div>
      </div>
      <div className="rm-objective-work">
        {split.visibleCampaigns.map((campaign) => (
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
        {split.directMissions.length ? (
          <div className="rm-campaign-block">
            <div className="rm-campaign-head"><div><strong>Direct missions</strong><small>No campaign identity recorded</small></div><span>{split.directMissions.length} mission{split.directMissions.length === 1 ? "" : "s"}</span></div>
            <MissionRows rows={split.directMissions} links={missionLinks} />
          </div>
        ) : null}
        {!split.visibleCampaigns.length && !split.directMissions.length ? null : null}
      </div>
    </section>
  );
}

function UnlinkedMissions({
  rows,
  links,
}: {
  rows: Mission[];
  links: Map<string, WorkMissionLink>;
}) {
  return (
    <section className="rm-objective" aria-labelledby="objective-unlinked">
      <div className="rm-objective-head">
        <span className="rm-objective-index">UN</span>
        <div><h2 id="objective-unlinked">Unlinked mission records</h2><p>No explicit Executive objective or campaign identity is published.</p></div>
      </div>
      <MissionRows rows={rows} links={links} />
    </section>
  );
}

export function WorkContent({ missions, hierarchy }: WorkData) {
  const { sourceMode } = useCommandCenter();
  const { t } = useLocale();
  const [params, setParams] = useSearchParams();
  const fromUrl = params.get("status") as WorkFilter | null;
  const [localFilter, setLocalFilter] = useState<WorkFilter>(fromUrl && ["active", "waiting", "attention", "completed", "all"].includes(fromUrl) ? fromUrl : "active");
  const filter = fromUrl && ["active", "waiting", "attention", "completed", "all"].includes(fromUrl) ? fromUrl : localFilter;
  const selectedObjective = params.get("objective");
  const selectedCampaign = params.get("campaign");
  // Goal drill-down (MissionDetail serves link): when `goal` is present,
  // goal sections not matching it are skipped — the same skip-gate precedent
  // as objective/campaign. Accepts a directive id or a goal id.
  const selectedGoal = params.get("goal");
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
    && (!selectedGoal || objectiveMatchesGoal(objective, selectedGoal, hierarchy?.goals))
  ));
  const activeCount = missions.filter((m) => matches(m, "active")).length;
  const attentionList = missions.filter((m) => matches(m, "attention"));
  const completedList = missions.filter((m) => matches(m, "completed"));
  const recentCompleted = completedList.slice(0, 3);
  const assignedMissionIds = new Set((hierarchy?.missions ?? [])
    .filter((mission) => mission.objective_id)
    .map((mission) => mission.mission_id));
  const unlinkedMissions = hierarchy
    ? filtered.filter((mission) => !assignedMissionIds.has(mission.id))
    : [];
  // Owner-goal-first grouping (spec §2, §4, §10, §11): rank hierarchy goals
  // with the Task 1 ranker and serve the top goal through the same
  // active-guard + no-goal-fallthrough pattern as Overview (Task 2 precedent).
  // Missions carry the durable created_from directive id on `owner` (see
  // snapshotMapper.mapMissions), so the join is exact lineage, never titles.
  // WorkGoal is projected to the selector shape here (interfaces carry no
  // implicit index signature); activeness stays on the record's own
  // active/status markers, read by the guard below — no second mechanism.
  const standingGoals: OwnerStandingGoal[] = (hierarchy?.goals ?? []).map((goal) => ({
    directive_id: goal.directive_id,
    status: goal.status,
    label: goal.label,
    goal_id: goal.goal_id,
  }));
  const rankedGoals = rankOwnerGoals(standingGoals, missions, hierarchy);
  const topRankedGoal = rankedGoals[0] ?? null;
  // rankOwnerGoals sorts active-first but does not filter, so a non-empty all-inactive list still yields a [0]: serve it only when its own active/status markers read active, else fall through to the no-goal path.
  const activeOwnerGoal = topRankedGoal && (topRankedGoal.active === 1 || topRankedGoal.active === true || topRankedGoal.active === "1" || topRankedGoal.active === "true" || (typeof topRankedGoal.active === "string" && topRankedGoal.active.toUpperCase() === "ACTIVE") || ((topRankedGoal.active === undefined || topRankedGoal.active === null) && typeof topRankedGoal.status === "string" && topRankedGoal.status.toUpperCase() === "ACTIVE")) ? topRankedGoal : null;
  const activeGoalDirectiveId = ownerGoalDirectiveId(activeOwnerGoal);
  // Exact-lineage children per ranked goal (unfiltered newest-first lineage;
  // the status filter scopes the objective/campaign/mission lists below, not
  // the served goal's own current/result/next/history card).
  const goalChildrenByDirective = new Map<string, Mission[]>();
  const allGoalChildIds = new Set<string>();
  for (const goal of rankedGoals) {
    const directiveId = ownerGoalDirectiveId(goal);
    if (!directiveId || goalChildrenByDirective.has(directiveId)) continue;
    const children = goalChildren(directiveId, missions, hierarchy);
    goalChildrenByDirective.set(directiveId, children);
    for (const child of children) allGoalChildIds.add(child.id);
  }
  const goalSections: GoalSection[] = [];
  let goalObjectiveOrdinal = 0;
  for (const goal of rankedGoals) {
    const directiveId = ownerGoalDirectiveId(goal);
    if (!directiveId) continue;
    if (goalSections.some((section) => section.directiveId === directiveId)) continue;
    // Goal drill-down isolation (same skip-gate precedent as
    // objective/campaign): a `goal` param skips every section that is
    // neither that directive nor that goal id.
    if (selectedGoal && directiveId !== selectedGoal && hierarchyGoalId(goal) !== selectedGoal) continue;
    const children = goalChildrenByDirective.get(directiveId) ?? [];
    const goalId = hierarchyGoalId(goal);
    const childIds = new Set(children.map((child) => child.id));
    const matchedObjectives = objectives.filter((objective) =>
      ((goalId && objective.goal_id === goalId) || objective.mission_ids.some((id) => childIds.has(id)))
      && (!selectedObjective || objective.objective_id === selectedObjective)
      && (!selectedCampaign || objective.campaign_ids.includes(selectedCampaign)),
    );
    const splits = matchedObjectives.map((objective) => ({
      objective,
      split: splitObjectiveMissions(objective, missionById, campaigns, filter, selectedCampaign),
    }));
    const renderedIds = new Set<string>();
    for (const { split } of splits) {
      for (const campaign of split.visibleCampaigns) {
        for (const id of campaign.mission_ids) {
          const linked = missionById.get(id);
          if (linked && matches(linked, filter)) renderedIds.add(id);
        }
      }
      for (const direct of split.directMissions) renderedIds.add(direct.id);
    }
    const orphans = children.filter((child) => matches(child, filter) && !renderedIds.has(child.id));
    const current = currentChildForGoal(directiveId, missions, hierarchy);
    const latestComplete = children.find((child) => child.status === "COMPLETE") ?? null;
    const isServed = activeGoalDirectiveId !== "" && directiveId === activeGoalDirectiveId;
    const hasMatchedSplits = splits.some(({ split }) => split.visibleCampaigns.length > 0 || split.directMissions.length > 0);
    // Drill-down isolation (explicit navigation intent): flat base showed only
    // the selected objective, so a drill-down param skips every goal section
    // with zero matched splits — including its served card, history, and
    // orphans. The served card's unconditional truth applies to the STATUS
    // filter only; the card stays one click away on the unfiltered view.
    if ((selectedObjective || selectedCampaign || selectedGoal) && !hasMatchedSplits) continue;
    const hasContent = hasMatchedSplits
      || orphans.length > 0
      || (isServed && children.length > 0);
    if (!hasContent) continue;
    const indexBase = goalObjectiveOrdinal;
    goalObjectiveOrdinal += matchedObjectives.length;
    goalSections.push({
      goal,
      directiveId,
      goalId,
      label: goalHeadingLabel(goal),
      status: goalStatusText(goal),
      isServed,
      children,
      current,
      latestComplete,
      splits,
      orphans,
      indexBase,
    });
  }
  // No-goal fallthrough: without a served active goal section, the existing
  // flat objective/campaign views render unchanged.
  const goalMode = activeOwnerGoal !== null && goalSections.some((section) => section.isServed);
  const goalUnlinked = hierarchy
    ? filtered.filter((mission) => !assignedMissionIds.has(mission.id) && !allGoalChildIds.has(mission.id))
    : [];
  const filters: Array<{ key: WorkFilter; label: string }> = [
    { key: "active", label: t("work.filterActive") }, { key: "waiting", label: t("work.filterWaiting") },
    { key: "attention", label: t("work.filterAttention") }, { key: "completed", label: t("work.filterCompleted") }, { key: "all", label: t("work.filterAll") },
  ];

  return (
    <div className="shell-content rm-page rm-work">
      <header className="rm-page-head">
        <div><h1>{t("page.work")}</h1><p>{t("page.workSubtitle")}</p></div>
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

      {filter === "active" && !selectedObjective && !selectedCampaign && !selectedGoal ? (
        <div className="work-summary-grid" aria-label="Work summary">
          <div className="work-summary-card">
            <h3>Active</h3>
            <p>{activeCount === 0 ? "No active work. Jarvis is monitoring for new opportunities." : `${activeCount} in progress`}</p>
            {filtered[0] ? <small>Now: {filtered[0].title}</small> : null}
          </div>
          <div className="work-summary-card">
            <h3>Needs you</h3>
            <p>{attentionList.length === 0 ? "Nothing needs your decision right now." : `${attentionList.length} waiting on you`}</p>
            {attentionList[0] ? <small>{attentionList[0].title} — {attentionList[0].nextAction}</small> : null}
          </div>
          <div className="work-summary-card">
            <h3>Recently completed</h3>
            <p>{recentCompleted.length === 0 ? "No verified completions yet." : recentCompleted[0].title}</p>
            {recentCompleted[0] ? <small>{recentCompleted[0].nextAction}</small> : null}
          </div>
        </div>
      ) : null}

      <div className="rm-filter-row" role="group" aria-label="Filter work">
        {filters.map((item) => (
          <button key={item.key} type="button" className={filter === item.key ? "is-active" : ""} aria-pressed={filter === item.key} onClick={() => {
            setLocalFilter(item.key);
            const next = new URLSearchParams(params); if (item.key === "active") next.delete("status"); else next.set("status", item.key); next.delete("objective"); next.delete("campaign"); next.delete("goal"); setParams(next, { replace: true });
          }}>{item.label}<span>{missions.filter((mission) => matches(mission, item.key)).length}</span></button>
        ))}
      </div>

      {goalMode ? (
        <div className="rm-goal-list">
          {goalSections.map((section, sectionIndex) => {
            const workerLine = section.current && section.current.agentsActive > 0
              ? `${section.current.agentsActive} active worker${section.current.agentsActive === 1 ? "" : "s"} linked`
              : "No active worker linked";
            const outcome = section.latestComplete
              && section.latestComplete.objective.trim()
              && section.latestComplete.objective !== "UNKNOWN"
              ? section.latestComplete.objective
              : null;
            const nextText = section.current
              && section.current.nextAction.trim()
              && section.current.nextAction !== "UNKNOWN"
              ? section.current.nextAction
              : "Next action not recorded";
            return (
              <section key={section.directiveId} className="rm-goal" aria-labelledby={`goal-${sectionIndex}`}>
                <div className="rm-goal-head">
                  <div>
                    <h2 id={`goal-${sectionIndex}`}>{section.label}</h2>
                    <p className="tech-id">{section.directiveId} · {section.status}</p>
                  </div>
                </div>
                {section.isServed && section.current ? (
                  <div className="rm-goal-card" aria-label="Active goal summary">
                    <div className="rm-goal-current">
                      <span>Current</span>
                      <strong>{section.current.title}</strong>
                      <small>{statusLabel(section.current.status)}</small>
                      <Link to={`/missions/${section.current.id}`}>Open current mission</Link>
                    </div>
                    <div className="rm-goal-workers">
                      <span>Workers</span>
                      <p>{workerLine}</p>
                    </div>
                    {section.latestComplete ? (
                      <div className="rm-goal-latest">
                        <span>Latest result</span>
                        <Link to={`/missions/${section.latestComplete.id}`}>{section.latestComplete.title}</Link>
                        {outcome ? <small>{outcome}</small> : null}
                      </div>
                    ) : null}
                    <div className="rm-goal-next">
                      <span>Next</span>
                      <p>{nextText}</p>
                    </div>
                  </div>
                ) : null}
                {section.splits.map(({ objective, split }, splitIndex) => {
                  if (!split.visibleCampaigns.length && !split.directMissions.length) return null;
                  return (
                    <ObjectiveBlock
                      key={objective.objective_id}
                      objective={objective}
                      index={section.indexBase + splitIndex}
                      split={split}
                      missionById={missionById}
                      goals={goals}
                      projects={projects}
                      missionLinks={missionLinks}
                      params={params}
                      filter={filter}
                    />
                  );
                })}
                {section.orphans.length ? (
                  <div className="rm-campaign-block">
                    <div className="rm-campaign-head"><div><strong>Direct missions</strong><small>No campaign identity recorded</small></div><span>{section.orphans.length} mission{section.orphans.length === 1 ? "" : "s"}</span></div>
                    <MissionRows rows={section.orphans} links={missionLinks} />
                  </div>
                ) : null}
                {section.isServed && section.children.length ? (
                  <div className="rm-goal-history" aria-label="Child history">
                    <MissionRows rows={section.children} links={missionLinks} />
                  </div>
                ) : null}
              </section>
            );
          })}
          {goalUnlinked.length && !selectedObjective && !selectedCampaign && !selectedGoal ? (
            <UnlinkedMissions rows={goalUnlinked} links={missionLinks} />
          ) : null}
        </div>
      ) : visibleObjectives.length ? (
        <div className="rm-objective-list">
          {visibleObjectives.map((objective, objectiveIndex) => {
            const split = splitObjectiveMissions(objective, missionById, campaigns, filter, selectedCampaign);
            if (!split.visibleCampaigns.length && !split.directMissions.length) return null;
            return (
              <ObjectiveBlock
                key={objective.objective_id}
                objective={objective}
                index={objectiveIndex}
                split={split}
                missionById={missionById}
                goals={goals}
                projects={projects}
                missionLinks={missionLinks}
                params={params}
                filter={filter}
              />
            );
          })}
          {unlinkedMissions.length && !selectedObjective && !selectedCampaign && !selectedGoal ? (
            <UnlinkedMissions rows={unlinkedMissions} links={missionLinks} />
          ) : null}
        </div>
      ) : (
        <div className="rm-page-empty"><FolderKanban size={22} /><h2>{t("work.noWork")}</h2><p>{sourceMode === "LIVE" ? "No canonical work matches this filter or hierarchy selection." : "No simulated mission matches this filter."}</p></div>
      )}

      <aside className="rm-work-footnote">
        <GitBranch size={17} /><div><strong>{t("work.hierarchy")}</strong><p>Missing programs, projects, or links stay unavailable. Mission detail carries action, worker, evidence, verifier, and repair drill-down.</p></div>
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
