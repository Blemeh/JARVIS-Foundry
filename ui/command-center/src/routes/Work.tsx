import { useSearchParams, Link } from "react-router-dom";
import { ArrowRight, FolderKanban } from "lucide-react";
import { RouteQueryGate } from "../components/RouteQueryGate";
import { useCommandCenter } from "../context/CommandCenterContext";
import { useRouteQuery } from "../hooks/useRouteQuery";
import type { Mission, WorkHierarchy, WorkObjective } from "../lib/contracts";
import { sourceModeLabel } from "../lib/sourceMode";
import { useLocale } from "../i18n/LocaleContext";
import "../styles/owner-summary.css";

type WorkData = { missions: Mission[]; hierarchy: WorkHierarchy | null };
type WorkFilter = "active" | "waiting" | "attention" | "completed" | "all";
const attentionStates = ["BLOCKED", "INTERVENTION", "HALTED", "WAIT_FOR_OWNER", "NEEDS_OWNER"];
function matches(status: string, filter: WorkFilter) {
  const value = status.toUpperCase();
  if (filter === "all") return true;
  if (filter === "attention") return attentionStates.includes(value);
  if (filter === "completed") return ["COMPLETE", "COMPLETED", "ACCEPTED", "DONE"].includes(value);
  if (filter === "waiting") return ["STANDBY", "HALTED", "WAITING", "WAIT"].includes(value) || value.startsWith("WAIT_");
  return ["ACTIVE", "IN_PROGRESS", "RUNNING", "WORKING", "OPEN", "READY", "PENDING", "STANDBY", "QUEUED", "WAITING", "WAIT"].includes(value) || (value.startsWith("WAIT_") && !attentionStates.includes(value));
}
function conciseWorkTitle(text: string) {
  const line = text.split(/\r?\n/).find((part) => part.trim())?.trim() || "Untitled work";
  return line.length > 110 ? `${line.slice(0, 107).trimEnd()}…` : line;
}
function WorkContent({ missions, hierarchy }: WorkData) {
  const { sourceMode } = useCommandCenter();
  const { t } = useLocale();
  const [params, setParams] = useSearchParams();
  const requested = params.get("status");
  const filter = requested && ["active", "waiting", "attention", "completed", "all"].includes(requested) ? requested as WorkFilter : null;
  const objectiveId = params.get("objective");
  const campaignId = params.get("campaign");
  const missionMap = new Map(missions.map((mission) => [mission.id, mission]));
  const objectives: WorkObjective[] = hierarchy?.objectives ?? [];
  const assigned = new Set(objectives.flatMap((objective) => objective.mission_ids));
  const links = new Map((hierarchy?.missions ?? []).map((link) => [link.mission_id, link]));
  function objectiveMatches(objective: WorkObjective, selected: WorkFilter) {
    const rows = objective.mission_ids.map((id) => missionMap.get(id)).filter((row): row is Mission => Boolean(row));
    if (selected === "all") return true;
    if (selected === "waiting") return matches(objective.status, selected) || rows.some((row) => matches(row.status, selected));
    const needsAttention = matches(objective.status, "attention") || rows.some((row) => matches(row.status, "attention"));
    const completed = matches(objective.status, "completed") || (objective.status === "UNAVAILABLE" && rows.length > 0 && rows.every((row) => matches(row.status, "completed")));
    if (selected === "attention") return needsAttention;
    if (selected === "completed") return !needsAttention && completed;
    return !needsAttention && !completed && (matches(objective.status, "active") || rows.some((row) => matches(row.status, "active")));
  }
  const sections: Array<{ label: string; filter: WorkFilter }> = filter ? [{ label: filter === "all" ? "All work" : filter === "completed" ? "Recently completed" : filter === "attention" ? "Needs you" : filter === "waiting" ? "Waiting" : "Active work", filter }] : [
    { label: "Active work", filter: "active" }, { label: "Needs you", filter: "attention" }, { label: "Recently completed", filter: "completed" },
  ];
  function missionRows(rows: Mission[]) {
    return <div className="owner-work-missions">{rows.map((mission) => <Link key={mission.id} to={`/missions/${mission.id}`}><span><strong>{conciseWorkTitle(mission.title)}</strong><small>{mission.status.replaceAll("_", " ")} · {mission.stage || "Stage not reported"}</small></span><ArrowRight size={16} /></Link>)}</div>;
  }
  return <div className="shell-content rm-page rm-work owner-summary-page">
    <header className="rm-page-head"><div><h1>{t("page.work")}</h1><p>What is moving, what needs you, and what finished.</p></div><div className="rm-page-source"><span>{sourceModeLabel(sourceMode)}</span><small>{missions.length} mission records</small></div></header>
    {sourceMode === "LIVE" && !hierarchy ? <p className="owner-empty" role="status">Work structure is unavailable. Mission records remain available; retry from the connection controls.</p> : null}
    <div className="rm-filter-row" role="group" aria-label="Filter work">{[{ key: "summary", label: "Summary" }, { key: "active", label: t("work.filterActive") }, { key: "waiting", label: t("work.filterWaiting") }, { key: "attention", label: t("work.filterAttention") }, { key: "completed", label: t("work.filterCompleted") }, { key: "all", label: t("work.filterAll") }].map((item) => <button key={item.key} type="button" aria-pressed={filter === item.key || (!filter && item.key === "summary")} className={filter === item.key || (!filter && item.key === "summary") ? "is-active" : ""} onClick={() => { const next = new URLSearchParams(); if (item.key !== "summary") next.set("status", item.key); setParams(next, { replace: true }); }}>{item.label}</button>)}</div>
    {sections.map((section) => {
      const visible = objectives.filter((objective) => (!objectiveId || objective.objective_id === objectiveId) && (!campaignId || objective.campaign_ids.includes(campaignId)) && objectiveMatches(objective, section.filter));
      const unlinked = !objectiveId && !campaignId ? missions.filter((mission) => !assigned.has(mission.id) && matches(mission.status, section.filter)) : [];
      return <section className="owner-work-section" key={section.filter} aria-label={section.label}><div className="rm-section-heading"><h2>{section.label}</h2><span>{visible.length + unlinked.length}</span></div>
        {!visible.length && !unlinked.length ? <p className="owner-empty"><FolderKanban size={17} />{section.filter === "attention" ? "Nothing in this view needs your attention." : section.filter === "completed" ? "No completed work is recorded in this view." : "No work matches this view."}</p> : null}
        {visible.map((objective) => {
          const rows = objective.mission_ids.map((id) => missionMap.get(id)).filter((row): row is Mission => Boolean(row));
          const current = rows.find((mission) => matches(mission.status, "active")) ?? rows.find((mission) => matches(mission.status, "attention")) ?? rows[0];
          const blocked = rows.find((mission) => matches(mission.status, "attention"));
          const latest = rows.filter((mission) => matches(mission.status, "completed")).sort((a, b) => b.startedAt.localeCompare(a.startedAt))[0];
          return <article className="owner-work-card" key={objective.objective_id}>
            <header><h3>{conciseWorkTitle(objective.label)}</h3><span>{objective.status.replaceAll("_", " ")}</span></header>
            <dl>{current ? <div><dt>Current action</dt><dd>{conciseWorkTitle(current.nextAction || current.title)}</dd></div> : <div><dt>Current action</dt><dd>No current action is published.</dd></div>}{blocked ? <div><dt>Blocker</dt><dd>{conciseWorkTitle(blocked.nextAction || blocked.description || blocked.status)}</dd></div> : null}{latest ? <div><dt>Latest outcome</dt><dd>{conciseWorkTitle(latest.nextAction || latest.title)}</dd></div> : null}</dl>
            <details open={objectiveId === objective.objective_id || Boolean(campaignId)}><summary>Work details · {rows.length} missions</summary><p className="owner-full-text">{objective.label}</p><dl><div><dt>Objective</dt><dd>{objective.objective_id}</dd></div><div><dt>Progress evidence</dt><dd>{objective.progress_evidence.status}</dd></div>{objective.goal_id ? <div><dt>Goal</dt><dd>{objective.goal_id}</dd></div> : null}{objective.project_id ? <div><dt>Project</dt><dd>{objective.project_id}</dd></div> : null}{objective.plan_ids.length ? <div><dt>Plans</dt><dd>{objective.plan_ids.join(" · ")}</dd></div> : null}</dl>
              {objective.campaign_ids.map((id) => { const campaign = hierarchy?.campaigns.find((item) => item.campaign_id === id); return campaign ? <p key={id} id={`campaign-${id}`}>Campaign {id} · {campaign.status}{campaign.wait_kinds.length ? ` · ${campaign.wait_kinds.join(", ")}` : ""}</p> : null; })}
              {missionRows(rows)}{rows.map((mission) => { const link = links.get(mission.id); return link ? <p key={mission.id} className="owner-proof">{mission.id} · {link.action_ids.length} actions · {link.worker_ids.length} workers · {link.verdict_ids.length} verdicts{link.source_event_id ? <> · <Link to={`/activity?event=${encodeURIComponent(link.source_event_id)}`}>Source event</Link></> : null}{link.evidence_refs.map((ref) => <Link key={ref} to={`/evidence/${encodeURIComponent(ref)}`}>Evidence {ref}</Link>)}</p> : null; })}
              {objective.progress_evidence.evidence_refs.map((ref) => <Link key={ref} to={`/evidence/${encodeURIComponent(ref)}`}>Evidence {ref}</Link>)}<Link to={`/work?status=all&objective=${encodeURIComponent(objective.objective_id)}`}>Open objective</Link>
            </details>
          </article>;
        })}{unlinked.length ? missionRows(unlinked) : null}
      </section>;
    })}
    <details className="owner-inspection"><summary>Inspect work structure and ledger</summary><div aria-label="Work hierarchy contract">{Object.entries(hierarchy?.levels ?? { objectives: "AVAILABLE", missions: "AVAILABLE" }).filter(([, state]) => state === "AVAILABLE").map(([level]) => <span key={level}>{level} → </span>)}</div><p>Only published relationships are shown. Mission detail retains actions, workers, evidence, verdicts, and repairs.</p><Link to="/missions">Mission forensics <ArrowRight size={15} /></Link></details>
  </div>;
}
export function Work() {
  const { client, version } = useCommandCenter();
  const query = useRouteQuery(async (): Promise<WorkData> => {
    const [missions, hierarchy] = await Promise.all([client.listMissions(), client.getWorkHierarchy?.().catch(() => null) ?? Promise.resolve(null)]);
    return { missions, hierarchy };
  }, [client, version], { keepPreviousWhileLoading: true });
  return <RouteQueryGate query={query} loadingLabel="Loading work" errorLabel="Work unavailable" surface="Work hierarchy projection">{(data) => <WorkContent {...data} />}</RouteQueryGate>;
}
