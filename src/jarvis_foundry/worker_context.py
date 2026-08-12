"""WP11 slice E (V2-OPERATIONAL): real worker context manifests.

deliver_worker_context assembles a bounded context payload for a real
worker run: the knowledge manifest (active/stale claims + conflicts)
plus the matching vault notes (text included, budget-capped with
EXPLICIT truncation), records manifest hash + sources + truncation
durably, and emits worker.manifest_delivered.

reconstruct_context rebuilds the EXACT payload from the recorded
sources (DB claims + vault files — NO chat history) and compares the
hash: a replacement worker reconstructs identical context while the
sources are unchanged; a changed source is detected honestly.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from pathlib import Path
from typing import Any

from .database import FoundryDatabase
from .events import Event, canonical_json
from .knowledge_retrieval import build_context_manifest
from .vault_retrieval import retrieve_vault


def _payload_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        canonical_json(payload).encode("utf-8")).hexdigest()


def _note_texts(vault_root: Path, rel_paths: list[str],
                budget_chars: int) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Read the matched notes (bounded by budget); explicit truncation."""
    root = vault_root.resolve()
    notes: list[dict[str, Any]] = []
    used = 0
    dropped = 0
    for rel in rel_paths:
        path = (root / rel).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            dropped += 1
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            dropped += 1
            continue
        if used + len(text) > budget_chars:
            notes.append({"rel_path": rel,
                          "text": text[: max(0, budget_chars - used)],
                          "truncated": True})
            dropped += 1
            break
        notes.append({"rel_path": rel, "text": text, "truncated": False})
        used += len(text)
    return notes, {"notes_truncated": any(n["truncated"] for n in notes),
                   "notes_dropped": dropped}


def _build_payload(db: FoundryDatabase, vault_root: Path, *,
                   mission_id: str, objective: str,
                   max_chars: int) -> dict[str, Any]:
    with db.connect() as connection:
        manifest = build_context_manifest(connection, mission_id, objective,
                                          max_chars=max_chars)
        vault_result = retrieve_vault(connection, vault_root, objective,
                                      mission_id=mission_id, limit=20)
    manifest_payload = manifest.as_dict()
    # generated_at is delivery metadata, not context — strip it so the
    # payload hash is deterministic over (objective, DB, vault)
    manifest_payload.pop("generated_at", None)
    notes, note_truncation = _note_texts(
        vault_root, [n["rel_path"] for n in vault_result["notes"]],
        max(0, max_chars - len(json.dumps(manifest_payload))))
    sources = {
        "claims": sorted({c.get("source_event_id") or c["claim_id"]
                          for c in vault_result["claims"]}),
        "notes": sorted(n["rel_path"] for n in vault_result["notes"]),
    }
    truncation = {
        "claims_truncated": manifest.truncated,
        "notes_truncated": note_truncation["notes_truncated"],
        "notes_dropped": note_truncation["notes_dropped"],
    }
    return {
        "objective": objective, "mission_id": mission_id,
        "claims": manifest_payload, "notes": notes,
        "sources": sources, "truncation": truncation,
    }


def deliver_worker_context(db: FoundryDatabase, vault_root: str | Path, *,
                           run_id: str, worker: str, mission_id: str,
                           objective: str, max_chars: int = 8000,
                           source_id: str = "worker-context",
                           ) -> dict[str, Any]:
    """Deliver a bounded context manifest for a real worker run.

    Records the delivery (row + event) and returns the record with the
    manifest hash.
    """
    payload = _build_payload(db, Path(vault_root), mission_id=mission_id,
                             objective=objective, max_chars=max_chars)
    manifest_hash = _payload_hash(payload)
    manifest_id = f"wm-{uuid.uuid4().hex[:12]}"
    record = {
        "manifest_id": manifest_id, "run_id": run_id, "worker": worker,
        "mission_id": mission_id, "objective": objective,
        "manifest_hash": manifest_hash,
        "sources_json": canonical_json(payload["sources"]),
        "truncation_json": canonical_json(payload["truncation"]),
    }
    db.append(Event.create(
        "worker.manifest_delivered",
        {"manifest_id": manifest_id, "run_id": run_id, "worker": worker,
         "mission_id": mission_id, "objective": objective,
         "manifest_hash": manifest_hash,
         "sources": payload["sources"], "truncation": payload["truncation"]},
        source_type="supervisor", source_id=source_id,
        idempotency_key=f"worker.manifest_delivered:{manifest_id}"))
    return record


def reconstruct_context(db: FoundryDatabase, vault_root: str | Path, *,
                        run_id: str, max_chars: int = 8000,
                        ) -> dict[str, Any]:
    """Rebuild the exact payload from the recorded sources (no chat
    history) and compare hashes. matches_recorded=False means a source
    changed since delivery — a replacement worker's context would differ.
    """
    with db.connect() as connection:
        row = connection.execute(
            "SELECT manifest_id, run_id, worker, mission_id, objective, "
            "manifest_hash, sources_json FROM worker_manifests "
            "WHERE run_id = ? ORDER BY rowid DESC LIMIT 1", (run_id,)).fetchone()
    if row is None:
        raise ValueError(f"no manifest delivered for run {run_id}")
    payload = _build_payload(db, Path(vault_root), mission_id=row["mission_id"],
                             objective=row["objective"], max_chars=max_chars)
    rebuilt_hash = _payload_hash(payload)
    return {"manifest_hash": rebuilt_hash,
            "matches_recorded": rebuilt_hash == row["manifest_hash"]}
