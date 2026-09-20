"""Wave 2 P0-2 pins — the Command Center must not lose the release authority.

Discriminating by construction.  On the pre-fix tree the owner-truth projection
calls ``release_authority_report()`` with no home, so for a perfectly valid
candidate home ``snapshot["runtime_identity"]["release_authority"]["status"]`` is
the constant ``FOUNDRY_HOME_MISSING`` while ``/api/health`` on the same process
reports the real authority — the first two pins below fail there.

The third pin fails if the identity is ever resolved from a different DB or home
than the one the projection was asked about.
"""
from __future__ import annotations

from pathlib import Path

from jarvis_foundry.command_center import build_health_snapshot, build_snapshot
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.executive.autonomy import create_objective

MISSING = "FOUNDRY_HOME_MISSING"


def _candidate_home(tmp_path: Path, name: str = "home") -> Path:
    home = tmp_path / name
    home.mkdir(parents=True)
    database = FoundryDatabase(home / "foundry.db")
    database.initialize()
    create_objective(database, "Command Center identity objective",
                     objective_id=f"objective-{name}")
    return home


def test_the_projection_resolves_the_authority_for_this_ledgers_own_home(tmp_path: Path) -> None:
    home = _candidate_home(tmp_path)
    database = FoundryDatabase(home / "foundry.db")

    snapshot = build_snapshot(database, runtime_home=home)
    identity = snapshot["runtime_identity"]

    assert identity["foundry_home"] == str(home.resolve())
    assert identity["foundry_home_source"] == "runtime_home_argument"
    assert identity["release_authority"]["status"] != MISSING, (
        "a valid candidate home must not be reported as FOUNDRY_HOME_MISSING")


def test_health_and_the_owner_truth_projection_agree_on_the_same_process(tmp_path: Path) -> None:
    home = _candidate_home(tmp_path)
    database = FoundryDatabase(home / "foundry.db")

    health = build_health_snapshot(database, runtime_home=home)
    snapshot = build_snapshot(database, runtime_home=home)
    projected = snapshot["runtime_identity"]["release_authority"]

    assert health["release_authority"]["status"] == projected["status"], (
        "the same process reported two different release authorities for one home")
    assert health["release_authority"]["consistent"] == projected["consistent"]
    assert projected["status"] != MISSING


def test_the_projection_without_a_caller_home_uses_this_ledgers_parent(tmp_path: Path) -> None:
    home = _candidate_home(tmp_path)
    database = FoundryDatabase(home / "foundry.db")

    identity = build_snapshot(database)["runtime_identity"]

    assert identity["foundry_home"] == str(home.resolve())
    assert identity["foundry_home_source"] == "database_parent"
    assert identity["release_authority"]["status"] != MISSING


def test_the_legacy_call_shape_no_longer_reports_a_missing_home(tmp_path: Path) -> None:
    """The symptom-level pin: it uses ONLY the pre-fix call shape.

    On the pre-fix tree this fails with the defect itself — the assertion reads
    "FOUNDRY_HOME_MISSING" != "FOUNDRY_HOME_MISSING" — for a home that exists and
    carries a valid ledger, while /api/health on the same database reports a real
    authority.  That is the reproduced product defect, not a signature change.
    """

    home = _candidate_home(tmp_path)
    database = FoundryDatabase(home / "foundry.db")

    legacy = build_snapshot(database)["runtime_identity"]["release_authority"]
    health = build_health_snapshot(database, runtime_home=home)["release_authority"]

    assert legacy["status"] != MISSING, (
        f"owner-truth projection reported {MISSING} for a valid home "
        f"while health reported {health['status']!r}")


def test_the_identity_is_resolved_from_the_requested_home_not_another(tmp_path: Path) -> None:
    home_a = _candidate_home(tmp_path, "home-a")
    home_b = _candidate_home(tmp_path, "home-b")
    database_a = FoundryDatabase(home_a / "foundry.db")

    identity_a = build_snapshot(database_a, runtime_home=home_a)["runtime_identity"]
    identity_b = build_snapshot(FoundryDatabase(home_b / "foundry.db"),
                                runtime_home=home_b)["runtime_identity"]

    assert identity_a["foundry_home"] == str(home_a.resolve())
    assert identity_b["foundry_home"] == str(home_b.resolve())
    assert identity_a["foundry_home"] != identity_b["foundry_home"]
