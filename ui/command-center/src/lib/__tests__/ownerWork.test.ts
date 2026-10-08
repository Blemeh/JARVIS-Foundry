import { describe, expect, it } from "vitest";
import type { Mission, OwnerAttentionRecord, WorkHierarchy } from "../contracts";
import {
  currentChildForGoal,
  goalChildren,
  isHistoricalMission,
  ownerActionForMission,
  plainSummary,
  rankOwnerGoals,
  servingGoalLabel,
} from "../ownerWork";

const HOOWEN_DIRECTIVE = "owner-delegation-643042f8f84795e7de8b3d79a4b7142c";

type TestMission = Mission & { created_from?: string; updated_at?: string };

function makeMission(init: {
  id: string;
  status: Mission["status"];
  updatedAt: string;
  createdFrom?: string;
  owner: string;
  objective?: string;
  nextAction?: string;
}): TestMission {
  return {
    id: init.id,
    code: init.id,
    title: init.id,
    description: init.id,
    status: init.status,
    priority: "NORMAL",
    agentsActive: 0,
    completion: init.status === "COMPLETE" ? 100 : 0,
    nextAction: init.nextAction ?? "UNKNOWN",
    stage: init.status,
    startedAt: init.updatedAt,
    objective: init.objective ?? `${init.id} objective.`,
    owner: init.owner,
    ...(init.createdFrom !== undefined ? { created_from: init.createdFrom } : {}),
    updated_at: init.updatedAt,
  };
}

const goals = [
  {
    directive_id: "owner-goal-retired-009",
    active: 0,
    command_type: "DELEGATE",
    updated_at: "2026-10-07T09:00:00Z",
    label: "Retired goal",
  },
  {
    directive_id: "owner-goal-older-001",
    active: 1,
    command_type: "GOAL",
    updated_at: "2026-09-01T10:00:00Z",
    label: "Older standing goal",
  },
  {
    directive_id: HOOWEN_DIRECTIVE,
    active: 1,
    command_type: "DELEGATE",
    updated_at: "2026-10-06T15:50:00Z",
    label: "Hoowen — Active strategic goal",
  },
];

const missions: TestMission[] = [
  makeMission({
    id: "mission-08a548a685ae",
    status: "COMPLETE",
    updatedAt: "2026-10-06T14:00:00Z",
    createdFrom: HOOWEN_DIRECTIVE,
    owner: HOOWEN_DIRECTIVE,
    objective: "Hoowen follow-up 1 shipped with verified evidence.",
    nextAction: "Start follow-up 2.",
  }),
  makeMission({
    id: "mission-62250d335ab6",
    status: "COMPLETE",
    updatedAt: "2026-10-06T15:34:00Z",
    createdFrom: `directive:${HOOWEN_DIRECTIVE}:follow-up-2`,
    owner: `directive:${HOOWEN_DIRECTIVE}:follow-up-2`,
    objective: "Hoowen follow-up 2 complete with its outcome summary.",
    nextAction: "Start follow-up 3.",
  }),
  makeMission({
    id: "mission-fe81cb0d1e37",
    status: "BLOCKED",
    updatedAt: "2026-10-06T15:50:00Z",
    createdFrom: `directive:${HOOWEN_DIRECTIVE}:follow-up-3`,
    owner: `directive:${HOOWEN_DIRECTIVE}:follow-up-3`,
    objective: "Hoowen follow-up 3 blocked after builder failure.",
    nextAction: "Repair the builder failure and re-run verification.",
  }),
  // Carries the directive id on owner only (mapMissions shape); still a Hoowen child.
  makeMission({
    id: "mission-owner-only-001",
    status: "COMPLETE",
    updatedAt: "2026-10-05T10:00:00Z",
    owner: HOOWEN_DIRECTIVE,
    objective: "Early Hoowen probe completed.",
  }),
  // created_from wins over owner: this is NOT a Hoowen child.
  makeMission({
    id: "mission-precedence-01",
    status: "COMPLETE",
    updatedAt: "2026-10-06T16:00:00Z",
    createdFrom: "portfolio:unrelated-thing",
    owner: HOOWEN_DIRECTIVE,
    objective: "Unrelated portfolio work.",
  }),
  makeMission({
    id: "mission-4646c7023b05",
    status: "BLOCKED",
    updatedAt: "2026-08-19T12:00:00Z",
    createdFrom: "portfolio:pw-dupkey-9f31:CI_FAILURE",
    owner: "portfolio:pw-dupkey-9f31:CI_FAILURE",
    objective: "Historical CI verification failure.",
    nextAction: "Fix the CI pipeline.",
  }),
  // Newest mission overall, but unlinked: recency alone must never promote it.
  makeMission({
    id: "mission-9c9c9c9c9c01",
    status: "BLOCKED",
    updatedAt: "2026-10-07T12:00:00Z",
    createdFrom: "portfolio:some-other-thing",
    owner: "portfolio:some-other-thing",
    objective: "Unlinked blocked work newer than Hoowen.",
  }),
  // Linked to Hoowen only through the work hierarchy (no created_from match).
  makeMission({
    id: "mission-hier-linked-01",
    status: "COMPLETE",
    updatedAt: "2026-10-06T12:00:00Z",
    createdFrom: "something:unrelated",
    owner: "something:unrelated",
    objective: "Hierarchy-linked Hoowen outcome.",
  }),
  makeMission({
    id: "mission-hier-blocked-02",
    status: "BLOCKED",
    updatedAt: "2026-10-06T13:00:00Z",
    createdFrom: "something:unrelated",
    owner: "something:unrelated",
    objective: "Hierarchy-linked Hoowen work now blocked.",
  }),
  makeMission({
    id: "mission-older-child-01",
    status: "COMPLETE",
    updatedAt: "2026-09-02T10:00:00Z",
    owner: "owner-goal-older-001",
    objective: "Older goal outcome.",
  }),
  makeMission({
    id: "mission-executing-01",
    status: "ACTIVE",
    updatedAt: "2026-10-08T08:00:00Z",
    createdFrom: "portfolio:unrelated-executing",
    owner: "portfolio:unrelated-executing",
    objective: "Unrelated executing work.",
    nextAction: "Keep executing.",
  }),
  makeMission({
    id: "mission-halted-01",
    status: "HALTED",
    updatedAt: "2026-09-10T10:00:00Z",
    createdFrom: "portfolio:old-halt",
    owner: "portfolio:old-halt",
    objective: "Old halted work.",
  }),
  makeMission({
    id: "mission-cancelled-01",
    status: "CANCELLED",
    updatedAt: "2026-09-11T10:00:00Z",
    createdFrom: "portfolio:old-cancel",
    owner: "portfolio:old-cancel",
    objective: "Old cancelled work.",
  }),
];

