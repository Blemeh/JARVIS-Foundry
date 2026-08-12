from __future__ import annotations

import json
import sqlite3

from .domain import require_transition
from .events import Event, canonical_json


PROJECTION_TABLES = (
    "missions",
    "resources",
    "directives",
    "action_decisions",
    "agent_runs",
    "evidence_bundles",
    "verification_verdicts",
    "budgets",
    "obsidian_writes",
    "knowledge_claims",
    "knowledge_contradictions",
    "knowledge_manifests",
    "knowledge_entities",
    "knowledge_claim_entities",
    "knowledge_ingestions",
    "knowledge_snapshots",
    "candidates",
    "candidate_scores",
    "monitors",
    "vault_files",
    "vault_sync_state",
    "ingestion_cursors",
    "research_queue",
    "worker_manifests",
)


def apply_event(connection: sqlite3.Connection, event: Event) -> None:
    payload = event.payload
    if event.event_type == "system.start_build_recorded":
        connection.execute(
            """UPDATE system_state SET start_active = 1, start_event_id = ?,
               start_received_at = ? WHERE singleton = 1""",
            (event.event_id, payload["received_at"]),
        )
    elif event.event_type == "mission.created":
        # mandatory payload key: clean validation error (not a deep
        # KeyError) — verifier deleg_verifier_slice2_adv C7d
        if "acceptance_criteria" not in payload:
            raise ValueError(
                "mission.created requires acceptance_criteria "
                "(mandatory payload key) — rolled back")
        # created_from exists only from schema v11 (ALTER ADD COLUMN);
        # the handler adapts so ledger replays against ANY schema
        # version work (mid-migration appends included)
        columns = {row[1] for row in connection.execute(
            "PRAGMA table_info(missions)")}
        base = (
            "INSERT INTO missions(mission_id, objective, resource_id, "
            "state, acceptance_json, next_action, last_event_id, "
            "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                payload["mission_id"], payload["objective"], payload.get("resource_id"),
                payload.get("state", "PLANNED"), canonical_json(payload["acceptance_criteria"]),
                payload.get("next_action"), event.event_id, event.recorded_at,
            ),
        )
        if "created_from" in columns:
            connection.execute(
                "INSERT INTO missions(mission_id, objective, resource_id, "
                "state, acceptance_json, next_action, created_from, "
                "last_event_id, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    payload["mission_id"], payload["objective"], payload.get("resource_id"),
                    payload.get("state", "PLANNED"),
                    canonical_json(payload["acceptance_criteria"]),
                    payload.get("next_action"), payload.get("created_from"),
                    event.event_id, event.recorded_at,
                ),
            )
        else:
            connection.execute(*base)
    elif event.event_type == "mission.completed":
        # V3 slice-2 wiring: a mission whose created_from is
        # candidate:<id> auto-records the candidate outcome (prediction
        # vs reality) and marks it EXECUTED.
        if "mission_id" not in payload:
            raise ValueError(
                "mission.completed requires mission_id "
                "(mandatory payload key) — rolled back")
        # created_from exists only from schema v11; on pre-v11 schemas
        # the outcome wiring is a no-op (same adaptation as
        # mission.created) — verifier deleg_verifier_slice2_adv C4
        columns = {row[1] for row in connection.execute(
            "PRAGMA table_info(missions)")}
        if "created_from" not in columns:
            return
        row = connection.execute(
            "SELECT created_from FROM missions WHERE mission_id = ?",
            (payload["mission_id"],)).fetchone()
        if row and row[0] and row[0].startswith("candidate:"):
            candidate_id = row[0].split(":", 1)[1]
            outcome = {"prediction": payload.get("objective"),
                       "reality": payload.get("outcome", {})}
            connection.execute(
                "UPDATE candidates SET status = 'EXECUTED', evidence_json = ?, "
                "last_event_id = ?, updated_at = ? WHERE candidate_id = ?",
                (canonical_json([{"outcome": outcome}]), event.event_id,
                 event.recorded_at, candidate_id),
            )
    elif event.event_type == "mission.state_changed":
        row = connection.execute(
            "SELECT state FROM missions WHERE mission_id = ?", (payload["mission_id"],)
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown mission: {payload['mission_id']}")
        require_transition(row[0], payload["state"])
        connection.execute(
            """UPDATE missions SET state = ?, next_action = ?, last_event_id = ?, updated_at = ?
               WHERE mission_id = ?""",
            (payload["state"], payload.get("next_action"), event.event_id, event.recorded_at, payload["mission_id"]),
        )
    elif event.event_type == "project.registered":
        connection.execute(
            """INSERT INTO resources(
                resource_id, kind, root_path, resolved_root, baseline_json,
                capabilities_json, readiness_state, last_event_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(resource_id) DO UPDATE SET
                kind=excluded.kind, root_path=excluded.root_path,
                resolved_root=excluded.resolved_root, baseline_json=excluded.baseline_json,
                capabilities_json=excluded.capabilities_json,
                readiness_state=excluded.readiness_state,
                last_event_id=excluded.last_event_id, updated_at=excluded.updated_at""",
            (
                payload["resource_id"], payload["kind"], payload["root_path"], payload["resolved_root"],
                canonical_json(payload["baseline"]), canonical_json(payload["capabilities"]),
                payload["readiness_state"], event.event_id, event.recorded_at,
            ),
        )
    elif event.event_type == "capability.state_changed":
        resource_id = payload["resource_id"]
        capability = payload["capability"]
        state = payload["state"]
        row = connection.execute(
            "SELECT capabilities_json FROM resources WHERE resource_id = ?", (resource_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"unknown resource: {resource_id}")
        capabilities = json.loads(row[0])
        capabilities[capability] = state
        connection.execute(
            """UPDATE resources SET capabilities_json = ?, last_event_id = ?, updated_at = ?
               WHERE resource_id = ?""",
            (canonical_json(capabilities), event.event_id, event.recorded_at, resource_id),
        )
    elif event.event_type == "owner_directive.recorded":
        connection.execute(
            """INSERT INTO directives(
                directive_id, command_type, target, exact_text, active, last_event_id, updated_at
            ) VALUES (?, ?, ?, ?, 1, ?, ?)""",
            (
                payload["directive_id"], payload["command_type"], payload.get("target"),
                payload["exact_text"], event.event_id, event.recorded_at,
            ),
        )
    elif event.event_type == "owner_directive.revoked":
        connection.execute(
            "UPDATE directives SET active = 0, last_event_id = ?, updated_at = ? WHERE directive_id = ?",
            (event.event_id, event.recorded_at, payload["directive_id"]),
        )
    elif event.event_type in {"action.allowed", "action.vetoed", "action.not_ready", "action.stopped", "action.invalid_target"}:
        connection.execute(
            """INSERT INTO action_decisions(
                action_id, mission_id, decision, reason, target, last_event_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(action_id) DO UPDATE SET
                decision=excluded.decision, reason=excluded.reason, target=excluded.target,
                last_event_id=excluded.last_event_id, updated_at=excluded.updated_at""",
            (
                payload["action_id"], payload["mission_id"], payload["decision"],
                payload["reason"], payload.get("target"), event.event_id, event.recorded_at,
            ),
        )
    elif event.event_type == "agent_run.started":
        connection.execute(
            """INSERT INTO agent_runs(
                run_id, mission_id, step_id, role, provider, model,
                context_manifest_hash, tool_policy_json, status, started_at,
                last_event_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'RUNNING', ?, ?, ?)
            ON CONFLICT(run_id) DO UPDATE SET
                provider=excluded.provider, model=excluded.model,
                context_manifest_hash=excluded.context_manifest_hash,
                tool_policy_json=excluded.tool_policy_json,
                status='RUNNING', last_event_id=excluded.last_event_id,
                updated_at=excluded.updated_at""",
            (
                payload["run_id"], payload["mission_id"], payload.get("step_id"),
                payload["role"], payload.get("provider"), payload.get("model"),
                payload.get("context_manifest_hash"),
                canonical_json(payload.get("tool_policy", {})),
                event.recorded_at, event.event_id, event.recorded_at,
            ),
        )
    elif event.event_type == "agent_run.heartbeat":
        connection.execute(
            "UPDATE agent_runs SET heartbeat_at = ?, last_event_id = ?, updated_at = ? WHERE run_id = ?",
            (event.recorded_at, event.event_id, event.recorded_at, payload["run_id"]),
        )
    elif event.event_type == "agent_run.completed":
        connection.execute(
            """UPDATE agent_runs SET status = 'COMPLETED', finished_at = ?,
               result_json = ?, cost_json = ?, failure_class = NULL,
               last_event_id = ?, updated_at = ? WHERE run_id = ?""",
            (
                event.recorded_at, canonical_json(payload.get("result", {})),
                canonical_json(payload.get("cost", {})), event.event_id, event.recorded_at,
                payload["run_id"],
            ),
        )
    elif event.event_type == "agent_run.failed":
        connection.execute(
            """UPDATE agent_runs SET status = 'FAILED', finished_at = ?, failure_class = ?,
               last_event_id = ?, updated_at = ? WHERE run_id = ?""",
            (event.recorded_at, payload.get("failure_class"), event.event_id, event.recorded_at, payload["run_id"]),
        )
    elif event.event_type == "evidence.created":
        connection.execute(
            """INSERT INTO evidence_bundles(
                bundle_id, mission_id, step_id, objective, criteria_json, before_state_json,
                baseline_commit, final_diff_json, files_changed_json, commands_run_json,
                artifact_hashes_json, after_state_json, uncertainty_json, rollback_proof_json,
                builder_claim_json, verifier_verdict_id, last_event_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                payload["bundle_id"], payload["mission_id"], payload.get("step_id"),
                payload["objective"], canonical_json(payload.get("criteria", [])),
                canonical_json(payload.get("before_state", {})), payload.get("baseline_commit"),
                canonical_json(payload.get("final_diff", {})),
                canonical_json(payload.get("files_changed", [])),
                canonical_json(payload.get("commands_run", [])),
                canonical_json(payload.get("artifact_hashes", {})),
                canonical_json(payload.get("after_state", {})),
                canonical_json(payload.get("uncertainty", {})),
                canonical_json(payload.get("rollback_proof", {})),
                canonical_json(payload.get("builder_claim", {})),
                payload.get("verifier_verdict_id"), event.event_id, event.recorded_at,
            ),
        )
    elif event.event_type == "verification.started":
        connection.execute(
            """INSERT INTO verification_verdicts(
                verdict_id, mission_id, subject_type, subject_id, status,
                verifier_run_id, builder_run_id, independence_json, criteria_json,
                last_event_id, updated_at
            ) VALUES (?, ?, ?, ?, 'STARTED', ?, ?, ?, ?, ?, ?)""",
            (
                payload["verdict_id"], payload["mission_id"], payload["subject_type"],
                payload["subject_id"], payload.get("verifier_run_id"),
                payload.get("builder_run_id"),
                canonical_json(payload.get("independence", {})),
                canonical_json(payload.get("criteria", [])),
                event.event_id, event.recorded_at,
            ),
        )
    elif event.event_type == "verification.passed":
        connection.execute(
            """UPDATE verification_verdicts SET status = 'PASS', verdict = 'PASS',
               verifier_run_id = COALESCE(?, verifier_run_id),
               findings_json = ?, evidence_json = ?, missing_proof_json = ?,
               scope_violations_json = ?, issued_at = ?, last_event_id = ?, updated_at = ?
               WHERE verdict_id = ?""",
            (
                payload.get("verifier_run_id"),
                canonical_json(payload.get("findings", [])),
                canonical_json(payload.get("evidence", {})),
                canonical_json(payload.get("missing_proof", [])),
                canonical_json(payload.get("scope_violations", [])),
                event.recorded_at, event.event_id, event.recorded_at, payload["verdict_id"],
            ),
        )
    elif event.event_type == "verification.failed":
        connection.execute(
            """UPDATE verification_verdicts SET status = 'FAIL', verdict = 'FAIL',
               verifier_run_id = COALESCE(?, verifier_run_id),
               findings_json = ?, repair_requirements_json = ?, scope_violations_json = ?,
               issued_at = ?, last_event_id = ?, updated_at = ? WHERE verdict_id = ?""",
            (
                payload.get("verifier_run_id"),
                canonical_json(payload.get("findings", [])),
                canonical_json(payload.get("repair_requirements", [])),
                canonical_json(payload.get("scope_violations", [])),
                event.recorded_at, event.event_id, event.recorded_at, payload["verdict_id"],
            ),
        )
    elif event.event_type == "verification.blocked":
        connection.execute(
            """UPDATE verification_verdicts SET status = 'BLOCKED', verdict = 'BLOCKED',
               blocking_dependency = ?, reason = ?, issued_at = ?,
               last_event_id = ?, updated_at = ? WHERE verdict_id = ?""",
            (
                payload.get("blocking_dependency"), payload.get("reason"),
                event.recorded_at, event.event_id, event.recorded_at, payload["verdict_id"],
            ),
        )
    elif event.event_type == "budget.allocation_recorded":
        connection.execute(
            """INSERT INTO budgets(
                provider, lifetime_budget_usd, daily_soft_limit_usd, billing_verified,
                last_event_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(provider) DO UPDATE SET
                lifetime_budget_usd = excluded.lifetime_budget_usd,
                daily_soft_limit_usd = excluded.daily_soft_limit_usd,
                billing_verified = excluded.billing_verified,
                last_event_id = excluded.last_event_id,
                updated_at = excluded.updated_at""",
            (
                payload["provider"], payload.get("lifetime_budget_usd"),
                payload.get("daily_soft_limit_usd"), 1 if payload.get("billing_verified") else 0,
                event.event_id, event.recorded_at,
            ),
        )
    elif event.event_type == "budget.usage_recorded":
        connection.execute(
            """INSERT INTO budgets(provider, spent_usd, last_event_id, updated_at)
               VALUES (?, ?, ?, ?)
               ON CONFLICT(provider) DO UPDATE SET
                   spent_usd = budgets.spent_usd + excluded.spent_usd,
                   last_event_id = excluded.last_event_id,
                   updated_at = excluded.updated_at""",
            (payload["provider"], float(payload.get("amount_usd", 0.0)), event.event_id, event.recorded_at),
        )
    elif event.event_type == "obsidian.write_started":
        connection.execute(
            """INSERT INTO obsidian_writes(
                write_id, note_name, note_path, status, expected_hash,
                before_hash, before_snapshot_path, last_event_id, updated_at
            ) VALUES (?, ?, ?, 'STARTED', ?, ?, ?, ?, ?)""",
            (
                payload["write_id"], payload["note_name"], payload["note_path"],
                payload.get("expected_hash"), payload.get("before_hash"),
                payload.get("before_snapshot_path"),
                event.event_id, event.recorded_at,
            ),
        )
    elif event.event_type == "obsidian.write_completed":
        connection.execute(
            """UPDATE obsidian_writes SET status = 'COMPLETED', after_hash = ?,
               last_event_id = ?, updated_at = ? WHERE write_id = ?""",
            (payload.get("after_hash"), event.event_id, event.recorded_at, payload["write_id"]),
        )
    elif event.event_type == "obsidian.conflict":
        connection.execute(
            """UPDATE obsidian_writes SET status = 'CONFLICT',
               conflict_preserved_path = ?, last_event_id = ?, updated_at = ?
               WHERE write_id = ?""",
            (payload.get("preserved_path"), event.event_id, event.recorded_at, payload["write_id"]),
        )
    elif event.event_type == "obsidian.rolled_back":
        connection.execute(
            """UPDATE obsidian_writes SET status = 'ROLLED_BACK',
               rollback_pointer = ?, last_event_id = ?, updated_at = ?
               WHERE write_id = ?""",
            (payload.get("rollback_pointer"), event.event_id, event.recorded_at, payload["write_id"]),
        )
    elif event.event_type == "knowledge.claim_recorded":
        from .knowledge import contradicts

        claim_id = payload["claim_id"]
        connection.execute(
            """INSERT INTO knowledge_claims(
                claim_id, mission_id, statement, claim_type, confidence,
                fresh_until, source_run_id, source_event_id, status,
                superseded_by, last_event_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'ACCEPTED', NULL, ?, ?)""",
            (
                claim_id, payload["mission_id"], payload["statement"],
                payload["claim_type"], payload.get("confidence", 0.5),
                payload.get("fresh_until"),
                payload.get("source", {}).get("run_id"),
                payload.get("source", {}).get("event_id"),
                event.event_id, event.recorded_at,
            ),
        )
        # deterministic contradiction detection at record time: compare the
        # new claim against every live ACCEPTED claim of the same type.
        # Derived projection (no separate event) — deterministic under
        # rebuild, both claims stay visible with statuses flagged.
        statement = payload["statement"]
        claim_type = payload["claim_type"]
        rivals = connection.execute(
            "SELECT claim_id, statement FROM knowledge_claims "
            "WHERE claim_type = ? AND status = 'ACCEPTED' AND claim_id != ?",
            (claim_type, claim_id),
        ).fetchall()
        for rival_id, rival_statement in rivals:
            if contradicts(statement, rival_statement):
                contradiction_id = f"contra:{claim_id}:{rival_id}"
                reason = (f"negation pair within claim_type '{claim_type}': "
                          f"'{statement}' vs '{rival_statement}'")
                connection.execute(
                    """INSERT OR IGNORE INTO knowledge_contradictions(
                        contradiction_id, claim_a_id, claim_b_id, claim_type,
                        reason, last_event_id, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (contradiction_id, claim_id, rival_id, claim_type,
                     reason, event.event_id, event.recorded_at),
                )
                connection.execute(
                    "UPDATE knowledge_claims SET status = 'CONTRADICTED', "
                    "last_event_id = ?, updated_at = ? WHERE claim_id IN (?, ?)",
                    (event.event_id, event.recorded_at, claim_id, rival_id),
                )
    elif event.event_type == "knowledge.claim_superseded":
        target = connection.execute(
            "SELECT claim_id FROM knowledge_claims WHERE claim_id = ?",
            (payload["claim_id"],)).fetchone()
        replacement = connection.execute(
            "SELECT claim_id FROM knowledge_claims WHERE claim_id = ?",
            (payload["superseded_by"],)).fetchone()
        if target is None or replacement is None:
            raise ValueError(
                "knowledge.claim_superseded requires both the target claim "
                f"({payload.get('claim_id')}) and the superseding claim "
                f"({payload.get('superseded_by')}) to exist — rolled back")
        connection.execute(
            "UPDATE knowledge_claims SET status = 'SUPERSEDED', "
            "superseded_by = ?, last_event_id = ?, updated_at = ? "
            "WHERE claim_id = ?",
            (payload["superseded_by"], event.event_id, event.recorded_at,
             payload["claim_id"]),
        )

    elif event.event_type == "knowledge.context_manifest_generated":
        connection.execute(
            """INSERT INTO knowledge_manifests(
                manifest_id, mission_id, step_id, objective_hash,
                active_count, stale_count, conflict_count, truncated,
                manifest_hash, last_event_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                payload["manifest_id"], payload["mission_id"],
                payload.get("step_id"), payload["objective_hash"],
                payload["active_count"], payload.get("stale_count", 0),
                payload.get("conflict_count", 0),
                int(bool(payload.get("truncated", 0))),
                payload["manifest_hash"], event.event_id, event.recorded_at,
            ),
        )

    elif event.event_type == "knowledge.entity_registered":
        connection.execute(
            """INSERT INTO knowledge_entities(
                entity_id, mission_id, canonical_name, aliases_json,
                last_event_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)""",
            (payload["entity_id"], payload["mission_id"],
             payload["canonical_name"],
             canonical_json(payload.get("aliases", [])),
             event.event_id, event.recorded_at),
        )
    elif event.event_type == "knowledge.entity_alias_added":
        row = connection.execute(
            "SELECT aliases_json FROM knowledge_entities WHERE entity_id = ?",
            (payload["entity_id"],)).fetchone()
        if row is None:
            raise ValueError(
                f"knowledge.entity_alias_added requires an existing entity "
                f"({payload.get('entity_id')}) — rolled back")
        aliases = json.loads(row[0])
        alias = payload["alias"]
        if alias not in aliases:
            aliases.append(alias)
            connection.execute(
                "UPDATE knowledge_entities SET aliases_json = ?, "
                "last_event_id = ?, updated_at = ? WHERE entity_id = ?",
                (canonical_json(aliases), event.event_id, event.recorded_at,
                 payload["entity_id"]),
            )
    elif event.event_type == "knowledge.claim_entity_linked":
        claim = connection.execute(
            "SELECT claim_id FROM knowledge_claims WHERE claim_id = ?",
            (payload["claim_id"],)).fetchone()
        entity = connection.execute(
            "SELECT entity_id FROM knowledge_entities WHERE entity_id = ?",
            (payload["entity_id"],)).fetchone()
        if claim is None or entity is None:
            raise ValueError(
                "knowledge.claim_entity_linked requires both the claim and "
                f"the entity to exist (claim={payload.get('claim_id')}, "
                f"entity={payload.get('entity_id')}) — rolled back")
        connection.execute(
            """INSERT OR IGNORE INTO knowledge_claim_entities(
                claim_id, entity_id, role, last_event_id, updated_at
            ) VALUES (?, ?, ?, ?, ?)""",
            (payload["claim_id"], payload["entity_id"],
             payload.get("role"), event.event_id, event.recorded_at),
        )

    elif event.event_type == "knowledge.ingestion_queued":
        connection.execute(
            """INSERT INTO knowledge_ingestions(
                ingestion_id, mission_id, source_type, source_ref,
                source_hash, status, claims_count, errors_json,
                last_event_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'QUEUED', 0, '[]', ?, ?)""",
            (payload["ingestion_id"], payload["mission_id"],
             payload.get("source_type", "document"), payload["source_ref"],
             payload["source_hash"], event.event_id, event.recorded_at),
        )
    elif event.event_type == "knowledge.ingestion_completed":
        connection.execute(
            "UPDATE knowledge_ingestions SET status = 'COMPLETED', "
            "claims_count = ?, errors_json = ?, last_event_id = ?, "
            "updated_at = ? WHERE ingestion_id = ?",
            (payload.get("claims_count", 0), canonical_json(payload.get("errors", [])),
             event.event_id, event.recorded_at, payload["ingestion_id"]),
        )
    elif event.event_type == "knowledge.ingestion_failed":
        connection.execute(
            "UPDATE knowledge_ingestions SET status = 'FAILED', "
            "errors_json = ?, last_event_id = ?, updated_at = ? "
            "WHERE ingestion_id = ?",
            (canonical_json(payload.get("errors", [])), event.event_id,
             event.recorded_at, payload["ingestion_id"]),
        )

    elif event.event_type == "knowledge.snapshot_created":
        connection.execute(
            """INSERT INTO knowledge_snapshots(
                snapshot_id, mission_id, scope, head_event_id,
                schema_version, claims_count, content_sha256, path,
                last_event_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (payload["snapshot_id"], payload["mission_id"],
             payload.get("scope", "knowledge"), payload["head_event_id"],
             payload["schema_version"], payload.get("claims_count", 0),
             payload.get("content_sha256", ""), payload["path"],
             event.event_id, event.recorded_at),
        )
    elif event.event_type == "knowledge.claims_rolled_back":
        for claim_id in payload.get("claim_ids", []):
            connection.execute(
                "UPDATE knowledge_claims SET status = 'ROLLED_BACK', "
                "rolled_back_by = ?, last_event_id = ?, updated_at = ? "
                "WHERE claim_id = ?",
                (payload["snapshot_id"], event.event_id, event.recorded_at,
                 claim_id),
            )

    elif event.event_type == "discovery.candidate_generated":
        connection.execute(
            """INSERT INTO candidates(
                candidate_id, mission_id, source, objective,
                objective_canonical, evidence_json, status, duplicate_of,
                created_from, last_event_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'NEW', ?, ?, ?, ?)""",
            (payload["candidate_id"], payload["mission_id"],
             payload["source"], payload["objective"],
             payload.get("objective_canonical", canonical_json(payload["objective"])),
             canonical_json(payload.get("evidence", [])),
             payload.get("duplicate_of"), payload.get("created_from"),
             event.event_id, event.recorded_at),
        )
    elif event.event_type == "discovery.candidate_scored":
        connection.execute(
            "UPDATE candidates SET status = 'SCORED', last_event_id = ?, "
            "updated_at = ? WHERE candidate_id = ?",
            (event.event_id, event.recorded_at, payload["candidate_id"]))
        connection.execute(
            """INSERT OR REPLACE INTO candidate_scores(
                candidate_id, score, weights_json, last_event_id, updated_at
            ) VALUES (?, ?, ?, ?, ?)""",
            (payload["candidate_id"], payload["score"],
             canonical_json(payload.get("weights", {})),
             event.event_id, event.recorded_at),
        )
    elif event.event_type == "discovery.candidate_approved":
        connection.execute(
            "UPDATE candidates SET status = 'APPROVED', last_event_id = ?, "
            "updated_at = ? WHERE candidate_id = ?",
            (event.event_id, event.recorded_at, payload["candidate_id"]))
    elif event.event_type == "discovery.candidate_rejected":
        connection.execute(
            "UPDATE candidates SET status = 'REJECTED', evidence_json = ?, "
            "last_event_id = ?, updated_at = ? WHERE candidate_id = ?",
            (canonical_json([{"rejection_reason": payload.get("reason", "")}]),
             event.event_id, event.recorded_at, payload["candidate_id"]))
    elif event.event_type == "discovery.candidate_outcome_recorded":
        connection.execute(
            "UPDATE candidates SET status = 'EXECUTED', evidence_json = ?, "
            "last_event_id = ?, updated_at = ? WHERE candidate_id = ?",
            (canonical_json([{"outcome": payload.get("outcome", {})}]),
             event.event_id, event.recorded_at, payload["candidate_id"]))

    elif event.event_type == "discovery.monitor_registered":
        connection.execute(
            """INSERT INTO monitors(
                monitor_id, mission_id, source_kind, source_ref,
                poll_interval_s, enabled, last_poll_at, last_event_id,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, 1, NULL, ?, ?)""",
            (payload["monitor_id"], payload["mission_id"],
             payload["source_kind"], payload["source_ref"],
             payload.get("poll_interval_s", 300),
             event.event_id, event.recorded_at),
        )
    elif event.event_type == "discovery.monitor_polled":
        connection.execute(
            "UPDATE monitors SET last_poll_at = ?, last_event_id = ?, "
            "updated_at = ? WHERE monitor_id = ?",
            (event.recorded_at, event.event_id, event.recorded_at,
             payload["monitor_id"]),
        )
    elif event.event_type == "discovery.monitor_disabled":
        connection.execute(
            "UPDATE monitors SET enabled = 0, last_event_id = ?, "
            "updated_at = ? WHERE monitor_id = ?",
            (event.event_id, event.recorded_at, payload["monitor_id"]),
        )
    elif event.event_type == "vault.file_indexed":
        # WP11 slice A: upsert the file row; prior hashes stay in the
        # event history (append-only), the row keeps the latest state.
        connection.execute(
            """INSERT INTO vault_files(
                rel_path, content_sha256, size_bytes, mtime,
                first_seen, last_synced, status, last_event_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'ACTIVE', ?, ?)
            ON CONFLICT(rel_path) DO UPDATE SET
                content_sha256 = excluded.content_sha256,
                size_bytes = excluded.size_bytes,
                mtime = excluded.mtime,
                last_synced = excluded.last_synced,
                status = 'ACTIVE',
                last_event_id = excluded.last_event_id,
                updated_at = excluded.updated_at""",
            (payload["rel_path"], payload["content_sha256"],
             payload["size_bytes"], payload["mtime"],
             event.recorded_at, event.recorded_at,
             event.event_id, event.recorded_at),
        )
    elif event.event_type == "vault.file_removed":
        connection.execute(
            "UPDATE vault_files SET status = 'REMOVED', last_event_id = ?, "
            "updated_at = ? WHERE rel_path = ?",
            (event.event_id, event.recorded_at, payload["rel_path"]),
        )
    elif event.event_type == "vault.sync_completed":
        connection.execute(
            """INSERT INTO vault_sync_state(
                sync_id, root, started_at, completed_at,
                files_indexed, files_changed, files_removed, cursor,
                last_event_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(sync_id) DO UPDATE SET
                completed_at = excluded.completed_at,
                files_indexed = excluded.files_indexed,
                files_changed = excluded.files_changed,
                files_removed = excluded.files_removed,
                cursor = excluded.cursor,
                last_event_id = excluded.last_event_id,
                updated_at = excluded.updated_at""",
            (payload["sync_id"], payload["root"], payload["started_at"],
             event.recorded_at, payload["files_indexed"],
             payload["files_changed"], payload["files_removed"],
             payload.get("cursor"), event.event_id, event.recorded_at),
        )

    elif event.event_type == "ingestion.cursor_updated":
        connection.execute(
            """INSERT INTO ingestion_cursors(
                source_kind, cursor, last_event_id, updated_at
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(source_kind) DO UPDATE SET
                cursor = excluded.cursor,
                last_event_id = excluded.last_event_id,
                updated_at = excluded.updated_at""",
            (payload["source_kind"], payload["cursor"],
             event.event_id, event.recorded_at),
        )

    elif event.event_type == "research.queue_item_created":
        connection.execute(
            """INSERT INTO research_queue(
                item_id, claim_id, reason, evidence_json, status,
                last_event_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (payload["item_id"], payload["claim_id"], payload["reason"],
             canonical_json(payload.get("evidence", {})),
             payload.get("status", "OPEN"),
             event.event_id, event.recorded_at),
        )
    elif event.event_type == "research.queue_item_resolved":
        connection.execute(
            "UPDATE research_queue SET status = ?, evidence_json = ?, "
            "last_event_id = ?, updated_at = ? WHERE item_id = ?",
            (payload["status"], canonical_json(payload.get("evidence", {})),
             event.event_id, event.recorded_at, payload["item_id"]),
        )

    elif event.event_type == "worker.manifest_delivered":
        connection.execute(
            """INSERT INTO worker_manifests(
                manifest_id, run_id, worker, mission_id, objective,
                manifest_hash, sources_json, truncation_json,
                last_event_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (payload["manifest_id"], payload["run_id"], payload["worker"],
             payload["mission_id"], payload["objective"],
             payload["manifest_hash"],
             canonical_json(payload.get("sources", {})),
             canonical_json(payload.get("truncation", {})),
             event.event_id, event.recorded_at),
        )

    if event.event_type.startswith("supervisor.") or event.event_type.startswith("emergency_stop."):
        status = {
            "supervisor.started": "RUNNING",
            "supervisor.heartbeat": "RUNNING",
            "supervisor.degraded": "DEGRADED",
            "supervisor.stopped": "STOPPED",
            "emergency_stop.requested": "FREEZING",
            "emergency_stop.frozen": "FROZEN",
            "emergency_stop.reconciled": "SUSPENDED",
        }.get(event.event_type)
        if status:
            trust = "SUSPENDED" if event.event_type.startswith("emergency_stop.") else payload.get("trust_state", "LAB")
            connection.execute(
                """UPDATE supervisor_state SET instance_id = ?, status = ?, trust_state = ?,
                   last_heartbeat_at = ?, last_event_id = ? WHERE singleton = 1""",
                (payload.get("instance_id"), status, trust, event.recorded_at, event.event_id),
            )


def reset(connection: sqlite3.Connection) -> None:
    for table in PROJECTION_TABLES:
        connection.execute(f"DELETE FROM {table}")
    connection.execute(
        """UPDATE supervisor_state SET instance_id = NULL, status = 'STOPPED',
           trust_state = 'LAB', last_heartbeat_at = NULL, last_event_id = NULL
           WHERE singleton = 1"""
    )
    connection.execute(
        """UPDATE system_state SET start_active = 0, start_event_id = NULL,
           start_received_at = NULL WHERE singleton = 1"""
    )
