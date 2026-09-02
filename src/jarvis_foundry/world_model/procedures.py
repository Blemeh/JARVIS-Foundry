"""Procedural knowledge - recognize repeated successful operational procedures."""
from __future__ import annotations

import json
from typing import Any

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.world_model.facade import WorldModel


def detect_procedures(db: FoundryDatabase, wm: WorldModel | None = None, min_occurrences: int = 2) -> list[dict[str, Any]]:
    """Detect repeated successful procedures that should become PROCEDURE knowledge.

    Looks at: successful mission completions with same capability/pattern,
    recovery successes, and AcceptanceGate-accepted verifications.  A raw
    ``verification.passed`` event is preliminary and is intentionally ignored.

    This lane owns UNDERSTANDING that the procedure exists and what evidence
    supports it. Lane 2 owns executable capability creation.
    """
    wm = wm or WorldModel(db)
    procedures: list[dict[str, Any]] = []

    with db.connect() as conn:
        # Find repeated accepted-success mission patterns. A mission.completed
        # row by itself is insufficient because AcceptanceGate may later
        # refuse or supersede the verifier identity.
        try:
            rows = conn.execute(
                "SELECT payload_json FROM events WHERE event_type = "
                "'mission.outcome_finalized' "
                "AND json_extract(payload_json, '$.status') = 'SUCCEEDED' "
                "ORDER BY sequence DESC LIMIT 100"
            ).fetchall()
            patterns: dict[str, int] = {}
            for row in rows:
                try:
                    p = json.loads(row[0])
                    obj = str(p.get("attempted", ""))[:80].lower()
                    if obj:
                        patterns[obj] = patterns.get(obj, 0) + 1
                except Exception:
                    pass
            for pattern, count in patterns.items():
                if count >= min_occurrences:
                    # Check if we already have a PROCEDURE entity for this
                    existing = [e for e in wm.entities.list(entity_type="PROCEDURE", limit=100) if pattern in e["display_name"].lower()]
                    if not existing:
                        eid = wm.entities.record(
                            "PROCEDURE", f"Procedure: {pattern}",
                            attributes={"pattern": pattern, "occurrences": count, "evidence": "repeated mission success"},
                            provenance={"kind": "DERIVED", "occurrences": count},
                            record_kind="PROCEDURE",
                            confidence=min(0.9, 0.5 + count * 0.1),
                        )
                        procedures.append({"entity_id": eid, "pattern": pattern, "occurrences": count})
        except Exception:
            pass

        # Find repeated successful verifications
        try:
            rows = conn.execute(
                "SELECT payload_json FROM events WHERE event_type = 'verification.accepted' ORDER BY sequence DESC LIMIT 50"
            ).fetchall()
            if len(rows) >= min_occurrences:
                # Generic verification procedure
                existing = [e for e in wm.entities.list(entity_type="PROCEDURE", limit=100) if "verification" in e["display_name"].lower()]
                if not existing and len(rows) >= 3:
                    eid = wm.entities.record(
                        "PROCEDURE", "Procedure: verification -> evidence -> pass",
                        attributes={"occurrences": len(rows)},
                        provenance={"kind": "DERIVED"},
                        record_kind="PROCEDURE",
                        confidence=0.6,
                    )
                    procedures.append({"entity_id": eid, "pattern": "verification", "occurrences": len(rows)})
        except Exception:
            pass

    return procedures
