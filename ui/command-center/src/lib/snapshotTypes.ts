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
    runtime_state?: string;
    state_reason?: string;
    state_source?: string;
    state_evidence?: Record<string, unknown>;
    decision_queue_open_count?: number;
  };
  missions: FoundryMission[];
  workers: FoundryWorker[];
  evidence: FoundryEvidence[];
  verification: FoundryVerification[];
  health: {
    supervisor_status: string;
    trust_state: string;
    schema?: { live?: number | null; package?: number | null };
    heartbeat_at?: string;
    degraded?: boolean;
    crashed_workers?: Record<string, unknown>[];
    stale_leases?: Record<string, unknown>[];
    trust_assessment?: Record<string, unknown>;
  };
  systems: FoundrySystem[];
  memory: FoundryMemory[];
  procedures: Record<string, unknown>[];
  mission_steps: Record<string, unknown>[];
  timeline: FoundryTimelineEvent[];
  release_identity: Record<string, unknown>;
  costs: Record<string, unknown>;
  executive: FoundryExecutive;
  owner_directives?: {
    records?: Record<string, unknown>[];
    active?: Record<string, unknown>[];
    unknown?: Record<string, unknown>[];
    history_count?: number;
    active_count?: number;
  };
  decision_queue?: {
    open?: Record<string, unknown>[];
    history?: Record<string, unknown>[];
    open_count?: number;
    history_count?: number;
    counts_by_classification?: Record<string, number>;
  };
  briefings?: Record<string, unknown>[];
  effect_truth?: Record<string, unknown>;
  evolution_truth?: Record<string, unknown>;
  result_traceability?: {
    schema?: string;
    status?: string;
    results?: Record<string, unknown>[];
    count?: number;
  };
  briefing_packet?: Record<string, unknown>;
  /** Additive §3.13 projection; absent on backends older than its landing. */
  discovery?: unknown;
  /** Additive V9 world-model read projection; absent when no durable state exists. */
  world_intelligence?: Record<string, unknown>;
  /** Additive, read-only projection of the canonical Capability Fabric catalog. */
  capability_fabric?: {
    status?: string;
    authority?: string;
    source?: string;
    reason?: string;
    count?: number;
    capabilities?: Array<{
      spec?: Record<string, unknown>;
      metadata?: Record<string, unknown> | null;
      readiness?: Record<string, unknown>;
    }>;
  };
  /** Additive portfolio hierarchy; older ledgers may omit it. */
  portfolio?: Record<string, unknown>;
  /** Indexed Obsidian vault files (ACTIVE rows from vault_files). */
  vault?: {
    files?: FoundryVaultFile[];
    count?: number;
  };
}

export interface FoundryVaultFile {
  rel_path: string;
  content_sha256?: string;
  status?: string;
  updated_at?: string;
  last_event_id?: string;
}

export interface FoundryExecutiveDecision {
  decision_id: string;
  decision?: string;
  reason?: string;
  decision_version?: string;
  candidate_id?: string | null;
  mission_id?: string | null;
  active_goals?: string[];
  factor_breakdown?: Record<string, { value?: unknown; kind?: string; contribution?: number }>;
  eligibility?: Record<string, unknown>;
  rejected?: { candidate_id: string; composite?: number; reason?: string }[];
  deferred?: { candidate_id: string; reason?: string }[];
  selected_score?: { candidate_id?: string; composite?: number; goal_fit?: number; calibration_adjustment?: number };
  wait?: {
    reason?: string;
    reconsider_condition?: string;
    wait_kind?: string;
    best_candidate_id?: string | null;
    best_score?: number | null;
    comparative_rationale?: string;
  } | null;
  prediction?: { candidate_id?: string; prediction?: Record<string, unknown> } | null;
  calibration?: {
    outcome?: string;
    repair_count?: number;
    actual?: Record<string, unknown>;
    calibration_version?: string;
  } | null;
  event_id?: string;
  occurred_at?: string;
}

