"""Wave 1A — round-3 repair pins (independent adversarial review, round 2).

These pins exist because the round-2 review found two defect classes the first
freeze shipped, and neither was reachable by the lane's own discriminating pin
file:

* **R1-F1** — re-delivering a contract that was recorded *before* this change
  raised ``EventConflict``: the contract's identity (``content_hash()``) is
  stable across releases, but its serialization (``to_payload()``) gained
  ``required_capabilities``, so a database spanning the upgrade held the same
  ledger idempotency key with different payload bytes.  Every re-entry of the
  product's own materialization paths after an upgrade would have raised on an
  existing database.
* **R3-I-2** — the 40 -> 41 schema bump left ``A2_BARRIER_SCHEMA = 40`` in
  ``scripts/v11_a3a4_proving.py``, so the A2 acceptance barrier compared the
  live schema against an obsolete literal and reported FAIL for a bump that was
  entirely correct.  Same defect class as F7, in a file the F7 sweep missed.

WHY THESE ARE NOT IN THE DISCRIMINATING PIN FILE
------------------------------------------------
``tests/foundry/test_wave1a_self_expansion_delivery.py`` holds the Wave 1A
seam pins, every one of which must FAIL on the pre-change tree (that is what
``proofs/pin_discrimination.py`` asserts, and exactly one declared guard pin is
allowed to pass there).  Both pins here PASS on the pre-change tree as well,
because both defects are *introduced by this change* — there is nothing to fail
against on the older base.  A pin that passes on both trees placed in that file
would read as an unexplained pre-change pass and break the discrimination gate.

**Do not delete these as vacuous.**  They are the guard pins the review asked
for: each one fails on the tree that shipped the defect (``d2e9a19`` for R1-F1
and the frozen head ``bd070f4`` for R3-I-2) and passes on the repaired head.
Their job is to keep the repair in place, not to discriminate the feature.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

from jarvis_foundry.events import Event
from jarvis_foundry.migrations import SCHEMA_VERSION
from jarvis_foundry.mission.contracts import (
    CONTRACT_EVENT,
    MissionStepContract,
    latest_contract,
    record_step_contract,
)

from worker_test_helpers import mission_database


def _contract() -> MissionStepContract:
    return MissionStepContract(
        mission_id="mission-1",
        step_id="01-action",
        version=1,
        resource_id="resource-1",
        capability="git_local_commit",
        target_relative_path="artifacts/round3.json",
        instructions=json.dumps({"target": "artifacts/round3.json", "content": "x"}),
        expected_effects=("the artifact is written",),
        rollback={"method": "delete_file", "reversible": True},
        verification_criteria=("the artifact exists",),
        recorded_by="supervisor",
        source="wave1a-round3-pin",
    )


def _pre_change_payload(contract: MissionStepContract) -> dict[str, object]:
    """The payload an earlier release wrote: canonical keys + transport keys.

    ``required_capabilities`` was added to ``to_payload()`` by this change, so
    the pre-change bytes are exactly the canonical payload plus the two
    transport keys.  The event is written through the canonical
    ``FoundryDatabase.append`` path, so its projection seals the same row an
    older release's event would have sealed - a raw SQL insert would bypass the
    projection and make the re-delivery fail for an unrelated reason.
    """
    payload = dict(contract.canonical_payload())
    payload["contract_id"] = contract.contract_id
    payload["contract_hash"] = contract.content_hash()
    return payload


def test_a_contract_recorded_before_the_change_re_delivers_across_the_upgrade(
    tmp_path: Path,
) -> None:
    """R1-F1: re-delivery must resolve by identity, never re-append payloads."""
    database, _ = mission_database(tmp_path)
    contract = _contract()
    legacy_payload = _pre_change_payload(contract)
    assert "required_capabilities" not in legacy_payload, (
        "the fixture must look like a pre-change event")

    sequence, added = database.append(Event.create(
        CONTRACT_EVENT,
        legacy_payload,
        source_type="supervisor",
        source_id="supervisor",
        idempotency_key=f"{CONTRACT_EVENT}:{contract.contract_id}:{contract.content_hash()}",
    ))
    assert added is True

    loaded = latest_contract(database, "mission-1")
    assert loaded is not None
    assert loaded.content_hash() == contract.content_hash(), (
        "the upgrade must not change a stored contract's identity")
    assert loaded.required_capabilities == ()

    events_before = len(list(database.events()))
    same_sequence, re_added = record_step_contract(database, loaded)

    assert (same_sequence, re_added) == (sequence, False), (
        "re-delivering a pre-change contract must resolve to its recorded event; "
        "the ledger refuses the same idempotency key with different payload bytes "
        "by design, so the rebuilt payload must never be appended")
    assert len(list(database.events())) == events_before, (
        "re-delivery must append no event to the ledger")


def test_a_same_head_re_delivery_is_still_idempotent(tmp_path: Path) -> None:
    """The control: the upgrade fix must not change same-release behaviour."""
    database, _ = mission_database(tmp_path)
    contract = _contract()
    first, first_added = record_step_contract(database, contract)
    assert first_added is True

    events_before = len(list(database.events()))
    again, again_added = record_step_contract(database, contract)
    assert (again, again_added) == (first, False)
    assert len(list(database.events())) == events_before


def test_the_a2_acceptance_barrier_derives_its_schema_from_the_product() -> None:
    """R3-I-2: a duplicated schema literal must not survive in the barrier."""
    root = Path(__file__).resolve().parents[2]
    script = root / "scripts" / "v11_a3a4_proving.py"
    spec = importlib.util.spec_from_file_location("_wave1a_a2_barrier", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.A2_BARRIER_SCHEMA == SCHEMA_VERSION, (
        "the A2 barrier must compare the live schema against the product's own "
        "constant; a duplicated literal turns a correct schema bump into a "
        "reported FAIL (that is how the 40 -> 41 bump broke it)")
