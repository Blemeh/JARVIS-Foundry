import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { CommandCenterProvider } from "../../context/CommandCenterContext";
import { JarvisCoreStage } from "../jarvis-core/JarvisCoreStage";
import * as webgl from "../jarvis-core/webglRenderer";

function renderCore() {
  return render(
    <MemoryRouter>
      <CommandCenterProvider>
        <JarvisCoreStage activeMissionCount={1} />
      </CommandCenterProvider>
    </MemoryRouter>,
  );
}

describe("JarvisCoreStage", () => {
  const probeSpy = vi.spyOn(webgl, "probeWebGL");

  beforeEach(() => {
    probeSpy.mockReturnValue(false);
  });

  afterEach(() => {
    probeSpy.mockRestore();
  });

  it("uses CSS fallback when WebGL is unavailable", () => {
    renderCore();
    expect(screen.getByLabelText(/JARVIS core visual state/i)).toBeInTheDocument();
    expect(document.querySelector('[data-jarvis-core-mode="fallback"]')).toBeTruthy();
  });

  it("respects prefers-reduced-motion with fallback", () => {
    vi.spyOn(window, "matchMedia").mockImplementation(
      (query: string) =>
        ({
          matches: query.includes("reduce"),
          media: query,
          onchange: null,
          addListener: () => undefined,
          removeListener: () => undefined,
          addEventListener: () => undefined,
          removeEventListener: () => undefined,
          dispatchEvent: () => false,
        }) as MediaQueryList,
    );
    probeSpy.mockReturnValue(true);
    renderCore();
    expect(document.querySelector('[data-jarvis-core-mode="fallback"]')).toBeTruthy();
    vi.mocked(window.matchMedia).mockRestore();
  });
});
