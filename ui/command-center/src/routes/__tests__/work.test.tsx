import { render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { WorkContent } from "../Work";
import { CommandCenterProvider } from "../../context/CommandCenterContext";
import type { Mission, WorkHierarchy } from "../../lib/contracts";

export const HOOWEN_DIRECTIVE = "owner-delegation-643042f8f84795e7de8b3d79a4b7142c";
export const HOOWEN_LABEL = "Hoowen — Active strategic goal";
export const HOOWEN_FOLLOWUP_2_OUTCOME = "Hoowen follow-up 2 complete with its outcome summary.";
export const HOOWEN_FOLLOWUP_3_NEXT = "Repair the builder failure and re-run verification.";

type WorkTestMission = Mission & { updated_at?: string };

function workMission(init: {
  id: string;
  status: Mission["status"];
  startedAt: string;
  owner: string;
  title: string;
  objective: string;
  nextAction: string;
  agentsActive?: number;
}): WorkTestMission {
  return {
    id: init.id,
    code: init.id,
    title: init.title,
    description: init.title,
    status: init.status,
    priority: "NORMAL",
    agentsActive: init.agentsActive ?? 0,
    completion: init.status === "COMPLETE" ? 100 : 0,
    nextAction: init.nextAction,
    stage: init.status,
    startedAt: init.startedAt,
    objective: init.objective,
    owner: init.owner,
    updated_at: init.startedAt,
  };
}

function hoowenMissions(): WorkTestMission[] {
  return [
    workMission({
      id: "mission-08a548a685ae",
      status: "COMPLETE",
      startedAt: "2026-10-06T14:00:00Z",
      owner: `directive:${HOOWEN_DIRECTIVE}:follow-up-1`,
      title: "Hoowen follow-up 1",
      objective: "Hoowen follow-up 1 shipped with verified evidence.",
      nextAction: "Start follow-up 2.",
    }),
    workMission({
      id: "mission-62250d335ab6",
      status: "COMPLETE",
      startedAt: "2026-10-06T15:34:00Z",
      owner: `directive:${HOOWEN_DIRECTIVE}:follow-up-2`,
      title: "Hoowen follow-up 2",
      objective: HOOWEN_FOLLOWUP_2_OUTCOME,
      nextAction: "Start follow-up 3.",
    }),
    workMission({
      id: "mission-fe81cb0d1e37",
      status: "BLOCKED",
      startedAt: "2026-10-06T15:50:00Z",
      owner: `directive:${HOOWEN_DIRECTIVE}:follow-up-3`,
      title: "Hoowen follow-up 3",
      objective: "Hoowen follow-up 3 blocked after builder failure.",
      nextAction: HOOWEN_FOLLOWUP_3_NEXT,
    }),
    workMission({
      id: "mission-4646c7023b05",
      status: "BLOCKED",
      startedAt: "2026-08-19T12:00:00Z",
      owner: "portfolio:pw-dupkey-9f31:CI_FAILURE",
      title: "Overnight CI verification sweep",
      objective: "Historical CI verification failure.",
      nextAction: "Fix the CI pipeline.",
    }),
    workMission({
      id: "mission-retired-01",
      status: "COMPLETE",
      startedAt: "2026-09-02T10:00:00Z",
      owner: "owner-goal-retired-009",
      title: "Retired upkeep task",
      objective: "Retired goal outcome recorded.",
      nextAction: "No follow-up recorded.",
    }),
  ];
}

export function buildHoowenWorkData(): { missions: WorkTestMission[]; hierarchy: WorkHierarchy } {
  return {
    missions: hoowenMissions(),
    hierarchy: {
      schema: "v13.command-center-work.v1",
      status: "AVAILABLE",
      authority: "canonical-ledger",
      ui_authority: "NONE",
      levels: {
        goals: "AVAILABLE",
        projects: "NOT_YET_INTEGRATED",
        objectives: "AVAILABLE",
        campaigns: "AVAILABLE",
        missions: "AVAILABLE",
        actions: "UNAVAILABLE",
      },
      // The retired goal is listed first on purpose: rank must still serve
      // the active Hoowen goal first, never input order.
      goals: [
        {
          goal_id: "goal-retired-1",
          directive_id: "owner-goal-retired-009",
          label: "Retired goal",
          status: "retired",
          target: "GLOBAL",
          source_event_id: null,
          objective_ids: ["obj-retired-1"],
        },
        {
          goal_id: "goal-hoowen-1",
          directive_id: HOOWEN_DIRECTIVE,
          label: HOOWEN_LABEL,
          status: "active",
          target: "GLOBAL",
          source_event_id: null,
          objective_ids: ["obj-hoowen-1"],
        },
      ],
      programs: [],
      projects: [],
      objectives: [
        {
          objective_id: "obj-hoowen-1",
          label: "Hoowen workforce management",
          goal_id: "goal-hoowen-1",
          status: "active",
          campaign_ids: ["camp-hoowen-1"],
          mission_ids: ["mission-fe81cb0d1e37", "mission-62250d335ab6", "mission-08a548a685ae"],
          plan_ids: [],
          action_ids: [],
          progress_evidence: { status: "active", evidence_refs: [] },
        },
        {
          objective_id: "obj-retired-1",
          label: "Retired objective",
          goal_id: "goal-retired-1",
          status: "retired",
          campaign_ids: [],
          mission_ids: ["mission-retired-01"],
          plan_ids: [],
          action_ids: [],
          progress_evidence: { status: "retired", evidence_refs: [] },
        },
      ],
      campaigns: [
        {
          campaign_id: "camp-hoowen-1",
          objective_id: "obj-hoowen-1",
          status: "active",
          mission_ids: ["mission-fe81cb0d1e37", "mission-62250d335ab6"],
          child_count: 2,
          pending_count: 0,
          wait_kinds: [],
          action_ids: [],
          source: "test",
        },
      ],
      missions: [
        {
          mission_id: "mission-fe81cb0d1e37",
          objective_id: "obj-hoowen-1",
          campaign_id: "camp-hoowen-1",
          status: "BLOCKED",
          action_ids: [],
          worker_ids: [],
          evidence_refs: [],
          verdict_ids: [],
        },
        {
          mission_id: "mission-62250d335ab6",
          objective_id: "obj-hoowen-1",
          campaign_id: "camp-hoowen-1",
          status: "COMPLETE",
          action_ids: [],
          worker_ids: [],
          evidence_refs: [],
          verdict_ids: [],
        },
        {
          mission_id: "mission-08a548a685ae",
          objective_id: "obj-hoowen-1",
          status: "COMPLETE",
          action_ids: [],
          worker_ids: [],
          evidence_refs: [],
          verdict_ids: [],
        },
        {
          mission_id: "mission-retired-01",
          objective_id: "obj-retired-1",
          status: "COMPLETE",
          action_ids: [],
          worker_ids: [],
          evidence_refs: [],
          verdict_ids: [],
        },
      ],
      actions: [],
    },
  };
}

export function buildAllInactiveWorkData(): { missions: WorkTestMission[]; hierarchy: WorkHierarchy } {
  const data = buildHoowenWorkData();
  if (!data.hierarchy.goals) throw new Error("Hoowen fixture must include hierarchy goals");
  // Non-empty but all inactive: the newest entry must NOT be served as the
  // active goal (the ranker sorts active-first but does not filter).
  data.hierarchy.goals = [
    {
      goal_id: "goal-retired-1",
      directive_id: "owner-goal-retired-009",
      label: "Retired goal",
      status: "retired",
      target: "GLOBAL",
      source_event_id: null,
      objective_ids: ["obj-retired-1"],
    },
    {
      goal_id: "goal-hoowen-1",
      directive_id: HOOWEN_DIRECTIVE,
      label: HOOWEN_LABEL,
      status: "suspended",
      target: "GLOBAL",
      source_event_id: null,
      objective_ids: ["obj-hoowen-1"],
    },
  ];
  return data;
}

export function buildEmptyGoalsWorkData(): { missions: WorkTestMission[]; hierarchy: WorkHierarchy } {
  const data = buildHoowenWorkData();
  data.hierarchy.goals = [];
  return data;
}

function renderWorkContent(
  missions: WorkTestMission[],
  hierarchy: WorkHierarchy,
  path = "/work?status=all",
) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <CommandCenterProvider>
        <WorkContent missions={missions} hierarchy={hierarchy} />
      </CommandCenterProvider>
    </MemoryRouter>,
  );
}

