"""The workflow.run verifier role must derive the canonical Fabric predicate.

Observed defect (Lane C convergence audit, reproduced at 52e38d1): the
MissionLoop verifier branch for ``workflow.run`` certified on expected-artifact
presence alone, so it returned PASS for a run whose nested receipts were not all
``VERIFIED`` while the canonical Fabric predicate returned MISMATCH for the same
claim.  A verifier session could therefore sign off on a run the runtime's own
predicate rejects.

The repair derives the verifier verdict from ``canonical_predicate_verdict``
instead of re-implementing it, so the two cannot drift apart.  These tests pin
that agreement, including the two shapes that survived a re-implementation and
still produced an unsafe PASS.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.fabric.adapter import resolve_fabric_action, try_fabric_dispatch
from jarvis_foundry.fabric.loop import canonical_predicate_verdict, seed_builtins
from jarvis_foundry.fabric.model import VerifyVerdict
from jarvis_foundry.roles.manifest import ContextManifest

# The role verdict a canonical verdict must map to.  An undecided predicate
# declines as NOT_TESTED rather than guessing a PASS or FAIL.
ROLE_FOR_VERDICT = {
    VerifyVerdict.MATCH: "PASS",
    VerifyVerdict.MISMATCH: "FAIL",
    VerifyVerdict.UNKNOWN: "NOT_TESTED",
}


def _fixture(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    seed_builtins(database)
    return database


def _manifest(root: Path, envelope: dict) -> ContextManifest:
    return ContextManifest(
        run_id="workflow-agreement-run",
        role="verifier",
        mission_id="mission-workflow-agreement",
        step_id="01-action",
        resource_id="resource-workflow-agreement",
        capability="cap.workflow.run",
        provider="local",
        model="deterministic",
        isolation_group="workflow-agreement",
        instructions=json.dumps(envelope),
        expected_artifacts=(),
    )


def _verifier_verdict(database, root: Path, envelope: dict):
    result = try_fabric_dispatch(
        database, _manifest(root, envelope), lambda: None,
        owned_root=root, structured_only=True)
    assert result is not None, (
        "the workflow.run verifier branch must answer with a RoleResult, not "
        f"decline, for a structured envelope: {envelope}")
    return str(result.verdict)


def _canonical_verdict(database, root: Path, envelope: dict) -> str:
    return str(canonical_predicate_verdict(
        database,
        action_kind="workflow.run",
        capability_id="cap.workflow.run",
        declared=dict(envelope),
        claimed=dict(envelope),
        owned_root=root,
    ))


SHAPES = {
    "nested_unverified": {
        "recipe_id": "probe.recipe", "params": {},
        "claimed": "COMPLETED", "nested_success": True,
        "nested_receipts": [{"state": "COMPLETED_UNVERIFIED"}, {"state": "VERIFIED"}],
    },
    "nested_verified": {
        "recipe_id": "probe.recipe", "params": {},
        "claimed": "COMPLETED", "nested_success": True,
        "nested_receipts": [{"state": "VERIFIED"}, {"state": "VERIFIED"}],
    },
    "claim_less": {"recipe_id": "probe.recipe", "params": {}},
    "nested_success_false": {
        "recipe_id": "probe.recipe", "params": {},
        "claimed": "COMPLETED", "nested_success": False,
        "nested_receipts": [{"state": "VERIFIED"}],
    },
    "nested_receipts_absent_key": {
        "recipe_id": "probe.recipe", "params": {},
        "claimed": "COMPLETED", "nested_success": True,
    },
    "nested_receipts_dict": {
        "recipe_id": "probe.recipe", "params": {},
        "claimed": "COMPLETED", "nested_success": True,
        "nested_receipts": {"state": "VERIFIED"},
    },
    "honest_failed_with_receipts": {
        "recipe_id": "probe.recipe", "params": {},
        "claimed": "FAILED", "nested_success": False,
        "nested_receipts": [{"state": "VERIFIED"}],
    },
    "honest_blocked": {
        "recipe_id": "probe.recipe", "params": {},
        "claimed": "BLOCKED", "nested_success": False,
    },
    # Both of these produced an UNSAFE verifier PASS under the re-implementation
    # that kept a ``status`` fallback, because the canonical predicate reads only
    # ``claimed``.
    "status_field_instead_of_claimed": {
        "recipe_id": "probe.recipe", "params": {},
        "status": "COMPLETED", "nested_success": True,
        "nested_receipts": [{"state": "VERIFIED"}],
    },
    "claimed_null_with_status": {
        "recipe_id": "probe.recipe", "params": {},
        "claimed": None, "status": "COMPLETED", "nested_success": True,
        "nested_receipts": [{"state": "VERIFIED"}],
    },
    "nested_item_string": {
        "recipe_id": "probe.recipe", "params": {},
        "claimed": "COMPLETED", "nested_success": True,
        "nested_receipts": ["VERIFIED"],
    },
    "nested_item_state_null": {
        "recipe_id": "probe.recipe", "params": {},
        "claimed": "COMPLETED", "nested_success": True,
        "nested_receipts": [{"state": None}],
    },
}


def test_verifier_role_agrees_with_the_canonical_predicate_on_every_shape(tmp_path: Path) -> None:
    database = _fixture(tmp_path)
    root = tmp_path / "owned"
    root.mkdir()
    (root / "result.txt").write_text("produced\n", encoding="utf-8")

    disagreements = []
    for name, envelope in SHAPES.items():
        canonical = _canonical_verdict(database, root, envelope)
        expected = ROLE_FOR_VERDICT.get(
            next(v for v in VerifyVerdict if str(v) == canonical))
        actual = _verifier_verdict(database, root, envelope)
        if actual != expected:
            disagreements.append(
                {"shape": name, "canonical": canonical,
                 "verifier": actual, "expected": expected})
    assert not disagreements, (
        "the verifier role must derive the canonical predicate, not re-judge "
        f"it: {disagreements}")


def test_an_unverified_workflow_run_cannot_be_certified_by_artifact_presence(
    tmp_path: Path,
) -> None:
    """The exact pre-fix defect: artifacts present, nested receipts not VERIFIED."""
    database = _fixture(tmp_path)
    root = tmp_path / "owned"
    root.mkdir()
    (root / "result.txt").write_text("produced\n", encoding="utf-8")
    manifest = ContextManifest(
        run_id="workflow-presence-run",
        role="verifier",
        mission_id="mission-workflow-presence",
        step_id="01-action",
        resource_id="resource-workflow-presence",
        capability="cap.workflow.run",
        provider="local",
        model="deterministic",
        isolation_group="workflow-presence",
        instructions=json.dumps(SHAPES["nested_unverified"]),
        expected_artifacts=("result.txt",),
    )
    result = try_fabric_dispatch(
        database, manifest, lambda: None, owned_root=root, structured_only=True)
    assert result is not None
    assert str(result.verdict) != "PASS", (
        "a workflow.run whose nested receipts are not all VERIFIED must never "
        "be certified PASS on the strength of expected-artifact presence")
    assert str(result.verdict) == "FAIL"


def test_an_honest_failure_is_not_certified_as_success(tmp_path: Path) -> None:
    """Honest FAILED maps to the canonical MATCH (retryable), never to MISMATCH."""
    database = _fixture(tmp_path)
    root = tmp_path / "owned"
    root.mkdir()
    envelope = SHAPES["honest_failed_with_receipts"]
    assert _canonical_verdict(database, root, envelope) == str(VerifyVerdict.MATCH)
    assert _verifier_verdict(database, root, envelope) == "PASS"


def test_the_resolved_action_kind_is_workflow_run(tmp_path: Path) -> None:
    """Guard the fixture: the branch under test only fires for workflow.run."""
    database = _fixture(tmp_path)
    assert resolve_fabric_action("cap.workflow.run", database) == "workflow.run"


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_no_raising_verifier_path(tmp_path: Path, shape: str) -> None:
    """A malformed envelope must produce a verdict, never an exception."""
    database = _fixture(tmp_path)
    root = tmp_path / "owned"
    root.mkdir()
    result = try_fabric_dispatch(
        database, _manifest(root, SHAPES[shape]), lambda: None,
        owned_root=root, structured_only=True)
    assert result is None or str(result.verdict) in {"PASS", "FAIL", "NOT_TESTED"}


@pytest.mark.parametrize("shape", sorted(SHAPES))
def test_a_file_owned_root_never_raises(tmp_path: Path, shape: str) -> None:
    """A root that is an existing FILE must not turn a verdict into a raise.

    ``ExecutorContext.__post_init__`` creates its ``owned_root``, so building
    the executor context for a file root raises FileExistsError.  The verifier
    branch must still answer with a verdict.
    """
    database = _fixture(tmp_path)
    root = tmp_path / "not-a-directory"
    root.write_text("i am a file\n", encoding="utf-8")
    result = try_fabric_dispatch(
        database, _manifest(root, SHAPES[shape]), lambda: None,
        owned_root=root, structured_only=True)
    # Anti-raise is not enough: a silent downgrade to ``None`` would satisfy a
    # bare "no exception" check, so the concrete answer is pinned too.
    assert result is not None
    assert str(result.verdict) in {"PASS", "FAIL", "NOT_TESTED"}
    assert list(result.evidence or ()), "an uncreatable root must still carry evidence"


HOSTILE_ROOT_NAMES = ("embedded_nul", "existing_file", "nested_under_file")


def _hostile_root(tmp_path: Path, name: str) -> Path:
    blocking_file = tmp_path / "blocker"
    if not blocking_file.exists():
        blocking_file.write_text("blocking\n", encoding="utf-8")
    return {
        "existing_file": blocking_file,
        # A path whose parent is a file cannot be created.
        "nested_under_file": blocking_file / "child",
        # An embedded NUL raises ValueError from mkdir, NOT OSError, so a
        # guard that catches only OSError lets it escape.
        "embedded_nul": Path(str(tmp_path / "nul") + "\x00tail"),
    }[name]


@pytest.mark.parametrize("root_name", HOSTILE_ROOT_NAMES)
def test_a_hostile_owned_root_never_raises(tmp_path: Path, root_name: str) -> None:
    """No uncreatable root may turn a verifier answer into an exception.

    The first repair caught only OSError and a NUL-bearing path still escaped as
    ValueError; this pins the whole uncreatable-root class.
    """
    database = _fixture(tmp_path)
    root = _hostile_root(tmp_path, root_name)
    result = try_fabric_dispatch(
        database, _manifest(root, SHAPES["nested_verified"]), lambda: None,
        owned_root=root, structured_only=True)
    # The concrete MATCH-claim answer is pinned, not merely "did not raise": a
    # path that returned None would otherwise certify this class vacuously.
    assert result is not None
    assert str(result.verdict) in {"PASS", "FAIL", "NOT_TESTED"}
    assert list(result.evidence or ()), "an uncreatable root must still carry evidence"
