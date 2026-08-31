"""World intelligence read model - bounded projection for command center / briefing."""
from __future__ import annotations

import json
from typing import Any

from jarvis_foundry.database import FoundryDatabase


def project_world_intelligence(db: FoundryDatabase, limit: int = 50) -> dict[str, Any]:
    """Build a bounded world intelligence projection.

    Reads only the derived world tables - never scans the full ledger.
    Every query is bounded by limit.
    """
    with db.connect(readonly=True) as conn:
        # Counts
        entity_count = conn.execute("SELECT COUNT(*) FROM world_entities").fetchone()[0]
        active_entity_count = conn.execute("SELECT COUNT(*) FROM world_entities WHERE state = 'ACTIVE'").fetchone()[0]
        stale_count = conn.execute("SELECT COUNT(*) FROM world_entities WHERE state = 'STALE'").fetchone()[0]
        rel_count = conn.execute("SELECT COUNT(*) FROM world_relationships").fetchone()[0]
        signal_count = conn.execute("SELECT COUNT(*) FROM world_signals").fetchone()[0]
        active_signal_count = conn.execute("SELECT COUNT(*) FROM world_signals WHERE state IN ('OPEN', 'MONITORING', 'ACKNOWLEDGED')").fetchone()[0]
        urgent_count = conn.execute("SELECT COUNT(*) FROM world_signals WHERE salience = 'URGENT' AND state IN ('OPEN', 'MONITORING')").fetchone()[0]
        pred_count = conn.execute("SELECT COUNT(*) FROM world_predictions").fetchone()[0]
        pending_pred = conn.execute("SELECT COUNT(*) FROM world_predictions WHERE outcome = 'PENDING'").fetchone()[0]
        gap_count = conn.execute("SELECT COUNT(*) FROM world_knowledge_gaps WHERE resolved = 0").fetchone()[0]
        blocking_gap = conn.execute("SELECT COUNT(*) FROM world_knowledge_gaps WHERE blocking = 1 AND resolved = 0").fetchone()[0]
        pattern_count = conn.execute("SELECT COUNT(*) FROM world_patterns").fetchone()[0]

        # Active signals (bounded, importance-ordered)
        sig_rows = conn.execute(
            "SELECT * FROM world_signals WHERE state IN ('OPEN', 'MONITORING', 'ACKNOWLEDGED') ORDER BY urgency DESC, importance DESC LIMIT ?", (min(limit, 50),)
        ).fetchall()
        signals = []
        for row in sig_rows:
            d = dict(row)
            for key in ("affected_entities_json", "evidence_json", "provenance_json"):
                raw = d.pop(key, None)
                target = key.replace("_json", "")
                if key == "affected_entities_json":
                    target = "affected_entities"
                try:
                    d[target] = json.loads(raw) if raw else ([] if target != "provenance" else {})
                except (TypeError, ValueError):
                    d[target] = [] if target != "provenance" else {}
            signals.append(d)

        # Recent entities
        ent_rows = conn.execute("SELECT * FROM world_entities ORDER BY updated_at DESC LIMIT ?", (min(limit, 50),)).fetchall()
        entities = []
        for row in ent_rows:
            d = dict(row)
            for key in ("attributes_json", "provenance_json", "derived_from_json"):
                raw = d.pop(key, None)
                target = key.replace("_json", "")
                if key == "derived_from_json":
                    target = "derived_from"
                try:
                    d[target] = json.loads(raw) if raw else ({} if target != "derived_from" else [])
                except (TypeError, ValueError):
                    d[target] = {} if target != "derived_from" else []
            entities.append(d)

        # Pending predictions
        pred_rows = conn.execute("SELECT * FROM world_predictions WHERE outcome = 'PENDING' ORDER BY predicted_at DESC LIMIT ?", (min(limit, 20),)).fetchall()
        predictions = []
        for row in pred_rows:
            d = dict(row)
            for key in ("predicted_value_json", "outcome_value_json", "provenance_json"):
                raw = d.pop(key, None)
                target = key.replace("_json", "")
                if key == "predicted_value_json":
                    target = "predicted_value"
                elif key == "outcome_value_json":
                    target = "outcome_value"
                try:
                    d[target] = json.loads(raw) if raw else {}
                except (TypeError, ValueError):
                    d[target] = {}
            predictions.append(d)

        # Unresolved gaps
        gap_rows = conn.execute("SELECT * FROM world_knowledge_gaps WHERE resolved = 0 ORDER BY blocking DESC, updated_at DESC LIMIT ?", (min(limit, 20),)).fetchall()
        gaps = [dict(r) for r in gap_rows]

        # Significant patterns
        pat_rows = conn.execute("SELECT * FROM world_patterns WHERE occurrences >= 2 ORDER BY occurrences DESC LIMIT ?", (min(limit, 20),)).fetchall()
        patterns = []
        for row in pat_rows:
            d = dict(row)
            raw = d.pop("evidence_json", None)
            try:
                d["evidence"] = json.loads(raw) if raw else []
            except (TypeError, ValueError):
                d["evidence"] = []
            patterns.append(d)

    return {
        "counts": {
            "entities_total": entity_count,
            "entities_active": active_entity_count,
            "entities_stale": stale_count,
            "relationships": rel_count,
            "signals_total": signal_count,
            "signals_active": active_signal_count,
            "signals_urgent": urgent_count,
            "predictions_total": pred_count,
            "predictions_pending": pending_pred,
            "gaps_unresolved": gap_count,
            "gaps_blocking": blocking_gap,
            "patterns": pattern_count,
        },
        "signals": signals,
        "entities": entities,
        "predictions": predictions,
        "gaps": gaps,
        "patterns": patterns,
    }