describe("Work grouped by owner goal", () => {
  it("renders the Hoowen ACTIVE goal first with current child, worker state, last result, next, and child history", () => {
    const { missions, hierarchy } = buildHoowenWorkData();
    renderWorkContent(missions, hierarchy);
    const hoowen = screen.getByRole("region", { name: HOOWEN_LABEL });
    const retiredGoal = screen.getByRole("region", { name: "Retired goal" });
    // Rank order, not input order: the retired goal is listed first in the
    // hierarchy but its section follows the active Hoowen section.
    expect(Boolean(hoowen.compareDocumentPosition(retiredGoal) & Node.DOCUMENT_POSITION_FOLLOWING)).toBe(true);

    const summary = within(hoowen).getByLabelText("Active goal summary");
    // Current child: the newest Hoowen lineage mission, truthfully Blocked.
    expect(within(summary).getByText("Hoowen follow-up 3")).toBeInTheDocument();
    expect(summary.textContent ?? "").toMatch(/Blocked/);
    expect(within(summary).getByRole("link", { name: "Open current mission" })).toHaveAttribute(
      "href",
      "/missions/mission-fe81cb0d1e37",
    );
    // Worker state: no RUNNING worker is linked to the current child.
    expect(within(summary).getByText("No active worker linked")).toBeInTheDocument();
    // Last result: the newest COMPLETE Hoowen child, linked, with its outcome.
    expect(within(summary).getByRole("link", { name: /Hoowen follow-up 2/ })).toHaveAttribute(
      "href",
      "/missions/mission-62250d335ab6",
    );
    expect(within(summary).getByText(HOOWEN_FOLLOWUP_2_OUTCOME)).toBeInTheDocument();
    // Next: the current child's exact next action.
    expect(within(summary).getByText(HOOWEN_FOLLOWUP_3_NEXT)).toBeInTheDocument();
    // Child history beneath: both COMPLETE follow-ups plus the BLOCKED current.
    const history = within(hoowen).getByLabelText("Child history");
    for (const title of ["Hoowen follow-up 1", "Hoowen follow-up 2", "Hoowen follow-up 3"]) {
      expect(within(history).getByRole("link", { name: new RegExp(title) })).toBeInTheDocument();
    }
    // No raw mission or directive id leaks into the goal heading.
    const heading = within(hoowen).getByRole("heading", { name: HOOWEN_LABEL });
    expect(heading.textContent ?? "").not.toMatch(/mission-[0-9a-f]{4,}|owner-delegation-/);
    // The directive id lives in the secondary tech-id line only.
    expect(within(hoowen).getByText(new RegExp(HOOWEN_DIRECTIVE))).toBeInTheDocument();
  });

  it("keeps the unlinked CI mission outside the Hoowen goal section", () => {
    const { missions, hierarchy } = buildHoowenWorkData();
    renderWorkContent(missions, hierarchy);
    const hoowen = screen.getByRole("region", { name: HOOWEN_LABEL });
    const unlinked = screen.getByRole("region", { name: "Unlinked mission records" });
    expect(within(unlinked).getByText("Overnight CI verification sweep")).toBeInTheDocument();
    expect(
      within(unlinked).getByRole("link", { name: /Overnight CI verification sweep/ }),
    ).toHaveAttribute("href", "/missions/mission-4646c7023b05");
    // Never inside Hoowen: neither the historical title nor its mission link.
    expect(within(hoowen).queryByText("Overnight CI verification sweep")).toBeNull();
    expect(within(hoowen).queryByRole("link", { name: /Overnight CI verification sweep/ })).toBeNull();
    // And Hoowen children never leak down into the unlinked section.
    expect(within(unlinked).queryByText("Hoowen follow-up 3")).toBeNull();
    expect(within(unlinked).queryByText("Hoowen follow-up 2")).toBeNull();
    expect(within(unlinked).queryByText("Hoowen follow-up 1")).toBeNull();
  });

  it("preserves the status filter inside goal nesting while the served card keeps goal truth", () => {
    const { missions, hierarchy } = buildHoowenWorkData();
    renderWorkContent(missions, hierarchy, "/work?status=attention");
    const hoowen = screen.getByRole("region", { name: HOOWEN_LABEL });
    // The served card is goal truth, not the filtered list: the newest
    // COMPLETE result still shows under the attention filter.
    const summary = within(hoowen).getByLabelText("Active goal summary");
    expect(within(summary).getByRole("link", { name: /Hoowen follow-up 2/ })).toHaveAttribute(
      "href",
      "/missions/mission-62250d335ab6",
    );
    // The objective → campaign → mission nesting honors the filter: only the
    // BLOCKED child remains, COMPLETE follow-ups drop out of the list.
    const objective = screen.getByRole("region", { name: "Hoowen workforce management" });
    expect(within(objective).getByRole("link", { name: /Hoowen follow-up 3/ })).toHaveAttribute(
      "href",
      "/missions/mission-fe81cb0d1e37",
    );
    expect(within(objective).queryByRole("link", { name: /Hoowen follow-up 2/ })).toBeNull();
    expect(within(objective).queryByText("Hoowen follow-up 1")).toBeNull();
    // The historical CI mission (BLOCKED) still matches the attention filter
    // in the unlinked section.
    const unlinked = screen.getByRole("region", { name: "Unlinked mission records" });
    expect(within(unlinked).getByText("Overnight CI verification sweep")).toBeInTheDocument();
  });

  it("falls back to flat objective views when every goal is inactive", () => {
    const { missions, hierarchy } = buildAllInactiveWorkData();
    renderWorkContent(missions, hierarchy);
    // No goal chrome: no served heading, card, or history.
    expect(screen.queryByRole("heading", { name: HOOWEN_LABEL })).toBeNull();
    expect(screen.queryByLabelText("Active goal summary")).toBeNull();
    expect(screen.queryByLabelText("Child history")).toBeNull();
    expect(screen.queryByRole("link", { name: "Open current mission" })).toBeNull();
    // Existing objective/campaign views render unchanged.
    expect(screen.getByRole("heading", { name: "Hoowen workforce management" })).toBeInTheDocument();
    const unlinked = screen.getByRole("region", { name: "Unlinked mission records" });
    expect(within(unlinked).getByText("Overnight CI verification sweep")).toBeInTheDocument();
  });

  it("falls back to flat objective views when no goals are published", () => {
    const { missions, hierarchy } = buildEmptyGoalsWorkData();
    renderWorkContent(missions, hierarchy);
    expect(screen.queryByRole("heading", { name: HOOWEN_LABEL })).toBeNull();
    expect(screen.queryByLabelText("Active goal summary")).toBeNull();
    expect(screen.queryByLabelText("Child history")).toBeNull();
    expect(screen.getByRole("heading", { name: "Hoowen workforce management" })).toBeInTheDocument();
    const unlinked = screen.getByRole("region", { name: "Unlinked mission records" });
    expect(within(unlinked).getByText("Overnight CI verification sweep")).toBeInTheDocument();
  });
});
