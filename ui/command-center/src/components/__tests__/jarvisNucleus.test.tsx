import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { JarvisNucleus, type JarvisNucleusState } from "../JarvisNucleus";

describe("JarvisNucleus", () => {
  it.each<JarvisNucleusState>([
    "WAITING",
    "OBSERVING",
    "PLANNING",
    "RESEARCHING",
    "EXECUTING",
    "VERIFYING",
    "RECOVERING",
    "BLOCKED",
    "NEEDS_OWNER",
    "STOPPED",
    "UNKNOWN",
  ])("publishes the %s operational state without collapsing semantics", (state) => {
    const { container } = render(<JarvisNucleus state={state} phase="Canonical phase" />);
    const figure = container.querySelector("figure");
    expect(figure).toHaveAttribute("data-jarvis-core-state", state.toLowerCase());
    expect(screen.getByText(state.replace("_", " "))).toBeInTheDocument();
    expect(screen.getByText("Canonical phase")).toBeInTheDocument();
  });
});
