import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { RouteErrorBoundary } from "../RouteErrorBoundary";

function Boom(): null {
  throw new Error("render exploded");
}

describe("RouteErrorBoundary", () => {
  it("contains route render failures and allows retry", async () => {
    const user = userEvent.setup();
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);

    const { rerender } = render(
      <MemoryRouter>
        <RouteErrorBoundary resetKey="/missions">
          <Boom />
        </RouteErrorBoundary>
      </MemoryRouter>,
    );

    expect(screen.getByText("This section failed to render")).toBeInTheDocument();
    expect(screen.getByText("render exploded")).toBeInTheDocument();

    rerender(
      <MemoryRouter>
        <RouteErrorBoundary resetKey="/missions">
          <div>recovered</div>
        </RouteErrorBoundary>
      </MemoryRouter>,
    );

    await user.click(screen.getByRole("button", { name: "Try again" }));
    expect(screen.getByText("recovered")).toBeInTheDocument();

    consoleError.mockRestore();
  });

  it("clears error state when resetKey changes", () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => undefined);

    const { rerender } = render(
      <MemoryRouter>
        <RouteErrorBoundary resetKey="/missions">
          <Boom />
        </RouteErrorBoundary>
      </MemoryRouter>,
    );

    expect(screen.getByText("render exploded")).toBeInTheDocument();

    rerender(
      <MemoryRouter>
        <RouteErrorBoundary resetKey="/agents">
          <div>agents ok</div>
        </RouteErrorBoundary>
      </MemoryRouter>,
    );

    expect(screen.getByText("agents ok")).toBeInTheDocument();
    consoleError.mockRestore();
  });
});
