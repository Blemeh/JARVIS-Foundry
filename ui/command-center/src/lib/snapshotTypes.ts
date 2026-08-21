/** Raw JSON shapes from GET /api/command-center (canonical ledger projection). */

export interface FoundrySnapshot {
  generated_at: string;
  source_of_truth: { kind: string; ledger_head_sequence: number; event_count: number };
  supervisor: Record<string, unknown>;
  system_state: Record<string, unknown>;
  stop: { active: boolean; record: unknown; source: string };
  controls: {
    goals: Record<string, unknown>[];
    constraints: Record<string, unknown>[];
    vetoes: Record<string, unknown>[];
    reprioritizations: Record<string, unknown>[];
    redirects: Record<string, unknown>[];
    paused: boolean;
    resume_available: boolean;
  };
  missions: FoundryMission[];
  workers: FoundryWorker[];
  evidence: FoundryEvidence[];
  verification: FoundryVerification[];
  health: {
    supervisor_status: string;
    trust_state: string;
    heartbeat_at?: string;
    degraded?: boolean;
    crashed_workers?: Record<string, unknown>[];
    stale_leases?: Record<string, unknown>[];
  };
  systems: FoundrySystem[];
  memory: FoundryMemory[];
  procedures: Record<string, unknown>[];
  mission_steps: Record<string, unknown>[];
  timeline: FoundryTimelineEvent[];
  release_identity: Record<string, unknown>;
  costs: Record<string, unknown>;
}

export interface FoundryMission {
  mission_id: string;
  objective?: string;
  state?: string;
  next_action?: string;
  updated_at?: string;
  created_from?: string;
  acceptance?: unknown;
}

export interface FoundryWorker {
  run_id: string;
  mission_id?: string;
  role?: string;
  provider?: string;
  model?: string;
  status?: string;
  heartbeat_at?: string;
  updated_at?: string;
  failure_class?: string;
}

export interface FoundryEvidence {
  bundle_id: string;
  mission_id?: string;
  step_id?: string;
  objective?: string;
  status?: string;
  updated_at?: string;
  artifact_hashes?: Record<string, string>;
}

export interface FoundryVerification {
  verdict_id: string;
  mission_id?: string;
  run_id?: string;
  status?: string;
  findings?: unknown;
  updated_at?: string;
}

export interface FoundrySystem {
  system_id: string;
  name?: string;
  description?: string;
  readiness_state?: string;
  updated_at?: string;
  last_heartbeat_at?: string;
}

export interface FoundryMemory {
  memory_id: string;
  state?: string;
  mem_type?: string;
  scope?: string;
  mission_id?: string;
  updated_at?: string;
}

export interface FoundryTimelineEvent {
  sequence: number;
  event_id: string;
  event_type: string;
  occurred_at?: string;
  recorded_at?: string;
  summary?: string;
  decision?: string;
  evidence_refs?: { kind: string; id?: string; event_id?: string }[];
}

export interface OwnerCommandResponse {
  status: "APPLIED" | "REJECTED" | "MALFORMED" | "BLOCKED";
  command_type?: string;
  directive_id?: string;
  event_id?: string;
  event_ids?: string[];
  reason?: string;
}