const hierarchy = {
  schema: "v13.command-center-work.v1",
  status: "AVAILABLE",
  authority: "canonical-ledger",
  ui_authority: "NONE",
  levels: {},
  goals: [
    {
      goal_id: "goal-hoowen-1",
      directive_id: HOOWEN_DIRECTIVE,
      label: "Hoowen — Active strategic goal",
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
      campaign_ids: [],
      mission_ids: ["mission-hier-linked-01", "mission-hier-blocked-02"],
      plan_ids: [],
      action_ids: [],
      progress_evidence: { status: "active", evidence_refs: [] },
    },
  ],
  campaigns: [],
  missions: [
    {
      mission_id: "mission-hier-linked-01",
      objective_id: "obj-hoowen-1",
      status: "COMPLETE",
      action_ids: [],
      worker_ids: [],
      evidence_refs: [],
      verdict_ids: [],
    },
    {
      mission_id: "mission-hier-blocked-02",
      objective_id: "obj-hoowen-1",
      status: "BLOCKED",
      action_ids: [],
      worker_ids: [],
      evidence_refs: [],
      verdict_ids: [],
    },
  ],
  actions: [],
} as unknown as WorkHierarchy;

const byId = (id: string): TestMission => missions.find((m) => m.id === id)!;

function openRecord(
  queueId: string,
  missionId: string,
  extra: Partial<OwnerAttentionRecord> = {},
): OwnerAttentionRecord {
  return {
    queue_id: queueId,
    status: "OPEN",
    classification: "DECISION_REQUIRED",
    summary: "Approve the builder repair plan.",
    consequence: "Repair stays paused until you approve.",
    mission_id: missionId,
    opened_at: "2026-10-06T16:00:00Z",
    ...extra,
  };
}

