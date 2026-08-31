import { describe, expect, it } from "vitest";
import {
  buildMockObsidianGraph,
  graphDegrees,
  graphSprings,
  seedGraphSimulation,
  settleGraphLayout,
  tickGraphForce,
} from "../obsidianVault";

describe("Obsidian graph force simulation", () => {
  it("counts undirected degrees from wikilinks", () => {
    const graph = buildMockObsidianGraph();
    const degrees = graphDegrees(graph.nodes, graph.links);
    expect(degrees.get("00 - V1 State.md")).toBe(4);
    expect(degrees.get("03 - Decisions.md")).toBe(3);
    expect(degrees.get("04 - Evidence Index.md")).toBe(0);
  });

  it("spreads a clustered seed without NaN after ticks", () => {
    const graph = buildMockObsidianGraph();
    const nodes = seedGraphSimulation(graph.nodes, graph.links);
    const springs = graphSprings(nodes, graph.links);
    for (let i = 0; i < 80; i += 1) {
      tickGraphForce(nodes, springs, 0.9);
    }
    for (const node of nodes) {
      expect(Number.isFinite(node.x)).toBe(true);
      expect(Number.isFinite(node.y)).toBe(true);
    }
    const span = Math.max(...nodes.map((n) => Math.hypot(n.x, n.y)));
    expect(span).toBeGreaterThan(20);
  });

  it("settles a reduced-motion layout into a bounded cluster", () => {
    const graph = buildMockObsidianGraph();
    const settled = settleGraphLayout(graph.nodes, graph.links);
    expect(settled).toHaveLength(graph.nodes.length);
    const xs = settled.map((n) => n.x);
    expect(Math.max(...xs) - Math.min(...xs)).toBeGreaterThan(30);
  });
});
