import { AlertTriangle, ClipboardCheck, Construction, FileCheck2, ShieldCheck, Wrench } from "lucide-react";
import type { LineageNode } from "../lib/contracts";
import { StatusBadge } from "./StatusBadge";
import { RefChipRow } from "./EntityRefLink";

const kindIcon = {
  builder: Construction,
  verifier: ClipboardCheck,
  finding: AlertTriangle,
  repair: Wrench,
  evidence: FileCheck2,
  pass: ShieldCheck,
} as const;

const statusVariant: Record<LineageNode["status"], LineageNode["status"]> = {
  PASS: "PASS",
  FAIL: "FAIL",
  ACTIVE: "ACTIVE",
  PENDING: "PENDING",
  BLOCKED: "BLOCKED",
  "NOT REQUIRED": "NOT REQUIRED",
  "NOT RECORDED": "NOT RECORDED",
};

const statusClass = (status: LineageNode["status"]): string =>
  status.toLowerCase().replace(/\s+/g, "-");

export function Lineage({ nodes }: { nodes: LineageNode[] }) {
  return (
    <ol className="lineage">
      {nodes.map((n, i) => {
        const Icon = kindIcon[n.kind];
        return (
          <li key={n.id} className={`lineage-node lineage-node-${statusClass(n.status)}`}>
            <div className="lineage-node-head">
              <span className="lineage-node-icon" aria-hidden="true">
                <Icon size={14} />
              </span>
              <div className="lineage-node-text">
                <span className="t-label lineage-node-label">{n.label}</span>
                <p className="prose lineage-node-detail">{n.detail}</p>
              </div>
              <StatusBadge status={statusVariant[n.status]} />
            </div>
            {n.refs.length > 0 ? <RefChipRow refs={n.refs} /> : null}
            {i < nodes.length - 1 ? <span className="lineage-node-connector" aria-hidden="true" /> : null}
          </li>
        );
      })}
    </ol>
  );
}
