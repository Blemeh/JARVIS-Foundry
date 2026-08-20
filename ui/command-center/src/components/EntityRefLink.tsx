/* eslint-disable react-refresh/only-export-components */
import { Link } from "react-router-dom";
import {
  Activity,
  Braces,
  ClipboardCheck,
  FileCheck2,
  GitBranch,
  History,
  MemoryStick,
  Settings,
  X,
} from "lucide-react";
import type { EntityRef } from "../lib/contracts";
import { verdictToMission } from "../lib/cohesion";

const kindIcon = {
  mission: Braces,
  agent: Activity,
  evidence: FileCheck2,
  system: Settings,
  memory: MemoryStick,
  timeline: History,
  verdict: ClipboardCheck,
  procedure: GitBranch,
} as const;

export function refToPath(ref: EntityRef, overrideTo?: string): string {
  if (overrideTo) return overrideTo;
  switch (ref.kind) {
    case "mission":
      return `/missions/${ref.id}`;
    case "agent":
      return `/agents/${ref.id}`;
    case "evidence":
      return `/evidence/${ref.id}`;
    case "system":
      return `/systems/${ref.id}`;
    case "memory":
      return `/memory/${ref.id}`;
    case "timeline":
      return `/timeline?event=${ref.id}`;
    case "verdict": {
      const mission = verdictToMission[ref.id];
      return mission ? `/missions/${mission}` : "/missions";
    }
    case "procedure":
      return `/memory/${ref.id}`;
  }
}

export function EntityRefLink({ entity, to }: { entity: EntityRef; to?: string }) {
  const Icon = kindIcon[entity.kind] ?? Braces;
  return (
    <Link to={refToPath(entity, to)} className="ref-link" title={`${entity.kind}: ${entity.id}`}>
      <Icon size={11} aria-hidden="true" />
      <span>{entity.label}</span>
    </Link>
  );
}

export function RefChipRow({ refs, emptyLabel = "No related records" }: { refs: EntityRef[]; emptyLabel?: string }) {
  if (refs.length === 0) {
    return <span className="t-mono text-dim ref-row-empty">{emptyLabel}</span>;
  }
  return (
    <div className="ref-row">
      {refs.map((r) => (
        <EntityRefLink key={`${r.kind}-${r.id}`} entity={r} />
      ))}
    </div>
  );
}

export function ContextChip({ label, onClear }: { label: string; onClear?: () => void }) {
  return (
    <span className="context-chip">
      <span className="t-label context-chip-label">Context</span>
      <span className="context-chip-value">{label}</span>
      {onClear ? (
        <button type="button" className="context-chip-clear" aria-label={`Clear context ${label}`} onClick={onClear}>
          <X size={11} aria-hidden="true" />
        </button>
      ) : null}
    </span>
  );
}
