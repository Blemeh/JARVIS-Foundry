from __future__ import annotations

from pathlib import Path

from r7_helpers import FIXED_UTC, make_store

from jarvis_foundry.kernel.clock import FixedClock
from jarvis_foundry.kernel.portfolio.monitor import MonitorService
from jarvis_foundry.kernel.portfolio.store import PortfolioStore


def _capture(content: str) -> tuple[bytes, str]:
    return content.encode("utf-8"), FIXED_UTC


def _file_capture(path: Path) -> tuple[bytes, str]:
    return _capture(path.read_text(encoding="utf-8"))


def test_change_produces_exactly_one_candidate(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    source_file = tmp_path / "health.txt"
    source_file.write_text("healthy\n", encoding="utf-8")
    monitor = MonitorService(store, clock=FixedClock(FIXED_UTC))
    monitor.register_source("health-feed", locator=str(source_file), freshness_seconds=900)
    monitor.poll("health-feed", lambda: _file_capture(source_file))
    source_file.write_text("regression detected\n", encoding="utf-8")
    result = monitor.poll("health-feed", lambda: _file_capture(source_file))
    assert result.changed is True
    assert result.meaningful is True
    assert result.candidate_id
    assert len(store.discoveries()) == 1
    unchanged = monitor.poll("health-feed", lambda: _file_capture(source_file))
    assert unchanged.changed is False
    assert len(store.discoveries()) == 1


def test_stale_snapshot_is_never_a_current_change(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    source_file = tmp_path / "health.txt"
    source_file.write_text("healthy\n", encoding="utf-8")
    monitor = MonitorService(store, clock=FixedClock(FIXED_UTC))
    monitor.register_source("health-feed", locator=str(source_file), freshness_seconds=60)
    monitor.poll("health-feed", lambda: (b"healthy\n", "2026-08-15T16:00:00Z"))
    result = monitor.poll("health-feed", lambda: (b"regression\n", "2026-08-15T16:00:00Z"))
    assert result.stale is True
    assert result.changed is False
    assert store.discoveries() == []


def test_restart_reconciles_cursor_without_duplicates(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    source_file = tmp_path / "health.txt"
    source_file.write_text("healthy\n", encoding="utf-8")
    monitor = MonitorService(store, clock=FixedClock(FIXED_UTC))
    monitor.register_source("health-feed", locator=str(source_file), freshness_seconds=900)
    monitor.poll("health-feed", lambda: _file_capture(source_file))
    source_file.write_text("regression detected\n", encoding="utf-8")
    first = monitor.poll("health-feed", lambda: _file_capture(source_file))

    reopened = PortfolioStore(store.root)
    restarted = MonitorService(reopened, clock=FixedClock(FIXED_UTC))
    again = restarted.poll("health-feed", lambda: _file_capture(source_file))
    assert again.candidate_id == first.candidate_id
    assert again.changed is False or again.duplicate_of == first.candidate_id
    assert len(reopened.discoveries()) == 1
    assert reopened.cursor("health-feed").last_content_hash == first.after_hash


def test_crash_before_cursor_advance_does_not_duplicate(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    source_file = tmp_path / "health.txt"
    source_file.write_text("healthy\n", encoding="utf-8")
    monitor = MonitorService(store, clock=FixedClock(FIXED_UTC))
    monitor.register_source("health-feed", locator=str(source_file), freshness_seconds=900)
    monitor.poll("health-feed", lambda: _file_capture(source_file))
    source_file.write_text("regression detected\n", encoding="utf-8")
    content = source_file.read_bytes()

    snapshot = store.snapshot_bytes()
    monitor.poll("health-feed", lambda: (content, FIXED_UTC))
    # Simulate a crash after candidate creation but before cursor advance by
    # restoring the pre-poll state, then replaying the same captured bytes.
    store.restore_bytes(snapshot)
    restarted = MonitorService(PortfolioStore(store.root), clock=FixedClock(FIXED_UTC))
    replayed = restarted.poll("health-feed", lambda: (content, FIXED_UTC))
    assert len(restarted.store.discoveries()) == 1
    assert replayed.candidate_id is not None
    assert restarted.store.cursor("health-feed").last_content_hash != ""


def test_rollback_restores_previous_cursor_and_rejects_candidate(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    source_file = tmp_path / "health.txt"
    source_file.write_text("healthy\n", encoding="utf-8")
    monitor = MonitorService(store, clock=FixedClock(FIXED_UTC))
    monitor.register_source("health-feed", locator=str(source_file), freshness_seconds=900)
    monitor.poll("health-feed", lambda: _file_capture(source_file))
    before_hash = store.cursor("health-feed").last_content_hash
    source_file.write_text("regression detected\n", encoding="utf-8")
    changed = monitor.poll("health-feed", lambda: _file_capture(source_file))
    assert store.discoveries()[0].status == "CANDIDATE"
    monitor.rollback("health-feed", reason="corrupt observation")
    assert store.cursor("health-feed").last_content_hash == before_hash
    assert store.discoveries()[0].status == "REJECTED"
    assert changed.candidate_id == store.discoveries()[0].candidate_id
