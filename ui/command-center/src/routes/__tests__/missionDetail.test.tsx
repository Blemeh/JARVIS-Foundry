import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { MissionDetail } from "../MissionDetail";
import { CommandCenterProvider } from "../../context/CommandCenterContext";

function renderRoute(path: string) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <CommandCenterProvider>
        <MissionDetail />
      </CommandCenterProvider>
    </MemoryRouter>,
  );
}

describe("Mission Detail", () => {
  it("renders a full non-blank mission surface", async () => {
    const { container } = renderRoute("/missions/M-HYB-014");

    await waitFor(() => expect(screen.getByText("Progress")).toBeInTheDocument());
    expect(screen.getByText("Agent Telemetry Log")).toBeInTheDocument();
    expect(screen.getByText("Verifier Verdicts")).toBeInTheDocument();
    expect(screen.getByText("Active Data Streams")).toBeInTheDocument();

    // Pipeline stages present
    for (const stage of ["Observe", "Plan", "Build", "Verify", "Write Back", "Next Action"]) {
      expect(screen.getAllByText(stage).length).toBeGreaterThan(0);
    }

    // Controls use safe semantics - no "Force Advance"
    expect(screen.getByText("Re-evaluate Gate")).toBeInTheDocument();
    expect(screen.getByText("Halt Execution")).toBeInTheDocument();
    expect(container.textContent).not.toContain("Force Advance");
    expect(container.textContent!.length).toBeGreaterThan(800);
  });

  it("replaces Force Advance with a conditional re-evaluation request", async () => {
    const user = (await import("@testing-library/user-event")).default;
    renderRoute("/missions/M-HYB-014");
    await waitFor(() => expect(screen.getByText("Re-evaluate Gate")).toBeInTheDocument());
    await user.click(screen.getByText("Re-evaluate Gate"));
    expect(screen.getByText(/Advancement stays conditional/i)).toBeInTheDocument();
    await user.click(screen.getByText("Request advance"));
    expect(screen.getByText(/RE-EVALUATION REQUESTED - OUTCOME DEPENDS ON RUNTIME EVIDENCE/i)).toBeInTheDocument();
  });

  it("shows a simulated halt flow without runtime claims", async () => {
    const user = (await import("@testing-library/user-event")).default;
    renderRoute("/missions/M-HYB-014");
    await waitFor(() => expect(screen.getByText("Halt Execution")).toBeInTheDocument());
    await user.click(screen.getByText("Halt Execution"));
    expect(screen.getByText(/Simulated halt request/i)).toBeInTheDocument();
    await user.click(screen.getByText("Request halt"));
    expect(screen.getByText(/HALT REQUEST SIMULATED - NO RUNTIME EFFECT/i)).toBeInTheDocument();
  });
});
