"""V3 slice-2 (WP10): monitored sources + mission-execution wiring.

Contract: a monitors registry (source kind/ref, poll interval, enabled);
polling a monitor feeds the candidate pipeline (repeat polls dedup via
the pipeline's canonicalized detection); disabled monitors don't poll;
mission completion with candidate provenance auto-records the candidate
outcome (prediction vs reality) and marks it EXECUTED; deterministic
rebuild + reconcile (schema v11)."""
import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event


def monitor_event(monitor_id="mon-1", source_kind="ledger",
                  source_ref="supervisor.heartbeat", poll_interval_s=300):
    return Event.create(
        "discovery.monitor_registered",
        {"monitor_id": monitor_id, "mission_id": "mission-1",
         "source_kind": source_kind, "source_ref": source_ref,
         "poll_interval_s": poll_interval_s},
        source_type="test", source_id="test-suite",
        idempotency_key=f"discovery.monitor_registered:{monitor_id}")


def test_monitor_registered_projects_row(tmp_path):
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    db.append(monitor_event())
    row = db.row("monitors", "monitor_id", "mon-1")
    assert row["enabled"] == 1
    assert row["poll_interval_s"] == 300


def test_poll_monitor_feeds_candidates_with_dedup(tmp_path):
    from jarvis_foundry.discovery_monitors import (register_monitor,
                                                   poll_monitor)
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    monitor_id = register_monitor(db, "mission-1", "ledger",
                                  "supervisor.heartbeat", 300)
    items = [{"objective": "Add a retry budget to the gateway",
              "source": "heartbeat-review"},
             {"objective": "Fix the nightly integration failure",
              "source": "heartbeat-review"}]
    first = poll_monitor(db, monitor_id, items)
    assert first["candidates_found"] == 2
    second = poll_monitor(db, monitor_id, items)
    assert second["candidates_found"] == 0  # dedup via the pipeline
    # duplicates are RECORDED as pointer rows (ledger discipline — the
    # feed's repeat is evidence), but no NEW distinct candidates
    rows = db.all("candidates")
    assert len(rows) == 4
    assert sum(1 for r in rows if r["duplicate_of"]) == 2
    row = db.row("monitors", "monitor_id", monitor_id)
    assert row["last_poll_at"] is not None


def test_poll_records_polled_event(tmp_path):
    from jarvis_foundry.discovery_monitors import (register_monitor,
                                                   poll_monitor)
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    monitor_id = register_monitor(db, "mission-1", "ledger",
                                  "supervisor.heartbeat", 300)
    poll_monitor(db, monitor_id, [{"objective": "X objective", "source": "s"}])
    with db.connect() as connection:
        count = connection.execute(
            "SELECT COUNT(*) FROM events WHERE event_type = "
            "'discovery.monitor_polled'").fetchone()[0]
    assert count == 1


def test_disabled_monitor_does_not_poll(tmp_path):
    from jarvis_foundry.discovery_monitors import (register_monitor,
                                                   disable_monitor,
                                                   poll_monitor)
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    monitor_id = register_monitor(db, "mission-1", "ledger",
                                  "supervisor.heartbeat", 300)
    disable_monitor(db, monitor_id, "no longer relevant")
    row = db.row("monitors", "monitor_id", monitor_id)
    assert row["enabled"] == 0
    with pytest.raises(Exception):
        poll_monitor(db, monitor_id,
                     [{"objective": "Y objective", "source": "s"}])


def test_mission_completion_wires_candidate_outcome(tmp_path):
    from jarvis_foundry.discovery_candidates import (generate_candidate,
                                                     score_candidate,
                                                     approve_candidate)
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    result = generate_candidate(db, "mission-1", "review-of-F8",
                                "Add a retry budget to the gateway")
    score_candidate(db, result["candidate_id"])
    mission_id = approve_candidate(db, result["candidate_id"], "builder")
    # mission completes -> the wiring must mark the candidate EXECUTED
    # with the outcome (prediction vs reality)
    db.append(Event.create(
        "mission.completed",
        {"mission_id": mission_id, "objective": "Add a retry budget to the gateway",
         "outcome": {"result": "retries bounded at 3", "evidence_ref": "e-1"}},
        source_type="test", source_id="test-suite",
        idempotency_key=f"mission.completed:{mission_id}"))
    row = db.row("candidates", "candidate_id", result["candidate_id"])
    assert row["status"] == "EXECUTED"
    assert "retries bounded at 3" in row["evidence_json"]


def test_mission_completion_without_provenance_no_candidate_change(tmp_path):
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    db.append(Event.create(
        "mission.created",
        {"mission_id": "mission-plain", "step_id": "step-0",
         "objective": "Plain mission", "role": "builder",
         "acceptance_criteria": ["done"]},
        source_type="test", source_id="test-suite",
        idempotency_key="mission.created:mission-plain"))
    db.append(Event.create(
        "mission.completed",
        {"mission_id": "mission-plain", "objective": "Plain mission",
         "outcome": {"result": "done"}},
        source_type="test", source_id="test-suite",
        idempotency_key="mission.completed:mission-plain"))
    # no candidate rows were touched (no candidates exist)
    assert db.count("candidates") == 0


def test_rebuild_reproduces_monitors(tmp_path):
    from jarvis_foundry.discovery_monitors import register_monitor
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    monitor_id = register_monitor(db, "mission-1", "ledger",
                                  "supervisor.heartbeat", 300)
    before = db.row("monitors", "monitor_id", monitor_id)
    db.rebuild_projections()
    after = db.row("monitors", "monitor_id", monitor_id)
    assert after["poll_interval_s"] == before["poll_interval_s"] == 300


def test_schema_version_matches_code(tmp_path):
    from jarvis_foundry.migrations import SCHEMA_VERSION
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    with db.connect() as connection:
        version = connection.execute(
            "SELECT COALESCE(MAX(version),0) FROM schema_migrations").fetchone()[0]
    assert version == SCHEMA_VERSION
