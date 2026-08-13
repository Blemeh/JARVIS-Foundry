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
    "systems",
    "system_actions",
    "vault_files",
    "vault_sync_state",
    "ingestion_cursors",
    "research_queue",
    "worker_manifests",
    "consolidation_ops",
    "cost_measurements",
    "briefing_outbox",
    "voice_interactions",
    "control_nonces",
    "improvement_records",
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
        # DIR-2026-08-12 item 2: a COMPLETED run never regresses to
        # RUNNING. FAILED rows MAY be re-started — the dispatcher's
        # provider-fallback chain records attempt failures
        # (provider_unavailable:<provider>) before re-starting under the
        # next provider; the run row legitimately walks
        # RUNNING -> FAILED -> RUNNING -> COMPLETED within one dispatch.
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
                updated_at=excluded.updated_at
            WHERE agent_runs.status IN ('RUNNING', 'FAILED')""",
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
        # DIR-2026-08-12 item 2: terminal states are MONOTONIC — a run
        # already marked FAILED (e.g. reaped) never becomes COMPLETED, no
        # matter when a late completion event lands.
        connection.execute(
            """UPDATE agent_runs SET status = 'COMPLETED', finished_at = ?,
               result_json = ?, cost_json = ?, failure_class = NULL,
               last_event_id = ?, updated_at = ? WHERE run_id = ? AND status != 'FAILED'""",
            (
                event.recorded_at, canonical_json(payload.get("result", {})),
                canonical_json(payload.get("cost", {})), event.event_id, event.recorded_at,
                payload["run_id"],
            ),
        )
    elif event.event_type == "agent_run.failed":
        # DIR-2026-08-12 item 2: a COMPLETED run never regresses to FAILED
        # via a late failure event (e.g. a reaper event that lands after
        # the completion event in the stream).
        connection.execute(
            """UPDATE agent_runs SET status = 'FAILED', finished_at = ?, failure_class = ?,
               last_event_id = ?, updated_at = ? WHERE run_id = ? AND status != 'COMPLETED'""",
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
        from .knowledge import contradicts, normalize_source_run_id

        claim_id = payload["claim_id"]
        run_id = payload.get("source", {}).get("run_id")
        if run_id:
            # V4-S1 hygiene: the malformed doubled prefix
            # ("handoff:handoff:...") is normalized at the PROJECTION
            # boundary — derived state is clean, history untouched,
            # rebuilds reproduce the same rows.
            run_id = normalize_source_run_id(run_id)
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
                run_id,
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
        # V4-S5 round 2 (deleg_71efdc9e HIGH): a duplicate records
        # duplicate_of and CREATES NOTHING selectable — the row lands
        # REJECTED (historical, visible, never selected). Before the
        # fix, duplicates inserted as NEW and _top_scorable selected
        # them as fresh work (duplicate missions with identical
        # objectives were created by repeated polls).
        duplicate_of = payload.get("duplicate_of")
        status = "REJECTED" if duplicate_of else "NEW"
        evidence = payload.get("evidence", [])
        if duplicate_of:
            evidence = [*evidence, {"duplicate_of": duplicate_of}]
        connection.execute(
            """INSERT INTO candidates(
                candidate_id, mission_id, source, objective,
                objective_canonical, evidence_json, status, duplicate_of,
                created_from, last_event_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (payload["candidate_id"], payload["mission_id"],
             payload["source"], payload["objective"],
             payload.get("objective_canonical", canonical_json(payload["objective"])),
             canonical_json(evidence),
             status,
             duplicate_of, payload.get("created_from"),
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
    elif event.event_type == "discovery.candidate_superseded":
        # V4-S1 additive correction: a candidate whose premise was
        # re-measured false is superseded — never deleted, never
        # selected (selection scans NEW/SCORED only), history intact.
        connection.execute(
            "UPDATE candidates SET status = 'SUPERSEDED', superseded_by = ?, "
            "evidence_json = ?, last_event_id = ?, updated_at = ? "
            "WHERE candidate_id = ?",
            (payload.get("superseded_by"),
             canonical_json([{"reason": payload.get("reason", "")}]),
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
        # V4-S5 round 2 (deleg_71efdc9e LOW): a poll for an unknown
        # monitor must fail the append atomically — no silent 0-row
        # UPDATE (the ledger would record a poll that never happened).
        known = connection.execute(
            "SELECT 1 FROM monitors WHERE monitor_id = ?",
            (payload["monitor_id"],)).fetchone()
        if known is None:
            raise ValueError(
                f"discovery.monitor_polled references unknown monitor "
                f"{payload['monitor_id']}")
        connection.execute(
            "UPDATE monitors SET last_poll_at = ?, last_event_id = ?, "
            "updated_at = ? WHERE monitor_id = ?",
            (event.recorded_at, event.event_id, event.recorded_at,
             payload["monitor_id"]),
        )
    elif event.event_type == "system.registered":
        # V5-S1: the durable system catalog (Multi-System Intelligence).
        connection.execute(
            """INSERT INTO systems(
                system_id, kind, name, capabilities_json, probe_ref,
                readiness, last_probe_at, last_event_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'NOT_TESTED', NULL, ?, ?)""",
            (payload["system_id"], payload["kind"], payload["name"],
             canonical_json(payload.get("capabilities", {})),
             payload.get("probe_ref"), event.event_id, event.recorded_at),
        )
    elif event.event_type == "system.probe_recorded":
        # readiness is honest: any FAILED probe -> UNREADY; all-READY ->
        # READY; otherwise the previous state stands (NOT_TESTED). The
        # probed capabilities are merged into the catalog row.
        known = connection.execute(
            "SELECT capabilities_json FROM systems WHERE system_id = ?",
            (payload["system_id"],)).fetchone()
        if known is None:
            raise ValueError(
                f"system.probe_recorded references unknown system "
                f"{payload['system_id']}")
        results = payload.get("results", {})
        if "FAILED" in results.values():
            readiness = "UNREADY"
        elif results and all(v == "READY" for v in results.values()):
            readiness = "READY"
        else:
            readiness = None
        capabilities = json.loads(known[0] or "{}")
        capabilities.update(results)
        if readiness:
            connection.execute(
                "UPDATE systems SET readiness = ?, capabilities_json = ?, "
                "last_probe_at = ?, last_event_id = ?, updated_at = ? "
                "WHERE system_id = ?",
                (readiness, canonical_json(capabilities), event.recorded_at,
                 event.event_id, event.recorded_at, payload["system_id"]))
        else:
            connection.execute(
                "UPDATE systems SET capabilities_json = ?, last_probe_at = ?, "
                "last_event_id = ?, updated_at = ? WHERE system_id = ?",
                (canonical_json(capabilities), event.recorded_at,
                 event.event_id, event.recorded_at, payload["system_id"]))
    elif event.event_type == "system.action_started":
        # V5 exit evidence 2: the journal row is UNIQUE on
        # (system_id, target_ref, payload_hash) — a retried delivery of
        # the same effect dedupes at the projection level too.
        connection.execute(
            """INSERT OR IGNORE INTO system_actions(
                action_key, system_id, target_ref, payload_hash, kind,
                status, last_event_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'STARTED', ?, ?)""",
            (payload["action_key"], payload["system_id"],
             payload["target_ref"], payload["payload_hash"],
             payload.get("kind", ""), event.event_id, event.recorded_at),
        )
    elif event.event_type == "system.action_completed":
        # V5-S6 monotonicity: a terminal journal state never regresses —
        # a late completed event cannot un-fail a failed effect (the
        # event is append-only history; the derived state is guarded).
        connection.execute(
            "UPDATE system_actions SET status = 'COMPLETED', "
            "last_event_id = ?, updated_at = ? WHERE action_key = ? "
            "AND status = 'STARTED'",
            (event.event_id, event.recorded_at, payload["action_key"]))
    elif event.event_type == "system.action_failed":
        connection.execute(
            "UPDATE system_actions SET status = 'FAILED', "
            "last_event_id = ?, updated_at = ? WHERE action_key = ? "
            "AND status = 'STARTED'",
            (event.event_id, event.recorded_at, payload["action_key"]))
    elif event.event_type == "effect.frozen":
        # explicit freeze request: only a STARTED effect freezes
        connection.execute(
            "UPDATE system_actions SET status = 'FROZEN', "
            "last_event_id = ?, updated_at = ? WHERE action_key = ? "
            "AND status = 'STARTED'",
            (event.event_id, event.recorded_at, payload["action_key"]))
    elif event.event_type == "effect.reconcile_mismatch":
        # provider disagreement is stronger than a request: a terminal
        # completion/failure claim that the provider contradicts moves
        # to FROZEN for review (never back to STARTED — monotonic).
        connection.execute(
            "UPDATE system_actions SET status = 'FROZEN', "
            "last_event_id = ?, updated_at = ? WHERE action_key = ? "
            "AND status IN ('COMPLETED', 'FAILED')",
            (event.event_id, event.recorded_at, payload["action_key"]))
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

    elif event.event_type.startswith("consolidation."):
        # one upsert handler for the whole op lifecycle — replaying the
        # events in order reproduces every transition deterministically
        connection.execute(
            """INSERT INTO consolidation_ops(
                op_id, note_path, kind, status, before_hash, after_hash,
                write_id, verified_by, evidence_json,
                last_event_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(op_id) DO UPDATE SET
                status = excluded.status,
                after_hash = excluded.after_hash,
                write_id = excluded.write_id,
                verified_by = excluded.verified_by,
                evidence_json = excluded.evidence_json,
                last_event_id = excluded.last_event_id,
                updated_at = excluded.updated_at""",
            (payload["op_id"], payload["note_path"], payload["kind"],
             payload["status"], payload["before_hash"],
             payload.get("after_hash"), payload.get("write_id"),
             payload.get("verified_by"),
            canonical_json(payload.get("evidence", {})),
            event.event_id, event.recorded_at),
        )

    elif event.event_type == "resource.usage_recorded":
        # Cost/resource data is truthful by construction: absent provider
        # measurements are persisted as UNKNOWN/NOT_AVAILABLE, never zero.
        status = payload.get("status", "UNKNOWN")
        if status not in {"MEASURED", "UNKNOWN", "NOT_AVAILABLE", "NOT_TESTED"}:
            raise ValueError(f"invalid cost measurement status: {status}")
        connection.execute(
            """INSERT INTO cost_measurements(
                measurement_id, mission_id, run_id, provider, model, status,
                input_tokens, output_tokens, total_tokens, estimated_cost_usd,
                actual_cost_usd, duration_ms, resource_json, failure_class,
                evidence_json, recorded_at, last_event_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(measurement_id) DO UPDATE SET
                status=excluded.status, input_tokens=excluded.input_tokens,
                output_tokens=excluded.output_tokens, total_tokens=excluded.total_tokens,
                estimated_cost_usd=excluded.estimated_cost_usd,
                actual_cost_usd=excluded.actual_cost_usd, duration_ms=excluded.duration_ms,
                resource_json=excluded.resource_json, failure_class=excluded.failure_class,
                evidence_json=excluded.evidence_json, last_event_id=excluded.last_event_id,
                updated_at=excluded.updated_at""",
            (payload["measurement_id"], payload.get("mission_id"), payload.get("run_id"),
             payload.get("provider"), payload.get("model"), status,
             payload.get("input_tokens"), payload.get("output_tokens"), payload.get("total_tokens"),
             payload.get("estimated_cost_usd"), payload.get("actual_cost_usd"),
             payload.get("duration_ms"), canonical_json(payload.get("resource", {})),
             payload.get("failure_class"), canonical_json(payload.get("evidence", {})),
             payload.get("recorded_at", event.recorded_at), event.event_id, event.recorded_at),
        )

    elif event.event_type == "briefing.planned":
        connection.execute(
            """INSERT INTO briefing_outbox(
                briefing_id, briefing_key, kind, period_key, status,
                payload_json, evidence_json, attempt_count, idempotency_key,
                last_event_id, updated_at
            ) VALUES (?, ?, ?, ?, 'PLANNED', ?, ?, 0, ?, ?, ?)
            ON CONFLICT(briefing_key) DO NOTHING""",
            (payload["briefing_id"], payload["briefing_key"], payload["kind"],
             payload["period_key"], canonical_json(payload.get("briefing", {})),
             canonical_json(payload.get("evidence", {})), payload["idempotency_key"],
             event.event_id, event.recorded_at),
        )
    elif event.event_type == "briefing.claimed":
        connection.execute(
            """UPDATE briefing_outbox SET status = 'CLAIMED',
                attempt_count = attempt_count + 1, last_event_id = ?, updated_at = ?
                WHERE briefing_id = ? AND status IN ('PLANNED', 'FAILED')""",
            (event.event_id, event.recorded_at, payload["briefing_id"]),
        )
    elif event.event_type == "briefing.delivered":
        connection.execute(
            """UPDATE briefing_outbox SET status = 'DELIVERED', delivered_at = ?,
                last_event_id = ?, updated_at = ? WHERE briefing_id = ?
                AND status IN ('CLAIMED', 'PLANNED')""",
            (event.recorded_at, event.event_id, event.recorded_at, payload["briefing_id"]),
        )
    elif event.event_type == "briefing.failed":
        connection.execute(
            """UPDATE briefing_outbox SET status = ?, failure_reason = ?,
                last_event_id = ?, updated_at = ? WHERE briefing_id = ?
                AND status != 'DELIVERED'""",
            (payload.get("status", "FAILED"), payload.get("reason"),
             event.event_id, event.recorded_at, payload["briefing_id"]),
        )

    elif event.event_type == "voice.interaction_recorded":
        connection.execute(
            """INSERT INTO voice_interactions(
                interaction_id, auth_status, language, transcript, response_text,
                stt_provider, tts_provider, command_type, directive_id, status,
                audio_metadata_json, evidence_json, last_event_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(interaction_id) DO UPDATE SET
                auth_status=excluded.auth_status, language=excluded.language,
                transcript=excluded.transcript, response_text=excluded.response_text,
                stt_provider=excluded.stt_provider, tts_provider=excluded.tts_provider,
                command_type=excluded.command_type, directive_id=excluded.directive_id,
                status=excluded.status, audio_metadata_json=excluded.audio_metadata_json,
                evidence_json=excluded.evidence_json, last_event_id=excluded.last_event_id,
                updated_at=excluded.updated_at""",
            (payload["interaction_id"], payload["auth_status"], payload.get("language"),
             payload.get("transcript"), payload.get("response_text"), payload.get("stt_provider"),
             payload.get("tts_provider"), payload.get("command_type"), payload.get("directive_id"),
             payload["status"], canonical_json(payload.get("audio_metadata", {})),
             canonical_json(payload.get("evidence", {})), event.event_id, event.recorded_at),
        )
    elif event.event_type == "owner_control.nonce_used":
        connection.execute(
            """INSERT OR IGNORE INTO control_nonces(
                nonce, command_hash, issued_at, used_at, last_event_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)""",
            (payload["nonce"], payload["command_hash"], payload["issued_at"],
             payload.get("used_at", event.recorded_at), event.event_id, event.recorded_at),
        )
    elif event.event_type == "improvement.recorded":
        for key in ("improvement_id", "problem", "evidence_refs",
                    "provenance", "hypothesis", "expected_mechanism",
                    "baseline_metric", "mmi", "guardrails", "workload",
                    "observation", "risk_class", "risk_class_declared",
                    "surfaces", "budget", "reversibility"):
            if key not in payload:
                raise ValueError(
                    f"improvement.recorded requires {key} "
                    "(mandatory payload key) — rolled back")
        if payload.get("supersedes") is not None:
            exists = connection.execute(
                "SELECT 1 FROM improvement_records WHERE improvement_id = ?",
                (payload["supersedes"],)).fetchone()
            if exists is None:
                raise ValueError(
                    f"improvement.recorded supersedes target "
                    f"{payload['supersedes']} does not exist — rolled back")
        connection.execute(
            """INSERT OR IGNORE INTO improvement_records(
                improvement_id, state, problem, evidence_refs_json,
                provenance_json, hypothesis, expected_mechanism,
                baseline_metric_json, mmi_json, guardrails_json,
                workload_json, observation_json, risk_class,
                risk_class_declared, surfaces_json, budget_json,
                reversibility_json, supersedes, created_at, last_event_id,
                updated_at
            ) VALUES (?, 'FROZEN', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?)""",
            (payload["improvement_id"], payload["problem"],
             canonical_json(payload["evidence_refs"]),
             canonical_json(payload["provenance"]), payload["hypothesis"],
             payload["expected_mechanism"],
             canonical_json(payload["baseline_metric"]),
             canonical_json(payload["mmi"]),
             canonical_json(payload["guardrails"]),
             canonical_json(payload["workload"]),
             canonical_json(payload["observation"]),
             payload["risk_class"], payload["risk_class_declared"],
             canonical_json(payload["surfaces"]),
             canonical_json(payload["budget"]),
             canonical_json(payload["reversibility"]),
             payload.get("supersedes"), event.recorded_at,
             event.event_id, event.recorded_at),
        )
    elif event.event_type == "improvement.risk_raised":
        for key in ("improvement_id", "risk_class", "risk_class_previous",
                    "reason"):
            if key not in payload:
                raise ValueError(
                    f"improvement.risk_raised requires {key} "
                    "(mandatory payload key) — rolled back")
        if not payload["reason"]:
            raise ValueError(
                "improvement.risk_raised requires a non-empty reason — "
                "rolled back")
        current = connection.execute(
            "SELECT risk_class FROM improvement_records "
            "WHERE improvement_id = ?",
            (payload["improvement_id"],)).fetchone()
        if current is None:
            raise ValueError(
                f"improvement.risk_raised target "
                f"{payload['improvement_id']} does not exist — rolled back")
        order = ("R0", "R1", "R2", "R3")
        if order.index(payload["risk_class"]) <= order.index(current[0]):
            raise ValueError(
                "improvement.risk_raised is not strictly ascending — "
                "rolled back")
        connection.execute(
            """UPDATE improvement_records SET risk_class = ?,
               last_event_id = ?, updated_at = ?
               WHERE improvement_id = ?""",
            (payload["risk_class"], event.event_id, event.recorded_at,
             payload["improvement_id"]),
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
