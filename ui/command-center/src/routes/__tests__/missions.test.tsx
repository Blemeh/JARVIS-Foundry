import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { Missions, MissionsContent } from "../Missions";
import { CommandCenterProvider } from "../../context/CommandCenterContext";
import type { Mission } from "../../lib/contracts";

function renderMissions() {
  return render(
    <MemoryRouter initialEntries={["/missions"]}>
      <CommandCenterProvider>
        <Missions />
      </CommandCenterProvider>
    </MemoryRouter>,
  );
}

describe("Missions", () => {
  it("paginates when there are more missions than fit on one page", async () => {
    const user = userEvent.setup();
    renderMissions();
    await waitFor(() => expect(screen.getByText(/Showing 1-6 of 12 missions/i)).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: "Next page" }));
    expect(screen.getByText(/Showing 7-12 of 12 missions/i)).toBeInTheDocument();
  });

  it("labels mission open controls with tooltips and aria-describedby", async () => {
    renderMissions();
    await waitFor(() => expect(screen.getAllByRole("link").length).toBeGreaterThan(0));
    const card = screen.getAllByRole("link").find((l) => l.getAttribute("aria-describedby")?.startsWith("tt-open-"));
    expect(card).toBeTruthy();
    const tooltipId = card!.getAttribute("aria-describedby")!;
    expect(document.getElementById(tooltipId)).toBeInTheDocument();
    expect(document.getElementById(tooltipId)!.textContent).toMatch(/Open mission/i);
  });

  it("filters by status", async () => {
    const user = userEvent.setup();
    renderMissions();
    await waitFor(() => expect(screen.getByText(/Showing 1-6 of 12 missions/i)).toBeInTheDocument());
    await user.click(screen.getByRole("button", { name: /Standby/i }));
    expect(screen.getByText(/Showing 1-3 of 3 missions/i)).toBeInTheDocument();
  });
});

const HOOWEN_DIRECTIVE = "owner-delegation-643042f8f84795e7de8b3d79a4b7142c";

type HoowenTestMission = Mission & { created_from?: string; updated_at?: string };

function hoowenTestMission(init: {
  id: string;
  title: string;
  status: Mission["status"];
  updatedAt: string;
  lineage: string;
}): HoowenTestMission {
  return {
    id: init.id,
    code: init.id,
    title: init.title,
    description: init.title,
    status: init.status,
    priority: "NORMAL",
    agentsActive: 0,
    completion: init.status === "COMPLETE" ? 100 : 0,
    nextAction: "Test next action.",
    stage: init.status,
    startedAt: init.updatedAt,
    objective: `${init.title} objective.`,
    owner: init.lineage,
    created_from: init.lineage,
    updated_at: init.updatedAt,
  };
}

/** Scrambled input order: the default sort (not insertion) must produce the owner-priority order. */
function hoowenSortMissions(): HoowenTestMission[] {
  return [
    hoowenTestMission({
      id: "mission-4646c7023b05",
      title: "Overnight CI verification sweep",
      status: "BLOCKED",
      updatedAt: "2026-08-19T12:00:00Z",
      lineage: "portfolio:pw-dupkey-9f31:CI_FAILURE",
    }),
    hoowenTestMission({
      id: "mission-08a548a685ae",
      title: "Hoowen follow-up 1",
      status: "COMPLETE",
      updatedAt: "2026-10-06T14:00:00Z",
      lineage: HOOWEN_DIRECTIVE,
    }),
    hoowenTestMission({
      id: "mission-executing-01",
      title: "Unrelated executing probe",
      status: "ACTIVE",
      updatedAt: "2026-10-08T08:00:00Z",
      lineage: "portfolio:unrelated-executing",
    }),
    hoowenTestMission({
      id: "mission-62250d335ab6",
      title: "Hoowen follow-up 2",
      status: "COMPLETE",
      updatedAt: "2026-10-06T15:34:00Z",
      lineage: `directive:${HOOWEN_DIRECTIVE}:follow-up-2`,
    }),
    hoowenTestMission({
      id: "mission-fe81cb0d1e37",
      title: "Hoowen follow-up 3",
      status: "BLOCKED",
      updatedAt: "2026-10-06T15:50:00Z",
      lineage: `directive:${HOOWEN_DIRECTIVE}:follow-up-3`,
    }),
  ];
}

function renderHoowenMissionsList(missions: HoowenTestMission[]) {
  return render(
    <MemoryRouter initialEntries={["/missions"]}>
      <CommandCenterProvider>
        <MissionsContent missions={missions} />
      </CommandCenterProvider>
    </MemoryRouter>,
  );
}

function missionCardTitles(): Array<string | null> {
  return screen.getAllByRole("link").map((link) => within(link).getByRole("heading").textContent);
}

function missionCardForCode(code: string): HTMLElement {
  const found = screen
    .getAllByRole("link")
    .find((link) => link.textContent?.includes(code));
  if (!found) throw new Error(`mission card ${code} not rendered`);
  return found as HTMLElement;
}

describe("Missions owner-priority default sort", () => {
  it("orders executing, then attention, then recent completed above the historical CI mission", () => {
    renderHoowenMissionsList(hoowenSortMissions());
    expect(screen.getByText(/Showing 1-5 of 5 missions/i)).toBeInTheDocument();
    expect(missionCardTitles()).toEqual([
      "Unrelated executing probe",
      "Hoowen follow-up 3",
      "Hoowen follow-up 2",
      "Hoowen follow-up 1",
      "Overnight CI verification sweep",
    ]);
  });

  it("marks the historical CI row with a Historical badge and leaves current work unbadged", () => {
    renderHoowenMissionsList(hoowenSortMissions());
    const historicalCard = missionCardForCode("mission-4646c7023b05");
    expect(within(historicalCard).getByText("Historical")).toBeInTheDocument();
    expect(historicalCard.querySelector(".badge")).not.toBeNull();
    for (const code of ["mission-executing-01", "mission-fe81cb0d1e37", "mission-62250d335ab6", "mission-08a548a685ae"]) {
      expect(within(missionCardForCode(code)).queryByText("Historical")).toBeNull();
    }
  });

  it("explicit sort=title still overrides the default order", async () => {
    const user = userEvent.setup();
    renderHoowenMissionsList(hoowenSortMissions());
    expect(screen.getByText(/Showing 1-5 of 5 missions/i)).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Sort missions"), "title");
    expect(missionCardTitles()).toEqual([
      "Hoowen follow-up 1",
      "Hoowen follow-up 2",
      "Hoowen follow-up 3",
      "Overnight CI verification sweep",
      "Unrelated executing probe",
    ]);
  });

  it("explicit sort=status still overrides the default order", async () => {
    const user = userEvent.setup();
    renderHoowenMissionsList(hoowenSortMissions());
    expect(screen.getByText(/Showing 1-5 of 5 missions/i)).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Sort missions"), "status");
    expect(missionCardTitles()).toEqual([
      "Unrelated executing probe",
      "Overnight CI verification sweep",
      "Hoowen follow-up 3",
      "Hoowen follow-up 1",
      "Hoowen follow-up 2",
    ]);
  });
});
