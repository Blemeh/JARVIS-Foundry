from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.general_work import (
    ArtifactAssertion,
    DeliverableSpec,
    GeneralWorkLedgerError,
    GeneralWorkPlan,
    SourceIdentity,
    WorkStep,
    load_recorded_plan,
    record_plan_and_claim_targets,
    target_claims,
)


def _database(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def _plan(
    root: Path,
    *,
    plan_id: str = "general-work-ledger-1",
    desired_outcome: str = "Produce the owner workbook",
    target: str = "delivery/report.xlsx",
) -> GeneralWorkPlan:
    source = root / "inputs" / "source.csv"
    source.parent.mkdir(exist_ok=True)
    source.write_text("Name,Value\nA,1\n", encoding="utf-8")
    assertion = ArtifactAssertion(
        "report-exists",
        "report",
        "exists",
        True,
        "",
    )
    return GeneralWorkPlan(
        plan_id=plan_id,
        objective_id="objective-ledger",
        campaign_id=f"campaign:{plan_id}",
        resource_id="resource-ledger",
        desired_outcome=desired_outcome,
        delivery_root="delivery",
        sources=(
            SourceIdentity(
                "source",
                "inputs/source.csv",
                hashlib.sha256(source.read_bytes()).hexdigest(),
                "csv",
                source.stat().st_size,
                "2026-09-04T08:00:00Z",
            ),
        ),
        deliverables=(
            DeliverableSpec(
                "report",
                target,
                "xlsx",
                ("source",),
                "build",
                ("report-exists",),
            ),
        ),
        steps=(
            WorkStep(
                "build",
                "Build the owner workbook",
                "artifact.xlsx.create",
                {
                    "source": "inputs/source.csv",
                    "target": target,
                },
                (),
                ("source",),
                ("report",),
                ("report-exists",),
            ),
        ),
        artifact_assertions=(assertion,),
        consistency_assertions=(),
        created_at="2026-09-04T08:05:00Z",
    )


def test_plan_and_target_claims_are_atomic_and_replayable(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    root = tmp_path / "owned"
    root.mkdir()
    plan = _plan(root)

    first = record_plan_and_claim_targets(
        database,
        plan,
        resource_root=root,
    )
    (root / "delivery").mkdir()
    (root / "delivery" / "report.xlsx").write_bytes(b"later output")
    replay = record_plan_and_claim_targets(
        database,
        plan,
        resource_root=root,
    )

    assert first.inserted == (True, True)
    assert replay.inserted == (False, False)
    assert replay.replayed is True
    assert load_recorded_plan(database, plan.plan_id).plan_hash == plan.plan_hash
    assert len(target_claims(database, plan_id=plan.plan_id)) == 1


def test_plan_id_is_an_immutable_identity_boundary(tmp_path: Path) -> None:
    database = _database(tmp_path)
    root = tmp_path / "owned"
    root.mkdir()
    first = _plan(root)
    changed = _plan(root, desired_outcome="Produce a different package")
    record_plan_and_claim_targets(database, first, resource_root=root)

    with pytest.raises(GeneralWorkLedgerError, match="different immutable work"):
        record_plan_and_claim_targets(database, changed, resource_root=root)

    assert len(target_claims(database)) == 1


def test_global_target_claim_rejects_another_plan(tmp_path: Path) -> None:
    database = _database(tmp_path)
    root = tmp_path / "owned"
    root.mkdir()
    first = _plan(root, plan_id="plan-a")
    second = _plan(root, plan_id="plan-b")
    record_plan_and_claim_targets(database, first, resource_root=root)

    with pytest.raises(GeneralWorkLedgerError, match="already claimed"):
        record_plan_and_claim_targets(database, second, resource_root=root)

    assert len(target_claims(database)) == 1


def test_unclaimed_existing_target_is_refused_without_partial_plan(
    tmp_path: Path,
) -> None:
    database = _database(tmp_path)
    root = tmp_path / "owned"
    root.mkdir()
    plan = _plan(root)
    target = root / "delivery" / "report.xlsx"
    target.parent.mkdir()
    target.write_bytes(b"foreign output")

    with pytest.raises(GeneralWorkLedgerError, match="unclaimed"):
        record_plan_and_claim_targets(database, plan, resource_root=root)

    assert database.event_count(event_type="general_work.plan_recorded") == 0
    assert database.event_count(event_type="general_work.target_claimed") == 0
