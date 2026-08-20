import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { Missions } from "../Missions";
import { CommandCenterProvider } from "../../context/CommandCenterContext";

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
