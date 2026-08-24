"""The unattended harness observes canonical state without fixture leakage."""
from __future__ import annotations

from pathlib import Path

from jarvis_foundry.acceptance_harness.observer import observe_canonical
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event


def test_observer_reports_ledger_state_only(tmp_path: Path) -> None:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    database.append(Event.create(
        "portfolio.selection_recorded",
        {"decision": "WAIT", "loop_id": "observer-loop", "decision_id": "observer-decision"},
        source_type="supervisor", source_id="observer-test",
        idempotency_key="observer:portfolio:wait",
    ))
    database.append(Event.create(
        "procedure.library.eligibility_checked",
        {"procedure_id": "jarvis.bug-triage", "eligible": False, "blocked_reasons": ["test"]},
        source_type="supervisor", source_id="observer-test",
        idempotency_key="observer:procedure:eligibility",
    ))
    observation = observe_canonical(database, now="2026-08-24T00:00:00+00:00")
    assert observation["source"] == "canonical_append_only_ledger"
    assert observation["fixture_leakage"] is False
    assert observation["portfolio"]["wait_count"] == 1
    assert observation["procedures"]["event_count"] == 1
    assert observation["manual_continuation_required"] == 0
    assert "credential_reference" not in " ".join(str(item) for item in observation["accounts"])
