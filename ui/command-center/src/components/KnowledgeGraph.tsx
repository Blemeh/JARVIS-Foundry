import { useMemo, useState } from "react";
import { Maximize2, Minus, Plus } from "lucide-react";
import type { MemoryGraph, MemoryNode } from "../lib/contracts";
import { categoryColors, categoryLegend } from "../lib/graphMeta";

interface KnowledgeGraphProps {
  graph: MemoryGraph;
  selectedId: string | null;
  onSelect: (node: MemoryNode) => void;
}

export function KnowledgeGraph({ graph, selectedId, onSelect }: KnowledgeGraphProps) {
  const [zoom, setZoom] = useState(1);
  const nodes = graph.nodes;

  const edges = useMemo(
    () =>
      graph.edges
        .map((e) => {
          const from = nodes.find((n) => n.id === e.from);
          const to = nodes.find((n) => n.id === e.to);
          return from && to ? { from, to, label: e.label } : null;
        })
        .filter(Boolean) as { from: MemoryNode; to: MemoryNode; label: string }[],
    [graph, nodes],
  );

  const edgeMidpoint = (a: { x: number; y: number }, b: { x: number; y: number }) => {
    const mx = (a.x + b.x) / 2;
    const my = (a.y + b.y) / 2;
    const dx = b.x - a.x;
    const dy = b.y - a.y;
    const len = Math.hypot(dx, dy) || 1;
    return { x: mx + (dy / len) * 2.6, y: my - (dx / len) * 2.6 };
  };

  const edgeLabelVisible = (mid: { x: number; y: number }) =>
    !nodes.some((n) => Math.hypot(n.x - mid.x, n.y - mid.y) < n.r + 4);

  const inView = useMemo(() => {
    const ids = new Set<string>();
    edges.forEach((e) => {
      ids.add(e.from.id);
      ids.add(e.to.id);
    });
    return nodes.filter((n) => ids.has(n.id) || n.id === "n-core");
  }, [edges, nodes]);

  return (
    <div className="graph-wrap" aria-label="Knowledge graph visualization">
      <div className="graph-canvas" style={{ transform: `scale(${zoom})` }}>
        <svg viewBox="0 0 100 100" className="graph-svg">
          <defs>
            <pattern id="graph-grid" width="6.25" height="6.25" patternUnits="userSpaceOnUse">
              <path d="M 6.25 0 L 0 0 0 6.25" fill="none" stroke="rgba(255,255,255,0.045)" strokeWidth="0.08" />
            </pattern>
          </defs>
          <rect width="100" height="100" fill="url(#graph-grid)" />
          {edges.map((e, i) => (
            <g key={i}>
              <line
                x1={e.from.x}
                y1={e.from.y}
                x2={e.to.x}
                y2={e.to.y}
                className="graph-edge"
                stroke={selectedId === e.from.id || selectedId === e.to.id ? "#22d3ee" : "#3c494c"}
                strokeWidth={selectedId === e.from.id || selectedId === e.to.id ? 0.45 : 0.3}
              />
              {edgeLabelVisible(edgeMidpoint(e.from, e.to)) ? (
                <text
                  x={edgeMidpoint(e.from, e.to).x}
                  y={edgeMidpoint(e.from, e.to).y}
                  textAnchor="middle"
                  className="graph-edge-label"
                >
                  {e.label}
                </text>
              ) : null}
            </g>
          ))}
          {inView.map((n) => {
            const selected = n.id === selectedId;
            const color = categoryColors[n.category];
            return (
              <g key={n.id} className="graph-node-g">
                {selected ? (
                  <circle cx={n.x} cy={n.y} r={n.r + 3.2} fill="none" stroke={color} strokeWidth="0.35" strokeDasharray="1.2 0.8" />
                ) : null}
                <circle
                  cx={n.x}
                  cy={n.y}
                  r={n.r}
                  fill={selected ? color : "rgba(18,18,20,0.96)"}
                  stroke={color}
                  strokeWidth={selected ? 0.6 : 0.4}
                  className="graph-node-hit"
                  role="button"
                  aria-label={`Select ${n.label}`}
                  tabIndex={0}
                  onKeyDown={(e) => {
                    if (e.key === "Enter" || e.key === " ") {
                      e.preventDefault();
                      onSelect(n);
                    }
                  }}
                  onClick={() => onSelect(n)}
                />
                <text
                  x={n.x}
                  y={n.y - n.r - 3}
                  textAnchor="middle"
                  className="graph-node-label"
                  fill={selected ? "#ffffff" : "#bbc9cd"}
                >
                  {n.label}
                </text>
              </g>
            );
          })}
        </svg>
      </div>
      <div className="graph-zoom" role="group" aria-label="Graph zoom controls">
        <button type="button" className="icon-btn" aria-label="Zoom in" onClick={() => setZoom((z) => Math.min(1.8, z + 0.15))}>
          <Plus size={16} />
        </button>
        <button type="button" className="icon-btn" aria-label="Zoom out" onClick={() => setZoom((z) => Math.max(0.6, z - 0.15))}>
          <Minus size={16} />
        </button>
        <button type="button" className="icon-btn" aria-label="Reset zoom" onClick={() => setZoom(1)}>
          <Maximize2 size={15} />
        </button>
      </div>
      <div className="graph-legend" aria-label="Graph legend">
        <p className="t-label text-muted graph-legend-title">Semantic categories</p>
        {categoryLegend.map((c) => (
          <span key={c.key} className="graph-legend-item" title={c.note}>
            <span className="graph-legend-dot" style={{ background: categoryColors[c.key] }} aria-hidden="true" />
            <span className="t-label">{c.label}</span>
          </span>
        ))}
      </div>
    </div>
  );
}
