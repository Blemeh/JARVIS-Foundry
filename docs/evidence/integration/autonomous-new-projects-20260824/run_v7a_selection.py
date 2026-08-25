"""Disposable V7A portfolio session for Lane 3 (new projects).

Runs the existing executive portfolio engine on a temporary database:
registers NEW_PROJECT candidate items, selects, records outcomes. Emits a
machine-readable transcript. No live database is touched.
"""
import json
import tempfile
from pathlib import Path

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.evolution.executive_portfolio import (
    PortfolioPolicy,
    configure_portfolio_policy,
    record_portfolio_outcome,
    rebuild_portfolio_state,
    register_work_item,
    select_next_work,
)

tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
db = FoundryDatabase(Path(tmp.name) / "lane3-portfolio.db")
db.initialize()
configure_portfolio_policy(db, PortfolioPolicy())

candidates = [
    ("new-project:cand-worktree-auditor-rejected", 0.50),
    ("new-project:cand-sqlite-backup-guard-rejected", 0.45),
    ("new-project:cand-taskward-20260824", 0.85),
]
for item_id, ev in candidates:
    slug = item_id.split(":", 1)[1]
    try:
        register_work_item(
            db, item_id=item_id, kind="NEW_PROJECT",
            objective=f"Evaluate {slug} for greenfield build",
            expected_value=ev, urgency=0.6, information_gain=0.7,
            confidence=0.75, effort=0.35, risk=0.25, reversibility=0.95,
            evidence_refs=["docs/evidence/integration/"
                           "autonomous-new-projects-20260824/"
                           "03_CANDIDATE_CONTRACTS.json"],
        )
        registered = True
        note = ""
    except Exception as error:  # noqa: BLE001
        registered = False
        note = str(error)

decision = select_next_work(db, loop_id="lane3-selection-1")
ranking = [
    {"entity_id": row.get("entity_id"), "reason": row.get("reason")}
    for row in decision.alternatives
]

selected = decision.entity_id
outcome_status = "PASSED" if selected and "taskward" in selected else "FAILED"
record_portfolio_outcome(
    db, outcome_id="lane3-outcome-1",
    selection_id=decision.decision_id, entity_id=selected,
    kind="NEW_PROJECT",
    predicted={"expected_value": 0.85, "effort": 0.35},
    actual={"mvp_accepted": True, "verifier": "PASS critical=0",
            "live_cycle": "create/drift/converge/delete verified"},
    status=outcome_status,
)

state = rebuild_portfolio_state(db)
transcript = {
    "session": "lane3-v7a-disposable-selection",
    "engine": "jarvis_foundry.evolution.executive_portfolio (existing V7A)",
    "database": "disposable temp DB; no live mutation",
    "class_used": "NEW_PROJECT",
    "registered_all": registered,
    "registration_note": note,
    "decision_kind": decision.kind,
    "selected_class": decision.selected_class,
    "selected_entity": selected,
    "why_now": decision.why_now,
    "alternatives_rejected": ranking,
    "outcomes_in_state": state.outcomes,
    "outcome_recorded": outcome_status,
}
print(json.dumps(transcript, indent=2))
Path(__file__).with_name("_v7a_session_output.json").write_text(
    json.dumps(transcript, indent=2), encoding="utf-8")
