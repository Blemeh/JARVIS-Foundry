import type { FoundrySnapshot, FoundryVaultFile } from "./snapshotTypes";

export interface ObsidianVaultFile {
  relPath: string;
  contentSha256?: string;
  updatedAt?: string;
}

export const OBSIDIAN_VAULT_FIXTURE: ObsidianVaultFile[] = [
  { relPath: "00 - V1 State.md" },
  { relPath: "01 - Registered Project.md" },
  { relPath: "02 - Active Mission.md" },
  { relPath: "03 - Decisions.md" },
  { relPath: "04 - Evidence Index.md" },
  { relPath: "05 - Failure and Repair Log.md" },
  { relPath: "06 - Lessons.md" },
  { relPath: "07 - Daily State.md" },
];

export function formatObsidianNoteLabel(relPath: string): string {
  const withoutExt = relPath.replace(/\.(md|markdown|txt)$/i, "");
  const withoutPrefix = withoutExt.replace(/^\d+\s*-\s*/, "");
  const label = withoutPrefix.trim() || withoutExt;
  return label.length > 22 ? `${label.slice(0, 20)}…` : label;
}

export function mapObsidianVault(snapshot: FoundrySnapshot): ObsidianVaultFile[] {
  const rows = snapshot.vault?.files ?? [];
  return rows
    .filter((row) => row.rel_path)
    .map((row: FoundryVaultFile) => ({
      relPath: row.rel_path,
      contentSha256: row.content_sha256,
      updatedAt: row.updated_at,
    }));
}

export function orbitPosition(index: number, total: number): { left: number; top: number } {
  if (total <= 0) return { left: 50, top: 50 };
  const angle = (index / total) * Math.PI * 2 - Math.PI / 2;
  const radiusX = 38;
  const radiusY = 36;
  return {
    left: 50 + Math.cos(angle) * radiusX,
    top: 50 + Math.sin(angle) * radiusY,
  };
}

export type ObsidianGraphNode = {
  id: string;
  label: string;
  relPath: string;
};

export type ObsidianGraphLink = {
  source: string;
  target: string;
};

export type ObsidianGraph = {
  vaultRoot?: string;
  nodes: ObsidianGraphNode[];
  links: ObsidianGraphLink[];
  count?: number;
};

export type ObsidianNote = {
  relPath: string;
  label: string;
  content: string;
  vaultRoot?: string;
  absolutePath?: string;
};

/** Fixture wikilinks mirroring the governed V1 note set. */
const MOCK_GRAPH_LINKS: ObsidianGraphLink[] = [
  { source: "00 - V1 State.md", target: "01 - Registered Project.md" },
  { source: "00 - V1 State.md", target: "02 - Active Mission.md" },
  { source: "00 - V1 State.md", target: "07 - Daily State.md" },
  { source: "03 - Decisions.md", target: "00 - V1 State.md" },
  { source: "03 - Decisions.md", target: "05 - Failure and Repair Log.md" },
  { source: "03 - Decisions.md", target: "06 - Lessons.md" },
];

export function buildMockObsidianGraph(): ObsidianGraph {
  const nodes = OBSIDIAN_VAULT_FIXTURE.map((file) => ({
    id: file.relPath,
    label: formatObsidianNoteLabel(file.relPath),
    relPath: file.relPath,
  }));
  return { nodes, links: MOCK_GRAPH_LINKS, count: nodes.length };
}

export async function fetchObsidianGraph(apiBase = "/api"): Promise<ObsidianGraph> {
  const response = await fetch(`${apiBase}/vault/graph`, { headers: { Accept: "application/json" } });
  if (!response.ok) throw new Error(`vault graph HTTP ${response.status}`);
  const payload = (await response.json()) as {
    vault_root?: string;
    nodes?: Array<{ id: string; label: string; rel_path: string }>;
    links?: Array<{ source: string; target: string }>;
    count?: number;
  };
  return {
    vaultRoot: payload.vault_root,
    nodes: (payload.nodes ?? []).map((n) => ({
      id: n.id,
      label: n.label,
      relPath: n.rel_path,
    })),
    links: (payload.links ?? []).map((l) => ({ source: l.source, target: l.target })),
    count: payload.count,
  };
}

