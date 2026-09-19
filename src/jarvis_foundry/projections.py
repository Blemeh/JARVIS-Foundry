from __future__ import annotations

import json
import sqlite3

from .domain import ARTIFACT_NAME_PATTERN, require_transition
from .events import Event, canonical_json
from .trust_states import ALLOWED_TRUST_STATES, TRUST_STATES


_V3_COLUMN_PROBE_TABLES = frozenset({
    "candidates", "candidate_outcomes", "scoring_policy_registry",
    "discovery_portfolio", "monitors",
})


def _table_columns(connection: sqlite3.Connection, table: str) -> set[str]:
    # Allowlist before interpolation (same discipline as database.row()).
    if table not in _V3_COLUMN_PROBE_TABLES:
        raise ValueError(f"unsupported projection column probe: {table}")
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def _has_table(connection: sqlite3.Connection, table: str) -> bool:
    return bool(connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,)).fetchone())

PROJECTION_TABLES = (
    "missions",
    "mission_step_contracts",
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
    "knowledge_claim_evidence",
    "knowledge_manifests",
    "knowledge_entities",
    "knowledge_claim_entities",
    "knowledge_ingestions",
    "knowledge_snapshots",
    "candidate_sources",
    "candidate_judgments",
    "candidates",
    "candidate_scores",
    "candidate_outcomes",
    "scoring_policy_registry",
    "discovery_portfolio",
    "monitors",
    "systems",
    "system_actions",
    "vault_files",
    "vault_sync_state",
    "ingestion_cursors",
    "research_queue",
    "initiative_source_runs",
    "initiative_cycles",
    "worker_manifests",
    "consolidation_ops",
    "cost_measurements",
    "briefing_outbox",
    "voice_interactions",
    "control_nonces",
    "improvement_records",
    "release_manifests",
    "release_slots",
    "candidate_builds",
    "evaluation_runs",
    "verifications",
    "shadow_runs",
    "canaries",
    "promotions",
    "release_control",
    "restore_points",
    "discovery_runs",
    "cycles",
    "acceptance_runs",
    # V8 hybrid memory: Tier C is event-derived like every other projection.
    # reset() deletes children BEFORE parents (revision rows carry NO ACTION
    # FKs so history can never be cascade-destroyed silently).
    "tier_c_memory_revisions",
    "tier_c_validations",
    "tier_c_memories",
    "tier_c_block_revisions",
    "tier_c_blocks",
    "tier_c_procedures",
    # Software-intelligence (Lane 4): derived alias projection. Deleted with
    # the other Tier-C-derived tables; rebuilt from memory.alias.bound events.
    "tier_c_project_aliases",
    "world_entities",
    "world_relationships",
    "world_observations",
    "world_signals",
    "world_predictions",
    "world_knowledge_gaps",
    "world_patterns",
    "world_calibration",
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
    elif event.event_type == "mission.step_contract_recorded":
        # Executable mission authority is immutable and event-derived.  The
        # payload is validated before any row is written; duplicate semantic
        # delivery is idempotent, while a conflicting hash is rejected.
        from .mission.contracts import MissionStepContract, MissionStepContractError

        if event.source_type != "supervisor" or event.source_id != "supervisor":
            raise ValueError("mission step contracts are Supervisor-owned")
        try:
            contract = MissionStepContract.from_payload(payload)
        except (MissionStepContractError, TypeError, ValueError) as error:
            raise ValueError(f"invalid mission step contract: {error}") from error
        mission = connection.execute(
            "SELECT resource_id FROM missions WHERE mission_id = ?",
            (contract.mission_id,),
        ).fetchone()
        if mission is None:
            raise ValueError(f"unknown mission: {contract.mission_id}")
        if str(mission[0] or "") != contract.resource_id:
            raise ValueError("mission step contract resource mismatch")
        resource = connection.execute(
            "SELECT resource_id FROM resources WHERE resource_id = ?",
            (contract.resource_id,),
        ).fetchone()
        if resource is None:
            raise ValueError(f"unknown resource: {contract.resource_id}")
        existing = connection.execute(
            "SELECT contract_hash FROM mission_step_contracts "
            "WHERE contract_id = ? OR (mission_id = ? AND step_id = ? AND version = ?)",
            (contract.contract_id, contract.mission_id, contract.step_id, contract.version),
        ).fetchone()
        if existing is not None:
            if str(existing[0]) == contract.content_hash():
                return
            raise ValueError("conflicting mission step contract for immutable version")
        if contract.version > 1:
            previous = connection.execute(
                "SELECT contract_hash FROM mission_step_contracts "
                "WHERE mission_id = ? AND step_id = ? ORDER BY version DESC LIMIT 1",
                (contract.mission_id, contract.step_id),
            ).fetchone()
            if previous is None or contract.supersedes_hash != str(previous[0]):
                raise ValueError("contract version must explicitly supersede the latest hash")
        connection.execute(
            """INSERT INTO mission_step_contracts(
                contract_id, mission_id, step_id, version, resource_id, capability,
                target_relative_path, instructions, expected_effects_json,
                rollback_json, risk_class, verification_criteria_json,
                expected_artifacts_json, forbidden_paths_json,
                required_capabilities_json, contract_hash,
                recorded_by, source, provenance_json, supersedes_hash,
                last_event_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                contract.contract_id, contract.mission_id, contract.step_id,
                contract.version, contract.resource_id, contract.capability,
                contract.target_relative_path, contract.instructions,
                canonical_json(contract.expected_effects),
                canonical_json(contract.rollback), contract.risk_class,
                canonical_json(contract.verification_criteria),
                canonical_json(contract.expected_artifacts),
                canonical_json(contract.forbidden_paths),
                canonical_json(contract.required_capabilities),
                contract.content_hash(),
                contract.recorded_by, contract.source, canonical_json(contract.provenance),
                contract.supersedes_hash, event.event_id, event.recorded_at,
            ),
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
        # Optional target fields are untrusted owner-channel input.  Preserve
        # the original event for the event-derived UNKNOWN projection, while
        # keeping the legacy enforcement row relationally safe.
        target = payload.get("target") if isinstance(payload.get("target"), str) else None
        connection.execute(
            """INSERT INTO directives(
                directive_id, command_type, target, exact_text, active, last_event_id, updated_at
            ) VALUES (?, ?, ?, ?, 1, ?, ?)""",
            (
                payload["directive_id"], payload["command_type"], target,
                payload["exact_text"], event.event_id, event.recorded_at,
            ),
        )
    elif event.event_type == "owner_directive.revoked":
        connection.execute(
            "UPDATE directives SET active = 0, last_event_id = ?, updated_at = ? WHERE directive_id = ?",
            (event.event_id, event.recorded_at, payload["directive_id"]),
        )
    elif event.event_type == "owner_directive.superseded":
        # Supersession is an additive lifecycle event.  The richer
        # owner_directives projection reconstructs provenance/history; this
        # legacy enforcement table only needs the prior directive disabled so
        # broker STOP/veto checks cannot keep applying superseded intent.
        connection.execute(
            "UPDATE directives SET active = 0, last_event_id = ?, updated_at = ? WHERE directive_id = ?",
            (event.event_id, event.recorded_at, payload["directive_id"]),
        )
    elif event.event_type == "mission.directive_interpreted":
        # R10 live consumer: interpretation is an event-derived projection,
        # never a second mission state store.  Reprioritization intentionally
        # leaves next_action unchanged; its durable priority is in the event.
        if payload.get("command_type") == "REDIRECT":
            connection.execute(
                "UPDATE missions SET next_action = ?, last_event_id = ?, "
                "updated_at = ? WHERE mission_id = ?",
                (payload.get("next_action"), event.event_id,
                 event.recorded_at, payload["mission_id"]),
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
    elif event.event_type == "verification.not_tested":
        connection.execute(
            """UPDATE verification_verdicts SET status = 'NOT_TESTED', verdict = 'NOT_TESTED',
               blocking_dependency = ?, reason = ?, findings_json = ?,
               issued_at = ?, last_event_id = ?, updated_at = ? WHERE verdict_id = ?""",
            (
                payload.get("blocking_dependency"), payload.get("reason"),
                canonical_json(payload.get("findings", [])),
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
        from .knowledge_records import (
            RECORD_KIND_DEFAULT,
            content_hash as record_content_hash,
        )

        claim_id = payload["claim_id"]
        run_id = payload.get("source", {}).get("run_id")
        if run_id:
            # V4-S1 hygiene: the malformed doubled prefix
            # ("handoff:handoff:...") is normalized at the PROJECTION
            # boundary — derived state is clean, history untouched,
            # rebuilds reproduce the same rows.
            run_id = normalize_source_run_id(run_id)
        record_kind = payload.get("record_kind", RECORD_KIND_DEFAULT)
        content_sha = record_content_hash(
            statement=payload["statement"],
            claim_type=payload["claim_type"],
            mission_id=payload["mission_id"], claim_id=claim_id)
        connection.execute(
            """INSERT INTO knowledge_claims(
                claim_id, mission_id, statement, claim_type, record_kind,
                confidence, fresh_until, source_run_id, source_event_id,
                status, superseded_by, evidence_status, unsupported_reason,
                content_sha256, last_event_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'ACCEPTED', NULL,
                      'UNSUPPORTED', NULL, ?, ?, ?)""",
            (
                claim_id, payload["mission_id"], payload["statement"],
                payload["claim_type"], record_kind,
                payload.get("confidence", 0.5),
                payload.get("fresh_until"),
                run_id,
                payload.get("source", {}).get("event_id"),
                content_sha,
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
        from .knowledge_records import (
            ENTITY_TYPE_DEFAULT,
            validate_entity_type,
        )

        # clean validation error BEFORE database contact (fail closed,
        # deterministic refusal — never a deep IntegrityError)
        validate_entity_type(
            payload.get("entity_type", ENTITY_TYPE_DEFAULT))
        connection.execute(
            """INSERT INTO knowledge_entities(
                entity_id, mission_id, canonical_name, aliases_json,
                entity_type, last_event_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (payload["entity_id"], payload["mission_id"],
             payload["canonical_name"],
             canonical_json(payload.get("aliases", [])),
             payload.get("entity_type", ENTITY_TYPE_DEFAULT),
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

    elif event.event_type == "knowledge.evidence_linked":
        # WP-24H-V2KB: immutable append-only provenance link. Requires the
        # claim to exist; duplicate identical delivery dedupes via the
        # idempotency key BEFORE reaching this branch. There is
        # deliberately NO unlink/rewrite event type.
        claim = connection.execute(
            "SELECT claim_id FROM knowledge_claims WHERE claim_id = ?",
            (payload["claim_id"],)).fetchone()
        if claim is None:
            raise ValueError(
                "knowledge.evidence_linked requires an existing claim "
                f"({payload.get('claim_id')}) — rolled back")
        connection.execute(
            """INSERT INTO knowledge_claim_evidence(
                claim_id, evidence_id, payload_hash, linked_at,
                last_event_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(claim_id, evidence_id) DO NOTHING""",
            (payload["claim_id"], payload["evidence_id"],
             payload.get("payload_hash"), event.recorded_at,
             event.event_id, event.recorded_at),
        )
        connection.execute(
            """UPDATE knowledge_claims
               SET evidence_status = 'SUPPORTED', unsupported_reason = NULL,
                   last_event_id = ?, updated_at = ?
               WHERE claim_id = ?""",
            (event.event_id, event.recorded_at, payload["claim_id"]),
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
        # V3 columns exist only from schema v35; the handler adapts so
        # ledger replays against ANY schema version work (mid-migration
        # appends included) — same contract as mission.created.
        v3_columns = _table_columns(connection, "candidates")
        if "fingerprint" in v3_columns:
            connection.execute(
                """INSERT INTO candidates(
                    candidate_id, mission_id, source, objective,
                    objective_canonical, evidence_json, status, duplicate_of,
                    created_from, fingerprint, candidate_type, expected_outcome,
                    provenance_hash, expires_at, last_event_id, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (payload["candidate_id"], payload["mission_id"],
                 payload["source"], payload["objective"],
                 payload.get("objective_canonical", canonical_json(payload["objective"])),
                 canonical_json(evidence),
                 status,
                 duplicate_of, payload.get("created_from"),
                 payload.get("fingerprint"), payload.get("candidate_type"),
                 payload.get("expected_outcome"), payload.get("provenance_hash"),
                 payload.get("expires_at"),
                 event.event_id, event.recorded_at),
            )
        else:
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
    elif event.event_type == "discovery.candidate_source_linked":
        if not _has_table(connection, "candidate_sources"):
            return
        candidate_id = str(payload.get("candidate_id") or "")
        source_kind = str(payload.get("source_kind") or "")
        source_identity = str(payload.get("source_identity") or "")
        if not candidate_id or not source_kind or not source_identity:
            raise ValueError(
                "discovery.candidate_source_linked requires candidate_id, "
                "source_kind, and source_identity")
        if connection.execute(
                "SELECT 1 FROM candidates WHERE candidate_id = ?",
                (candidate_id,)).fetchone() is None:
            raise ValueError(
                "discovery.candidate_source_linked references unknown "
                f"candidate {candidate_id}")
        link_id = str(payload.get("link_id") or payload.get("source_link_id")
                      or event.idempotency_key)
        connection.execute(
            """INSERT OR IGNORE INTO candidate_sources(
                link_id, candidate_id, observation_id, source_kind,
                source_identity, source_ref, provenance_json, content_hash,
                observed_at, fresh_until, authority, last_event_id,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (link_id, candidate_id, payload.get("observation_id"),
             source_kind, source_identity, payload.get("source_ref"),
             canonical_json(payload.get("provenance") or {}),
             payload.get("content_hash"),
             payload.get("observed_at", event.recorded_at),
             payload.get("fresh_until"),
             str(payload.get("authority") or "DATA_ONLY"),
              event.event_id, event.recorded_at, event.recorded_at),
        )
    elif event.event_type == "discovery.candidate_refreshed":
        candidate_id = str(payload.get("candidate_id") or "")
        if not candidate_id or connection.execute(
                "SELECT 1 FROM candidates WHERE candidate_id = ?",
                (candidate_id,)).fetchone() is None:
            raise ValueError(
                "discovery.candidate_refreshed references unknown candidate "
                f"{candidate_id}")
        # New source evidence reopens only pre-admission truth.  Executed,
        # approved, and superseded work remain monotonic while prior rejection
        # decisions stay available in candidate_judgments.
        connection.execute(
            """UPDATE candidates SET
               objective = ?, objective_canonical = ?,
               expected_outcome = COALESCE(?, expected_outcome),
               provenance_hash = COALESCE(?, provenance_hash),
               expires_at = COALESCE(?, expires_at),
               status = CASE
                   WHEN status IN ('NEW', 'SCORED', 'ELIGIBLE',
                                   'REJECTED', 'DEFERRED') THEN 'NEW'
                   ELSE status
               END,
               last_event_id = ?, updated_at = ?
               WHERE candidate_id = ?""",
            (payload["objective"], payload["objective_canonical"],
             payload.get("expected_outcome"), payload.get("provenance_hash"),
             payload.get("expires_at"), event.event_id, event.recorded_at,
             candidate_id),
        )
    elif event.event_type == "discovery.candidate_judged":
        if not _has_table(connection, "candidate_judgments"):
            return
        candidate_id = str(payload.get("candidate_id") or "")
        judgment_id = str(payload.get("judgment_id") or "")
        outcome = str(payload.get("outcome") or "")
        reason_code = str(payload.get("reason_code") or "")
        allowed_outcomes = {
            "REJECTED", "DEFERRED", "ELIGIBLE", "WAIT", "SUPERSEDED",
        }
        if (not candidate_id or not judgment_id or not reason_code
                or outcome not in allowed_outcomes):
            raise ValueError(
                "discovery.candidate_judged requires judgment_id, "
                "candidate_id, a supported outcome, and reason_code")
        if connection.execute(
                "SELECT 1 FROM candidates WHERE candidate_id = ?",
                (candidate_id,)).fetchone() is None:
            raise ValueError(
                f"discovery.candidate_judged references unknown candidate {candidate_id}")
        score = payload.get("score")
        connection.execute(
            """INSERT OR IGNORE INTO candidate_judgments(
                judgment_id, candidate_id, fingerprint, decision_id, cycle_id,
                stage, outcome, reason_code, reason_detail, score,
                factors_json, evidence_json, rank, do_nothing_won,
                policy_version, evidence_hash, decided_at, last_event_id,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (judgment_id, candidate_id, payload.get("fingerprint"),
             payload.get("decision_id"), payload.get("cycle_id"),
             str(payload.get("stage") or "FINAL"), outcome, reason_code,
             str(payload.get("reason_detail") or ""),
             float(score) if score is not None else None,
             canonical_json(payload.get("factors") or {}),
             canonical_json(payload.get("evidence") or []),
             payload.get("rank"), int(bool(payload.get("do_nothing_won"))),
             payload.get("policy_version"), payload.get("evidence_hash"),
             payload.get("decided_at", event.recorded_at), event.event_id,
             event.recorded_at),
        )
        projected_status = {
            "REJECTED": "REJECTED",
            "DEFERRED": "DEFERRED",
            "WAIT": "DEFERRED",
            "ELIGIBLE": "ELIGIBLE",
            "SUPERSEDED": "SUPERSEDED",
        }[outcome]
        connection.execute(
            "UPDATE candidates SET status = ?, last_event_id = ?, "
            "updated_at = ? WHERE candidate_id = ?",
            (projected_status, event.event_id, event.recorded_at, candidate_id),
        )
    elif event.event_type == "initiative.candidate_admitted":
        candidate_id = str(payload.get("candidate_id") or "")
        objective_id = str(payload.get("objective_id") or "")
        if not candidate_id or not objective_id:
            raise ValueError(
                "initiative.candidate_admitted requires candidate_id and objective_id")
        row = connection.execute(
            "SELECT status FROM candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        if row is None:
            raise ValueError(
                f"initiative.candidate_admitted references unknown candidate {candidate_id}")
        if str(row[0] or "") not in {"ELIGIBLE", "APPROVED"}:
            raise ValueError(
                f"initiative.candidate_admitted requires ELIGIBLE candidate {candidate_id}")
        connection.execute(
            "UPDATE candidates SET status = 'APPROVED', last_event_id = ?, "
            "updated_at = ? WHERE candidate_id = ?",
            (event.event_id, event.recorded_at, candidate_id),
        )
    elif event.event_type == "initiative.candidate_admission_blocked":
        candidate_id = str(payload.get("candidate_id") or "")
        if not candidate_id:
            raise ValueError(
                "initiative.candidate_admission_blocked requires candidate_id")
        row = connection.execute(
            "SELECT status FROM candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        if row is None:
            raise ValueError(
                "initiative.candidate_admission_blocked references unknown "
                f"candidate {candidate_id}")
        connection.execute(
            "UPDATE candidates SET status = 'DEFERRED', last_event_id = ?, "
            "updated_at = ? WHERE candidate_id = ?",
            (event.event_id, event.recorded_at, candidate_id),
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
             event.event_id, event.recorded_at))
        policy_version = payload.get("policy_version")
        if policy_version and "fingerprint" in _table_columns(
                connection, "candidates"):
            # V3 WP2: a registry-scored candidate carries the trace and
            # becomes ELIGIBLE (portfolio state machine: SCORED pass
            # filters -> ELIGIBLE). The legacy loop path (no
            # policy_version) keeps the historical SCORED semantics.
            connection.execute(
                "UPDATE candidates SET status = 'ELIGIBLE', "
                "scoring_policy_version = ?, fingerprint = COALESCE(fingerprint, ?), "
                "candidate_type = COALESCE(candidate_type, ?), "
                "expected_outcome = COALESCE(expected_outcome, ?), "
                "provenance_hash = COALESCE(provenance_hash, ?), "
                "expires_at = COALESCE(expires_at, ?), "
                "last_event_id = ?, updated_at = ? WHERE candidate_id = ?",
                (policy_version,
                 payload.get("fingerprint"),
                 payload.get("candidate_type"),
                 payload.get("expected_outcome"),
                 payload.get("provenance_hash"),
                 payload.get("expires_at"),
                 event.event_id, event.recorded_at,
                 payload["candidate_id"]))
    elif event.event_type == "scoring.policy_registered":
        if not _has_table(connection, "scoring_policy_registry"):
            return
        connection.execute("UPDATE scoring_policy_registry SET active = 0")
        connection.execute(
            """INSERT OR REPLACE INTO scoring_policy_registry(
                policy_version, active, weights_json, thresholds_json,
                hard_filters_json, last_event_id, updated_at
            ) VALUES (?, 1, ?, ?, ?, ?, ?)""",
            (payload["policy_version"],
             canonical_json(payload.get("weights", {})),
             canonical_json(payload.get("thresholds", {})),
             canonical_json(payload.get("hard_filters", {})),
             event.event_id, event.recorded_at))
    elif event.event_type == "scoring.policy_superseded":
        if not _has_table(connection, "scoring_policy_registry"):
            return
        connection.execute(
            "UPDATE scoring_policy_registry SET active = 0 "
            "WHERE policy_version IN (?, ?)",
            (payload["old_version"], payload["new_version"]))
        connection.execute(
            "UPDATE scoring_policy_registry SET active = 1, last_event_id = ?, "
            "updated_at = ? WHERE policy_version = ? AND EXISTS("
            "SELECT 1 FROM scoring_policy_registry)",
            (event.event_id, event.recorded_at, payload["new_version"]))
    elif event.event_type == "portfolio.evaluated":
        if not _has_table(connection, "discovery_portfolio"):
            return
        connection.execute(
            """INSERT OR REPLACE INTO discovery_portfolio(
                loop_id, policy_version, eligible_count, deferred_count,
                rejected_count, capacity_snapshot_json, last_event_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (payload["loop_id"], payload.get("policy_version"),
             int(payload.get("eligible_count", 0)),
             int(payload.get("deferred_count", 0)),
             int(payload.get("rejected_count", 0)),
             canonical_json(payload.get("capacity_snapshot", {})),
             event.event_id, event.recorded_at))
    elif event.event_type == "portfolio.candidate_deferred":
        connection.execute(
            "UPDATE candidates SET status = 'DEFERRED', evidence_json = ?, "
            "last_event_id = ?, updated_at = ? WHERE candidate_id = ?",
            (canonical_json([{"defer_reason": payload.get("reason", ""),
                              "cooldown_until": payload.get("cooldown_until")}]),
             event.event_id, event.recorded_at, payload["candidate_id"]))
    elif event.event_type == "portfolio.candidate_requeued":
        connection.execute(
            "UPDATE candidates SET status = 'SCORED', last_event_id = ?, "
            "updated_at = ? WHERE candidate_id = ? AND status = 'DEFERRED'",
            (event.event_id, event.recorded_at, payload["candidate_id"]))
    elif event.event_type == "discovery.candidate_approved":
        connection.execute(
            "UPDATE candidates SET status = 'APPROVED', last_event_id = ?, "
            "updated_at = ? WHERE candidate_id = ?",
            (event.event_id, event.recorded_at, payload["candidate_id"]))
    elif event.event_type == "discovery.candidate_rejected":
        connection.execute(
            """UPDATE candidates SET status = 'REJECTED',
               evidence_json = json_insert(
                   CASE WHEN json_valid(evidence_json)
                             AND json_type(evidence_json) = 'array'
                        THEN evidence_json ELSE '[]' END,
                   '$[#]', json_object(
                       'rejection_reason', ?, 'reason_code', ?)),
               last_event_id = ?, updated_at = ? WHERE candidate_id = ?""",
            (str(payload.get("reason") or ""),
             str(payload.get("reason_code") or "LEGACY_REJECTION"),
             event.event_id, event.recorded_at, payload["candidate_id"]))
        if _has_table(connection, "candidate_judgments"):
            connection.execute(
                """INSERT OR IGNORE INTO candidate_judgments(
                    judgment_id, candidate_id, fingerprint, decision_id,
                    cycle_id, stage, outcome, reason_code, reason_detail,
                    score, factors_json, evidence_json, rank,
                    do_nothing_won, policy_version, evidence_hash, decided_at,
                    last_event_id, updated_at
                ) VALUES (?, ?, NULL, NULL, NULL, 'FINAL', 'REJECTED', ?, ?,
                          NULL, '{}', '[]', NULL, 0, NULL, NULL, ?, ?, ?)""",
                (f"legacy-rejection:{event.idempotency_key}",
                 payload["candidate_id"],
                 str(payload.get("reason_code") or "LEGACY_REJECTION"),
                 str(payload.get("reason") or ""), event.recorded_at,
                 event.event_id, event.recorded_at),
            )
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
        # V3 WP1/WP6: outcomes land in their own projection. Predicted
        # and actual stay DISTINCT; a payload without either value is
        # honest UNKNOWN, never manufactured PASS.
        if _has_table(connection, "candidate_outcomes"):
            window = str(payload.get("window") or "immediate")
            predicted = payload.get("predicted_value")
            actual = payload.get("actual_value",
                                 payload.get("outcome"))
            has_measurement = bool(predicted) or bool(actual)
            outcome_status = str(payload.get("status")
                                 or ("RECORDED" if has_measurement else "UNKNOWN"))
            connection.execute(
                """INSERT OR REPLACE INTO candidate_outcomes(
                    outcome_id, candidate_id, mission_id, window_kind,
                    predicted_value_json, actual_value_json, status,
                    confidence, last_event_id, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (payload.get("outcome_id")
                 or f"{payload['candidate_id']}:{window}",
                 payload["candidate_id"], payload.get("mission_id", ""),
                 window,
                 canonical_json(predicted or {}),
                 canonical_json(actual or {}),
                 outcome_status,
                 float(payload.get("confidence", 0.0)),
                 event.event_id, event.recorded_at))

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
    elif event.event_type == "discovery.monitor_configured":
        columns = _table_columns(connection, "monitors")
        if "source_id" not in columns:
            return
        monitor_id = str(payload.get("monitor_id") or "")
        if not monitor_id or connection.execute(
                "SELECT 1 FROM monitors WHERE monitor_id = ?",
                (monitor_id,)).fetchone() is None:
            raise ValueError(
                "discovery.monitor_configured references unknown monitor "
                f"{monitor_id}")
        enabled = 1 if bool(payload.get("enabled", True)) else 0
        connection.execute(
            """UPDATE monitors SET
               source_id = ?, source_kind = ?, source_ref = ?,
               poll_interval_s = ?, config_json = ?, config_hash = ?,
               enabled = ?,
               readiness_state = CASE WHEN ? = 1 THEN readiness_state
                                      ELSE 'DISABLED' END,
               next_due_at = CASE
                   WHEN ? = 1 AND (config_hash IS NULL OR config_hash != ?)
                       THEN ?
                   WHEN ? = 0 THEN NULL
                   ELSE next_due_at
               END,
               backoff_until = CASE
                   WHEN config_hash IS NULL OR config_hash != ? THEN NULL
                   ELSE backoff_until
               END,
               blocked_reason = CASE WHEN ? = 1 THEN blocked_reason ELSE ? END,
               last_event_id = ?, updated_at = ?
               WHERE monitor_id = ?""",
            (payload["source_id"], payload["source_kind"],
             payload["source_identity"], int(payload["poll_interval_s"]),
             canonical_json(payload.get("config") or {}), payload["config_hash"],
             enabled, enabled, enabled, payload["config_hash"],
             payload.get("next_due_at", event.recorded_at), enabled,
             payload["config_hash"], enabled,
             str(payload.get("reason") or "disabled by configuration"),
             event.event_id, event.recorded_at, monitor_id),
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
        columns = _table_columns(connection, "monitors")
        if "readiness_state" not in columns:
            connection.execute(
                "UPDATE monitors SET last_poll_at = ?, last_event_id = ?, "
                "updated_at = ? WHERE monitor_id = ?",
                (event.recorded_at, event.event_id, event.recorded_at,
                 payload["monitor_id"]),
            )
        else:
            status = str(payload.get("status") or "READY").upper()
            if status not in {"READY", "BLOCKED", "UNKNOWN", "IDLE"}:
                raise ValueError(f"unsupported monitor readiness {status}")
            meaningful = bool(payload.get("meaningful_change", False))
            attempted_at = payload.get("attempted_at", event.recorded_at)
            observation_at = payload.get("observation_at")
            failed = status in {"BLOCKED", "UNKNOWN"}
            connection.execute(
                """UPDATE monitors SET
                   last_poll_at = ?, last_attempt_at = ?,
                   last_observation_at = COALESCE(?, last_observation_at),
                   last_success_at = CASE WHEN ? = 0 THEN ? ELSE last_success_at END,
                   last_failure_at = CASE WHEN ? = 1 THEN ? ELSE last_failure_at END,
                   last_meaningful_change_at = CASE
                       WHEN ? = 1 THEN ? ELSE last_meaningful_change_at END,
                   last_content_hash = CASE
                       WHEN ? = 1 THEN ? ELSE last_content_hash END,
                   readiness_state = ?, next_due_at = ?,
                   consecutive_failures = ?, backoff_until = ?,
                   blocked_reason = ?, last_event_id = ?, updated_at = ?
                   WHERE monitor_id = ?""",
                (attempted_at, attempted_at, observation_at,
                 int(failed), attempted_at, int(failed), attempted_at,
                 int(meaningful), attempted_at, int(meaningful),
                 payload.get("content_hash"), status,
                 payload.get("next_due_at"),
                 max(0, int(payload.get("consecutive_failures") or 0)),
                 payload.get("backoff_until"),
                 str(payload.get("reason") or "") if failed else "",
                 event.event_id, event.recorded_at, payload["monitor_id"]),
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
        # V3 WP5 git effects journal under a synthetic system id (the
        # adapter carries its own identity payload for provenance).
        connection.execute(
            """INSERT OR IGNORE INTO system_actions(
                action_key, system_id, target_ref, payload_hash, kind,
                status, last_event_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, 'STARTED', ?, ?)""",
            (payload["action_key"], payload.get("system_id", "git"),
             payload["target_ref"],
             payload.get("payload_hash")
             or payload.get("action_key", ""),
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
        if "readiness_state" in _table_columns(connection, "monitors"):
            connection.execute(
                "UPDATE monitors SET enabled = 0, readiness_state = 'DISABLED', "
                "next_due_at = NULL, backoff_until = NULL, blocked_reason = ?, "
                "last_event_id = ?, updated_at = ? WHERE monitor_id = ?",
                (str(payload.get("reason") or "disabled"), event.event_id,
                 event.recorded_at, payload["monitor_id"]),
            )
        else:
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
    elif event.event_type == "research.candidate_enqueued":
        research_id = str(payload.get("research_id") or "")
        candidate_id = str(payload.get("candidate_id") or "")
        pass_number = payload.get("pass_number")
        status = str(payload.get("status") or "QUEUED")
        if (not research_id or not candidate_id or pass_number is None
                or status not in {
                    "QUEUED", "ENQUEUED", "IN_PROGRESS", "BLOCKED", "DEFERRED",
                }):
            raise ValueError(
                "research.candidate_enqueued requires research_id, "
                "candidate_id, pass_number, and a candidate research status")
        if connection.execute(
                "SELECT 1 FROM candidates WHERE candidate_id = ?",
                (candidate_id,)).fetchone() is None:
            raise ValueError(
                f"research.candidate_enqueued references unknown candidate {candidate_id}")
        connection.execute(
            """INSERT OR IGNORE INTO research_queue(
                item_id, claim_id, reason, evidence_json, status,
                candidate_id, cycle_id, pass_number, budget_used, budget_json,
                content_hash, input_hash, source, source_ref, queued_at, started_at,
                completed_at, result_json, last_event_id, updated_at
            ) VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, '{}', ?, ?, ?, ?, ?, ?, NULL,
                      '{}', ?, ?)""",
            (research_id, str(payload.get("reason") or "candidate_research"),
             canonical_json(payload.get("evidence") or []), status,
             candidate_id, payload.get("cycle_id"), int(pass_number),
              float(payload.get("budget_used") or 0.0),
              payload.get("content_hash"), payload.get("input_hash"),
              str(payload.get("source") or "candidate"),
             payload.get("source_ref"), event.recorded_at,
             payload.get("attempted_at"), event.event_id, event.recorded_at),
        )
    elif event.event_type == "research.candidate_started":
        research_id = str(payload.get("research_id") or "")
        if not research_id:
            raise ValueError("research.candidate_started requires research_id")
        cursor = connection.execute(
            """UPDATE research_queue SET status = 'IN_PROGRESS',
               started_at = ?, last_event_id = ?, updated_at = ?
               WHERE item_id = ? AND status = 'ENQUEUED'""",
            (payload.get("attempted_at", event.recorded_at), event.event_id,
             event.recorded_at, research_id),
        )
        if cursor.rowcount == 0:
            raise ValueError(
                "research.candidate_started references no ENQUEUED research "
                f"item {research_id}"
            )
    elif event.event_type == "research.candidate_completed":
        research_id = str(payload.get("research_id") or "")
        status = str(payload.get("status") or "COMPLETED")
        if not research_id or status not in {
                "COMPLETED", "BLOCKED", "DEFERRED", "FAILED", "REJECTED",
                "UNKNOWN",
        }:
            raise ValueError(
                "research.candidate_completed requires research_id and a "
                "terminal candidate research status")
        if connection.execute(
                "SELECT 1 FROM research_queue WHERE item_id = ? "
                "AND status = 'IN_PROGRESS'",
                (research_id,)).fetchone() is None:
            raise ValueError(
                "research.candidate_completed references no IN_PROGRESS "
                f"research item {research_id}")
        connection.execute(
            """UPDATE research_queue SET
                status = ?, budget_used = ?, content_hash = COALESCE(?, content_hash),
                source_ref = COALESCE(?, source_ref), evidence_json = ?,
                result_json = ?, started_at = COALESCE(started_at, ?),
                completed_at = ?, last_event_id = ?, updated_at = ?
               WHERE item_id = ?""",
            (status, float(payload.get("budget_used") or 0.0),
             payload.get("content_hash"), payload.get("source_ref"),
             canonical_json(payload.get("evidence") or []),
             canonical_json(payload.get("result") or {}),
             payload.get("attempted_at"),
             payload.get("completed_at", event.recorded_at),
             event.event_id, event.recorded_at, research_id),
        )

    elif event.event_type == "initiative.cycle_started":
        if not _has_table(connection, "initiative_cycles"):
            return
        cycle_id = str(payload.get("cycle_id") or "")
        status = str(payload.get("status") or "RUNNING")
        if not cycle_id:
            raise ValueError("initiative.cycle_started requires cycle_id")
        connection.execute(
            """INSERT OR IGNORE INTO initiative_cycles(
                cycle_id, stage, status, started_at, completed_at,
                bounds_json, budgets_json, metrics_json, reason, next_due_at,
                last_event_id, updated_at
            ) VALUES (?, 'STARTED', ?, ?, NULL, ?, ?, ?, ?, ?, ?, ?)""",
            (cycle_id, status, payload.get("started_at", event.recorded_at),
             canonical_json(payload.get("bounds") or {}),
             canonical_json(payload.get("budgets") or {}),
             canonical_json(payload.get("metrics") or {}),
             str(payload.get("reason") or ""), payload.get("next_due_at"),
             event.event_id, event.recorded_at),
        )
    elif event.event_type == "initiative.cycle_stage_changed":
        if not _has_table(connection, "initiative_cycles"):
            return
        cycle_id = str(payload.get("cycle_id") or "")
        stage = str(payload.get("stage") or "")
        if not cycle_id or not stage:
            raise ValueError(
                "initiative.cycle_stage_changed requires cycle_id and stage")
        if connection.execute(
                "SELECT 1 FROM initiative_cycles WHERE cycle_id = ?",
                (cycle_id,)).fetchone() is None:
            raise ValueError(
                f"initiative.cycle_stage_changed references unknown cycle {cycle_id}")
        connection.execute(
            """UPDATE initiative_cycles SET stage = ?,
               status = COALESCE(?, status), last_event_id = ?, updated_at = ?
               WHERE cycle_id = ?""",
            (stage, payload.get("status"), event.event_id,
             event.recorded_at, cycle_id),
        )
    elif event.event_type == "initiative.cycle_completed":
        if not _has_table(connection, "initiative_cycles"):
            return
        cycle_id = str(payload.get("cycle_id") or "")
        if not cycle_id or connection.execute(
                "SELECT 1 FROM initiative_cycles WHERE cycle_id = ?",
                (cycle_id,)).fetchone() is None:
            raise ValueError(
                f"initiative.cycle_completed references unknown cycle {cycle_id}")
        connection.execute(
            """UPDATE initiative_cycles SET stage = 'COMPLETED', status = ?,
               completed_at = ?, budgets_json = ?, metrics_json = ?,
               reason = ?, next_due_at = ?, last_event_id = ?, updated_at = ?
               WHERE cycle_id = ?""",
            (str(payload.get("status") or "COMPLETED"),
             payload.get("completed_at", event.recorded_at),
             canonical_json(payload.get("budgets") or {}),
             canonical_json(payload.get("metrics") or {}),
             str(payload.get("reason") or ""), payload.get("next_due_at"),
             event.event_id, event.recorded_at, cycle_id),
        )
    elif event.event_type == "initiative.source_recorded":
        if not _has_table(connection, "initiative_source_runs"):
            return
        cycle_id = str(payload.get("cycle_id") or "")
        source_run_id = str(payload.get("source_id") or "")
        source_kind = str(payload.get("source_kind") or "")
        source_identity = str(payload.get("source_identity") or "")
        status = str(payload.get("status") or "")
        if (not cycle_id or not source_run_id or not source_kind
                or not source_identity
                or status not in {"READY", "BLOCKED", "UNKNOWN", "IDLE"}):
            raise ValueError(
                "initiative.source_recorded requires cycle_id, source_id, "
                "source_kind, source_identity, and a supported status")
        if connection.execute(
                "SELECT 1 FROM initiative_cycles WHERE cycle_id = ?",
                (cycle_id,)).fetchone() is None:
            raise ValueError(
                f"initiative.source_recorded references unknown cycle {cycle_id}")
        connection.execute(
            """INSERT INTO initiative_source_runs(
                source_run_id, cycle_id, source_class, source_identity,
                status, attempted_at, completed_at, observations_count,
                candidates_count, content_hash, next_due_at, provenance_json,
                error_json, reason, last_event_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '{}', '{}', ?, ?, ?)
            ON CONFLICT(cycle_id, source_class, source_identity) DO UPDATE SET
                status=excluded.status,
                observations_count=excluded.observations_count,
                candidates_count=excluded.candidates_count,
                content_hash=excluded.content_hash,
                next_due_at=excluded.next_due_at,
                reason=excluded.reason,
                last_event_id=excluded.last_event_id,
                updated_at=excluded.updated_at""",
            (source_run_id, cycle_id, source_kind, source_identity, status,
             event.recorded_at, event.recorded_at,
             max(0, int(payload.get("observations_recorded") or 0)),
             max(0, int(payload.get(
                 "candidates_recorded", payload.get("candidates_count", 0)) or 0)),
             payload.get("content_hash"), payload.get("next_due_at"),
             str(payload.get("reason") or ""), event.event_id,
             event.recorded_at),
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
    elif event.event_type == "release.manifest_recorded":
        for key in ("release_id", "source_commit", "artifact_hashes",
                    "dependencies", "runtime_identity",
                    "parent_release_id", "improvement_id", "risk_class",
                    "migrations", "verdicts", "rollback_target"):
            if key not in payload:
                raise ValueError(
                    f"release.manifest_recorded requires {key} "
                    "(mandatory payload key) — rolled back")
        if payload.get("parent_release_id") is not None:
            parent = connection.execute(
                "SELECT 1 FROM release_manifests WHERE release_id = ?",
                (payload["parent_release_id"],)).fetchone()
            if parent is None:
                raise ValueError(
                    f"release.manifest_recorded parent "
                    f"{payload['parent_release_id']} does not exist — "
                    "rolled back")
        if payload.get("rollback_target") is not None:
            target = connection.execute(
                "SELECT 1 FROM release_manifests WHERE release_id = ?",
                (payload["rollback_target"],)).fetchone()
            if target is None:
                raise ValueError(
                    f"release.manifest_recorded rollback target "
                    f"{payload['rollback_target']} does not exist — "
                    "rolled back")
        for name in payload["artifact_hashes"]:
            if not ARTIFACT_NAME_PATTERN.match(name):
                raise ValueError(
                    f"release.manifest_recorded artifact name {name!r} "
                    "is not a plain file name — rolled back")
        existing = connection.execute(
            "SELECT artifact_hashes_json, source_commit, "
            "improvement_id, risk_class FROM release_manifests "
            "WHERE release_id = ?",
            (payload["release_id"],)).fetchone()
        if existing is not None:
            identity = canonical_json({
                "artifact_hashes": payload["artifact_hashes"],
                "source_commit": payload["source_commit"],
                "improvement_id": payload["improvement_id"],
                "risk_class": payload["risk_class"]})
            if identity != canonical_json({
                    "artifact_hashes":
                        json.loads(existing["artifact_hashes_json"]),
                    "source_commit": existing["source_commit"],
                    "improvement_id": existing["improvement_id"],
                    "risk_class": existing["risk_class"]}):
                raise ValueError(
                    f"release.manifest_recorded conflicts with the "
                    f"immutable manifest {payload['release_id']} "
                    "(same release id, different content) — rolled "
                    "back")
        connection.execute(
            """INSERT OR IGNORE INTO release_manifests(
                release_id, source_commit, artifact_hashes_json,
                dependencies_json, runtime_identity_json,
                parent_release_id, improvement_id, risk_class,
                migrations_json, verdicts_json, rollback_target,
                created_at, last_event_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (payload["release_id"], payload["source_commit"],
             canonical_json(payload["artifact_hashes"]),
             canonical_json(payload["dependencies"]),
             canonical_json(payload["runtime_identity"]),
             payload.get("parent_release_id"),
             payload["improvement_id"], payload["risk_class"],
             canonical_json(payload["migrations"]),
             canonical_json(payload["verdicts"]),
             payload.get("rollback_target"), event.recorded_at,
             event.event_id, event.recorded_at),
        )
    elif event.event_type == "candidate.build_started":
        for key in ("build_id", "improvement_id", "candidate_root",
                    "command_hash"):
            if key not in payload:
                raise ValueError(
                    f"candidate.build_started requires {key} "
                    "(mandatory payload key) — rolled back")
        connection.execute(
            """INSERT OR IGNORE INTO candidate_builds(
                build_id, improvement_id, candidate_root, status,
                started_at, last_event_id, updated_at
            ) VALUES (?, ?, ?, 'BUILDING', ?, ?, ?)""",
            (payload["build_id"], payload["improvement_id"],
             payload["candidate_root"], event.recorded_at,
             event.event_id, event.recorded_at),
        )
    elif event.event_type in ("candidate.build_completed",
                              "candidate.build_failed"):
        for key in ("build_id", "exit_code"):
            if key not in payload:
                raise ValueError(
                    f"{event.event_type} requires {key} "
                    "(mandatory payload key) — rolled back")
        status = ("COMPLETED" if event.event_type ==
                  "candidate.build_completed" else "FAILED")
        cursor = connection.execute(
            """UPDATE candidate_builds SET status = ?, exit_code = ?,
               artifact_hashes_json = ?, reason = ?, finished_at = ?,
               last_event_id = ?, updated_at = ?
               WHERE build_id = ?""",
            (status, payload["exit_code"],
             canonical_json(payload.get("artifact_hashes", {})),
             payload.get("reason"), event.recorded_at,
             event.event_id, event.recorded_at, payload["build_id"]),
        )
        if cursor.rowcount == 0:
            raise ValueError(
                f"{event.event_type} references unknown build "
                f"{payload['build_id']} — rolled back")
    elif event.event_type == "candidate.build_reaped":
        for key in ("build_id", "reason"):
            if key not in payload:
                raise ValueError(
                    f"candidate.build_reaped requires {key} "
                    "(mandatory payload key) — rolled back")
        connection.execute(
            """INSERT OR IGNORE INTO candidate_builds(
                build_id, improvement_id, candidate_root, status,
                reason, finished_at, last_event_id, updated_at
            ) VALUES (?, '', '', 'REAPED', ?, ?, ?, ?)""",
            (payload["build_id"], payload["reason"], event.recorded_at,
             event.event_id, event.recorded_at),
        )
        connection.execute(
            """UPDATE candidate_builds SET status = 'REAPED',
               reason = ?, finished_at = ?, last_event_id = ?,
               updated_at = ?
               WHERE build_id = ? AND status = 'BUILDING'""",
            (payload["reason"], event.recorded_at, event.event_id,
             event.recorded_at, payload["build_id"]),
        )
    elif event.event_type == "evaluation.run_started":
        for key in ("run_id", "workload_id", "subject_kind",
                    "subject_identity", "command_hash"):
            if key not in payload:
                raise ValueError(
                    f"evaluation.run_started requires {key} "
                    "(mandatory payload key) — rolled back")
        connection.execute(
            """INSERT OR IGNORE INTO evaluation_runs(
                run_id, workload_id, subject_kind, subject_identity,
                command_hash, input_hash, created_at, last_event_id,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, '', ?, ?, ?)""",
            (payload["run_id"], payload["workload_id"],
             payload["subject_kind"], payload["subject_identity"],
             payload["command_hash"], event.recorded_at,
             event.event_id, event.recorded_at),
        )
    elif event.event_type == "evaluation.run_completed":
        for key in ("run_id", "workload_id", "subject_kind",
                    "subject_identity", "command_hash", "input_hash",
                    "exit_code", "duration_s", "error_rate", "cost_usd",
                    "case_results", "output_hashes",
                    "nondeterminism_handling"):
            if key not in payload:
                raise ValueError(
                    f"evaluation.run_completed requires {key} "
                    "(mandatory payload key) — rolled back")
        cursor = connection.execute(
            """UPDATE evaluation_runs SET input_hash = ?, exit_code = ?,
               duration_s = ?, error_rate = ?, cost_usd = ?,
               case_results_json = ?, output_hashes_json = ?,
               nondeterminism_handling = ?, last_event_id = ?,
               updated_at = ?
               WHERE run_id = ?""",
            (payload["input_hash"], payload["exit_code"],
             payload["duration_s"], payload["error_rate"],
             payload["cost_usd"],
             canonical_json(payload["case_results"]),
             canonical_json(payload["output_hashes"]),
             payload["nondeterminism_handling"], event.event_id,
             event.recorded_at, payload["run_id"]),
        )
        if cursor.rowcount == 0:
            raise ValueError(
                f"evaluation.run_completed references unknown run "
                f"{payload['run_id']} — rolled back")
    elif event.event_type == "verification.requested":
        for key in ("verification_id", "improvement_id", "subject_ref",
                    "criteria", "artifacts", "builder_identity",
                    "controller"):
            if key not in payload:
                raise ValueError(
                    f"verification.requested requires {key} "
                    "(mandatory payload key) — rolled back")
        connection.execute(
            """INSERT OR IGNORE INTO verifications(
                verification_id, improvement_id, subject_ref, status,
                criteria_json, artifacts_json, builder_identity_json,
                created_at, last_event_id, updated_at
            ) VALUES (?, ?, ?, 'REQUESTED', ?, ?, ?, ?, ?, ?)""",
            (payload["verification_id"], payload["improvement_id"],
             payload["subject_ref"],
             canonical_json(payload["criteria"]),
             canonical_json(payload["artifacts"]),
             canonical_json(payload["builder_identity"]),
             event.recorded_at, event.event_id, event.recorded_at),
        )
    elif event.event_type == "verification.verdict_recorded":
        for key in ("verification_id", "verdict", "criteria_results",
                    "findings", "verifier_identity", "evidence_refs",
                    "verdict_hash", "same_provider_limitation"):
            if key not in payload:
                raise ValueError(
                    f"verification.verdict_recorded requires {key} "
                    "(mandatory payload key) — rolled back")
        if payload["verdict"] not in ("PASS", "FAIL", "BLOCKED",
                                      "NOT_TESTED"):
            raise ValueError(
                f"verification.verdict_recorded invalid verdict "
                f"{payload['verdict']} — rolled back")
        cursor = connection.execute(
            """UPDATE verifications SET status = 'VERDICTED',
               verdict = ?, criteria_results_json = ?,
               findings_json = ?, verifier_identity_json = ?,
               evidence_refs_json = ?, verdict_hash = ?,
               same_provider_limitation = ?, last_event_id = ?,
               updated_at = ?
               WHERE verification_id = ? AND status = 'REQUESTED'""",
            (payload["verdict"],
             canonical_json(payload["criteria_results"]),
             canonical_json(payload["findings"]),
             canonical_json(payload["verifier_identity"]),
             canonical_json(payload["evidence_refs"]),
             payload["verdict_hash"],
             int(bool(payload["same_provider_limitation"])),
             event.event_id, event.recorded_at,
             payload["verification_id"]),
        )
        if cursor.rowcount == 0:
            raise ValueError(
                f"verification.verdict_recorded has no REQUESTED "
                f"verification {payload['verification_id']} — rolled "
                "back (forgery or double verdict)")
    elif event.event_type == "shadow.run_started":
        for key in ("run_id", "improvement_id", "candidate_release_id",
                    "controller", "stop_observed"):
            if key not in payload:
                raise ValueError(
                    f"shadow.run_started requires {key} "
                    "(mandatory payload key) — rolled back")
        connection.execute(
            """INSERT OR IGNORE INTO shadow_runs(
                run_id, improvement_id, candidate_release_id, status,
                stop_observed, created_at, last_event_id, updated_at
            ) VALUES (?, ?, ?, 'RUNNING', ?, ?, ?, ?)""",
            (payload["run_id"], payload["improvement_id"],
             payload["candidate_release_id"],
             int(bool(payload["stop_observed"])),
             event.recorded_at, event.event_id, event.recorded_at),
        )
    elif event.event_type == "shadow.run_completed":
        for key in ("run_id", "decisions_count", "divergences",
                    "zero_live_effects", "stop_observed",
                    "live_attempts", "blocked_attempts", "input_hash"):
            if key not in payload:
                raise ValueError(
                    f"shadow.run_completed requires {key} "
                    "(mandatory payload key) — rolled back")
        cursor = connection.execute(
            """UPDATE shadow_runs SET status = 'COMPLETED',
               decisions_count = ?, divergences_json = ?,
               zero_live_effects = ?, stop_observed = ?,
               live_attempts = ?, blocked_attempts = ?,
               input_hash = ?, last_event_id = ?, updated_at = ?
               WHERE run_id = ? AND status = 'RUNNING'""",
            (payload["decisions_count"],
             canonical_json(payload["divergences"]),
             int(bool(payload["zero_live_effects"])),
             int(bool(payload["stop_observed"])),
             payload["live_attempts"], payload["blocked_attempts"],
             payload["input_hash"], event.event_id,
             event.recorded_at, payload["run_id"]),
        )
        if cursor.rowcount == 0:
            raise ValueError(
                f"shadow.run_completed has no RUNNING run "
                f"{payload['run_id']} — rolled back")
    elif event.event_type == "shadow.run_failed":
        for key in ("run_id", "reason", "stop_observed",
                    "decisions_count", "live_attempts",
                    "blocked_attempts"):
            if key not in payload:
                raise ValueError(
                    f"shadow.run_failed requires {key} "
                    "(mandatory payload key) — rolled back")
        cursor = connection.execute(
            """UPDATE shadow_runs SET status = 'FAILED',
               reason = ?, stop_observed = ?,
               decisions_count = ?, live_attempts = ?,
               blocked_attempts = ?, last_event_id = ?, updated_at = ?
               WHERE run_id = ? AND status = 'RUNNING'""",
            (payload["reason"], int(bool(payload["stop_observed"])),
             payload["decisions_count"], payload["live_attempts"],
             payload["blocked_attempts"], event.event_id,
             event.recorded_at, payload["run_id"]),
        )
        if cursor.rowcount == 0:
            raise ValueError(
                f"shadow.run_failed has no RUNNING run "
                f"{payload['run_id']} — rolled back")
    elif event.event_type == "canary.started":
        for key in ("canary_id", "improvement_id",
                    "candidate_release_id", "traffic_share",
                    "duration_cycles", "max_cost_usd",
                    "health_thresholds", "controller"):
            if key not in payload:
                raise ValueError(
                    f"canary.started requires {key} "
                    "(mandatory payload key) — rolled back")
        connection.execute(
            """INSERT OR IGNORE INTO canaries(
                canary_id, improvement_id, candidate_release_id,
                status, traffic_share, duration_cycles, max_cost_usd,
                health_thresholds_json, started_at, last_event_id,
                updated_at
            ) VALUES (?, ?, ?, 'RUNNING', ?, ?, ?, ?, ?, ?, ?)""",
            (payload["canary_id"], payload["improvement_id"],
             payload["candidate_release_id"], payload["traffic_share"],
             payload["duration_cycles"], payload["max_cost_usd"],
             canonical_json(payload["health_thresholds"]),
             event.recorded_at, event.event_id, event.recorded_at),
        )
    elif event.event_type == "canary.tick":
        for key in ("canary_id", "cycle", "traffic_served", "errors",
                    "latency_s", "cost_usd", "error_rate", "healthy",
                    "regression_observed", "invariant_violated",
                    "controller"):
            if key not in payload:
                raise ValueError(
                    f"canary.tick requires {key} "
                    "(mandatory payload key) — rolled back")
        cursor = connection.execute(
            """UPDATE canaries SET cycles_run = cycles_run + 1,
               last_tick_cycle = ?, total_cost_usd =
                   total_cost_usd + ?, total_errors = total_errors + ?,
               last_health = ?, last_event_id = ?, updated_at = ?
               WHERE canary_id = ? AND status = 'RUNNING'""",
            (payload["cycle"], payload["cost_usd"], payload["errors"],
             int(bool(payload["healthy"])), event.event_id,
             event.recorded_at, payload["canary_id"]),
        )
        if cursor.rowcount == 0:
            raise ValueError(
                f"canary.tick has no RUNNING canary "
                f"{payload['canary_id']} — rolled back")
    elif event.event_type in ("canary.terminated", "canary.completed",
                              "canary.reaped"):
        for key in ("canary_id", "reason", "controller"):
            if key not in payload:
                raise ValueError(
                    f"{event.event_type} requires {key} "
                    "(mandatory payload key) — rolled back")
        status = ("COMPLETED" if event.event_type == "canary.completed"
                  else "TERMINATED")
        cursor = connection.execute(
            """UPDATE canaries SET status = ?, reason = ?,
               finished_at = ?, last_event_id = ?, updated_at = ?
               WHERE canary_id = ? AND status = 'RUNNING'""",
            (status, payload["reason"], event.recorded_at,
             event.event_id, event.recorded_at, payload["canary_id"]),
        )
        if cursor.rowcount == 0:
            raise ValueError(
                f"{event.event_type} has no RUNNING canary "
                f"{payload['canary_id']} — rolled back")
    elif event.event_type == "restore.point_created":
        for key in ("point_id", "label", "path", "sha256",
                    "schema_version", "event_count", "controller"):
            if key not in payload:
                raise ValueError(
                    f"restore.point_created requires {key} "
                    "(mandatory payload key) — rolled back")
        connection.execute(
            """INSERT OR IGNORE INTO restore_points(
                point_id, label, path, sha256, schema_version,
                event_count, status, controller, created_at,
                last_event_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, 'CREATED', ?, ?, ?, ?)""",
            (payload["point_id"], payload["label"], payload["path"],
             payload["sha256"], payload["schema_version"],
             payload["event_count"], payload["controller"],
             event.recorded_at, event.event_id, event.recorded_at),
        )
    elif event.event_type == "restore.executed":
        for key in ("point_id", "before_events", "after_events",
                    "lost_events", "controller"):
            if key not in payload:
                raise ValueError(
                    f"restore.executed requires {key} "
                    "(mandatory payload key) — rolled back")
        cursor = connection.execute(
            """UPDATE restore_points SET status = 'EXECUTED',
               last_event_id = ?, updated_at = ?
               WHERE point_id = ?""",
            (event.event_id, event.recorded_at, payload["point_id"]),
        )
        if cursor.rowcount == 0:
            raise ValueError(
                f"restore.executed references unknown point "
                f"{payload['point_id']} — rolled back")
    elif event.event_type == "discovery.observed":
        for key in ("run_id", "sources_hash", "candidates_count",
                    "recorded_count", "controller"):
            if key not in payload:
                raise ValueError(
                    f"discovery.observed requires {key} "
                    "(mandatory payload key) — rolled back")
        for key in ("candidates_count", "recorded_count"):
            if not isinstance(payload[key], int) or isinstance(
                    payload[key], bool):
                raise ValueError(
                    f"discovery.observed {key} must be an integer — "
                    "rolled back (S10 verifier deleg_b65820c2)")
        connection.execute(
            """INSERT OR IGNORE INTO discovery_runs(
                run_id, sources_hash, candidates_count, recorded_count,
                controller, created_at, last_event_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (payload["run_id"], payload["sources_hash"],
             payload["candidates_count"], payload["recorded_count"],
             payload["controller"], event.recorded_at, event.event_id,
             event.recorded_at),
        )
    elif event.event_type == "acceptance.completed":
        for key in ("run_id", "passed", "report_json", "controller"):
            if key not in payload:
                raise ValueError(
                    f"acceptance.completed requires {key} "
                    "(mandatory payload key) — rolled back")
        if not isinstance(payload["passed"], bool):
            raise ValueError(
                "acceptance.completed passed must be a boolean — "
                "rolled back")
        try:
            import json as _json
            _json.loads(payload["report_json"])
        except (ValueError, TypeError):
            raise ValueError(
                "acceptance.completed report_json must be valid JSON — "
                "rolled back") from None
        connection.execute(
            """INSERT OR IGNORE INTO acceptance_runs(
                run_id, passed, report_json, controller, created_at,
                last_event_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (payload["run_id"], int(payload["passed"]),
             payload["report_json"], payload["controller"],
             event.recorded_at, event.event_id, event.recorded_at),
        )
    elif event.event_type == "cycle.started":
        for key in ("cycle_id", "improvement_id", "controller"):
            if key not in payload:
                raise ValueError(
                    f"cycle.started requires {key} "
                    "(mandatory payload key) — rolled back")
        connection.execute(
            """INSERT OR IGNORE INTO cycles(
                cycle_id, improvement_id, outcome, controller,
                created_at, last_event_id, updated_at
            ) VALUES (?, ?, 'rejected', ?, ?, ?, ?)""",
            (payload["cycle_id"], payload["improvement_id"],
             payload["controller"], event.recorded_at, event.event_id,
             event.recorded_at),
        )
    elif event.event_type == "cycle.completed":
        for key in ("cycle_id", "improvement_id", "outcome",
                    "controller"):
            if key not in payload:
                raise ValueError(
                    f"cycle.completed requires {key} "
                    "(mandatory payload key) — rolled back")
        if payload["outcome"] not in ("promoted", "rejected"):
            raise ValueError(
                f"cycle.completed unknown outcome "
                f"{payload['outcome']} — rolled back")
        cursor = connection.execute(
            """UPDATE cycles SET outcome = ?, reason = ?,
               promotion_id = ?, canary_id = ?, verification_id = ?,
               baseline_run_id = ?, candidate_run_id = ?, build_id = ?,
               candidate_release_id = ?, genesis_release_id = ?,
               workload_id = ?, last_event_id = ?, updated_at = ?
               WHERE cycle_id = ?""",
            (payload["outcome"], payload.get("reason"),
             payload.get("promotion_id"), payload.get("canary_id"),
             payload.get("verification_id"),
             payload.get("baseline_run_id"),
             payload.get("candidate_run_id"), payload.get("build_id"),
             payload.get("candidate_release_id"),
             payload.get("genesis_release_id"),
             payload.get("workload_id"), event.event_id,
             event.recorded_at, payload["cycle_id"]),
        )
        if cursor.rowcount == 0:
            raise ValueError(
                f"cycle.completed references unknown cycle "
                f"{payload['cycle_id']} — rolled back")
    elif event.event_type == "release.promotion_intent":
        for key in ("promotion_id", "release_id", "verification_id",
                    "baseline_run_id", "candidate_run_id", "risk_class",
                    "planned", "controller"):
            if key not in payload:
                raise ValueError(
                    f"release.promotion_intent requires {key} "
                    "(mandatory payload key) — rolled back")
        connection.execute(
            """INSERT OR IGNORE INTO promotions(
                promotion_id, release_id, verification_id, canary_id,
                baseline_run_id, candidate_run_id, risk_class, status,
                controller, planned_json, created_at, last_event_id,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'INTENT', ?, ?, ?, ?, ?)""",
            (payload["promotion_id"], payload["release_id"],
             payload["verification_id"], payload.get("canary_id"),
             payload["baseline_run_id"], payload["candidate_run_id"],
             payload["risk_class"], payload["controller"],
             canonical_json(payload["planned"]),
             event.recorded_at, event.event_id, event.recorded_at),
        )
    elif event.event_type == "release.promoted":
        for key in ("promotion_id", "release_id", "controller"):
            if key not in payload:
                raise ValueError(
                    f"release.promoted requires {key} "
                    "(mandatory payload key) — rolled back")
        cursor = connection.execute(
            """UPDATE promotions SET status = 'PROMOTED',
               finished_at = ?, last_event_id = ?, updated_at = ?
               WHERE promotion_id = ? AND status = 'INTENT'""",
            (event.recorded_at, event.event_id, event.recorded_at,
             payload["promotion_id"]),
        )
        if cursor.rowcount == 0:
            raise ValueError(
                f"release.promoted has no INTENT promotion "
                f"{payload['promotion_id']} — rolled back")
    elif event.event_type == "release.rolled_back":
        for key in ("promotion_id", "trigger", "reason", "from", "to",
                    "controller"):
            if key not in payload:
                raise ValueError(
                    f"release.rolled_back requires {key} "
                    "(mandatory payload key) — rolled back")
        if payload["trigger"] not in (
                "stop", "veto", "lost_heartbeat", "health_failure",
                "invariant_violation", "outcome_regression",
                "evidence_mismatch", "schema_incompatibility",
                "absent_effect", "verifier_contradiction", "manual"):
            raise ValueError(
                f"release.rolled_back unknown trigger "
                f"{payload['trigger']} — rolled back")
        cursor = connection.execute(
            """UPDATE promotions SET status = 'ROLLED_BACK',
               trigger = ?, reason = ?, finished_at = ?,
               last_event_id = ?, updated_at = ?
               WHERE promotion_id = ?
                 AND status IN ('INTENT', 'PROMOTED')""",
            (payload["trigger"], payload["reason"], event.recorded_at,
             event.event_id, event.recorded_at,
             payload["promotion_id"]),
        )
        if cursor.rowcount == 0:
            raise ValueError(
                f"release.rolled_back has no promotable promotion "
                f"{payload['promotion_id']} — rolled back")
    elif event.event_type == "release.frozen":
        for key in ("reason", "controller"):
            if key not in payload:
                raise ValueError(
                    f"release.frozen requires {key} "
                    "(mandatory payload key) — rolled back")
        connection.execute(
            """INSERT INTO release_control(id, frozen, frozen_reason,
                frozen_at, last_event_id, updated_at)
               VALUES (1, 1, ?, ?, ?, ?)
               ON CONFLICT(id) DO UPDATE SET frozen = 1,
                frozen_reason = excluded.frozen_reason,
                frozen_at = excluded.frozen_at,
                last_event_id = excluded.last_event_id,
                updated_at = excluded.updated_at""",
            (payload["reason"], event.recorded_at, event.event_id,
             event.recorded_at),
        )
    elif event.event_type == "release.unfrozen":
        connection.execute(
            """INSERT INTO release_control(id, frozen, last_event_id,
                updated_at)
               VALUES (1, 0, ?, ?)
               ON CONFLICT(id) DO UPDATE SET frozen = 0,
                frozen_reason = NULL, frozen_at = NULL,
                last_event_id = excluded.last_event_id,
                updated_at = excluded.updated_at""",
            (event.event_id, event.recorded_at),
        )
    elif event.event_type == "release.slot_changed":
        for key in ("slot", "release_id", "previous_release_id",
                    "reason", "controller"):
            if key not in payload:
                raise ValueError(
                    f"release.slot_changed requires {key} "
                    "(mandatory payload key) — rolled back")
        if payload["slot"] not in ("live", "candidate", "previous",
                                   "quarantine"):
            raise ValueError(
                f"release.slot_changed unknown slot "
                f"{payload['slot']} — rolled back")
        if not payload["reason"] or not payload["controller"]:
            raise ValueError(
                "release.slot_changed requires a non-empty reason and "
                "controller — rolled back")
        if payload["release_id"] is not None:
            exists = connection.execute(
                "SELECT 1 FROM release_manifests WHERE release_id = ?",
                (payload["release_id"],)).fetchone()
            if exists is None:
                raise ValueError(
                    f"release.slot_changed release "
                    f"{payload['release_id']} does not exist — rolled "
                    "back")
        connection.execute(
            """INSERT INTO release_slots(
                slot, release_id, updated_at, last_event_id
            ) VALUES (?, ?, ?, ?)
            ON CONFLICT(slot) DO UPDATE SET
                release_id = excluded.release_id,
                updated_at = excluded.updated_at,
                last_event_id = excluded.last_event_id""",
            (payload["slot"], payload["release_id"], event.recorded_at,
             event.event_id),
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
            if event.event_type.startswith("emergency_stop."):
                trust = "SUSPENDED"
            else:
                trust = str(payload.get("trust_state", "LAB")).upper()
                if trust == "PROVEN":
                    trust = "PROVING"
                # Older Supervisor/degraded events carried LAB as a default
                # even after an explicit trust transition.  A health event is
                # not an implicit trust transition; preserve the durable
                # lifecycle state unless the event explicitly supplies a
                # different state.
                current_trust = connection.execute(
                    "SELECT trust_state FROM supervisor_state WHERE singleton = 1"
                ).fetchone()
                current_value = (
                    str(current_trust[0] or "LAB").upper()
                    if current_trust is not None else "LAB"
                )
                if current_value == "PROVEN":
                    current_value = "PROVING"
                if trust not in ALLOWED_TRUST_STATES:
                    # A malformed imported heartbeat must not corrupt the
                    # durable projection.  Preserve a valid current state;
                    # otherwise fail closed to LAB.
                    trust = current_value if current_value in TRUST_STATES else "LAB"
                elif (trust == "LAB" and current_value != "LAB"
                      and current_value in TRUST_STATES):
                    trust = current_value
            connection.execute(
                """UPDATE supervisor_state SET instance_id = ?, status = ?, trust_state = ?,
                   last_heartbeat_at = ?, last_event_id = ? WHERE singleton = 1""",
                (payload.get("instance_id"), status, trust, event.recorded_at, event.event_id),
            )
    elif event.event_type == "trust.state_changed":
        # Trust is a Supervisor-owned authority.  Preserve a forged or
        # imported event in the append-only ledger for reconciliation evidence,
        # but never let a builder/owner/test source mutate the trust projection.
        if event.source_type != "supervisor" or event.source_id != "supervisor":
            return
        target = str(payload.get("to_state") or "LAB").upper()
        if target not in TRUST_STATES:
            raise ValueError(f"invalid trust state: {target}")
        connection.execute(
            "UPDATE supervisor_state SET trust_state = ?, last_event_id = ?, "
            "last_heartbeat_at = ? WHERE singleton = 1",
            (target, event.event_id, event.recorded_at),
        )
    elif event.event_type == "memory.observed":
        connection.execute(
            """INSERT INTO tier_c_memories (
                 memory_id, mission_id, agent_run_id, created_by, mem_type, scope, content,
                 source_ids_json, evidence_ids_json, confidence, state, created_at,
                 last_verified, expires_at, context_cost_chars, last_event_id, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(memory_id) DO UPDATE SET
                 content=excluded.content, source_ids_json=excluded.source_ids_json,
                 evidence_ids_json=excluded.evidence_ids_json,
                 confidence=excluded.confidence, expires_at=excluded.expires_at,
                 last_event_id=excluded.last_event_id, updated_at=excluded.updated_at""",
            (payload["memory_id"], payload.get("mission_id", ""), payload.get("agent_run_id"),
             payload.get("created_by", "worker"), payload.get("mem_type", "episodic"),
             payload.get("scope", "role"), payload["content"],
             canonical_json(payload.get("source_ids") or []),
             canonical_json(payload.get("evidence_ids") or []),
             max(0.0, min(1.0, payload.get("confidence", 0.5))),
             "OBSERVED", event.recorded_at, event.recorded_at,
             payload.get("expires_at"), len(payload["content"]),
             event.event_id, event.recorded_at))
    elif event.event_type == "memory.transition":
        row = connection.execute(
            "SELECT content FROM tier_c_memories WHERE memory_id=?",
            (payload["memory_id"],)).fetchone()
        connection.execute(
            "UPDATE tier_c_memories SET state=?, last_verified=?, last_event_id=?, updated_at=?, "
            "supersedes=COALESCE(?, supersedes), contradicts=COALESCE(?, contradicts) "
            "WHERE memory_id=?",
            (payload["to"], event.recorded_at, event.event_id, event.recorded_at,
             payload.get("supersedes"), payload.get("contradicts"), payload["memory_id"]))
        connection.execute(
            "INSERT INTO tier_c_memory_revisions (revision_id, memory_id, content, state, actor, reason, changed_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (event.event_id, payload["memory_id"],
             payload.get("content_before") or (row["content"] if row else ""),
             payload.get("from", ""), payload.get("actor", "memory-service"),
             payload.get("reason", ""), event.recorded_at))
    elif event.event_type == "memory.validation_passed":
        connection.execute(
            """INSERT INTO tier_c_validations (
                 memory_id, verdict_json, validated_by, validated_at, last_event_id)
               VALUES (?,?,?,?,?)
               ON CONFLICT(memory_id) DO UPDATE SET
                 verdict_json=excluded.verdict_json, validated_by=excluded.validated_by,
                 validated_at=excluded.validated_at, last_event_id=excluded.last_event_id""",
            (payload["memory_id"], canonical_json(payload.get("verdict") or {}),
             payload.get("validated_by", "curation"), event.recorded_at, event.event_id))
    elif event.event_type == "memory.quarantined":
        row = connection.execute(
            "SELECT content FROM tier_c_memories WHERE memory_id=?",
            (payload["memory_id"],)).fetchone()
        connection.execute(
            "UPDATE tier_c_memories SET state='ARCHIVED', last_verified=?, last_event_id=?, updated_at=? "
            "WHERE memory_id=?",
            (event.recorded_at, event.event_id, event.recorded_at, payload["memory_id"]))
        connection.execute(
            "INSERT INTO tier_c_memory_revisions (revision_id, memory_id, content, state, actor, reason, changed_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (event.event_id, payload["memory_id"], row["content"] if row else "", "QUARANTINED",
             payload.get("actor", "defense"), payload.get("reason", ""), event.recorded_at))
    elif event.event_type == "block.updated":
        connection.execute(
            """INSERT INTO tier_c_blocks (
                 label, value, char_limit, read_only, description, owner, revision,
                 last_event_id, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(label) DO UPDATE SET
                 value=excluded.value, char_limit=excluded.char_limit,
                 read_only=excluded.read_only, description=excluded.description,
                 owner=excluded.owner, revision=excluded.revision,
                 last_event_id=excluded.last_event_id, updated_at=excluded.updated_at""",
            (payload["label"], payload.get("value", ""), payload.get("char_limit", 2000),
             int(bool(payload.get("read_only", False))), payload.get("description", ""),
             payload.get("owner", "owner"), payload.get("revision", 0),
             event.event_id, event.recorded_at))
        connection.execute(
            "INSERT INTO tier_c_block_revisions (revision_id, label, value, actor, reason, changed_at) "
            "VALUES (?,?,?,?,?,?)",
            (event.event_id, payload["label"], payload.get("value", ""),
             payload.get("actor", "memory-service"), payload.get("reason", ""),
             event.recorded_at))
    elif event.event_type == "procedure.created":
        connection.execute(
            """INSERT INTO tier_c_procedures (
                 procedure_id, name, description, body, trigger_terms_json, scope, state,
                 confidence, source_ids_json, evidence_ids_json, mission_id, created_by,
                 created_at, last_verified, expires_at, last_event_id, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(procedure_id) DO UPDATE SET
                 body=excluded.body, state=excluded.state, confidence=excluded.confidence,
                 last_event_id=excluded.last_event_id, updated_at=excluded.updated_at""",
            (payload["procedure_id"], payload.get("name", ""), payload.get("description", ""),
             payload.get("body", ""), canonical_json(payload.get("trigger_terms") or []),
             payload.get("scope", "role"), payload.get("state", "ACTIVE"),
             max(0.0, min(1.0, payload.get("confidence", 0.5))),
             canonical_json(payload.get("source_ids") or []),
             canonical_json(payload.get("evidence_ids") or []),
             payload.get("mission_id", ""), payload.get("created_by", "worker"),
             event.recorded_at, event.recorded_at, payload.get("expires_at"),
             event.event_id, event.recorded_at))
    elif event.event_type == "procedure.updated":
        connection.execute(
            """UPDATE tier_c_procedures SET state=?, last_verified=?, last_event_id=?, updated_at=?,
               supersedes=COALESCE(?, supersedes) WHERE procedure_id=?""",
            (payload.get("state", "ACTIVE"), event.recorded_at, event.event_id,
             event.recorded_at, payload.get("supersedes"), payload["procedure_id"]))
    elif event.event_type == "memory.alias.bound":
        alias = str(payload["alias"]).strip().lower()
        connection.execute(
            """INSERT INTO tier_c_project_aliases(
                 alias, resource_id, alias_kind, active, last_event_id, updated_at)
               VALUES (?, ?, ?, 1, ?, ?)
               ON CONFLICT(alias) DO UPDATE SET
                 resource_id=excluded.resource_id, alias_kind=excluded.alias_kind,
                 active=1, last_event_id=excluded.last_event_id,
                 updated_at=excluded.updated_at""",
            (alias, payload["resource_id"], payload.get("alias_kind"),
             event.event_id, event.recorded_at))
    # ---- V9 World Model (Lane 3): event-derived understanding ----
    elif event.event_type == "world.entity_recorded":
        connection.execute(
            """INSERT INTO world_entities(
                entity_id, entity_type, display_name, state, attributes_json,
                valid_from, valid_until, observed_at, superseded_at,
                last_verified, stale_after, due_at, resolved_at,
                provenance_json, confidence, salience, authority, record_kind,
                supersedes, derived_from_json, source_event_id, last_event_id,
                version, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(entity_id) DO UPDATE SET
                entity_type=excluded.entity_type, display_name=excluded.display_name,
                state=excluded.state, attributes_json=excluded.attributes_json,
                valid_from=excluded.valid_from, valid_until=excluded.valid_until,
                observed_at=excluded.observed_at, superseded_at=excluded.superseded_at,
                last_verified=excluded.last_verified, stale_after=excluded.stale_after,
                due_at=excluded.due_at, resolved_at=excluded.resolved_at,
                provenance_json=excluded.provenance_json, confidence=excluded.confidence,
                salience=excluded.salience, authority=excluded.authority,
                record_kind=excluded.record_kind, supersedes=excluded.supersedes,
                derived_from_json=excluded.derived_from_json,
                last_event_id=excluded.last_event_id,
                version=excluded.version, updated_at=excluded.updated_at""",
            (
                payload["entity_id"], payload["entity_type"], payload["display_name"],
                payload.get("state", "ACTIVE"), canonical_json(payload.get("attributes") or {}),
                payload.get("valid_from", event.recorded_at), payload.get("valid_until"),
                payload.get("observed_at", event.recorded_at), payload.get("superseded_at"),
                payload.get("last_verified"), payload.get("stale_after"),
                payload.get("due_at"), payload.get("resolved_at"),
                canonical_json(payload.get("provenance") or {}),
                float(payload.get("confidence", 0.5)), float(payload.get("salience", 0.5)),
                payload.get("authority", "DERIVED"), payload.get("record_kind", "FACT"),
                payload.get("supersedes"), canonical_json(payload.get("derived_from") or []),
                payload.get("source_event_id", event.event_id), event.event_id,
                int(payload.get("version", 1)), event.recorded_at, event.recorded_at,
            ),
        )
    elif event.event_type == "world.entity_state_changed":
        row = connection.execute(
            "SELECT entity_id FROM world_entities WHERE entity_id = ?",
            (payload["entity_id"],)).fetchone()
        if row is None:
            raise ValueError(f"world.entity_state_changed: unknown entity {payload.get('entity_id')}")
        updates: dict[str, object] = {"last_event_id": event.event_id, "updated_at": event.recorded_at}
        for key in ("state", "superseded_at", "last_verified", "stale_after", "due_at",
                     "resolved_at", "confidence", "salience", "supersedes", "valid_until"):
            if key in payload:
                updates[key] = payload[key]
        if "attributes" in payload:
            updates["attributes_json"] = canonical_json(payload["attributes"])
        if "provenance" in payload:
            updates["provenance_json"] = canonical_json(payload["provenance"])
        if updates:
            sets = ", ".join(f"{k} = ?" for k in updates)
            connection.execute(
                f"UPDATE world_entities SET {sets} WHERE entity_id = ?",
                tuple(updates.values()) + (payload["entity_id"],),
            )
            connection.execute(
                "UPDATE world_entities SET version = version + 1 WHERE entity_id = ?",
                (payload["entity_id"],),
            )
    elif event.event_type == "world.relationship_recorded":
        connection.execute(
            """INSERT INTO world_relationships(
                relationship_id, source_id, target_id, relationship_type,
                strength, valid_from, valid_until, observed_at,
                provenance_json, confidence, source_event_id, last_event_id,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(relationship_id) DO UPDATE SET
                strength=excluded.strength, valid_from=excluded.valid_from,
                valid_until=excluded.valid_until, observed_at=excluded.observed_at,
                provenance_json=excluded.provenance_json, confidence=excluded.confidence,
                last_event_id=excluded.last_event_id, updated_at=excluded.updated_at""",
            (
                payload["relationship_id"], payload["source_id"], payload["target_id"],
                payload["relationship_type"], float(payload.get("strength", 1.0)),
                payload.get("valid_from", event.recorded_at), payload.get("valid_until"),
                payload.get("observed_at", event.recorded_at),
                canonical_json(payload.get("provenance") or {}),
                float(payload.get("confidence", 0.5)),
                payload.get("source_event_id", event.event_id), event.event_id,
                event.recorded_at, event.recorded_at,
            ),
        )
    elif event.event_type == "world.observation_recorded":
        connection.execute(
            """INSERT INTO world_observations(
                observation_id, source_kind, source_ref, observed_at,
                payload_json, normalized_json, entities_json, relationships_json,
                provenance_json, processed, last_event_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(observation_id) DO UPDATE SET
                normalized_json=excluded.normalized_json,
                entities_json=excluded.entities_json,
                relationships_json=excluded.relationships_json,
                provenance_json=excluded.provenance_json,
                processed=excluded.processed,
                last_event_id=excluded.last_event_id, updated_at=excluded.updated_at""",
            (
                payload["observation_id"], payload["source_kind"], payload["source_ref"],
                payload.get("observed_at", event.recorded_at),
                canonical_json(payload.get("payload") or {}),
                canonical_json(payload.get("normalized") or {}),
                canonical_json(payload.get("entities") or []),
                canonical_json(payload.get("relationships") or []),
                canonical_json(payload.get("provenance") or {}),
                int(bool(payload.get("processed", 0))),
                event.event_id, event.recorded_at, event.recorded_at,
            ),
        )
    elif event.event_type == "world.signal_recorded":
        connection.execute(
            """INSERT INTO world_signals(
                signal_id, signal_kind, salience, state, title, summary,
                affected_entities_json, evidence_json, provenance_json,
                confidence, importance, urgency, dedupe_key, supersedes,
                resolved_at, stale_after, due_at, recommended_action,
                last_event_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(signal_id) DO UPDATE SET
                salience=excluded.salience, state=excluded.state,
                title=excluded.title, summary=excluded.summary,
                affected_entities_json=excluded.affected_entities_json,
                evidence_json=excluded.evidence_json,
                provenance_json=excluded.provenance_json,
                confidence=excluded.confidence, importance=excluded.importance,
                urgency=excluded.urgency, dedupe_key=excluded.dedupe_key,
                supersedes=excluded.supersedes, resolved_at=excluded.resolved_at,
                stale_after=excluded.stale_after, due_at=excluded.due_at,
                recommended_action=excluded.recommended_action,
                last_event_id=excluded.last_event_id, updated_at=excluded.updated_at""",
            (
                payload["signal_id"], payload["signal_kind"], payload["salience"],
                payload.get("state", "OPEN"), payload["title"],
                payload.get("summary", ""),
                canonical_json(payload.get("affected_entities") or []),
                canonical_json(payload.get("evidence") or []),
                canonical_json(payload.get("provenance") or {}),
                float(payload.get("confidence", 0.5)), float(payload.get("importance", 0.5)),
                float(payload.get("urgency", 0.5)), payload["dedupe_key"],
                payload.get("supersedes"), payload.get("resolved_at"),
                payload.get("stale_after"), payload.get("due_at"),
                payload.get("recommended_action"),
                event.event_id, event.recorded_at, event.recorded_at,
            ),
        )
    elif event.event_type == "world.signal_state_changed":
        row = connection.execute(
            "SELECT signal_id FROM world_signals WHERE signal_id = ?",
            (payload["signal_id"],)).fetchone()
        if row is None:
            raise ValueError(f"world.signal_state_changed: unknown signal {payload.get('signal_id')}")
        connection.execute(
            """UPDATE world_signals SET state = ?, resolved_at = COALESCE(?, resolved_at),
               supersedes = COALESCE(?, supersedes), last_event_id = ?, updated_at = ?
               WHERE signal_id = ?""",
            (payload["state"], payload.get("resolved_at"), payload.get("supersedes"),
             event.event_id, event.recorded_at, payload["signal_id"]),
        )
    elif event.event_type == "world.prediction_recorded":
        connection.execute(
            """INSERT INTO world_predictions(
                prediction_id, subject_type, subject_id, prediction_kind,
                predicted_value_json, confidence, predicted_at, horizon_until,
                outcome, outcome_value_json, outcome_at, calibration_delta,
                provenance_json, last_event_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(prediction_id) DO UPDATE SET
                predicted_value_json=excluded.predicted_value_json,
                confidence=excluded.confidence, horizon_until=excluded.horizon_until,
                outcome=COALESCE(excluded.outcome, world_predictions.outcome),
                outcome_value_json=excluded.outcome_value_json,
                outcome_at=excluded.outcome_at, calibration_delta=excluded.calibration_delta,
                provenance_json=excluded.provenance_json,
                last_event_id=excluded.last_event_id, updated_at=excluded.updated_at""",
            (
                payload["prediction_id"], payload["subject_type"], payload["subject_id"],
                payload["prediction_kind"], canonical_json(payload.get("predicted_value") or {}),
                float(payload.get("confidence", 0.5)), payload.get("predicted_at", event.recorded_at),
                payload.get("horizon_until"), payload.get("outcome", "PENDING"),
                canonical_json(payload.get("outcome_value") or {}),
                payload.get("outcome_at"), payload.get("calibration_delta"),
                canonical_json(payload.get("provenance") or {}),
                event.event_id, event.recorded_at, event.recorded_at,
            ),
        )
    elif event.event_type == "world.prediction_outcome_recorded":
        row = connection.execute(
            "SELECT prediction_id FROM world_predictions WHERE prediction_id = ?",
            (payload["prediction_id"],)).fetchone()
        if row is None:
            raise ValueError(f"world.prediction_outcome_recorded: unknown prediction {payload.get('prediction_id')}")
        connection.execute(
            """UPDATE world_predictions SET outcome = ?, outcome_value_json = ?,
               outcome_at = ?, calibration_delta = ?, last_event_id = ?, updated_at = ?
               WHERE prediction_id = ?""",
            (payload["outcome"], canonical_json(payload.get("outcome_value") or {}),
             payload.get("outcome_at", event.recorded_at), payload.get("calibration_delta"),
             event.event_id, event.recorded_at, payload["prediction_id"]),
        )
    elif event.event_type == "world.knowledge_gap_recorded":
        connection.execute(
            """INSERT INTO world_knowledge_gaps(
                gap_id, gap_kind, subject_type, subject_id, description,
                blocking, resolved, resolved_by, last_event_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(gap_id) DO UPDATE SET
                description=excluded.description, blocking=excluded.blocking,
                resolved=excluded.resolved, resolved_by=excluded.resolved_by,
                last_event_id=excluded.last_event_id, updated_at=excluded.updated_at""",
            (
                payload["gap_id"], payload["gap_kind"], payload.get("subject_type"),
                payload.get("subject_id"), payload["description"],
                int(bool(payload.get("blocking", 0))), int(bool(payload.get("resolved", 0))),
                payload.get("resolved_by"), event.event_id, event.recorded_at, event.recorded_at,
            ),
        )
    elif event.event_type == "world.pattern_recorded":
        connection.execute(
            """INSERT INTO world_patterns(
                pattern_id, pattern_kind, description, occurrences,
                first_seen, last_seen, evidence_json, confidence,
                last_event_id, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(pattern_id) DO UPDATE SET
                occurrences=excluded.occurrences, last_seen=excluded.last_seen,
                evidence_json=excluded.evidence_json, confidence=excluded.confidence,
                last_event_id=excluded.last_event_id, updated_at=excluded.updated_at""",
            (
                payload["pattern_id"], payload["pattern_kind"], payload["description"],
                int(payload.get("occurrences", 1)),
                payload.get("first_seen", event.recorded_at),
                payload.get("last_seen", event.recorded_at),
                canonical_json(payload.get("evidence") or []),
                float(payload.get("confidence", 0.5)),
                event.event_id, event.recorded_at, event.recorded_at,
            ),
        )
    elif event.event_type == "world.calibration_recorded":
        connection.execute(
            """INSERT INTO world_calibration(
                calibration_id, subject_type, subject_id,
                predictions_total, predictions_confirmed, predictions_refuted,
                calibration_score, adjustment, last_event_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(calibration_id) DO UPDATE SET
                predictions_total=excluded.predictions_total,
                predictions_confirmed=excluded.predictions_confirmed,
                predictions_refuted=excluded.predictions_refuted,
                calibration_score=excluded.calibration_score,
                adjustment=excluded.adjustment,
                last_event_id=excluded.last_event_id, updated_at=excluded.updated_at""",
            (
                payload["calibration_id"], payload["subject_type"], payload["subject_id"],
                int(payload.get("predictions_total", 0)),
                int(payload.get("predictions_confirmed", 0)),
                int(payload.get("predictions_refuted", 0)),
                float(payload.get("calibration_score", 0.0)),
                float(payload.get("adjustment", 0.0)),
                event.event_id, event.recorded_at,
            ),
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
