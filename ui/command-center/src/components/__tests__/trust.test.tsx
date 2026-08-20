import { render, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { TrustDialog } from "../TrustDialog";
import { CommandCenterProvider } from "../../context/CommandCenterContext";

describe("Trust model", () => {
  it("never renders the hard-coded 99.8% and explains its state", async () => {
    const { container } = render(
      <CommandCenterProvider>
        <TrustDialog open onClose={() => undefined} />
      </CommandCenterProvider>,
    );

    await waitFor(() => expect(screen.getAllByText("Evidence coverage").length).toBeGreaterThan(0));
    expect(container.textContent).not.toContain("99.8");
    expect(screen.getByText(/SCORE: NOT_AVAILABLE/i)).toBeInTheDocument();
    expect(screen.getByText("Gate")).toBeInTheDocument();
    expect(screen.getByText("GATED")).toBeInTheDocument();
    expect(screen.getByText(/SIMULATED FIXTURE - not canonical JARVIS truth/i)).toBeInTheDocument();
  });

  it("exposes per-factor explanations", async () => {
    render(
      <CommandCenterProvider>
        <TrustDialog open onClose={() => undefined} />
      </CommandCenterProvider>,
    );
    await waitFor(() => expect(screen.getAllByText("Evidence coverage").length).toBeGreaterThan(0));
    expect(screen.getAllByText("Open findings").length).toBeGreaterThan(0);
    expect(screen.getByText(/ledger mismatch EV-1131-007/i)).toBeInTheDocument();
  });
});
