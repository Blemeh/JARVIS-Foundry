"""Command Center state composition over real evidence-backed proving stores."""

from __future__ import annotations

from pathlib import Path

from r10_helpers import FIXED_UTC, make_layout, seed_layout

from jarvis_foundry.command_center.state import (
    build_r10_snapshot,
    evidence_for_record,
    record_lookup,
)


def test_snapshot_contains_all_required_sections(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    summary = seed_layout(layout)
    assert summary["portfolio_events"] > 0
    snapshot = build_r10_snapshot(layout, now=FIXED_UTC)
    for section in (
        "goals",
        "projects",
        "missions",
        "candidate_decisions",
        "rejected_alternatives",
        "workers",
        "verification",
        "findings",
        "releases",
        "capabilities",
        "costs",
        "resources",
        "evidence",
        "health",
        "incidents",
        "waiting_blockers",
        "next_actions",
        "timeline",
    ):
        assert section in snapshot, section
    assert snapshot["projects"][0]["payload"]["project_id"]
    assert snapshot["missions"][0]["payload"]["mission_id"].startswith("mission-r8")
    assert snapshot["candidate_decisions"][0]["payload"]["decision_id"]
    assert snapshot["capabilities"][0]["payload"]["status"]
    assert snapshot["evidence"]["count"] > 0
    assert snapshot["timeline"]
    assert snapshot["data_quality"]["empty"] is False
    assert snapshot["data_quality"]["provider_failure"] is True
    assert snapshot["health"]["live_workers"] == 0


def test_snapshot_is_deterministic_for_fixed_now(tmp_path: Path) -> None:
    layout = make_layout(tmp_path, "first")
    seed_layout(layout)
    first = build_r10_snapshot(layout, now=FIXED_UTC)
    second = build_r10_snapshot(layout, now=FIXED_UTC)
    assert first == second
    assert first["generated_at"] == FIXED_UTC


def test_seeding_is_hash_stable_across_fresh_layouts(tmp_path: Path) -> None:
    first = make_layout(tmp_path, "a")
    second = make_layout(tmp_path, "b")
    first_summary = seed_layout(first)
    second_summary = seed_layout(second)
    assert first_summary["state_hash"] == second_summary["state_hash"]
    assert (
        build_r10_snapshot(first, now=FIXED_UTC)["candidate_decisions"]
        == build_r10_snapshot(second, now=FIXED_UTC)["candidate_decisions"]
    )


def test_evidence_drilldown_returns_hash_and_provenance(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    seed_layout(layout)
    snapshot = build_r10_snapshot(layout, now=FIXED_UTC)
    mission = snapshot["missions"][0]
    entry = record_lookup(snapshot, mission["record_id"])
    assert entry is not None and entry["kind"] == "missions"
    evidence = evidence_for_record(entry)
    assert evidence["record_id"] == mission["record_id"]
    assert len(evidence["json_hash"]) == 64
    assert evidence["provenance"]
    assert evidence["evidence_refs"][0]["content_hash"]


def test_empty_store_renders_honest_empty_state(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    snapshot = build_r10_snapshot(layout, now=FIXED_UTC)
    assert snapshot["data_quality"]["empty"] is True
    assert snapshot["evidence"]["count"] == 0
    assert snapshot["missions"] == []


def test_stale_flag_when_state_is_older_than_freshness_window(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    seed_layout(layout)
    stale_now = "2026-09-30T12:00:00Z"
    snapshot = build_r10_snapshot(layout, now=stale_now)
    assert snapshot["data_quality"]["stale"] is True
    assert snapshot["health"]["stale"] is True


def test_degraded_and_provider_failure_flags_from_real_evidence(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    seed_layout(layout)
    snapshot = build_r10_snapshot(layout, now=FIXED_UTC)
    assert snapshot["health"]["degraded"] is True
    assert snapshot["health"]["provider_failure"] is True
    blocked = [
        capability
        for capability in snapshot["capabilities"]
        if capability["payload"].get("status") == "BLOCKED"
    ]
    assert blocked
