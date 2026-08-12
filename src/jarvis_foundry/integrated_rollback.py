"""WP11 slice G (V2-OPERATIONAL): integrated rollback.

The full chain, orchestrated:

  snapshot (DB file backup + ledger record)
  → mutation (caller-supplied; e.g. a governed consolidation)
  → degradation detection (statement preservation of the note vs its
    pre-mutation text, or a caller-supplied detector)
  → freeze (rollback.triggered recorded; further accepts on the note
    are refused until the restore completes)
  → byte restoration (before-snapshot bytes via ObsidianService;
    refuses when the note changed again — a rollback never clobbers a
    newer edit)
  → semantic rollback of the claims derived from the note (append-only
    ROLLED_BACK flags)
  → projection rebuild + reconciliation (reconcile consistent, snapshot
    verifiable)
  → rollback.completed with before/after state

Restart-safe (gate criterion 12): a crash mid-flow (freeze recorded,
restore not done) resumes to exactly ONE restore — the mutation is NOT
re-run; a completed rollback with no newer write makes a re-run a no-op.
The live DB is never replaced while the daemon is active — the DB leg
is semantic (flags + rebuild), the file leg is byte restore.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Callable

from .database import FoundryDatabase
from .events import Event
from .knowledge_extract import extract_statements
from .knowledge_snapshots import create_snapshot, semantic_rollback
from .obsidian.writer import ObsidianService
from .reconcile import reconcile

_DEFAULT_REASON = "semantic degradation detected"


def _latest(db: FoundryDatabase, event_type: str) -> tuple[int, dict[str, Any]] | None:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT sequence, payload_json FROM events WHERE event_type = ? "
            "ORDER BY sequence DESC LIMIT 1", (event_type,)).fetchone()
    if row is None:
        return None
    import json
    return row[0], json.loads(row[1])


def _latest_for_note(db: FoundryDatabase, event_type: str,
                     note_path: str) -> tuple[int, dict[str, Any]] | None:
    import json
    with db.connect() as connection:
        row = connection.execute(
            "SELECT sequence, payload_json FROM events WHERE event_type = ? "
            "AND json_extract(payload_json, '$.note_path') = ? "
            "ORDER BY sequence DESC LIMIT 1", (event_type, note_path)).fetchone()
    if row is None:
        return None
    return row[0], json.loads(row[1])


def _latest_write_seq(db: FoundryDatabase, note_path: str) -> int | None:
    with db.connect() as connection:
        row = connection.execute(
            "SELECT e.sequence FROM events e WHERE e.event_type = "
            "'obsidian.write_completed' AND EXISTS (SELECT 1 FROM "
            "obsidian_writes w WHERE w.write_id = json_extract("
            "e.payload_json, '$.write_id') AND w.note_path = ?) "
            "ORDER BY e.sequence DESC LIMIT 1", (note_path,)).fetchone()
    return row[0] if row else None


def _statements(text: str) -> set[str]:
    from .knowledge import canonicalize_statement
    return {canonicalize_statement(s) for s in extract_statements(text)}


def _note_text(note_path: str) -> str:
    return Path(note_path).read_text(encoding="utf-8")


def _default_degrade_check(note_path: str, original_text: str) -> bool:
    """Statement preservation: any original statement missing from the
    current note is semantic degradation."""
    current = _note_text(note_path)
    original_statements = _statements(original_text)
    current_statements = _statements(current)
    return not original_statements <= current_statements


def _claims_for_note(db: FoundryDatabase, note_path: str,
                     note_name: str | None = None) -> list[str]:
    clauses = ["source_event_id = ?", "source_event_id LIKE ?"]
    args: list[str] = [note_path, f"{note_path}:%"]
    if note_name:
        clauses.append("source_event_id LIKE ?")
        args.append(f"%/{note_name}:%")
    with db.connect() as connection:
        rows = connection.execute(
            f"SELECT claim_id FROM knowledge_claims WHERE "
            f"{' OR '.join(clauses)}", args).fetchall()
    return [r[0] for r in rows]


def _record(db: FoundryDatabase, event_type: str, payload: dict[str, Any],
            *, key: str) -> None:
    db.append(Event.create(
        event_type, payload, source_type="supervisor",
        source_id="integrated-rollback", idempotency_key=key))


def run_integrated_rollback(db: FoundryDatabase, service: ObsidianService, *,
                            mission_id: str, note_name: str,
                            snapshot_dir: str | Path,
                            mutate_fn: Callable[[], Any],
                            degrade_fn: Callable[[], bool] | None = None,
                            after_freeze_fn: Callable[[], None] | None = None,
                            reason: str = _DEFAULT_REASON,
                            ) -> dict[str, Any]:
    """Run the integrated chain. Returns the report dict."""
    note_path = str(service.writer.note_path(note_name))
    trigger = _latest_for_note(db, "rollback.triggered", note_path)
    completed = _latest_for_note(db, "rollback.completed", note_path)
    latest_write = _latest_write_seq(db, note_path)

    if trigger is not None:
        trigger_seq, trigger_payload = trigger
        if completed is None or completed[0] < trigger_seq:
            # crash mid-flow: resume the restore — the mutation is NOT
            # re-run (no duplicate mutation; criterion 12)
            return _restore_and_finish(
                db, service, mission_id=mission_id, note_name=note_name,
                note_path=note_path, payload=trigger_payload, reason=reason)
        if latest_write is None or latest_write < completed[0]:
            # already restored and no new mutation since → no-op
            return {"noop": True, "degraded": False, "restored": False,
                    "reconciled": True}

    original_text = _note_text(note_path)
    snapshot = create_snapshot(db, mission_id, scope="integrated-rollback",
                               snap_dir=Path(snapshot_dir))
    snapshot_id = snapshot["snapshot_id"]

    mutate_fn()

    degraded = (degrade_fn() if degrade_fn is not None
                else _default_degrade_check(note_path, original_text))
    if not degraded:
        return {"noop": False, "degraded": False, "restored": False,
                "snapshot_id": snapshot_id, "reconciled": True}

    # freeze: record the trigger; further accepts on this note are refused
    # (consolidation.accept_consolidation honors the freeze)
    from .obsidian.writer import sha256_bytes
    claim_ids = _claims_for_note(db, note_path, note_name)
    frozen_hash = sha256_bytes(Path(note_path).read_bytes())
    rollback_id = f"rb-{uuid.uuid4().hex[:12]}"
    payload = {"rollback_id": rollback_id, "note_path": note_path,
               "note_name": note_name, "snapshot_id": snapshot_id,
               "claim_ids": claim_ids, "reason": reason,
               "frozen_hash": frozen_hash,
               "original_text": original_text}
    _record(db, "rollback.triggered", payload,
            key=f"rollback.triggered:{rollback_id}")

    if after_freeze_fn is not None:
        after_freeze_fn()

    return _restore_and_finish(db, service, mission_id=mission_id,
                               note_name=note_name, note_path=note_path,
                               payload=payload, reason=reason)


def _restore_and_finish(db: FoundryDatabase, service: ObsidianService, *,
                        mission_id: str, note_name: str, note_path: str,
                        payload: dict[str, Any], reason: str) -> dict[str, Any]:
    """Byte restoration + semantic flags + rebuild + reconciliation.

    The restore is a NEW governed write of the snapshot bytes with an
    expected-hash guard: if the note changed between the freeze and the
    restore (e.g. a human edit), ObsidianConflictError is raised and the
    newer edit is never clobbered. The ledger stays append-only — the
    restore is an honest write event, not a rewrite.
    """
    # the restore is only legitimate against the FROZEN state: any change
    # since the freeze (e.g. a human edit) raises ObsidianConflictError
    # in write_note and the newer edit is never clobbered
    expected = payload["frozen_hash"]
    original_text = payload["original_text"]
    result = service.write_note(note_name=note_name, content=original_text,
                                expected_hash=expected, mission_id=mission_id)
    restored = result.status == "COMPLETED"

    # flip any ACCEPTED consolidation ops on the note to ROLLED_BACK
    with db.connect() as connection:
        op_rows = connection.execute(
            "SELECT op_id FROM consolidation_ops WHERE note_path = ? "
            "AND status = 'ACCEPTED'", (note_path,)).fetchall()
    from .consolidation import mark_consolidation_rolled_back
    for op_row in op_rows:
        mark_consolidation_rolled_back(db, op_row[0], reason)

    # semantic rollback of the note's claims (append-only flags)
    claim_ids = payload.get("claim_ids") or _claims_for_note(db, note_path)
    rolled = 0
    if claim_ids:
        rolled = semantic_rollback(db, mission_id,
                                   payload["snapshot_id"], claim_ids, reason)

    db.rebuild_projections()
    report = reconcile(db)
    _record(db, "rollback.completed", {
        "rollback_id": payload["rollback_id"], "note_path": note_path,
        "note_name": note_name, "snapshot_id": payload["snapshot_id"],
        "restored_bytes": restored, "restore_write_id": result.write_id,
        "claims_rolled_back": rolled,
        "reconciled": report.consistent,
        "reason": reason,
    }, key=f"rollback.completed:{payload['rollback_id']}")
    return {"noop": False, "degraded": True, "restored": restored,
            "snapshot_id": payload["snapshot_id"],
            "claims_rolled_back": rolled, "reconciled": report.consistent}