describe("ownerWork — Hoowen DELEGATE goal outranks historical CI BLOCKED", () => {
  it("ranks the active Hoowen DELEGATE goal first by directive recency", () => {
    const ranked = rankOwnerGoals(goals, missions, hierarchy, null);
    expect(ranked.map((g) => g.directive_id)).toEqual([
      HOOWEN_DIRECTIVE,
      "owner-goal-older-001",
      "owner-goal-retired-009",
    ]);
  });

  it("current child is the newest created_from match, not the historical row", () => {
    expect(currentChildForGoal(HOOWEN_DIRECTIVE, missions)?.id).toBe("mission-fe81cb0d1e37");
    expect(currentChildForGoal("no-such-goal", missions)).toBeNull();
  });

  it("falls back to owner when created_from is absent", () => {
    expect(currentChildForGoal("owner-goal-older-001", missions)?.id).toBe(
      "mission-older-child-01",
    );
  });

  it("lists goal children newest-first via created_from and hierarchy links", () => {
    expect(goalChildren(HOOWEN_DIRECTIVE, missions, hierarchy).map((m) => m.id)).toEqual([
      "mission-fe81cb0d1e37",
      "mission-62250d335ab6",
      "mission-08a548a685ae",
      "mission-hier-blocked-02",
      "mission-hier-linked-01",
      "mission-owner-only-001",
    ]);
  });

  it("treats the old CI mission as historical by linkage, not recency", () => {
    // Historical even though it is the oldest row; newest unlinked BLOCKED is historical too.
    expect(isHistoricalMission(byId("mission-4646c7023b05"), [HOOWEN_DIRECTIVE], [])).toBe(true);
    expect(isHistoricalMission(byId("mission-9c9c9c9c9c01"), [HOOWEN_DIRECTIVE], [])).toBe(true);
    expect(isHistoricalMission(byId("mission-halted-01"), [HOOWEN_DIRECTIVE], [])).toBe(true);
    expect(isHistoricalMission(byId("mission-cancelled-01"), [HOOWEN_DIRECTIVE], [])).toBe(true);
    // Linked Hoowen BLOCKED child is current work, never history.
    expect(isHistoricalMission(byId("mission-fe81cb0d1e37"), [HOOWEN_DIRECTIVE], [])).toBe(false);
    // Non-terminal states are never historical.
    expect(isHistoricalMission(byId("mission-08a548a685ae"), [HOOWEN_DIRECTIVE], [])).toBe(false);
    expect(isHistoricalMission(byId("mission-executing-01"), [HOOWEN_DIRECTIVE], [])).toBe(false);
  });

  it("keeps hierarchy-linked BLOCKED work out of history", () => {
    expect(
      isHistoricalMission(byId("mission-hier-blocked-02"), [HOOWEN_DIRECTIVE], [], hierarchy),
    ).toBe(false);
    expect(isHistoricalMission(byId("mission-hier-blocked-02"), [HOOWEN_DIRECTIVE], [])).toBe(true);
  });

  it("labels the serving goal without borrowing across missions", () => {
    expect(servingGoalLabel(byId("mission-fe81cb0d1e37"), goals, hierarchy)).toBe(
      "Hoowen — Active strategic goal",
    );
    expect(servingGoalLabel(byId("mission-hier-linked-01"), goals, hierarchy)).toBe(
      "Hoowen — Active strategic goal",
    );
    expect(servingGoalLabel(byId("mission-older-child-01"), goals, hierarchy)).toBe(
      "Older standing goal",
    );
    expect(servingGoalLabel(byId("mission-4646c7023b05"), goals, hierarchy)).toBe("UNKNOWN");
    expect(servingGoalLabel(byId("mission-precedence-01"), goals, hierarchy)).toBe("UNKNOWN");
  });

  it("returns null owner action without a linked open decision record", () => {
    const blocked = byId("mission-fe81cb0d1e37");
    expect(ownerActionForMission(blocked, [])).toBeNull();
    expect(
      ownerActionForMission(blocked, [
        { ...openRecord("q-resolved", blocked.id), status: "RESOLVED" },
      ]),
    ).toBeNull();
    expect(
      ownerActionForMission(blocked, [openRecord("q-other", "mission-4646c7023b05")]),
    ).toBeNull();
    expect(ownerActionForMission(blocked, [openRecord("q-1", blocked.id)])).toBe(
      "Approve the builder repair plan.",
    );
  });

  it("falls back to the decision consequence when no summary is recorded", () => {
    const blocked = byId("mission-fe81cb0d1e37");
    expect(
      ownerActionForMission(blocked, [
        openRecord("q-2", blocked.id, { summary: undefined, consequence: "Wait for parts." }),
      ]),
    ).toBe("Wait for parts.");
    expect(
      ownerActionForMission(blocked, { open: [openRecord("q-3", blocked.id)] }),
    ).toBe("Approve the builder repair plan.");
  });

  it("summarizes verbatim leading sentences without paraphrase", () => {
    const objective =
      "Turn Hoowen into a workforce product. Ship follow-up two with verified outcomes. Repair the blocked builder run. Re-verify evidence. Brief the owner.";
    expect(plainSummary(objective)).toBe(
      "Turn Hoowen into a workforce product. Ship follow-up two with verified outcomes. Repair the blocked builder run.",
    );
    expect(plainSummary(objective, 2)).toBe(
      "Turn Hoowen into a workforce product. Ship follow-up two with verified outcomes.",
    );
    expect(plainSummary(objective, 10)).toBe(objective);
    expect(plainSummary("Rebuild the dashboard")).toBe("Rebuild the dashboard");
    expect(plainSummary("")).toBe("UNKNOWN");
  });
});
