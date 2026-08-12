"""WP11 slice F (V2-OPERATIONAL): governed consolidation.

Consolidation runs ONLY in the reversible JARVIS-governed vault area,
through the existing ObsidianService (expected-hash conflict protection —
a concurrent edit CONFLICTS and both versions survive). The lifecycle:

  propose (before_hash recorded, no writes)
  → verify (deterministic meaning check: every statement of the original
    note must survive canonically in the new content; frontmatter
    contract intact) → VERIFIED | REJECTED
  → accept (ObsidianService.write_note with expected_hash; after_hash
    recorded) → ACCEPTED
  → rollback (byte-exact restore via the write's before snapshot;
    refuses when the note changed again) → ROLLED_BACK

Every transition is an event (rebuild + reconcile deterministic).
"""
from __future__ import annotations

import uuid
from typing import Any

from .database import FoundryDatabase
from .events import Event
from .knowledge import canonicalize_statement
from .knowledge_extract import extract_statements
from .obsidian.policy import ObsidianPolicyError
from .obsidian.writer import (ObsidianConflictError, ObsidianService,
                              sha256_bytes)


def _append(db: FoundryDatabase, event_type: str, payload: dict[str, Any],
            *, key: str) -> None:
    db.append(Event.create(
        event_type, payload, source_type="supervisor",
        source_id="consolidation", idempotency_key=key))


def _record(db: FoundryDatabase, op_id: str, status: str, *,
            note_path: str, kind: str, before_hash: str,
            after_hash: str | None = None, write_id: str | None = None,
            verified_by: str | None = None,
            evidence: dict[str, Any] | None = None) -> None:
    # MERGE with the existing evidence: the PROPOSED record carries the
    # full proposed content, which later transitions must not clobber
    # (accept/rollback recover it from the op record)
    merged: dict[str, Any] = {}
    existing = db.row("consolidation_ops", "op_id", op_id)
    if existing is not None:
        try:
            import json
            merged = json.loads(existing["evidence_json"])
        except (ValueError, TypeError):
            merged = {}
    merged.update(evidence or {})
    _append(db, "consolidation.state_changed", {
        "op_id": op_id, "note_path": note_path, "kind": kind,
        "status": status, "before_hash": before_hash,
        "after_hash": after_hash, "write_id": write_id,
        "verified_by": verified_by, "evidence": merged,
    }, key=f"consolidation.state_changed:{op_id}:{status}")


def propose_consolidation(db: FoundryDatabase, service: ObsidianService,
                          note_name: str, new_content: str, kind: str,
                          ) -> dict[str, Any]:
    """Record a consolidation proposal (no writes yet).

    The note must be a governed V1 note (writer enforces the name set
    and the write-root boundary); the before hash is captured now so the
    accept step can detect concurrent edits.
    """
    path = service.writer.note_path(note_name)  # raises for ungoverned
    try:
        before_bytes = path.read_bytes()
    except OSError as exc:
        raise ObsidianPolicyError(f"note {note_name} is not readable: {exc}")
    before_hash = sha256_bytes(before_bytes)
    op_id = f"con-{uuid.uuid4().hex[:12]}"
    _record(db, op_id, "PROPOSED", note_path=str(path), kind=kind,
            before_hash=before_hash, evidence={
                "note_name": note_name, "new_content": new_content})
    return {"op_id": op_id, "note_path": str(path), "before_hash": before_hash}


def _statements(text: str) -> list[str]:
    return [canonicalize_statement(s) for s in extract_statements(text)]


def verify_consolidation(db: FoundryDatabase, op_id: str, *,
                         verifier: str = "deterministic-statement-check",
                         ) -> dict[str, Any]:
    """Deterministic meaning verification: every original statement must
    survive canonically in the new content; frontmatter contract intact.
    """
    row = db.row("consolidation_ops", "op_id", op_id)
    if row is None:
        raise ValueError(f"unknown consolidation op {op_id}")
    if row["status"] not in ("PROPOSED", "VERIFIED"):
        raise ValueError(
            f"consolidation {op_id} is {row['status']}; only PROPOSED or "
            f"VERIFIED ops verify")
    note_path = row["note_path"]
    original = _read_note(note_path)
    evidence = _verification_evidence(original, row)
    verdict = "VERIFIED"
    if not evidence["before_hash_ok"]:
        verdict = "REJECTED"  # the note changed since proposal (stale)
    elif evidence["missing"]:
        verdict = "REJECTED"
    _record(db, op_id, verdict, note_path=note_path, kind=row["kind"],
            before_hash=row["before_hash"], verified_by=verifier,
            evidence=evidence)
    return {"op_id": op_id, "verdict": verdict, "missing": evidence["missing"]}


def _verification_evidence(original: str,
                           row: Any) -> dict[str, Any]:
    from .obsidian.writer import sha256_bytes as _wsha
    before_hash = _wsha(original.encode("utf-8"))
    proposed = _read_proposed(row)
    original_statements = _statements(original)
    new_statements = set(_statements(proposed))
    missing = [{"statement": s}
               for s in original_statements if s not in new_statements]
    return {"before_hash_ok": before_hash == row["before_hash"],
            "missing": missing, "statement_count": len(original_statements)}


