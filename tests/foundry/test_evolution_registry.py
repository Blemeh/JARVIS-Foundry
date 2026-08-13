"""V6-S2: immutable release registry and slots.

Immutable release manifests bind source commit, artifact hashes,
dependencies, runtime identity, parent release, improvement (hypothesis)
id, deterministic risk class, migrations, verification verdicts and the
rollback target. An external controller (the Supervisor side of V6 —
never a candidate) owns the live/candidate/previous/quarantine slot
pointers. Transitions are idempotent, refusals are deterministic
(unknown parent, unknown release, hash mismatch, missing/FAIL verdicts,
ineligible improvement, lower risk), and failed history is preserved.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.evolution.records import (
    improvement_eligibility,
    record_improvement,
)
from jarvis_foundry.evolution.registry import (
    ReleaseError,
    promote_candidate,
    quarantine_release,
    register_release,
    rollback_live,
    set_slot,
    slot_pointers,
    verify_manifest,
)
from jarvis_foundry.migrations import SCHEMA_VERSION
from jarvis_foundry.reconcile import reconcile

CONTROLLER = "v6-controller-test"


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _improvement(db: FoundryDatabase) -> str:
    return record_improvement(
        db,
        problem="suite runtime exceeds the 5-minute budget on this host",
        evidence_refs=["var/evidence/v6-s0/suite-v6-s0-baseline.txt"],
        provenance={"source": "v6-s0 baseline measurement",
                    "observed_at": "2026-08-13T15:00:00Z"},
        hypothesis="Splitting fault-injection tests reduces wall-clock "
                   "runtime",
        expected_mechanism="parallel session isolation removes serialized "
                           "daemon-spawn waits",
        baseline_metric={"name": "suite_runtime_s", "value": 313.92,
                         "unit": "seconds",
                         "source": "suite-v6-s0-baseline.txt"},
        mmi={"minimum_delta": 30.0, "direction": "decrease",
             "unit": "seconds"},
        guardrails={"correctness": "suite green", "safety": "no live "
                    "mutation", "cost": "no paid calls",
                    "latency": "no regression"},
        workload={"ref": "tests/foundry",
                  "hash": "139f26833c9690b521460664f6ab9de0f0cbdbb61b9d547850004bf4c44dafef"},
        observation={"requirement": "3 full-suite runs", "cycles": 3},
        declared_risk="R2",
        surfaces=["tests/foundry", "pyproject.toml"],
        budget={"max_cost_usd": 0.0, "capability_scope": ["pytest"]},
        reversibility={"plan": "git revert", "rollback_threshold": "5m"},
    )


def _manifest(**overrides: object) -> dict[str, object]:
    manifest: dict[str, object] = {
        "source_commit": "a" * 40,
        "artifact_hashes": {"code.tar.gz": "b" * 64},
        "dependencies": ["pytest==9.1.1"],
        "runtime_identity": {"python": "3.13.5",
                             "provider": "opencode_go",
                             "model": "deepseek-v4-flash"},
        "parent_release_id": None,
        "risk_class": "R2",
        "migrations": [21],
        "verdicts": [],
        "rollback_target": None,
    }
    manifest.update(overrides)
    return manifest


def _eligible_improvement_id(db: FoundryDatabase) -> str:
    improvement_id = _improvement(db)
    eligible, _ = improvement_eligibility(db, improvement_id)
    assert eligible is True
    return improvement_id


# ---------------------------------------------------------------------------
# immutable manifests
# ---------------------------------------------------------------------------

def test_genesis_manifest_registered(db: FoundryDatabase) -> None:
    release_id = register_release(  # type: ignore[arg-type]
        db, **_manifest(), improvement_id=_eligible_improvement_id(db),
        controller=CONTROLLER)
    assert release_id.startswith("release-")
    row = db.row("release_manifests", "release_id", release_id)
    assert row is not None
    assert row["parent_release_id"] is None
    assert row["risk_class"] == "R2"
    identical = register_release(  # type: ignore[arg-type]
        db, **_manifest(), improvement_id=_eligible_improvement_id(db),
        controller=CONTROLLER)
    assert identical == release_id
    events = [e for e in db.events()
              if e.event_type == "release.manifest_recorded"]
    assert len(events) == 1


def test_unknown_parent_refused(db: FoundryDatabase) -> None:
    with pytest.raises(ReleaseError):
        register_release(  # type: ignore[arg-type]
            db, **_manifest(parent_release_id="release-nope"),
            improvement_id=_eligible_improvement_id(db),
            controller=CONTROLLER)


def test_child_manifest_links_parent(db: FoundryDatabase) -> None:
    parent = register_release(  # type: ignore[arg-type]
        db, **_manifest(), improvement_id=_eligible_improvement_id(db),
        controller=CONTROLLER)
    child = register_release(  # type: ignore[arg-type]
        db, **_manifest(source_commit="c" * 40,
                        artifact_hashes={"code.tar.gz": "d" * 64},
                        parent_release_id=parent),
        improvement_id=_eligible_improvement_id(db),
        controller=CONTROLLER)
    assert child != parent
    row = db.row("release_manifests", "release_id", child)
    assert row is not None and row["parent_release_id"] == parent


def test_bad_commit_and_bad_hash_refused(db: FoundryDatabase) -> None:
    with pytest.raises(ReleaseError):
        register_release(  # type: ignore[arg-type]
            db, **_manifest(source_commit="short"),
            improvement_id=_eligible_improvement_id(db),
            controller=CONTROLLER)
    with pytest.raises(ReleaseError):
        register_release(  # type: ignore[arg-type]
            db, **_manifest(artifact_hashes={"code.tar.gz": "xyz"}),
            improvement_id=_eligible_improvement_id(db),
            controller=CONTROLLER)


def test_unknown_or_ineligible_improvement_refused(
        db: FoundryDatabase) -> None:
    with pytest.raises(ReleaseError):
        register_release(  # type: ignore[arg-type]
            db, **_manifest(), improvement_id="improvement-nope",
            controller=CONTROLLER)
    # a record whose guardrails were emptied is ineligible -> refused
    improvement_id = _improvement(db)
    with db.connect() as connection:
        connection.execute(
            "UPDATE improvement_records SET guardrails_json = '{}' "
            "WHERE improvement_id = ?", (improvement_id,))
    with pytest.raises(ReleaseError):
        register_release(  # type: ignore[arg-type]
            db, **_manifest(), improvement_id=improvement_id,
            controller=CONTROLLER)


def test_risk_mismatch_with_improvement_refused(db: FoundryDatabase) -> None:
    # improvement resolves to R2; a manifest claiming R1 must be refused
    with pytest.raises(ReleaseError):
        register_release(  # type: ignore[arg-type]
            db, **_manifest(risk_class="R1"),
            improvement_id=_eligible_improvement_id(db),
            controller=CONTROLLER)


def test_manifest_immutable_after_record(db: FoundryDatabase) -> None:
    release_id = register_release(  # type: ignore[arg-type]
        db, **_manifest(), improvement_id=_eligible_improvement_id(db),
        controller=CONTROLLER)
    from jarvis_foundry.events import Event
    with pytest.raises(Exception):
        db.append(Event.create(
            "release.manifest_recorded",
            {"release_id": release_id, "source_commit": "f" * 40,
             "artifact_hashes": {"code.tar.gz": "b" * 64},
             "dependencies": [], "runtime_identity": {},
             "parent_release_id": None, "improvement_id": "x",
             "risk_class": "R0", "migrations": [], "verdicts": [],
             "rollback_target": None},
            source_type="executive", source_id="v6-s2",
            idempotency_key=f"release.manifest_recorded:{release_id}"))


# ---------------------------------------------------------------------------
# slots
# ---------------------------------------------------------------------------

def test_promote_moves_pointers_and_records_controller(
        db: FoundryDatabase) -> None:
    improvement_id = _eligible_improvement_id(db)
    genesis = register_release(  # type: ignore[arg-type]
        db, **_manifest(), improvement_id=improvement_id,
        controller=CONTROLLER)
    set_slot(db, "live", genesis, reason="genesis", controller=CONTROLLER)
    candidate = register_release(  # type: ignore[arg-type]
        db, **_manifest(source_commit="e" * 40,
                        artifact_hashes={"code.tar.gz": "f" * 64},
                        parent_release_id=genesis),
        improvement_id=improvement_id, controller=CONTROLLER)
    set_slot(db, "candidate", candidate, reason="build",
             controller=CONTROLLER)
    promote_candidate(db, candidate, controller=CONTROLLER,
                      verdicts=[{"verifier_run_id": "run-1",
                                 "verdict": "PASS",
                                 "criteria": "v6-s2-test"}],
                      reason="gate passed")
    pointers = slot_pointers(db)
    assert pointers["live"] == candidate
    assert pointers["previous"] == genesis
    assert pointers["candidate"] is None
    events = [e for e in db.events()
              if e.event_type == "release.slot_changed"]
    assert len(events) == 5  # live genesis + candidate + 3 promote transitions
    assert all(e.payload["controller"] == CONTROLLER
               for e in events)


def test_promote_requires_pass_verdicts(db: FoundryDatabase) -> None:
    improvement_id = _eligible_improvement_id(db)
    genesis = register_release(  # type: ignore[arg-type]
        db, **_manifest(), improvement_id=improvement_id,
        controller=CONTROLLER)
    set_slot(db, "live", genesis, reason="genesis", controller=CONTROLLER)
    candidate = register_release(  # type: ignore[arg-type]
        db, **_manifest(source_commit="e" * 40,
                        artifact_hashes={"code.tar.gz": "f" * 64},
                        parent_release_id=genesis),
        improvement_id=improvement_id, controller=CONTROLLER)
    set_slot(db, "candidate", candidate, reason="build",
             controller=CONTROLLER)
    with pytest.raises(ReleaseError):
        promote_candidate(db, candidate, controller=CONTROLLER,
                          verdicts=[], reason="no verdicts")
    with pytest.raises(ReleaseError):
        promote_candidate(db, candidate, controller=CONTROLLER,
                          verdicts=[{"verifier_run_id": "run-2",
                                     "verdict": "FAIL",
                                     "criteria": "x"}],
                          reason="failed verdict")
    assert slot_pointers(db)["live"] == genesis
    assert slot_pointers(db)["candidate"] == candidate


def test_promote_requires_candidate_slot(db: FoundryDatabase) -> None:
    improvement_id = _eligible_improvement_id(db)
    genesis = register_release(  # type: ignore[arg-type]
        db, **_manifest(), improvement_id=improvement_id,
        controller=CONTROLLER)
    set_slot(db, "live", genesis, reason="genesis", controller=CONTROLLER)
    with pytest.raises(ReleaseError):
        promote_candidate(db, genesis, controller=CONTROLLER,
                          verdicts=[{"verifier_run_id": "run-3",
                                     "verdict": "PASS",
                                     "criteria": "x"}],
                          reason="genesis is live, not candidate")


def test_slot_transition_idempotent(db: FoundryDatabase) -> None:
    improvement_id = _eligible_improvement_id(db)
    genesis = register_release(  # type: ignore[arg-type]
        db, **_manifest(), improvement_id=improvement_id,
        controller=CONTROLLER)
    set_slot(db, "live", genesis, reason="genesis", controller=CONTROLLER)
    set_slot(db, "live", genesis, reason="genesis", controller=CONTROLLER)
    assert slot_pointers(db)["live"] == genesis
    events = [e for e in db.events()
              if e.event_type == "release.slot_changed"
              and e.payload["slot"] == "live"]
    assert len(events) == 1


def test_unknown_release_into_slot_refused(db: FoundryDatabase) -> None:
    with pytest.raises(ReleaseError):
        set_slot(db, "live", "release-nope", reason="x",
                 controller=CONTROLLER)
    with pytest.raises(ReleaseError):
        set_slot(db, "not-a-slot", "release-nope", reason="x",
                 controller=CONTROLLER)


def test_rollback_restores_previous_and_quarantines(
        db: FoundryDatabase) -> None:
    improvement_id = _eligible_improvement_id(db)
    genesis = register_release(  # type: ignore[arg-type]
        db, **_manifest(), improvement_id=improvement_id,
        controller=CONTROLLER)
    set_slot(db, "live", genesis, reason="genesis", controller=CONTROLLER)
    candidate = register_release(  # type: ignore[arg-type]
        db, **_manifest(source_commit="e" * 40,
                        artifact_hashes={"code.tar.gz": "f" * 64},
                        parent_release_id=genesis),
        improvement_id=improvement_id, controller=CONTROLLER)
    set_slot(db, "candidate", candidate, reason="build",
             controller=CONTROLLER)
    promote_candidate(db, candidate, controller=CONTROLLER,
                      verdicts=[{"verifier_run_id": "run-4",
                                 "verdict": "PASS",
                                 "criteria": "x"}],
                      reason="gate passed")
    assert slot_pointers(db)["live"] == candidate
    rollback_live(db, reason="outcome regression", controller=CONTROLLER)
    pointers = slot_pointers(db)
    assert pointers["live"] == genesis
    assert pointers["previous"] is None
    assert pointers["quarantine"] == candidate
    # history preserved: the failed release is still fully auditable
    row = db.row("release_manifests", "release_id", candidate)
    assert row is not None and row["risk_class"] == "R2"
    events = [e for e in db.events()
              if e.event_type == "release.slot_changed"]
    assert len(events) == 8  # live + candidate + 3 promote + 3 rollback


def test_rollback_without_previous_refused(db: FoundryDatabase) -> None:
    improvement_id = _eligible_improvement_id(db)
    genesis = register_release(  # type: ignore[arg-type]
        db, **_manifest(), improvement_id=improvement_id,
        controller=CONTROLLER)
    set_slot(db, "live", genesis, reason="genesis", controller=CONTROLLER)
    with pytest.raises(ReleaseError):
        rollback_live(db, reason="nothing to roll back to",
                      controller=CONTROLLER)


def test_quarantine_preserves_history(db: FoundryDatabase) -> None:
    improvement_id = _eligible_improvement_id(db)
    release_id = register_release(  # type: ignore[arg-type]
        db, **_manifest(), improvement_id=improvement_id,
        controller=CONTROLLER)
    set_slot(db, "candidate", release_id, reason="build",
             controller=CONTROLLER)
    quarantine_release(db, release_id, reason="broken candidate",
                       controller=CONTROLLER)
    pointers = slot_pointers(db)
    assert pointers["candidate"] is None
    assert pointers["quarantine"] == release_id
    assert db.row("release_manifests", "release_id", release_id) is not None


# ---------------------------------------------------------------------------
# artifact verification
# ---------------------------------------------------------------------------

def test_verify_manifest_detects_tampering(
        db: FoundryDatabase, tmp_path: Path) -> None:
    artifact = tmp_path / "code.tar.gz"
    artifact.write_bytes(b"real artifact bytes")
    real_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()
    release_id = register_release(  # type: ignore[arg-type]
        db, **_manifest(artifact_hashes={"code.tar.gz": real_hash}),
        improvement_id=_eligible_improvement_id(db), controller=CONTROLLER)
    report = verify_manifest(db, release_id, tmp_path)
    assert report == {"code.tar.gz": True}
    artifact.write_bytes(b"tampered bytes")
    report = verify_manifest(db, release_id, tmp_path)
    assert report == {"code.tar.gz": False}
    # missing file also fails verification
    artifact.unlink()
    report = verify_manifest(db, release_id, tmp_path)
    assert report == {"code.tar.gz": False}


def test_verify_manifest_unknown_release(db: FoundryDatabase,
                                         tmp_path: Path) -> None:
    with pytest.raises(ReleaseError):
        verify_manifest(db, "release-nope", tmp_path)


# ---------------------------------------------------------------------------
# projection integrity
# ---------------------------------------------------------------------------

def test_projection_and_schema(db: FoundryDatabase) -> None:
    improvement_id = _eligible_improvement_id(db)
    release_id = register_release(  # type: ignore[arg-type]
        db, **_manifest(), improvement_id=improvement_id,
        controller=CONTROLLER)
    set_slot(db, "live", release_id, reason="genesis", controller=CONTROLLER)
    report = reconcile(db)
    assert report.consistent is True
    assert report.mismatches == []
    before_manifests = db.count("release_manifests")
    before_slots = db.count("release_slots")
    db.rebuild_projections()
    assert db.count("release_manifests") == before_manifests
    assert db.count("release_slots") == before_slots
    assert db.row("release_manifests", "release_id", release_id) is not None
    with db.connect() as connection:
        version = connection.execute(
            "SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    assert version == SCHEMA_VERSION  # pinned to the constant, >= 22
    assert SCHEMA_VERSION >= 22
