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


# ---------------------------------------------------------------------------
# round-2 repairs (deleg_a18e9d68 findings)
# ---------------------------------------------------------------------------

def test_artifact_name_path_escape_refused(db: FoundryDatabase) -> None:
    """S2 r2 LOW-1: artifact names with path separators, '..' or drive
    letters are refused at registration (verify_manifest containment)."""
    for bad in ("../outside_secret.txt", "a/b.txt", "C:/evil.txt",
                "a" + chr(92) + "b.txt", "/abs.txt"):
        with pytest.raises(ReleaseError):
            register_release(  # type: ignore[arg-type]
                db, **_manifest(artifact_hashes={bad: "b" * 64}),
                improvement_id=_eligible_improvement_id(db),
                controller=CONTROLLER)
    assert db.count("release_manifests") == 0


def test_verify_manifest_containment_defense(
        db: FoundryDatabase, tmp_path: Path) -> None:
    """S2 r2 LOW-1: a LEGACY manifest row that somehow declares an
    escaping artifact name verifies False without reading outside the
    root (defense in depth for rows predating the name validation)."""
    outside = tmp_path / "outside_secret.txt"
    outside.write_bytes(b"secret bytes")
    root = tmp_path / "artifacts"
    root.mkdir()
    (root / "real.bin").write_bytes(b"real")
    real_hash = hashlib.sha256(b"real").hexdigest()
    # simulate a legacy row: direct SQL insert, bypassing API + handler
    import json as _json
    with db.connect() as connection:
        connection.execute(
            """INSERT INTO release_manifests(
                release_id, source_commit, artifact_hashes_json,
                dependencies_json, runtime_identity_json,
                parent_release_id, improvement_id, risk_class,
                migrations_json, verdicts_json, rollback_target,
                created_at, last_event_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("release-legacy-000001", "a" * 40,
             _json.dumps({"../outside_secret.txt": real_hash,
                          "real.bin": real_hash}),
             "[]", "{}", None, "improvement-legacy-000001", "R2",
             "[]", "[]", None, "2026-08-13T00:00:00Z", "evt-legacy",
             "2026-08-13T00:00:00Z"))
        connection.commit()
    # the legacy row must NOT be readable by the orphaned callers, but
    # verify_manifest must fail closed on it: no read outside the root
    report = verify_manifest(db, "release-legacy-000001", root)
    assert report == {"../outside_secret.txt": False, "real.bin": True}
    # raw-writer path is closed too: the handler refuses bad names
    from jarvis_foundry.events import Event
    with pytest.raises(Exception):
        db.append(Event.create(
            "release.manifest_recorded",
            {"release_id": "release-raw-000001", "source_commit": "a" * 40,
             "artifact_hashes": {"../escape.txt": "0" * 64},
             "dependencies": [], "runtime_identity": {},
             "parent_release_id": None, "improvement_id": "improvement-x",
             "risk_class": "R2", "migrations": [], "verdicts": [],
             "rollback_target": None, "controller": CONTROLLER},
            source_type="supervisor", source_id="v6-s2-r2",
            idempotency_key="release.manifest_recorded:release-raw-000001"))


def test_slot_oscillation_with_transition_ids(db: FoundryDatabase) -> None:
    """S2 r2 LOW-2: oscillating histories record every distinct
    transition when an explicit transition_id is supplied."""
    improvement_id = _eligible_improvement_id(db)
    a = register_release(db, **_manifest(),  # type: ignore[arg-type]
                         improvement_id=improvement_id,
                         controller=CONTROLLER)
    b = register_release(  # type: ignore[arg-type]
        db, **_manifest(source_commit="e" * 40,
                        artifact_hashes={"code.tar.gz": "f" * 64},
                        parent_release_id=a),
        improvement_id=improvement_id, controller=CONTROLLER)
    set_slot(db, "live", a, reason="genesis", controller=CONTROLLER)
    set_slot(db, "live", b, reason="promote", controller=CONTROLLER,
             transition_id="t-1")
    set_slot(db, "live", a, reason="rollback", controller=CONTROLLER,
             transition_id="t-2")
    set_slot(db, "live", b, reason="re-promote", controller=CONTROLLER,
             transition_id="t-3")
    assert slot_pointers(db)["live"] == b
    events = [e for e in db.events()
              if e.event_type == "release.slot_changed"
              and e.payload["slot"] == "live"]
    assert len(events) == 4
    # re-delivery of the same transition_id dedupes
    set_slot(db, "live", b, reason="re-promote", controller=CONTROLLER,
             transition_id="t-3")
    assert len([e for e in db.events()
                if e.event_type == "release.slot_changed"
                and e.payload["slot"] == "live"]) == 4


def test_promote_already_live_refused(db: FoundryDatabase) -> None:
    """S2 r2 INFO-3: promoting a release that is already live is
    refused instead of recording redundant history."""
    improvement_id = _eligible_improvement_id(db)
    genesis = register_release(  # type: ignore[arg-type]
        db, **_manifest(), improvement_id=improvement_id,
        controller=CONTROLLER)
    set_slot(db, "live", genesis, reason="genesis", controller=CONTROLLER)
    set_slot(db, "candidate", genesis, reason="stale", controller=CONTROLLER)
    with pytest.raises(ReleaseError):
        promote_candidate(db, genesis, controller=CONTROLLER,
                          verdicts=[{"verifier_run_id": "run-9",
                                     "verdict": "PASS",
                                     "criteria": "x"}],
                          reason="already live")
    assert slot_pointers(db)["live"] == genesis
    assert slot_pointers(db)["previous"] is None


def test_manifest_same_id_different_content_rolled_back(
        db: FoundryDatabase) -> None:
    """S2 r2 INFO-4: a raw-writer event with the same release_id but
    different content is rolled back by the projection handler."""
    from jarvis_foundry.events import Event
    improvement_id = _eligible_improvement_id(db)
    release_id = register_release(  # type: ignore[arg-type]
        db, **_manifest(), improvement_id=improvement_id,
        controller=CONTROLLER)
    row = db.row("release_manifests", "release_id", release_id)
    assert row is not None
    with pytest.raises(Exception):
        db.append(Event.create(
            "release.manifest_recorded",
            {"release_id": release_id, "source_commit": "9" * 40,
             "artifact_hashes": {"other.bin": "8" * 64},
             "dependencies": [], "runtime_identity": {},
             "parent_release_id": None, "improvement_id": improvement_id,
             "risk_class": "R2", "migrations": [], "verdicts": [],
             "rollback_target": None, "controller": CONTROLLER},
            source_type="supervisor", source_id="v6-s2-r2",
            idempotency_key=f"release.manifest_recorded:{release_id}:raw"))
    # the immutable row is untouched and the conflicting event absent
    after = db.row("release_manifests", "release_id", release_id)
    assert after["source_commit"] == row["source_commit"]
    assert after["artifact_hashes_json"] == row["artifact_hashes_json"]
    events = [e for e in db.events()
              if e.event_type == "release.manifest_recorded"]
    assert len(events) == 1
