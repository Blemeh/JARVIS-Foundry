import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { CommandCenterProvider } from "../../context/CommandCenterContext";
import { canResume, StopControl, EmergencyStopDialog } from "../EmergencyStop";

function renderStop() {
  return render(
    <CommandCenterProvider>
      <StopControl />
      <EmergencyStopDialog />
    </CommandCenterProvider>,
  );
}

describe("Emergency STOP", () => {
  it("keeps live Resume available while canonical STOP is active", () => {
    expect(canResume(true, "LIVE")).toBe(true);
    expect(canResume(true, "DISCONNECTED")).toBe(false);
    expect(canResume(false, "SIMULATED")).toBe(true);
  });

  it("is prominent, opens a dialog, and only simulates in simulated mode", async () => {
    const fetchSpy = vi.spyOn(window, "fetch");
    const user = userEvent.setup();
    renderStop();

    const control = screen.getByRole("button", { name: /Emergency STOP/i });
    expect(control).toBeInTheDocument();
    await user.click(control);

    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByText(/not connected to the JARVIS Supervisor/i)).toBeInTheDocument();

    await user.click(screen.getByText("Simulate STOP"));

    await waitFor(() => expect(screen.getByText(/SIMULATED STOP - UI CANDIDATE/i)).toBeInTheDocument());
    expect(screen.getByText(/NO PRODUCTION SIDE EFFECT/i)).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /STOP simulation active/i })).toBeInTheDocument();
    expect(fetchSpy).not.toHaveBeenCalled();
    fetchSpy.mockRestore();
  });

  it("can be reset back to the normal control", async () => {
    const user = userEvent.setup();
    renderStop();
    await user.click(screen.getByRole("button", { name: /Emergency STOP/i }));
    await user.click(screen.getByText("Simulate STOP"));
    const armed = await screen.findByRole("button", { name: /STOP simulation active/i });
    await user.click(armed);
    await waitFor(() => expect(screen.getByRole("button", { name: /Resume/i })).toBeInTheDocument());
    await user.click(screen.getByText("Resume"));
    expect(screen.getByRole("button", { name: /Emergency STOP/i })).toBeInTheDocument();
  });
});
