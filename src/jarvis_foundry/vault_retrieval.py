"""WP11 slice B (V2-OPERATIONAL): whole-vault retrieval.

Deterministic + bounded:
- one envelope for SQLite knowledge claims AND real vault notes (read
  via the vault index allowlist — never raw user paths);
- claims are token-filtered (ALL query tokens must match the statement),
  mission/entity/type/confidence/source/time filters, confidence-ordered,
  capped at limit;
- notes match on the same AND-token rule over the note text, scored by
  matched-token count, capped; every note carries provenance (path +
  content sha256) and a stale flag (file changed since indexing);
- every claim carries support (SUPPORTED when its source event points at
  an ACTIVE indexed file) and its contradiction rows — the envelope
  never hides stale knowledge or contradictions (gate criteria 3-5);
- evaluate_retrieval measures precision/recall/FP/FN/cross-mission
  leakage over labeled probes.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from .knowledge import canonicalize_statement
from .knowledge_entities import claims_for_entity, resolve_entity

_TOKEN_SPLIT = re.compile(r"[^a-z0-9]+")

_CLAIM_COLUMNS = ("claim_id, mission_id, statement, claim_type, confidence, "
                  "fresh_until, source_run_id, source_event_id, status, "
                  "updated_at")


def _tokens(text: str) -> set[str]:
    return {t for t in _TOKEN_SPLIT.split(canonicalize_statement(text)) if t}


def note_support(connection: sqlite3.Connection, claim: dict[str, Any]) -> str:
    """SUPPORTED when the claim's source event maps to an ACTIVE indexed
    vault file; else UNSUPPORTED (every claim is either supported or
    marked unsupported — gate criterion 3)."""
    source_event = claim.get("source_event_id")
    if not source_event:
        return "UNSUPPORTED"
    rel_path = source_event.split(":", 1)[0]
    row = connection.execute(
        "SELECT status FROM vault_files WHERE rel_path = ?",
        (rel_path,)).fetchone()
    if row is None or row[0] != "ACTIVE":
        return "UNSUPPORTED"
    return "SUPPORTED"


def _claim_contradictions(connection: sqlite3.Connection,
                          claim_id: str) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT contradiction_id, claim_a_id, claim_b_id, claim_type, reason "
        "FROM knowledge_contradictions WHERE claim_a_id = ? OR claim_b_id = ? "
        "ORDER BY rowid", (claim_id, claim_id)).fetchall()
    return [dict(r) for r in rows]


def _claim_matches(claim: dict[str, Any], wanted: set[str], *,
                   mission_id: str | None, entity: str | None,
                   claim_type: str | None, min_confidence: float | None,
                   source: str | None, time_from: str | None,
                   time_to: str | None, connection: sqlite3.Connection) -> bool:
    if mission_id and claim["mission_id"] != mission_id:
        return False
    if claim_type and claim["claim_type"] != claim_type:
        return False
    if min_confidence is not None and (claim["confidence"] or 0.0) < min_confidence:
        return False
    if source and not (claim["source_run_id"] or "").startswith(source):
        return False
    if time_from or time_to:
        anchor = claim.get("updated_at") or ""
        if time_from and anchor < time_from:
            return False
        if time_to and anchor > time_to:
            return False
    if entity:
        entity_id = resolve_entity(connection, entity)
        if entity_id is None or claim["claim_id"] not in claims_for_entity(
                connection, entity_id):
            return False
    if wanted and not wanted <= _tokens(claim["statement"]):
        return False
    return True


def retrieve_vault(connection: sqlite3.Connection, vault_root: str | Path,
                   query: str, *, mission_id: str | None = None,
                   entity: str | None = None, claim_type: str | None = None,
                   min_confidence: float | None = None,
                   source: str | None = None, time_from: str | None = None,
                   time_to: str | None = None,
                   limit: int = 50) -> dict[str, Any]:
    """Bounded retrieval envelope: claims + matching vault notes."""
    limit = max(1, min(int(limit), 500))
    root = Path(vault_root).resolve()
    wanted = _tokens(query)

    raw = connection.execute(
        f"SELECT {_CLAIM_COLUMNS} FROM knowledge_claims").fetchall()
    claims = []
    for row in raw:
        claim = dict(row)
        if _claim_matches(claim, wanted, mission_id=mission_id, entity=entity,
                          claim_type=claim_type, min_confidence=min_confidence,
                          source=source, time_from=time_from, time_to=time_to,
                          connection=connection):
            claims.append(claim)
    claims.sort(key=lambda c: (-(c["confidence"] or 0.0), c["claim_id"]))
    claims = claims[:limit]

    enriched = [{
        **claim,
        "support": note_support(connection, claim),
        "contradictions": _claim_contradictions(connection, claim["claim_id"]),
    } for claim in claims]

    notes = []
    note_rows = connection.execute(
        "SELECT rel_path, content_sha256, mtime FROM vault_files "
        "WHERE status = 'ACTIVE' ORDER BY rel_path LIMIT 2000").fetchall()
    for rel_path, digest, indexed_mtime in note_rows:
        path = (root / rel_path).resolve()
        # the index allowlist is the ONLY source of paths — a row pointing
        # outside the vault root is excluded, never read
        if not path.is_relative_to(root) or not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        matched = wanted & _tokens(text)
        if len(matched) != len(wanted):
            continue
        try:
            current_mtime = str(path.stat().st_mtime_ns)
        except OSError:
            current_mtime = indexed_mtime
        notes.append({
            "rel_path": rel_path, "content_sha256": digest,
            "matched_tokens": sorted(matched), "score": len(matched),
            "stale": current_mtime != indexed_mtime,
        })
    notes.sort(key=lambda n: (-n["score"], n["rel_path"]))
    notes = notes[:limit]

    return {"query": query, "claims": enriched, "notes": notes,
            "claim_count": len(enriched), "note_count": len(notes),
            "truncated": len(enriched) + len(notes) >= limit}


def evaluate_retrieval(connection: sqlite3.Connection, vault_root: str | Path,
                       probes: list[dict[str, Any]]) -> dict[str, Any]:
    """Precision/recall/FP/FN/cross-mission-leakage over labeled probes.

    probe = {"query", "mission_id", "relevant_claims": [ids],
             "relevant_notes": [rel_paths]}
    """
    true_positives = false_positives = false_negatives = leakage = 0
    for probe in probes:
        result = retrieve_vault(connection, vault_root, probe["query"],
                                mission_id=probe.get("mission_id"), limit=100)
        returned_ids = {c["claim_id"] for c in result["claims"]}
        returned_paths = {n["rel_path"] for n in result["notes"]}
        relevant = set(probe.get("relevant_claims", []))
        relevant_notes = set(probe.get("relevant_notes", []))
        true_positives += (len(returned_ids & relevant)
                           + len(returned_paths & relevant_notes))
        false_positives += (len(returned_ids - relevant)
                            + len(returned_paths - relevant_notes))
        false_negatives += (len(relevant - returned_ids)
                            + len(relevant_notes - returned_paths))
        for claim in result["claims"]:
            if probe.get("mission_id") and claim["mission_id"] != probe["mission_id"]:
                leakage += 1
    total = true_positives + false_positives
    precision = true_positives / total if total else 0.0
    total = true_positives + false_negatives
    recall = true_positives / total if total else 0.0
    return {"precision": round(precision, 4), "recall": round(recall, 4),
            "true_positives": true_positives,
            "false_positives": false_positives,
            "false_negatives": false_negatives,
            "cross_mission_leakage": leakage}