export async function fetchObsidianNote(relPath: string, apiBase = "/api"): Promise<ObsidianNote> {
  const response = await fetch(
    `${apiBase}/vault/note?path=${encodeURIComponent(relPath)}`,
    { headers: { Accept: "application/json" } },
  );
  if (!response.ok) throw new Error(`vault note HTTP ${response.status}`);
  const payload = (await response.json()) as {
    rel_path: string;
    label: string;
    content: string;
    vault_root?: string;
    absolute_path?: string;
  };
  return {
    relPath: payload.rel_path,
    label: payload.label,
    content: payload.content,
    vaultRoot: payload.vault_root,
    absolutePath: payload.absolute_path,
  };
}

export function obsidianOpenUrl(absolutePath: string): string {
  return `obsidian://open?path=${encodeURIComponent(absolutePath)}`;
}

export type GraphSimNode = ObsidianGraphNode & {
  x: number;
  y: number;
  vx: number;
  vy: number;
  degree: number;
  radius: number;
  fx: number | null;
  fy: number | null;
};

export type GraphSpring = {
  source: GraphSimNode;
  target: GraphSimNode;
  /** d3-style bias: hub links pull less so dense centres do not collapse. */
  strength: number;
};

function hash01(value: string): number {
  let hash = 2166136261;
  for (let i = 0; i < value.length; i += 1) {
    hash ^= value.charCodeAt(i);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0) / 4294967296;
}

export function graphDegrees(
  nodes: ObsidianGraphNode[],
  links: ObsidianGraphLink[],
): Map<string, number> {
  const degrees = new Map<string, number>();
  for (const node of nodes) degrees.set(node.id, 0);
  for (const link of links) {
    if (!degrees.has(link.source) || !degrees.has(link.target)) continue;
    degrees.set(link.source, (degrees.get(link.source) ?? 0) + 1);
    degrees.set(link.target, (degrees.get(link.target) ?? 0) + 1);
  }
  return degrees;
}

export function nodeRadiusForDegree(degree: number): number {
  return 3.2 + Math.sqrt(Math.max(degree, 0)) * 2.4;
}

/** Seed near the origin so the live force sim can bloom outward like Obsidian. */
export function seedGraphSimulation(
  nodes: ObsidianGraphNode[],
  links: ObsidianGraphLink[],
): GraphSimNode[] {
  const degrees = graphDegrees(nodes, links);
  return nodes.map((node) => {
    const degree = degrees.get(node.id) ?? 0;
    const a = hash01(node.id);
    const b = hash01(`${node.id}:y`);
    return {
      ...node,
      x: (a - 0.5) * 48,
      y: (b - 0.5) * 48,
      vx: 0,
      vy: 0,
      degree,
      radius: nodeRadiusForDegree(degree),
      fx: null,
      fy: null,
    };
  });
}

export function graphSprings(nodes: GraphSimNode[], links: ObsidianGraphLink[]): GraphSpring[] {
  const byId = new Map(nodes.map((node) => [node.id, node]));
  const springs: GraphSpring[] = [];
  for (const link of links) {
    const source = byId.get(link.source);
    const target = byId.get(link.target);
    if (!source || !target || source === target) continue;
    const strength = 1 / Math.max(1, Math.min(source.degree, target.degree));
    springs.push({ source, target, strength });
  }
  return springs;
}

const CHARGE_STRENGTH = -900;
const LINK_DISTANCE = 78;
const CENTERING = 0.055;
const VELOCITY_DECAY = 0.62;

/**
 * One d3-force style tick: many-body repulsion, degree-weighted link springs,
 * collision separation, and weak centering. Hub links are deliberately weak so
 * a vault with a few high-degree notes still spreads instead of collapsing.
 */
