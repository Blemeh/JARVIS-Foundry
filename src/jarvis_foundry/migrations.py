from __future__ import annotations

import sqlite3


SCHEMA_VERSION = 7

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
    return destination
