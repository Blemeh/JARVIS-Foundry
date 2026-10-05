import { render, screen, fireEvent } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import type { Mission, WorkObjective } from "../../lib/contracts";
import { Work } from "../Work";
const data = vi.hoisted(() => ({ missions: [] as Mission[], hierarchy: { objectives: [] as WorkObjective[], missions: [], campaigns: [], levels: { objectives: "AVAILABLE", goals: "NOT_YET_INTEGRATED" } } }));
vi.mock("../../context/CommandCenterContext", () => ({ useCommandCenter: () => ({ client: {}, version: 0, sourceMode: "LIVE" }) }));
vi.mock("../../hooks/useRouteQuery", () => ({ useRouteQuery: () => ({ status: "ready", data, reload: vi.fn() }) }));
vi.mock("../../components/RouteQueryGate", () => ({ RouteQueryGate: ({ children }: { children: (value: typeof data) => React.ReactNode }) => children(data) }));
const objective = (id: string, status: string, missionIds: string[] = []): WorkObjective => ({ objective_id: id, label: `${id} summary\n${"Detailed instructions ".repeat(30)}`, status, mission_ids: missionIds, campaign_ids: [], plan_ids: [], action_ids: [], progress_evidence: { status: "UNAVAILABLE", evidence_refs: ["proof-1"] } });
function route(path: string) { render(<MemoryRouter initialEntries={[path]}><Work /></MemoryRouter>); }
describe("owner Work summary", () => {
  it("does not include completed objectives in the active filter", () => {
    data.missions = []; data.hierarchy.objectives = [objective("active", "ACTIVE"), objective("finished", "COMPLETED")];
    route("/work?status=active");
    expect(screen.getByRole("heading", { name: "active summary" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "finished summary" })).not.toBeInTheDocument();
    expect(screen.queryByText("NOT YET INTEGRATED")).not.toBeInTheDocument();
  });
  it("keeps full instructions and evidence behind disclosure", () => {
    data.missions = []; data.hierarchy.objectives = [objective("active", "ACTIVE")];
    route("/work");
    const summary = screen.getByText("Work details · 0 missions");
    const detail = summary.closest("details")!;
    expect(detail.open).toBe(false);
    fireEvent.click(summary);
    expect(detail.open).toBe(true);
    expect(screen.getByRole("link", { name: "Evidence proof-1" })).toHaveAttribute("href", "/evidence/proof-1");
    expect(detail.textContent).toContain("Detailed instructions");
    expect(screen.getByRole("heading", { name: "Needs you" })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Recently completed" })).toBeInTheDocument();
  });
  it("honors direct objective links and opens details", () => {
    data.missions = []; data.hierarchy.objectives = [objective("first", "ACTIVE"), objective("second", "COMPLETED")];
    route("/work?status=all&objective=second");
    expect(screen.queryByRole("heading", { name: "first summary" })).not.toBeInTheDocument();
    expect(screen.getByText("Work details · 0 missions").closest("details")!.open).toBe(true);
  });
});
