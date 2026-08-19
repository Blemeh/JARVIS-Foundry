from __future__ import annotations

import sqlite3

from .v4_schema import ensure_v4_tables


SCHEMA_VERSION = 34

MIGRATIONS: dict[int, str] = {
    1: """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY,
        applied_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS events (
        sequence INTEGER PRIMARY KEY AUTOINCREMENT,
        event_id TEXT NOT NULL UNIQUE,
        event_type TEXT NOT NULL,
        occurred_at TEXT NOT NULL,
        recorded_at TEXT NOT NULL,
        source_type TEXT NOT NULL,
        source_id TEXT NOT NULL,
        correlation_id TEXT NOT NULL,
        causation_id TEXT,
        idempotency_key TEXT NOT NULL UNIQUE,
        schema_version INTEGER NOT NULL,
        payload_json TEXT NOT NULL,
        payload_hash TEXT NOT NULL CHECK(length(payload_hash) = 64)
    );
    CREATE INDEX IF NOT EXISTS events_type_sequence ON events(event_type, sequence);
    CREATE INDEX IF NOT EXISTS events_correlation ON events(correlation_id, sequence);

    CREATE TABLE IF NOT EXISTS missions (
        mission_id TEXT PRIMARY KEY,
        objective TEXT NOT NULL,
        resource_id TEXT,
        state TEXT NOT NULL,
        acceptance_json TEXT NOT NULL,
        next_action TEXT,
        last_event_id TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS resources (
        resource_id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        root_path TEXT NOT NULL,
        resolved_root TEXT NOT NULL,
        baseline_json TEXT NOT NULL,
        capabilities_json TEXT NOT NULL,
        readiness_state TEXT NOT NULL,
        last_event_id TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS directives (
        directive_id TEXT PRIMARY KEY,
        command_type TEXT NOT NULL,
        target TEXT,
        exact_text TEXT NOT NULL,
        active INTEGER NOT NULL CHECK(active IN (0, 1)),
        last_event_id TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS action_decisions (
        action_id TEXT PRIMARY KEY,
        mission_id TEXT NOT NULL,
        decision TEXT NOT NULL,
        reason TEXT NOT NULL,
        target TEXT,
        last_event_id TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS leases (
        lease_key TEXT PRIMARY KEY,
        owner_id TEXT NOT NULL,
        expires_at TEXT NOT NULL,
        heartbeat_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS supervisor_state (
        singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
        instance_id TEXT,
        status TEXT NOT NULL,
        trust_state TEXT NOT NULL,
        last_heartbeat_at TEXT,
        last_event_id TEXT
    );
    INSERT OR IGNORE INTO supervisor_state(singleton, status, trust_state)
    VALUES (1, 'STOPPED', 'LAB');
    """,
    2: """
    CREATE TABLE IF NOT EXISTS system_state (
        singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
        start_active INTEGER NOT NULL CHECK(start_active IN (0, 1)),
        start_event_id TEXT,
        start_received_at TEXT
    );
    INSERT OR IGNORE INTO system_state(singleton, start_active)
    VALUES (1, 0);
    """,
    3: """
    CREATE TABLE IF NOT EXISTS agent_runs (
        run_id TEXT PRIMARY KEY,
        mission_id TEXT NOT NULL,
        step_id TEXT,
        role TEXT NOT NULL,
        provider TEXT,
        model TEXT,
        context_manifest_hash TEXT,
        tool_policy_json TEXT NOT NULL DEFAULT '{}',
        status TEXT NOT NULL CHECK(status IN ('RUNNING', 'COMPLETED', 'FAILED', 'CANCELLED')),
        started_at TEXT NOT NULL,
        heartbeat_at TEXT,
        finished_at TEXT,
        result_json TEXT,
        failure_class TEXT,
        cost_json TEXT,
        last_event_id TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS agent_runs_mission ON agent_runs(mission_id, started_at);

    CREATE TABLE IF NOT EXISTS evidence_bundles (
        bundle_id TEXT PRIMARY KEY,
        mission_id TEXT NOT NULL,
        step_id TEXT,
        objective TEXT NOT NULL,
        criteria_json TEXT NOT NULL,
        before_state_json TEXT NOT NULL DEFAULT '{}',
        baseline_commit TEXT,
        final_diff_json TEXT NOT NULL DEFAULT '{}',
        files_changed_json TEXT NOT NULL DEFAULT '[]',
        commands_run_json TEXT NOT NULL DEFAULT '[]',
        artifact_hashes_json TEXT NOT NULL DEFAULT '{}',
        after_state_json TEXT NOT NULL DEFAULT '{}',
        uncertainty_json TEXT NOT NULL DEFAULT '{}',
        rollback_proof_json TEXT NOT NULL DEFAULT '{}',
        builder_claim_json TEXT NOT NULL DEFAULT '{}',
        verifier_verdict_id TEXT,
        last_event_id TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS evidence_bundles_mission ON evidence_bundles(mission_id);

    CREATE TABLE IF NOT EXISTS verification_verdicts (
        verdict_id TEXT PRIMARY KEY,
        mission_id TEXT NOT NULL,
        subject_type TEXT NOT NULL,
        subject_id TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('STARTED', 'PASS', 'FAIL', 'BLOCKED', 'NOT_TESTED')),
        verdict TEXT CHECK(verdict IS NULL OR verdict IN ('PASS', 'FAIL', 'BLOCKED', 'NOT_TESTED')),
        verifier_run_id TEXT,
        builder_run_id TEXT,
        independence_json TEXT NOT NULL DEFAULT '{}',
        criteria_json TEXT NOT NULL DEFAULT '[]',
        findings_json TEXT NOT NULL DEFAULT '[]',
        evidence_json TEXT NOT NULL DEFAULT '{}',
        missing_proof_json TEXT NOT NULL DEFAULT '[]',
        scope_violations_json TEXT NOT NULL DEFAULT '[]',
        repair_requirements_json TEXT NOT NULL DEFAULT '[]',
        reason TEXT,
        blocking_dependency TEXT,
        issued_at TEXT,
        last_event_id TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS verification_verdicts_subject ON verification_verdicts(subject_type, subject_id);

    CREATE TABLE IF NOT EXISTS budgets (
        provider TEXT PRIMARY KEY,
        lifetime_budget_usd REAL,
        daily_soft_limit_usd REAL,
        billing_verified INTEGER NOT NULL DEFAULT 0 CHECK(billing_verified IN (0, 1)),
        spent_usd REAL NOT NULL DEFAULT 0.0 CHECK(spent_usd >= 0.0),
        last_event_id TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    4: """
    CREATE TABLE IF NOT EXISTS obsidian_writes (
        write_id TEXT PRIMARY KEY,
        note_name TEXT NOT NULL,
        note_path TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('STARTED', 'COMPLETED', 'CONFLICT', 'ROLLED_BACK')),
        expected_hash TEXT,
        before_hash TEXT,
        after_hash TEXT,
        before_snapshot_path TEXT,
        conflict_preserved_path TEXT,
        rollback_pointer TEXT,
        last_event_id TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS obsidian_writes_note ON obsidian_writes(note_name);
    """,
    5: """
    CREATE TABLE IF NOT EXISTS knowledge_claims (
        claim_id TEXT PRIMARY KEY,
        mission_id TEXT NOT NULL,
        statement TEXT NOT NULL,
        claim_type TEXT NOT NULL,
        confidence REAL NOT NULL CHECK(confidence >= 0.0 AND confidence <= 1.0),
        fresh_until TEXT,
        source_run_id TEXT,
        source_event_id TEXT,
        status TEXT NOT NULL DEFAULT 'ACCEPTED'
            CHECK(status IN ('ACCEPTED', 'CONTRADICTED', 'SUPERSEDED')),
        superseded_by TEXT,
        last_event_id TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS knowledge_claims_mission ON knowledge_claims(mission_id, claim_type);
    CREATE INDEX IF NOT EXISTS knowledge_claims_status ON knowledge_claims(status);

    CREATE TABLE IF NOT EXISTS knowledge_contradictions (
        contradiction_id TEXT PRIMARY KEY,
        claim_a_id TEXT NOT NULL,
        claim_b_id TEXT NOT NULL,
        claim_type TEXT NOT NULL,
        reason TEXT NOT NULL,
        last_event_id TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS knowledge_contradictions_claims
        ON knowledge_contradictions(claim_a_id, claim_b_id);
    """,
    6: """
    CREATE TABLE IF NOT EXISTS knowledge_manifests (
        manifest_id TEXT PRIMARY KEY,
        mission_id TEXT NOT NULL,
        step_id TEXT,
        objective_hash TEXT NOT NULL,
        active_count INTEGER NOT NULL,
        stale_count INTEGER NOT NULL,
        conflict_count INTEGER NOT NULL,
        truncated INTEGER NOT NULL DEFAULT 0 CHECK(truncated IN (0, 1)),
        manifest_hash TEXT NOT NULL,
        last_event_id TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS knowledge_manifests_mission
        ON knowledge_manifests(mission_id);
    """,
    7: """
    CREATE TABLE IF NOT EXISTS knowledge_entities (
        entity_id TEXT PRIMARY KEY,
        mission_id TEXT NOT NULL,
        canonical_name TEXT NOT NULL,
        aliases_json TEXT NOT NULL DEFAULT '[]',
        last_event_id TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS knowledge_entities_mission
        ON knowledge_entities(mission_id);

    CREATE TABLE IF NOT EXISTS knowledge_claim_entities (
        claim_id TEXT NOT NULL,
        entity_id TEXT NOT NULL,
        role TEXT,
        last_event_id TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (claim_id, entity_id)
    );
    CREATE INDEX IF NOT EXISTS knowledge_claim_entities_entity
        ON knowledge_claim_entities(entity_id);
    """,
    8: """
    CREATE TABLE IF NOT EXISTS knowledge_ingestions (
        ingestion_id TEXT PRIMARY KEY,
        mission_id TEXT NOT NULL,
        source_type TEXT NOT NULL,
        source_ref TEXT NOT NULL,
        source_hash TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'QUEUED'
            CHECK(status IN ('QUEUED', 'RUNNING', 'COMPLETED', 'FAILED')),
        claims_count INTEGER NOT NULL DEFAULT 0,
        errors_json TEXT NOT NULL DEFAULT '[]',
        last_event_id TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS knowledge_ingestions_hash
        ON knowledge_ingestions(source_hash);
    CREATE INDEX IF NOT EXISTS knowledge_ingestions_mission
        ON knowledge_ingestions(mission_id);
    """,
    9: """
    -- ROLLED_BACK status + rolled_back_by require a knowledge_claims
    -- rebuild (SQLite cannot alter CHECK constraints).
    CREATE TABLE IF NOT EXISTS knowledge_claims_v9 (
        claim_id TEXT PRIMARY KEY,
        mission_id TEXT NOT NULL,
        statement TEXT NOT NULL,
        claim_type TEXT NOT NULL,
        confidence REAL NOT NULL CHECK(confidence >= 0.0 AND confidence <= 1.0),
        fresh_until TEXT,
        source_run_id TEXT,
        source_event_id TEXT,
        status TEXT NOT NULL DEFAULT 'ACCEPTED'
            CHECK(status IN ('ACCEPTED', 'CONTRADICTED', 'SUPERSEDED',
                             'ROLLED_BACK')),
        superseded_by TEXT,
        rolled_back_by TEXT,
        last_event_id TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    INSERT INTO knowledge_claims_v9(
        claim_id, mission_id, statement, claim_type, confidence,
        fresh_until, source_run_id, source_event_id, status,
        superseded_by, rolled_back_by, last_event_id, updated_at)
    SELECT claim_id, mission_id, statement, claim_type, confidence,
        fresh_until, source_run_id, source_event_id, status,
        superseded_by, NULL, last_event_id, updated_at
    FROM knowledge_claims;
    DROP TABLE knowledge_claims;
    ALTER TABLE knowledge_claims_v9 RENAME TO knowledge_claims;
    CREATE INDEX IF NOT EXISTS knowledge_claims_mission
        ON knowledge_claims(mission_id, claim_type);
    CREATE INDEX IF NOT EXISTS knowledge_claims_status
        ON knowledge_claims(status);

    CREATE TABLE IF NOT EXISTS knowledge_snapshots (
        snapshot_id TEXT PRIMARY KEY,
        mission_id TEXT NOT NULL,
        scope TEXT NOT NULL,
        head_event_id INTEGER NOT NULL,
        schema_version INTEGER NOT NULL,
        claims_count INTEGER NOT NULL DEFAULT 0,
        content_sha256 TEXT NOT NULL DEFAULT '',
        path TEXT NOT NULL,
        last_event_id TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS knowledge_snapshots_mission
        ON knowledge_snapshots(mission_id);
    """,
    10: """
    CREATE TABLE IF NOT EXISTS candidates (
        candidate_id TEXT PRIMARY KEY,
        mission_id TEXT NOT NULL,
        source TEXT NOT NULL,
        objective TEXT NOT NULL,
        objective_canonical TEXT NOT NULL,
        evidence_json TEXT NOT NULL DEFAULT '[]',
        status TEXT NOT NULL DEFAULT 'NEW'
            CHECK(status IN ('NEW', 'SCORED', 'APPROVED', 'REJECTED',
                             'EXECUTED')),
        duplicate_of TEXT,
        created_from TEXT,
        last_event_id TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS candidates_canonical
        ON candidates(objective_canonical);
    CREATE INDEX IF NOT EXISTS candidates_mission
        ON candidates(mission_id);

    CREATE TABLE IF NOT EXISTS candidate_scores (
        candidate_id TEXT PRIMARY KEY,
        score REAL NOT NULL,
        weights_json TEXT NOT NULL,
        last_event_id TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    11: """
    ALTER TABLE missions ADD COLUMN created_from TEXT;

    CREATE TABLE IF NOT EXISTS monitors (
        monitor_id TEXT PRIMARY KEY,
        mission_id TEXT NOT NULL,
        source_kind TEXT NOT NULL,
        source_ref TEXT NOT NULL,
        poll_interval_s INTEGER NOT NULL DEFAULT 300,
        enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0, 1)),
        last_poll_at TEXT,
        last_event_id TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS monitors_mission
        ON monitors(mission_id);
    """,
    12: """
    -- WP11 slice A (V2-OPERATIONAL): vault indexing (files + sync records).
    CREATE TABLE IF NOT EXISTS vault_files (
        rel_path TEXT PRIMARY KEY,
        content_sha256 TEXT NOT NULL,
        size_bytes INTEGER NOT NULL,
        mtime TEXT NOT NULL,
        first_seen TEXT NOT NULL,
        last_synced TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'ACTIVE'
            CHECK(status IN ('ACTIVE', 'REMOVED')),
        last_event_id TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS vault_files_status
        ON vault_files(status);

    -- run records for syncs (event-derived, reconcile-verified)
    CREATE TABLE IF NOT EXISTS vault_sync_state (
        sync_id TEXT PRIMARY KEY,
        root TEXT NOT NULL,
        started_at TEXT NOT NULL,
        completed_at TEXT,
        files_indexed INTEGER NOT NULL DEFAULT 0,
        files_changed INTEGER NOT NULL DEFAULT 0,
        files_removed INTEGER NOT NULL DEFAULT 0,
        cursor TEXT,
        last_event_id TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    13: """
    -- WP11 slice C (V2-OPERATIONAL): per-source ingestion cursors
    -- (event-derived, reconcile-verified, bounds event scans so a
    -- restart never re-scans the whole ledger)
    CREATE TABLE IF NOT EXISTS ingestion_cursors (
        source_kind TEXT PRIMARY KEY,
        cursor TEXT NOT NULL,
        last_event_id TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    14: """
    -- WP11 slice D (V2-OPERATIONAL): research queue items
    -- (event-derived, reconcile-verified)
    CREATE TABLE IF NOT EXISTS research_queue (
        item_id TEXT PRIMARY KEY,
        claim_id TEXT NOT NULL,
        reason TEXT NOT NULL,
        evidence_json TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'OPEN'
            CHECK(status IN ('OPEN', 'CANDIDATE_CREATED', 'REJECTED')),
        last_event_id TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS research_queue_status
        ON research_queue(status);
    """,
    15: """
    -- WP11 slice E (V2-OPERATIONAL): worker context manifest deliveries
    -- (event-derived, reconcile-verified)
    CREATE TABLE IF NOT EXISTS worker_manifests (
        manifest_id TEXT PRIMARY KEY,
        run_id TEXT NOT NULL,
        worker TEXT NOT NULL,
        mission_id TEXT NOT NULL,
        objective TEXT NOT NULL,
        manifest_hash TEXT NOT NULL,
        sources_json TEXT NOT NULL,
        truncation_json TEXT NOT NULL,
        last_event_id TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS worker_manifests_run
        ON worker_manifests(run_id);
    """,
    16: """
    -- WP11 slice F (V2-OPERATIONAL): governed consolidation operations
    -- (event-derived, reconcile-verified)
    CREATE TABLE IF NOT EXISTS consolidation_ops (
        op_id TEXT PRIMARY KEY,
        note_path TEXT NOT NULL,
        kind TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'PROPOSED'
            CHECK(status IN ('PROPOSED', 'VERIFIED', 'ACCEPTED',
                             'REJECTED', 'ROLLED_BACK')),
        before_hash TEXT NOT NULL,
        after_hash TEXT,
        write_id TEXT,
        verified_by TEXT,
        evidence_json TEXT NOT NULL DEFAULT '{}',
        last_event_id TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    17: """
    -- V4-S1 (DIR-2026-08-12 item 5): candidates gain SUPERSEDED +
    -- superseded_by for additive corrections (SQLite cannot ALTER CHECK
    -- constraints — table rebuild, v9 idiom).
    CREATE TABLE IF NOT EXISTS candidates_v17 (
        candidate_id TEXT PRIMARY KEY,
        mission_id TEXT NOT NULL,
        source TEXT NOT NULL,
        objective TEXT NOT NULL,
        objective_canonical TEXT NOT NULL,
        evidence_json TEXT NOT NULL DEFAULT '[]',
        status TEXT NOT NULL DEFAULT 'NEW'
            CHECK(status IN ('NEW', 'SCORED', 'APPROVED', 'REJECTED',
                             'EXECUTED', 'SUPERSEDED')),
        duplicate_of TEXT,
        created_from TEXT,
        superseded_by TEXT,
        last_event_id TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    INSERT INTO candidates_v17(
        candidate_id, mission_id, source, objective, objective_canonical,
        evidence_json, status, duplicate_of, created_from, superseded_by,
        last_event_id, updated_at)
    SELECT candidate_id, mission_id, source, objective, objective_canonical,
        evidence_json, status, duplicate_of, created_from, NULL,
        last_event_id, updated_at
    FROM candidates;
    DROP TABLE candidates;
    ALTER TABLE candidates_v17 RENAME TO candidates;
    CREATE INDEX IF NOT EXISTS candidates_canonical
        ON candidates(objective_canonical);
    CREATE INDEX IF NOT EXISTS candidates_mission
        ON candidates(mission_id);
    """,
    18: """
    -- V4-S5 round 2 (deleg_71efdc9e INFO): the monitors registry gets a
    -- UNIQUE(source_kind, source_ref) index — registration dedup is
    -- race-safe at the schema level, not only in the app-layer query.
    -- Safe on any existing data: the live table was empty at the audit
    -- and registration is app-layer-deduped, so no duplicates can exist.
    CREATE UNIQUE INDEX IF NOT EXISTS monitors_kind_ref
        ON monitors(source_kind, source_ref);
    """,
    19: """
    -- V5-S1 (Multi-System Intelligence): the durable system registry —
    -- the catalog + external-effect journal (retries never duplicate
    -- effects: UNIQUE on system+target+payload-hash).
    CREATE TABLE IF NOT EXISTS systems (
        system_id TEXT PRIMARY KEY,
        kind TEXT NOT NULL,
        name TEXT NOT NULL,
        capabilities_json TEXT NOT NULL DEFAULT '{}',
        probe_ref TEXT,
        readiness TEXT NOT NULL DEFAULT 'NOT_TESTED'
            CHECK(readiness IN ('READY', 'UNREADY', 'NOT_TESTED')),
        last_probe_at TEXT,
        last_event_id TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS system_actions (
        action_key TEXT PRIMARY KEY,
        system_id TEXT NOT NULL,
        target_ref TEXT NOT NULL,
        payload_hash TEXT NOT NULL,
        kind TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'STARTED'
            CHECK(status IN ('STARTED', 'COMPLETED', 'FAILED')),
        last_event_id TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(system_id, target_ref, payload_hash)
    );
    CREATE INDEX IF NOT EXISTS system_actions_system
        ON system_actions(system_id, status);
    """,
    20: """
    -- V5-S6: the effect journal gains the FROZEN state (compensation
    -- and freeze path for ambiguous effects). Table rebuild, since
    -- SQLite cannot ALTER a CHECK constraint, the rebuild preserves
    -- every row.
    CREATE TABLE system_actions_v20 (
        action_key TEXT PRIMARY KEY,
        system_id TEXT NOT NULL,
        target_ref TEXT NOT NULL,
        payload_hash TEXT NOT NULL,
        kind TEXT NOT NULL,
        status TEXT NOT NULL DEFAULT 'STARTED'
            CHECK(status IN ('STARTED', 'COMPLETED', 'FAILED', 'FROZEN')),
        last_event_id TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(system_id, target_ref, payload_hash)
    );
    INSERT INTO system_actions_v20(
        action_key, system_id, target_ref, payload_hash, kind, status,
        last_event_id, updated_at
    )
    SELECT action_key, system_id, target_ref, payload_hash, kind, status,
        last_event_id, updated_at
    FROM system_actions;
    DROP TABLE system_actions;
    ALTER TABLE system_actions_v20 RENAME TO system_actions;
    CREATE INDEX IF NOT EXISTS system_actions_system
        ON system_actions(system_id, status);
    """,
    21: """
    CREATE TABLE IF NOT EXISTS improvement_records (
        improvement_id TEXT PRIMARY KEY,
        state TEXT NOT NULL CHECK(state = 'FROZEN'),
        problem TEXT NOT NULL,
        evidence_refs_json TEXT NOT NULL,
        provenance_json TEXT NOT NULL,
        hypothesis TEXT NOT NULL,
        expected_mechanism TEXT NOT NULL,
        baseline_metric_json TEXT NOT NULL,
        mmi_json TEXT NOT NULL,
        guardrails_json TEXT NOT NULL,
        workload_json TEXT NOT NULL,
        observation_json TEXT NOT NULL,
        risk_class TEXT NOT NULL,
        risk_class_declared TEXT NOT NULL,
        surfaces_json TEXT NOT NULL,
        budget_json TEXT NOT NULL,
        reversibility_json TEXT NOT NULL,
        supersedes TEXT,
        created_at TEXT NOT NULL,
        last_event_id TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    22: """
    CREATE TABLE IF NOT EXISTS release_manifests (
        release_id TEXT PRIMARY KEY,
        source_commit TEXT NOT NULL,
        artifact_hashes_json TEXT NOT NULL,
        dependencies_json TEXT NOT NULL,
        runtime_identity_json TEXT NOT NULL,
        parent_release_id TEXT,
        improvement_id TEXT NOT NULL,
        risk_class TEXT NOT NULL,
        migrations_json TEXT NOT NULL,
        verdicts_json TEXT NOT NULL,
        rollback_target TEXT,
        created_at TEXT NOT NULL,
        last_event_id TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS release_slots (
        slot TEXT PRIMARY KEY
            CHECK(slot IN ('live', 'candidate', 'previous', 'quarantine')),
        release_id TEXT,
        updated_at TEXT NOT NULL,
        last_event_id TEXT NOT NULL
    );
    """,
    23: """
    CREATE TABLE IF NOT EXISTS candidate_builds (
        build_id TEXT PRIMARY KEY,
        improvement_id TEXT NOT NULL,
        candidate_root TEXT NOT NULL,
        status TEXT NOT NULL
            CHECK(status IN ('BUILDING', 'COMPLETED', 'FAILED', 'REAPED')),
        exit_code INTEGER,
        artifact_hashes_json TEXT NOT NULL DEFAULT '{}',
        reason TEXT,
        started_at TEXT,
        finished_at TEXT,
        last_event_id TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    24: """
    CREATE TABLE IF NOT EXISTS evaluation_runs (
        run_id TEXT PRIMARY KEY,
        workload_id TEXT NOT NULL,
        subject_kind TEXT NOT NULL
            CHECK(subject_kind IN ('baseline', 'candidate')),
        subject_identity TEXT NOT NULL,
        command_hash TEXT NOT NULL,
        input_hash TEXT NOT NULL,
        exit_code INTEGER,
        duration_s REAL,
        error_rate REAL,
        cost_usd REAL NOT NULL DEFAULT 0,
        case_results_json TEXT NOT NULL DEFAULT '[]',
        output_hashes_json TEXT NOT NULL DEFAULT '{}',
        nondeterminism_handling TEXT NOT NULL DEFAULT 'not_seeded',
        created_at TEXT NOT NULL,
        last_event_id TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    25: """
    CREATE TABLE IF NOT EXISTS verifications (
        verification_id TEXT PRIMARY KEY,
        improvement_id TEXT NOT NULL,
        subject_ref TEXT NOT NULL,
        status TEXT NOT NULL
            CHECK(status IN ('REQUESTED', 'VERDICTED')),
        verdict TEXT,
        criteria_json TEXT NOT NULL DEFAULT '[]',
        artifacts_json TEXT NOT NULL DEFAULT '{}',
        criteria_results_json TEXT NOT NULL DEFAULT '[]',
        findings_json TEXT NOT NULL DEFAULT '[]',
        verifier_identity_json TEXT NOT NULL DEFAULT '{}',
        builder_identity_json TEXT NOT NULL DEFAULT '{}',
        evidence_refs_json TEXT NOT NULL DEFAULT '[]',
        verdict_hash TEXT,
        same_provider_limitation INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        last_event_id TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    26: """
    CREATE TABLE IF NOT EXISTS shadow_runs (
        run_id TEXT PRIMARY KEY,
        improvement_id TEXT NOT NULL,
        candidate_release_id TEXT NOT NULL,
        status TEXT NOT NULL
            CHECK(status IN ('RUNNING', 'COMPLETED', 'FAILED')),
        decisions_count INTEGER NOT NULL DEFAULT 0,
        divergences_json TEXT NOT NULL DEFAULT '[]',
        zero_live_effects INTEGER NOT NULL DEFAULT 0,
        stop_observed INTEGER NOT NULL DEFAULT 0,
        live_attempts INTEGER NOT NULL DEFAULT 0,
        blocked_attempts INTEGER NOT NULL DEFAULT 0,
        input_hash TEXT NOT NULL DEFAULT '',
        reason TEXT,
        created_at TEXT NOT NULL,
        last_event_id TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    27: """
    CREATE TABLE IF NOT EXISTS canaries (
        canary_id TEXT PRIMARY KEY,
        improvement_id TEXT NOT NULL,
        candidate_release_id TEXT NOT NULL,
        status TEXT NOT NULL
            CHECK(status IN ('RUNNING', 'COMPLETED', 'TERMINATED')),
        traffic_share REAL NOT NULL,
        duration_cycles INTEGER NOT NULL,
        max_cost_usd REAL NOT NULL,
        health_thresholds_json TEXT NOT NULL,
        cycles_run INTEGER NOT NULL DEFAULT 0,
        last_tick_cycle INTEGER,
        total_cost_usd REAL NOT NULL DEFAULT 0,
        total_errors INTEGER NOT NULL DEFAULT 0,
        last_health INTEGER NOT NULL DEFAULT 1,
        reason TEXT,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        last_event_id TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    28: """
    CREATE TABLE IF NOT EXISTS promotions (
        promotion_id TEXT PRIMARY KEY,
        release_id TEXT NOT NULL,
        verification_id TEXT NOT NULL,
        canary_id TEXT,
        baseline_run_id TEXT NOT NULL,
        candidate_run_id TEXT NOT NULL,
        risk_class TEXT NOT NULL,
        status TEXT NOT NULL
            CHECK(status IN ('INTENT', 'PROMOTED', 'ROLLED_BACK')),
        controller TEXT NOT NULL,
        trigger TEXT,
        reason TEXT,
        created_at TEXT NOT NULL,
        finished_at TEXT,
        last_event_id TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS release_control (
        id INTEGER PRIMARY KEY CHECK(id = 1),
        frozen INTEGER NOT NULL DEFAULT 0,
        frozen_reason TEXT,
        frozen_at TEXT,
        last_event_id TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    29: """
    ALTER TABLE promotions ADD COLUMN planned_json TEXT;
    CREATE TABLE IF NOT EXISTS restore_points (
        point_id TEXT PRIMARY KEY,
        label TEXT NOT NULL,
        path TEXT NOT NULL,
        sha256 TEXT NOT NULL,
        schema_version INTEGER NOT NULL,
        event_count INTEGER NOT NULL,
        status TEXT NOT NULL DEFAULT 'CREATED'
            CHECK(status IN ('CREATED', 'VERIFIED', 'EXECUTED')),
        controller TEXT NOT NULL,
        created_at TEXT NOT NULL,
        last_event_id TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    30: """
    CREATE TABLE IF NOT EXISTS discovery_runs (
        run_id TEXT PRIMARY KEY,
        sources_hash TEXT NOT NULL,
        candidates_count INTEGER NOT NULL,
        recorded_count INTEGER NOT NULL,
        controller TEXT NOT NULL,
        created_at TEXT NOT NULL,
        last_event_id TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    31: """
    CREATE TABLE IF NOT EXISTS cycles (
        cycle_id TEXT PRIMARY KEY,
        improvement_id TEXT NOT NULL,
        outcome TEXT NOT NULL
            CHECK(outcome IN ('promoted', 'rejected')),
        reason TEXT,
        promotion_id TEXT,
        canary_id TEXT,
        verification_id TEXT,
        baseline_run_id TEXT,
        candidate_run_id TEXT,
        build_id TEXT,
        candidate_release_id TEXT,
        genesis_release_id TEXT,
        workload_id TEXT,
        controller TEXT NOT NULL,
        created_at TEXT NOT NULL,
        last_event_id TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    32: """
    CREATE TABLE IF NOT EXISTS acceptance_runs (
        run_id TEXT PRIMARY KEY,
        passed INTEGER NOT NULL,
        report_json TEXT NOT NULL,
        controller TEXT NOT NULL,
        created_at TEXT NOT NULL,
        last_event_id TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    """,
    33: """
    -- V8 hybrid memory (R2): additive Tier C experiential memory
    -- Design source: JARVIS-Lab/jarvis-hybrid-memory FINAL_AUDIT/08
    -- Tier C is advisory: no FK into Tier A/B tables. Authority flows
    -- downward only. Rows are event-derived (last_event_id UNIQUE per row).
    CREATE TABLE IF NOT EXISTS tier_c_memories (
        memory_id TEXT PRIMARY KEY,
        mission_id TEXT NOT NULL DEFAULT '',
        agent_run_id TEXT,
        created_by TEXT NOT NULL DEFAULT 'unknown',
        mem_type TEXT NOT NULL CHECK(mem_type IN ('core', 'episodic', 'procedural')),
        scope TEXT NOT NULL DEFAULT 'role',
        content TEXT NOT NULL,
        source_ids_json TEXT NOT NULL DEFAULT '[]',
        evidence_ids_json TEXT NOT NULL DEFAULT '[]',
        confidence REAL NOT NULL CHECK(confidence >= 0.0 AND confidence <= 1.0),
        state TEXT NOT NULL CHECK(state IN ('OBSERVED', 'CANDIDATE_MEMORY', 'VALIDATING',
            'ACCEPTED_EXPERIENCE', 'CORE', 'EXTERNAL', 'PROCEDURAL', 'STALE',
            'SUPERSEDED', 'ARCHIVED')),
        created_at TEXT NOT NULL,
        last_verified TEXT,
        expires_at TEXT,
        supersedes TEXT,
        contradicts TEXT,
        importance REAL NOT NULL DEFAULT 0.5,
        context_cost_chars INTEGER NOT NULL DEFAULT 0,
        promotion_reason TEXT,
        last_event_id TEXT NOT NULL UNIQUE,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS tier_c_memories_state ON tier_c_memories(state);
    CREATE INDEX IF NOT EXISTS tier_c_memories_scope_state ON tier_c_memories(scope, state);
    CREATE INDEX IF NOT EXISTS tier_c_memories_expires ON tier_c_memories(expires_at);
    CREATE INDEX IF NOT EXISTS tier_c_memories_mission ON tier_c_memories(mission_id);

    CREATE TABLE IF NOT EXISTS tier_c_memory_revisions (
        revision_id TEXT PRIMARY KEY,
        memory_id TEXT NOT NULL REFERENCES tier_c_memories(memory_id),
        content TEXT NOT NULL,
        state TEXT NOT NULL,
        actor TEXT NOT NULL,
        reason TEXT NOT NULL,
        changed_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS tier_c_memory_revisions_memory
        ON tier_c_memory_revisions(memory_id, changed_at);

    CREATE TABLE IF NOT EXISTS tier_c_validations (
        memory_id TEXT PRIMARY KEY REFERENCES tier_c_memories(memory_id),
        verdict_json TEXT NOT NULL,
        validated_by TEXT NOT NULL,
        validated_at TEXT NOT NULL,
        last_event_id TEXT NOT NULL UNIQUE
    );

    CREATE TABLE IF NOT EXISTS tier_c_blocks (
        label TEXT PRIMARY KEY,
        value TEXT NOT NULL DEFAULT '',
        char_limit INTEGER NOT NULL CHECK(char_limit > 0),
        read_only INTEGER NOT NULL DEFAULT 0 CHECK(read_only IN (0, 1)),
        description TEXT NOT NULL DEFAULT '',
        owner TEXT NOT NULL DEFAULT 'owner',
        revision INTEGER NOT NULL DEFAULT 0,
        last_event_id TEXT NOT NULL UNIQUE,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS tier_c_block_revisions (
        revision_id TEXT PRIMARY KEY,
        label TEXT NOT NULL REFERENCES tier_c_blocks(label),
        value TEXT NOT NULL,
        actor TEXT NOT NULL,
        reason TEXT NOT NULL,
        changed_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS tier_c_block_revisions_label
        ON tier_c_block_revisions(label, changed_at);

    CREATE TABLE IF NOT EXISTS tier_c_procedures (
        procedure_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        body TEXT NOT NULL,
        trigger_terms_json TEXT NOT NULL DEFAULT '[]',
        scope TEXT NOT NULL DEFAULT 'role',
        state TEXT NOT NULL CHECK(state IN ('ACTIVE', 'STALE', 'SUPERSEDED', 'ARCHIVED')),
        confidence REAL NOT NULL DEFAULT 0.5 CHECK(confidence >= 0.0 AND confidence <= 1.0),
        source_ids_json TEXT NOT NULL DEFAULT '[]',
        evidence_ids_json TEXT NOT NULL DEFAULT '[]',
        mission_id TEXT NOT NULL DEFAULT '',
        created_by TEXT NOT NULL DEFAULT 'unknown',
        created_at TEXT NOT NULL,
        last_verified TEXT,
        expires_at TEXT,
        supersedes TEXT,
        last_event_id TEXT NOT NULL UNIQUE,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS tier_c_procedures_state ON tier_c_procedures(state);
    CREATE INDEX IF NOT EXISTS tier_c_procedures_scope ON tier_c_procedures(scope);
    """,
    34: """
    -- Durable executable authority for an existing mission step.  Contract
    -- rows are immutable. Corrections use a higher version and explicit
    -- supersession.  The event ledger remains the source of truth.
    CREATE TABLE IF NOT EXISTS mission_step_contracts (
        contract_id TEXT PRIMARY KEY,
        mission_id TEXT NOT NULL,
        step_id TEXT NOT NULL,
        version INTEGER NOT NULL CHECK(version > 0),
        resource_id TEXT NOT NULL,
        capability TEXT NOT NULL,
        target_relative_path TEXT NOT NULL,
        instructions TEXT NOT NULL,
        expected_effects_json TEXT NOT NULL,
        rollback_json TEXT NOT NULL,
        risk_class TEXT NOT NULL CHECK(risk_class IN ('C0', 'C1', 'C2', 'C3')),
        verification_criteria_json TEXT NOT NULL,
        expected_artifacts_json TEXT NOT NULL,
        forbidden_paths_json TEXT NOT NULL,
        contract_hash TEXT NOT NULL CHECK(length(contract_hash) = 64),
        recorded_by TEXT NOT NULL,
        source TEXT NOT NULL,
        provenance_json TEXT NOT NULL DEFAULT '{}',
        supersedes_hash TEXT,
        last_event_id TEXT NOT NULL UNIQUE,
        updated_at TEXT NOT NULL,
        UNIQUE(mission_id, step_id, version)
    );
    CREATE INDEX IF NOT EXISTS mission_step_contracts_mission
        ON mission_step_contracts(mission_id, step_id, version);
    """,
}


def split_sql_statements(script: str) -> list[str]:
    """Split a SQL script into individual statements.

    Splits on semicolons at top level, ignoring semicolons inside single-quoted
    string literals. Used instead of ``sqlite3.Connection.executescript``
    because ``executescript`` implicitly commits any pending transaction and
    runs each statement in autocommit mode, which breaks all-or-nothing
    migration atomicity (a crash mid-migration would persist partial schema).
    """
    statements: list[str] = []
    current: list[str] = []
    in_string = False
    for character in script:
        if character == "'":
            in_string = not in_string
            current.append(character)
        elif character == ";" and not in_string:
            statement = "".join(current).strip()
            if statement:
                statements.append(statement)
            current = []
        else:
            current.append(character)
    if in_string:
        raise ValueError("unterminated string literal in migration script")
    statement = "".join(current).strip()
    if statement:
        statements.append(statement)
    return statements


def migrate(connection: sqlite3.Connection, now: str, *, target: int | None = None) -> int:
    """Migrate the schema to SCHEMA_VERSION (or ``target`` when given).

    Every DDL statement runs through ``connection.execute`` so all statements
    remain inside the caller's transaction (``initialize`` wraps this call in
    ``BEGIN IMMEDIATE``). A crash or failure anywhere in the migration rolls
    the entire migration back; the database reopens at the previous schema
    version and migrates cleanly on the next initialization.
    """
    destination = SCHEMA_VERSION if target is None else target
    if destination > SCHEMA_VERSION or destination not in MIGRATIONS:
        raise KeyError(f"no migration defined for schema version {destination}")
    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    current = connection.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()[0]
    for version in range(current + 1, destination + 1):
        for statement in split_sql_statements(MIGRATIONS[version]):
            connection.execute(statement)
        connection.execute(
            "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
            (version, now),
        )
    # Roadmap-V4 tables are additive projections.  They intentionally do not
    # advance the historical migration number: an isolated candidate can add
    # them to a schema-v20 copy without rewriting V5's migration history.
    # Keep the additive tables available on schema-target copies too.  This
    # makes isolated migration/rebuild tests safe without changing the
    # historical schema_migrations sequence.
    ensure_v4_tables(connection)
    return destination
