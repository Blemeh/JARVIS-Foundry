"""Unit tests for ``scripts/doctor.py`` pure logic (PATH hygiene)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def doctor():
    spec = importlib.util.spec_from_file_location("jarvis_doctor_script", ROOT / "scripts" / "doctor.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


LEAKED = [
    r"C:\Users\b\AppData\Local\Temp\pytest-of-b\pytest-12\test_cli_setup_show_and_test0\JARVIS\bin",
    r"C:\temp\jarvis-final\test_cli_setup_show_and_test0\JARVIS\bin",
    r"C:\work\repo\.pytest-tmp-x\case\JARVIS\bin",
]
REAL = [r"C:\Users\b\AppData\Local\JARVIS\bin", r"C:\Tools\bin"]


def test_leaked_test_temp_entries_are_identified(doctor) -> None:
    buckets = doctor.classify_path_entries(REAL + LEAKED, exists=lambda _p: True)
    assert buckets["test_temp"] == LEAKED
    assert buckets["healthy"] == REAL


def test_dead_entries_and_duplicates_are_separated(doctor) -> None:
    entries = [REAL[0], r"C:\Gone\bin", REAL[0].lower(), REAL[1]]
    buckets = doctor.classify_path_entries(entries, exists=lambda p: "Gone" not in p)
    assert buckets["missing"] == [r"C:\Gone\bin"]
    assert buckets["duplicate"] == [REAL[0].lower()]
    assert buckets["healthy"] == [REAL[0], REAL[1]]


def test_cleaned_path_keeps_order_and_only_good_entries(doctor) -> None:
    entries = LEAKED[:1] + [REAL[0]] + [r"C:\Gone\bin"] + [REAL[1], REAL[0]]
    buckets = doctor.classify_path_entries(entries, exists=lambda p: "Gone" not in p)
    assert doctor.cleaned_path(entries, buckets) == ";".join(REAL)


def test_split_path_ignores_empty_segments(doctor) -> None:
    assert doctor.split_path(";C:\\a;;C:\\b;") == ["C:\\a", "C:\\b"]
    assert doctor.split_path("") == []
    assert doctor.split_path(None) == []


def test_user_path_writes_are_refused_inside_the_test_suite(doctor) -> None:
    """The suite-wide guard (conftest) must also stop the doctor's PATH fixer."""
    winreg = pytest.importorskip("winreg")
    with pytest.raises(AssertionError, match="registry"):
        winreg.SetValueEx(None, "Path", 0, winreg.REG_EXPAND_SZ, "x")

def test_worker_config_with_conflict_markers_is_reported_unloadable(doctor) -> None:
    broken = "enabled: true\n<<<<<<< HEAD\nstatus: A\n=======\nstatus: B\n>>>>>>> main\n"
    problems = doctor.worker_config_problems(broken)
    assert any("conflict markers" in item for item in problems)


def test_worker_config_that_does_not_parse_or_is_not_a_mapping(doctor) -> None:
    pytest.importorskip("yaml")
    assert any("does not parse" in p for p in doctor.worker_config_problems("a: [unclosed\n"))
    assert any("not a mapping" in p for p in doctor.worker_config_problems("- just\n- a list\n"))
    assert doctor.worker_config_problems("enabled: true\nstatus: CONFIGURED\n") == []


def test_activity_summary_counts_without_reading_payloads(doctor, tmp_path) -> None:
    import sqlite3
    from datetime import datetime, timedelta, timezone

    db_path = tmp_path / "foundry.db"
    now = datetime(2026, 10, 21, 12, 0, tzinfo=timezone.utc)
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE events (sequence INTEGER PRIMARY KEY AUTOINCREMENT, event_type TEXT, "
            "occurred_at TEXT, payload_json TEXT)")
        for event_type, hours_ago in (
            ("executive.autonomy_cycle_recorded", 1),
            ("executive.autonomy_cycle_recorded", 30),  # outside 24h
            ("mission.created", 24 * 3),
            ("mission.created", 24 * 9),  # outside 7d
            ("owner_directive.recorded", 24 * 5),
            ("delegation.intent_blocked", 2),
        ):
            stamp = (now - timedelta(hours=hours_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
            connection.execute(
                "INSERT INTO events (event_type, occurred_at, payload_json) VALUES (?, ?, ?)",
                (event_type, stamp, '{"secret_goal_text": "must never be read"}'))
    info = doctor.activity_summary(db_path, now=now)
    assert info["cycles_24h"] == 1
    assert info["missions_created_7d"] == 1
    assert info["missions_completed_7d"] == 0
    assert info["blocked_delegations_24h"] == 1
    assert str(info["last_owner_directive"]).startswith("2026-10-16")
    assert "must never be read" not in str(info)

def test_guarded_checks_turn_crashes_into_findings(doctor) -> None:
    def boom():
        raise RuntimeError("probe exploded")

    findings = doctor._guarded("probe", boom)
    assert len(findings) == 1 and findings[0].status == doctor.WARN and "RuntimeError" in findings[0].summary

def test_missing_resource_roots_lists_only_registered_roots_that_vanished(doctor, tmp_path) -> None:
    import sqlite3

    present = tmp_path / "present-repo"
    present.mkdir()
    db_path = tmp_path / "foundry.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            "CREATE TABLE resources (resource_id TEXT, readiness_state TEXT, resolved_root TEXT)")
        connection.executemany(
            "INSERT INTO resources VALUES (?, ?, ?)",
            [
                ("resource-ok", "READY", str(present)),
                ("resource-gone", "PROBING", str(tmp_path / "deleted-worktree")),
                ("resource-blank", "READY", ""),
            ],
        )
    assert doctor.missing_resource_roots(db_path) == [
        ("resource-gone", "PROBING", str(tmp_path / "deleted-worktree")),
    ]