export interface FoundryExecutive {
  activation_epoch: string | null;
  admission_authority: string;
  latest_decision: FoundryExecutiveDecision | null;
  recent_decisions: FoundryExecutiveDecision[];
  calibrations: {
    decision_id?: string;
    candidate_id?: string;
    mission_id?: string;
    outcome?: string;
    repair_count?: number;
    actual?: Record<string, unknown>;
    calibration_version?: string;
    event_id?: string;
  }[];
  /** V8 durable objectives, plans, continuation, and operational proposals. */
  autonomy?: FoundryExecutiveAutonomy;
}

export interface FoundryExecutiveAutonomy {
  objectives?: Record<string, Record<string, unknown>>;
  plans?: Record<string, Record<string, unknown>>;
  priorities?: Record<string, unknown>[];
  continuations?: Record<string, Record<string, unknown>>;
  escalations?: Record<string, Record<string, unknown>>;
  coordination?: Record<string, Record<string, unknown>>;
  progress?: Record<string, Record<string, unknown>>;
  mission_requests?: Record<string, Record<string, unknown>>;
  operational_followup_proposals?: Record<string, Record<string, unknown>>;
  latest_cycle?: Record<string, unknown>;
  briefing?: Record<string, unknown> | null;
  status?: string;
  reason?: string;
}

export interface FoundryMission {
  mission_id: string;
  objective?: string;
  state?: string;
  next_action?: string;
  updated_at?: string;
  created_from?: string;
  acceptance?: unknown;
  last_event_id?: string;
  record_ref?: Record<string, unknown>;
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
  step_id?: string;
  last_event_id?: string;
  record_ref?: Record<string, unknown>;
  result?: unknown;
}

export interface FoundryEvidence {
  bundle_id: string;
  mission_id?: string;
  step_id?: string;
  objective?: string;
  status?: string;
  updated_at?: string;
  artifact_hashes?: Record<string, string>;
  builder_claim?: unknown;
  verifier_verdict_id?: string;
  uncertainty?: unknown;
  last_event_id?: string;
  record_ref?: Record<string, unknown>;
}

export interface FoundryVerification {
  verdict_id: string;
  mission_id?: string;
  run_id?: string;
  status?: string;
  findings?: unknown;
  updated_at?: string;
  subject_type?: string;
  subject_id?: string;
  verifier_run_id?: string;
  builder_run_id?: string;
  repair_requirements?: unknown;
  missing_proof?: unknown;
  last_event_id?: string;
  record_ref?: Record<string, unknown>;
}

export interface FoundrySystem {
  system_id: string;
  name?: string;
  description?: string;
  readiness_state?: string;
  updated_at?: string;
  last_heartbeat_at?: string;
  last_probe_at?: string;
  readiness?: string;
  capabilities?: Record<string, unknown>;
  state?: string;
  health?: string;
  dependencies?: string[];
  recovery_state?: string;
  version?: string;
  last_event_id?: string;
  record_ref?: Record<string, unknown>;
}

export interface FoundryMemory {
  memory_id: string;
  state?: string;
  mem_type?: string;
  scope?: string;
  mission_id?: string;
  updated_at?: string;
  agent_run_id?: string;
  created_by?: string;
  content?: string;
  confidence?: number;
  source_ids?: string[];
  evidence_ids?: string[];
  last_verified?: string;
  last_event_id?: string;
  record_ref?: Record<string, unknown>;
}

export interface FoundryTimelineEvent {
  sequence: number;
  event_id: string;
  event_type: string;
  occurred_at?: string;
  recorded_at?: string;
  summary?: string;
  decision?: string;
  state?: string;
  next_action?: string;
  evidence_refs?: { kind: string; id?: string; event_id?: string }[];
  source?: { type?: string; id?: string };
  correlation_id?: string;
  causation_id?: string;
  rejected_alternative?: string | null;
}

export interface OwnerCommandResponse {
  status: "APPLIED" | "REJECTED" | "MALFORMED" | "BLOCKED";
  command_type?: string;
  directive_id?: string;
  event_id?: string;
  event_ids?: string[];
  reason?: string;
}
