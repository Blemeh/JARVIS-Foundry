import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { CommandCenterProvider } from "../../context/CommandCenterContext";
import { Capabilities } from "../Capabilities";
import { Work } from "../Work";
import { World } from "../World";
import { You } from "../You";

function renderRoute(path: string, route: React.ReactNode) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <CommandCenterProvider>{route}</CommandCenterProvider>
    </MemoryRouter>,
  );
}

describe("remastered information architecture", () => {
  it("hides missing hierarchy stages behind a compact inspection surface", async () => {
    renderRoute("/work", <Work />);
    await waitFor(() => expect(screen.getByRole("heading", { name: "Work" })).toBeInTheDocument());
    expect(screen.queryByText("NOT YET INTEGRATED")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Work hierarchy contract")).toHaveTextContent("missions");
    expect(screen.getByRole("link", { name: /Hybrid Worker Activation/ })).toHaveAttribute("href", "/missions/M-HYB-014");
  });

  it("does not promote timeline fallback records into world signals", async () => {
    renderRoute("/world", <World />);
    await waitFor(() => expect(screen.getByRole("heading", { name: "World" })).toBeInTheDocument());
    expect(screen.getByText(/not a world-signal projection/i)).toBeInTheDocument();
    expect(screen.getByText(/are not promoted to signals/i)).toBeInTheDocument();
  });

  it("keeps Capability Fabric absence and Gmail scope honest", async () => {
    renderRoute("/capabilities", <Capabilities />);
    await waitFor(() => expect(screen.getByRole("heading", { name: "Capabilities" })).toBeInTheDocument());
    expect(screen.getByText(/Deferred by owner/i)).toBeInTheDocument();
    expect(screen.getByText("DEFERRED_BY_OWNER")).toBeInTheDocument();
    expect(screen.getByText(/No fixture capabilities are substituted/i)).toBeInTheDocument();
    expect(screen.queryByText("NOT YET INTEGRATED")).not.toBeInTheDocument();
    expect(screen.getByText(/Capabilities are unavailable from this source/i)).toBeInTheDocument();
  });

  it("preserves human owner authority without simulated directives", async () => {
    renderRoute("/you", <You />);
    await waitFor(() => expect(screen.getByRole("heading", { name: "You" })).toBeInTheDocument());
    expect(screen.getByText("Owner authority remains human")).toBeInTheDocument();
    expect(screen.getByText(/No simulated goals or preferences are substituted/i)).toBeInTheDocument();
  });
});
