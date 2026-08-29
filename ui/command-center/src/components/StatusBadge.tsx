import type { ComponentState, EvidenceVerdict, Health, MemoryNodeCategory, MissionStatus, VerdictState, WorkerStatus } from "../lib/contracts";

type Status =
  | MissionStatus
  | WorkerStatus
  | EvidenceVerdict
  | VerdictState
  | ComponentState
  | Health
  | "READY"
  | "AVAILABLE"
  | "PARTIAL"
  | "EMPTY_LEDGER"
  | "NOT_READY"
  | "DEGRADED"
  | "SIMULATED"
  | "PROVEN"
  | "SUSPENDED"
  | "PENDING"
  | "PASS"
  | "WARN"
  | "FAIL"
  | "NOT_TESTED"
  | "FAILED"
  | "CANCELLED"
  | "RECOVERING"
  | "QUEUED"
  | "UNAVAILABLE"
  | MemoryNodeCategory;

const variantMap: Record<string, "accent" | "teal" | "amber" | "error" | "muted"> = {
  ACTIVE: "accent",
  IN_PROGRESS: "accent",
  ARMED: "accent",
  RUNNING: "accent",
  READY: "accent",
  AVAILABLE: "teal",
  PARTIAL: "amber",
  EMPTY_LEDGER: "muted",
  PASSED: "teal",
  VERIFIED: "teal",
  PASS: "teal",
  STANDBY: "teal",
  PROVEN: "teal",
  HEALTHY: "teal",
  COMPLETE: "teal",
  WARN_ACCEPT: "amber",
  WARN: "amber",
  EVALUATING: "amber",
  DEGRADED: "amber",
  BLOCKED: "amber",
  INTERVENTION: "error",
  HALTED: "error",
  TAMPERED: "error",
  FAILED: "error",
  RECOVERING: "amber",
  QUEUED: "muted",
  FAIL: "error",
  CRITICAL: "error",
  SUSPENDED: "error",
  STOPPED: "error",
  CANCELLED: "error",
  RATE_LIMITED: "error",
  SIMULATED: "muted",
  NOT_READY: "muted",
  NOT_TESTED: "muted",
  UNKNOWN: "muted",
  UNAVAILABLE: "muted",
  MISSION: "accent",
  EVIDENCE: "teal",
  BELIEF: "muted",
  DECISION: "accent",
  PROCEDURE: "amber",
  OBSERVATION: "amber",
  RESOURCE: "muted",
  PENDING: "amber",
};

export function StatusBadge({ status, pulse = false }: { status: Status; pulse?: boolean }) {
  const variant = variantMap[status] ?? "muted";
  return (
    <span className={`badge badge-${variant}`}>
      <span className={`lamp lamp-${variant} ${pulse ? "lamp-pulse-on" : ""}`} aria-hidden="true" />
      {status}
    </span>
  );
}
