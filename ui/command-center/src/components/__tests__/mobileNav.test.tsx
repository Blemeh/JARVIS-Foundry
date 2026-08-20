import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { AppShell } from "../AppShell";
import { CommandCenterProvider } from "../../context/CommandCenterContext";

describe("Mobile navigation", () => {
  it("opens a drawer containing all sections and keeps STOP reachable", async () => {
    const user = userEvent.setup();
    render(
      <MemoryRouter initialEntries={["/"]}>
        <CommandCenterProvider>
          <AppShell />
        </CommandCenterProvider>
      </MemoryRouter>,
    );

    const mobileNav = screen.getByLabelText("Mobile");
    expect(mobileNav).toBeInTheDocument();
    expect(within(mobileNav).getByRole("button", { name: /STOP/i })).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Open all sections" }));
    const drawer = screen.getByRole("dialog", { name: "Navigation" });
    expect(drawer).toBeInTheDocument();
    expect(screen.getAllByText("Timeline").length).toBeGreaterThan(0);
    await user.click(screen.getByRole("button", { name: "Close navigation" }));
    expect(screen.queryByRole("dialog", { name: "Navigation" })).not.toBeInTheDocument();
  });
});