export function tickGraphForce(
  nodes: GraphSimNode[],
  springs: GraphSpring[],
  alpha: number,
): void {
  for (let i = 0; i < nodes.length; i += 1) {
    const a = nodes[i];
    for (let j = i + 1; j < nodes.length; j += 1) {
      const b = nodes[j];
      let dx = b.x - a.x;
      let dy = b.y - a.y;
      let distSq = dx * dx + dy * dy;
      if (distSq < 0.5) {
        dx = (i % 7) - 3 + 0.5;
        dy = (j % 5) - 2 + 0.5;
        distSq = dx * dx + dy * dy || 1;
      }
      const charge = (CHARGE_STRENGTH * alpha) / distSq;
      if (a.fx == null) {
        a.vx += dx * charge;
        a.vy += dy * charge;
      }
      if (b.fx == null) {
        b.vx -= dx * charge;
        b.vy -= dy * charge;
      }

      const dist = Math.sqrt(distSq);
      const minDist = a.radius + b.radius + 16;
      if (dist < minDist) {
        const push = ((minDist - dist) / dist) * 0.5;
        const px = dx * push;
        const py = dy * push;
        if (a.fx == null) {
          a.vx -= px;
          a.vy -= py;
        }
        if (b.fx == null) {
          b.vx += px;
          b.vy += py;
        }
      }
    }
  }

  for (const spring of springs) {
    const dx = spring.target.x - spring.source.x;
    const dy = spring.target.y - spring.source.y;
    const dist = Math.hypot(dx, dy) || 1;
    const pull = ((dist - LINK_DISTANCE) / dist) * alpha * spring.strength;
    const fx = dx * pull;
    const fy = dy * pull;
    if (spring.source.fx == null) {
      spring.source.vx += fx;
      spring.source.vy += fy;
    }
    if (spring.target.fx == null) {
      spring.target.vx -= fx;
      spring.target.vy -= fy;
    }
  }

  const gravity = CENTERING * alpha;
  for (const node of nodes) {
    if (node.fx != null && node.fy != null) {
      node.x = node.fx;
      node.y = node.fy;
      node.vx = 0;
      node.vy = 0;
      continue;
    }
    node.vx -= node.x * gravity;
    node.vy -= node.y * gravity;
    node.vx *= VELOCITY_DECAY;
    node.vy *= VELOCITY_DECAY;
    node.x += node.vx;
    node.y += node.vy;
  }
}

/** Instant settle used when the owner prefers reduced motion. */
export function settleGraphLayout(
  nodes: ObsidianGraphNode[],
  links: ObsidianGraphLink[],
): GraphSimNode[] {
  const sim = seedGraphSimulation(nodes, links);
  const springs = graphSprings(sim, links);
  let alpha = 1;
  for (let i = 0; i < 320; i += 1) {
    tickGraphForce(sim, springs, alpha);
    alpha *= 0.978;
  }
  return sim;
}

function quantile(sorted: number[], q: number): number {
  if (sorted.length === 0) return 0;
  const index = Math.min(sorted.length - 1, Math.max(0, Math.round((sorted.length - 1) * q)));
  return sorted[index];
}

/**
 * Frame the main cluster. A handful of orphan notes drift far from the centre,
 * so the bounds ignore the outer 3% rather than shrinking the whole graph.
 */
export function fitGraphView(
  nodes: Array<{ x: number; y: number }>,
  viewWidth: number,
  viewHeight: number,
  padding = 48,
): { x: number; y: number; k: number } {
  if (nodes.length === 0) return { x: 0, y: 0, k: 1 };

  const xs = nodes.map((node) => node.x).sort((a, b) => a - b);
  const ys = nodes.map((node) => node.y).sort((a, b) => a - b);
  const trim = nodes.length >= 24 ? 0.03 : 0;
  const minX = quantile(xs, trim);
  const maxX = quantile(xs, 1 - trim);
  const minY = quantile(ys, trim);
  const maxY = quantile(ys, 1 - trim);

  const contentW = Math.max(1, maxX - minX) + padding * 2;
  const contentH = Math.max(1, maxY - minY) + padding * 2;
  const k = Math.min(Math.max(Math.min(viewWidth / contentW, viewHeight / contentH), 0.22), 1.4);
  const centerX = (minX + maxX) / 2;
  const centerY = (minY + maxY) / 2;

  return {
    k,
    x: viewWidth / 2 - centerX * k,
    y: viewHeight / 2 - centerY * k,
  };
}