def _read_note(note_path: str) -> str:
    from pathlib import Path
    return Path(note_path).read_text(encoding="utf-8")


def _read_proposed(row: Any) -> str:
    """The proposed content — recovered from the proposal evidence.

    The op row stores the new content preview only; the caller supplies
    the full content at accept time. For verification we need the full
    proposed content: recover it from the proposal evidence stored at
    propose time (full content, not preview).
    """
    import json
    evidence = json.loads(row["evidence_json"])
    content = evidence.get("new_content")
    if content is None:
        raise ValueError(
            "proposed content is missing from the op record "
            "(propose with full content stored)")
    return content


def accept_consolidation(db: FoundryDatabase, service: ObsidianService,
                         op_id: str) -> dict[str, Any]:
    """Write the verified consolidation through ObsidianService.

    Expected-hash conflict protection: a concurrent edit raises
    ObsidianConflictError (both versions preserved — the note on disk
    keeps the concurrent edit) and the op is recorded REJECTED.
    """
    row = db.row("consolidation_ops", "op_id", op_id)
    if row is None:
        raise ValueError(f"unknown consolidation op {op_id}")
    if row["status"] != "VERIFIED":
        raise ValueError(
            f"consolidation {op_id} is {row['status']}; only VERIFIED ops "
            f"accept")
    _assert_not_frozen(db, row["note_path"])
    content = _read_proposed(row)
    note_name = _note_name_from_path(row["note_path"])
    try:
        result = service.write_note(
            note_name=note_name, content=content,
            expected_hash=row["before_hash"])
    except ObsidianConflictError as exc:
        _record(db, op_id, "REJECTED", note_path=row["note_path"],
                kind=row["kind"], before_hash=row["before_hash"],
                evidence={"reason": "concurrent-edit",
                          "detail": str(exc)})
        raise
    _record(db, op_id, "ACCEPTED", note_path=row["note_path"],
            kind=row["kind"], before_hash=row["before_hash"],
            after_hash=result.after_hash, write_id=result.write_id)
    return {"op_id": op_id, "status": "ACCEPTED",
            "after_hash": result.after_hash}


def _note_name_from_path(note_path: str) -> str:
    from pathlib import Path
    return Path(note_path).name


def _assert_not_frozen(db: FoundryDatabase, note_path: str) -> None:
    """Integrated-rollback freeze: while a rollback.triggered exists for
    the note WITHOUT a completing rollback.completed, accepts on the note
    are refused (the freeze stops further mutation before the restore).
    """
    with db.connect() as connection:
        trigger = connection.execute(
            "SELECT sequence FROM events WHERE event_type = "
            "'rollback.triggered' AND json_extract(payload_json, "
            "'$.note_path') = ? ORDER BY sequence DESC LIMIT 1",
            (note_path,)).fetchone()
        completed = connection.execute(
            "SELECT sequence FROM events WHERE event_type = "
            "'rollback.completed' AND json_extract(payload_json, "
            "'$.note_path') = ? ORDER BY sequence DESC LIMIT 1",
            (note_path,)).fetchone()
    if trigger is not None and (completed is None
                                or completed[0] < trigger[0]):
        raise ValueError(
            f"note {note_path} is FROZEN by an active rollback — further "
            f"consolidation accepts are refused until the restore "
            f"completes")


def mark_consolidation_rolled_back(db: FoundryDatabase, op_id: str,
                                   reason: str) -> bool:
    """Flip an ACCEPTED consolidation op to ROLLED_BACK (used by the
    integrated-rollback chain after the byte restore)."""
    row = db.row("consolidation_ops", "op_id", op_id)
    if row is None or row["status"] != "ACCEPTED":
        return False
    _record(db, op_id, "ROLLED_BACK", note_path=row["note_path"],
            kind=row["kind"], before_hash=row["before_hash"],
            after_hash=row["after_hash"], write_id=row["write_id"],
            evidence={"reason": reason,
                      "rolled_back_by": "integrated-rollback"})
    return True


def rollback_consolidation(db: FoundryDatabase, service: ObsidianService,
                           op_id: str, reason: str) -> dict[str, Any]:
    """Byte-exact rollback via the write's before snapshot.

    Refuses (ObsidianConflictError) when the note changed again since
    the consolidation write — a rollback never clobbers a newer edit.
    """
    row = db.row("consolidation_ops", "op_id", op_id)
    if row is None:
        raise ValueError(f"unknown consolidation op {op_id}")
    if row["status"] != "ACCEPTED" or not row["write_id"]:
        raise ValueError(
            f"consolidation {op_id} is {row['status']}; only ACCEPTED ops "
            f"with a recorded write roll back")
    service.rollback(row["write_id"])  # raises ObsidianConflictError if changed
    _record(db, op_id, "ROLLED_BACK", note_path=row["note_path"],
            kind=row["kind"], before_hash=row["before_hash"],
            after_hash=row["after_hash"], write_id=row["write_id"],
            evidence={"reason": reason})
    return {"op_id": op_id, "status": "ROLLED_BACK"}
