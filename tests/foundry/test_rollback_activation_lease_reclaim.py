"""Activation-suffix lease-reclaim acceptance for the rollback controller.

The independent Supervisor reclaims a dead predecessor's singleton lease while
taking over the failed release (supervisor/control.py: record_lease_reclaimed ->
CONTROL_LEASE_RECLAIMED). Before this repair the activation-suffix whitelist in
deployment/rollback_controller.py accepted only worker_loop.lease_reclaimed, so a
real takeover made the guarded cross-schema rollback refuse its own release
authority. These tests pin both the acceptance and the fail-closed rejection.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "foundry"))

from jarvis_foundry.database import FoundryDatabase  # noqa: E402
from jarvis_foundry.deployment.rollback_controller import (  # noqa: E402
    CrossSchemaRollbackController,
    RollbackRefusal,
)
from jarvis_foundry.events import Event  # noqa: E402
from test_cross_schema_rollback_controller import (  # noqa: E402
    FAILED_IDENTITY,
    PREDECESSOR_IDENTITY,
    _assert_completed,
    _refresh_sandbox_reports,
    _rollback_sandbox,
)


def _append_activation_suffix(sandbox, database) -> None:
    """Append the exact failed-release activation suffix (manifest + slots)."""
    database.append(Event.create(
        "release.manifest_recorded",
        {
            "artifact_hashes": {
                "RELEASE.json": sandbox.request.failed_manifest_sha256,
            },
            "authority_kind": "immutable_installed_release",
            "dependencies": [],
            "improvement_id": f"deployment:{FAILED_IDENTITY['git_commit']}",
            "migrations": [40],
            "parent_release_id": None,
            "release_id": FAILED_IDENTITY["release_id"],
            "risk_class": "R0",
            "rollback_target": None,
            "runtime_identity": FAILED_IDENTITY,
            "source_commit": FAILED_IDENTITY["git_commit"],
            "verdicts": [],
        },
        source_type="supervisor",
        source_id="deployment-authority",
        idempotency_key=f"release.manifest_recorded:{FAILED_IDENTITY['release_id']}",
    ))
    database.append(Event.create(
        "release.slot_changed",
        {
            "controller": "supervisor",
            "previous_release_id": None,
            "reason": "test activation",
            "release_id": PREDECESSOR_IDENTITY["release_id"],
            "slot": "previous",
        },
        source_type="supervisor",
        source_id="deployment-authority",
        idempotency_key="release.slot_changed:test-previous",
    ))
    database.append(Event.create(
        "release.slot_changed",
        {
            "controller": "supervisor",
            "previous_release_id": PREDECESSOR_IDENTITY["release_id"],
            "reason": "test activation",
            "release_id": FAILED_IDENTITY["release_id"],
            "slot": "live",
        },
        source_type="supervisor",
        source_id="deployment-authority",
        idempotency_key="release.slot_changed:test-live",
    ))




def test_activation_suffix_accepts_proven_supervisor_lease_reclaim(tmp_path):
    """A proven dead-predecessor reclaim is release authority."""
    sandbox = _rollback_sandbox(tmp_path)
    database = FoundryDatabase(sandbox.request.foundry_home / "foundry.db")
    _append_activation_suffix(sandbox, database)
    database.append(Event.create(
        "runtime.control.lease_reclaimed",
        {
            "owner_id": "supervisor-dead-predecessor",
            "pid": 4242,
            "reason": "supervisor process is no longer alive",
        },
        source_type="supervisor",
        source_id="lease-reconciler",
        idempotency_key="runtime.control.lease_reclaimed:accept",
    ))
    _refresh_sandbox_reports(sandbox)

    report = CrossSchemaRollbackController(
        sandbox.request, host=sandbox.host,
    ).run()

    _assert_completed(sandbox, report)
    suffix = report["snapshot"]["rollback_continuity"]["event_continuity"][
        "suffix_event_types"]
    assert suffix["runtime.control.lease_reclaimed"] == 1


@pytest.mark.parametrize(
    ("source_id", "payload"),
    (
        (
            "deployment-authority",
            {
                "owner_id": "supervisor-dead",
                "pid": 4242,
                "reason": "supervisor process is no longer alive",
            },
        ),
        (
            "lease-reconciler",
            {
                "owner_id": "supervisor-dead",
                "pid": 4242,
                "reason": "because I said so",
            },
        ),
        (
            "lease-reconciler",
            {
                "owner_id": "",
                "pid": 4242,
                "reason": "supervisor process is no longer alive",
            },
        ),
        (
            "lease-reconciler",
            {
                "owner_id": "supervisor-dead",
                "pid": 0,
                "reason": "supervisor process is no longer alive",
            },
        ),
    ),
)
def test_activation_suffix_refuses_unproven_supervisor_lease_reclaim(
    tmp_path, source_id, payload,
):
    """Anything but a proven reclaim still refuses the rollback."""
    sandbox = _rollback_sandbox(tmp_path)
    database = FoundryDatabase(sandbox.request.foundry_home / "foundry.db")
    _append_activation_suffix(sandbox, database)
    database.append(Event.create(
        "runtime.control.lease_reclaimed",
        payload,
        source_type="supervisor",
        source_id=source_id,
        idempotency_key="runtime.control.lease_reclaimed:refuse",
    ))
    _refresh_sandbox_reports(sandbox)

    with pytest.raises(RollbackRefusal):
        CrossSchemaRollbackController(
            sandbox.request, host=sandbox.host,
        ).run()
