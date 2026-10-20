"""Purpose Recovery II lane B — verifier routing for deterministic actions.

Covers the R6 repair: a mission step whose contract capability resolves to a
Fabric action kind is certified by re-running the canonical JARVIS predicate
over the step's recorded receipt, never by asking a model to judge a
deterministic local effect.

The pre-repair behaviour was an honest refusal, not a false success: the
runtime dispatched a *model* verifier step for a deterministic local action,
the verdict came back FAIL ("model PASS is not backed by a JARVIS-owned
artifact predicate"), and the mission ended BLOCKED while the Fabric spine had
already re-derived the effect itself as MATCH.  These tests pin the repaired
behaviour and the fail-closed properties that must survive it:

* the verdict must be re-derived from reality, never trusted from the claim;
* a model verdict carrying the marker text still cannot authorize a step;
* path-shaped artifact predicates still gate every other step;
* a receipt recorded for another subject is declined, not certified;
* reality diverging from the recorded claim must not produce a PASS.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from jarvis_foundry.control.registration import inspect_git_resource, registration_event
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.evolution.executive_portfolio import (
    PortfolioPolicy,
    configure_portfolio_policy,
    register_work_item,
    select_next_work,
)
from jarvis_foundry.executive.portfolio_bridge import materialize_portfolio_selection
from jarvis_foundry.fabric.adapter import (
    FabricRoleAdapter,
    _receipt_is_decisive,
    _same_subject,
    _subject_diverges,
    canonical_verifier_dispatch,
    pending_spine_receipt,
    resolve_verification_subject,
)
from jarvis_foundry.fabric.expansion import acquire_hash_capability
from jarvis_foundry.fabric.loop import canonical_predicate_verdict, seed_builtins
from jarvis_foundry.fabric.model import CANONICAL_PREDICATE_EVIDENCE, VerifyVerdict
from jarvis_foundry.mission.contracts import next_executable_contract
from jarvis_foundry.mission.loop import PASSED, MissionLoop
from jarvis_foundry.roles.adapter import FakeHermesAdapter
from jarvis_foundry.roles.results import RoleResult
from jarvis_foundry.roles.router import RoleRouter
from jarvis_foundry.v3_fault import FaultInjector, V3InjectedFault

RESOURCE_ID = "resource-lane-b-routing"
ACTION_KIND = "hash.file.sha256"
CAPABILITY_ID = "cap.hash.file.sha256"


def _git(*args: str, cwd: Path) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
        check=False, timeout=60,
    )
    assert completed.returncode == 0, completed.stderr
    return completed.stdout.strip()


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "lane-b-routing.db")
    database.initialize()
    seed_builtins(database)
    return database


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    _git("init", "-b", "main", cwd=root)
    _git("config", "user.name", "Lane B Routing", cwd=root)
    _git("config", "user.email", "lane-b@example.invalid", cwd=root)
    (root / "README.md").write_text("# lane b routing\n", encoding="utf-8")
    (root / "evidence.bin").write_bytes(b"lane-b-verifier-routing-evidence")
    _git("add", "README.md", "evidence.bin", cwd=root)
    _git("commit", "-m", "init", cwd=root)
    return root


def _register(db: FoundryDatabase, repo: Path) -> None:
    payload = inspect_git_resource(repo, resource_id=RESOURCE_ID)
    db.append(registration_event(payload, source_id="lane-b-routing-test"))


def _adapter(db: FoundryDatabase, repo: Path) -> FabricRoleAdapter:
    return FabricRoleAdapter(
        db, owned_root=repo, inner=FakeHermesAdapter(working_root=repo),
        operator_executable_allowlist=(),
    )


def _materialized_hash_mission(db: FoundryDatabase, repo: Path, *, loop_id: str) -> str:
    """Owner-goal intent -> capability resolution -> durable mission contract."""
    configure_portfolio_policy(db, PortfolioPolicy())
    acquired = acquire_hash_capability(db, repo)
    assert acquired["status"] == "REGISTERED", acquired
    sample = repo / "evidence.bin"
    register_work_item(
        db,
        item_id=f"owner-goal:{loop_id}",
        kind="OWNER_GOAL",
        objective="Compute and record the integrity digest of the supplied evidence file.",
        expected_value=0.9, urgency=0.5, confidence=0.9, effort=0.2, dependencies=(),
        metadata={
            "goal_id": f"owner-goal:{loop_id}",
            "success_criteria": ["the evidence digest is computed and independently verified"],
            "intent": {
                "needs": ["compute.file_hash"],
                "inputs": {"file_to_hash": str(sample)},
                "success_criteria": [
                    "the evidence digest is computed and independently verified",
                ],
            },
        },
    )
    decision = select_next_work(db, loop_id=loop_id)
    assert decision.kind == "SELECT", decision.to_payload()
    result = materialize_portfolio_selection(
        db, decision=decision.to_payload(), resource_id=RESOURCE_ID,
        source_id="lane-b-routing-test",
    )
    assert result["materialized"] is True, result.get("blocked_reason")
    mission_id = str(result["mission_id"])
    contract = next_executable_contract(db, mission_id)
    assert contract is not None
    # The caller never named a recipe or built a parameter block: the runtime
    # resolved the declared need into an action plan on the acquired capability.
    assert contract.capability == CAPABILITY_ID
    assert contract.source == "owner-goal-intent-resolution"
    return mission_id


def _latest_verdict(db: FoundryDatabase, mission_id: str) -> dict:
    with db.connect(readonly=True) as connection:
        rows = [
            dict(row) for row in connection.execute(
                "SELECT * FROM verification_verdicts WHERE mission_id = ? "
                "ORDER BY rowid",
                (mission_id,),
            ).fetchall()
        ]
    assert rows, "the mission must record a verification verdict"
    return rows[-1]


def test_deterministic_action_verification_is_re_derived_not_judged(
    db: FoundryDatabase, repo: Path, tmp_path: Path,
) -> None:
    """The verifier session is answered by the runtime predicate, not a model."""
    _register(db, repo)
    mission_id = _materialized_hash_mission(db, repo, loop_id="lane-b-routing-pass")
    contract = next_executable_contract(db, mission_id)
    loop = MissionLoop(
        db, RoleRouter(), _adapter(db, repo), max_repairs=1,
        stop_path=tmp_path / "STOP",
    )
    outcome = loop.run_until_settled(**contract.runtime_kwargs(db))
    assert outcome.outcome == PASSED, outcome.reason

    verdict = _latest_verdict(db, mission_id)
    assert verdict["status"] == "PASS"
    evidence = json.loads(str(verdict["evidence_json"]))
    assert evidence["jarvis_predicate"] == CANONICAL_PREDICATE_EVIDENCE
    # The frozen binding names the routed provider; the record must also name
    # who actually answered the verification.
    assert evidence["verifier_answered_by"] == "fabric"
    assert db.events(event_types=("mission.completed",))

    observed = db.events(event_types=("fabric.canonical_verification_observed",))
    assert observed, "the predicate re-derivation must be durable"
    payload = observed[-1].payload
    assert payload["predicate"] == "canonical-fabric-predicate"
    assert payload["reverified"] == VerifyVerdict.MATCH.value
    assert payload["action_kind"] == ACTION_KIND
    assert payload["effect_id"], "the observation must keep the effect lineage"
    assert payload["receipt_id"], "the observation must keep the receipt lineage"
    assert payload["subject_ref"] == str(repo / "evidence.bin")


def test_predicate_recomputes_reality_instead_of_trusting_the_claim(
    db: FoundryDatabase, repo: Path,
) -> None:
    """A claim that disagrees with the artifact is a MISMATCH, never a PASS."""
    sample = repo / "evidence.bin"
    real = hashlib.sha256(sample.read_bytes()).hexdigest()
    claim = {"claimed": "COMPLETED", "path": str(sample), "sha256": real}

    def verdict_for(value: str) -> VerifyVerdict:
        return canonical_predicate_verdict(
            db,
            action_kind=ACTION_KIND,
            capability_id=CAPABILITY_ID,
            declared={"path": str(sample)},
            claimed={**claim, "sha256": value},
            owned_root=repo,
        )

    assert verdict_for(real) is VerifyVerdict.MATCH
    assert verdict_for("0" * 64) is VerifyVerdict.MISMATCH


def test_predicate_fails_closed_when_the_subject_vanished(
    db: FoundryDatabase, repo: Path,
) -> None:
    """A re-derivation that cannot run is not a verdict, and never a crash."""
    vanished = repo / "gone.bin"
    verdict = canonical_predicate_verdict(
        db,
        action_kind=ACTION_KIND,
        capability_id=CAPABILITY_ID,
        declared={"path": str(vanished)},
        claimed={"claimed": "COMPLETED", "path": str(vanished), "sha256": "0" * 64},
        owned_root=repo,
    )
    assert verdict is VerifyVerdict.UNKNOWN


def test_undecided_receipts_stay_on_the_judgment_path() -> None:
    """An action the Fabric cannot decide is not re-derived, it is left alone."""
    assert _receipt_is_decisive({"verified": "MATCH"})
    assert _receipt_is_decisive({"verified": "MISMATCH"})
    assert not _receipt_is_decisive({"verified": "UNKNOWN"})
    assert not _receipt_is_decisive({"state": "VERIFIED"})
    assert not _receipt_is_decisive({})


def test_model_marker_text_cannot_authorize_a_step() -> None:
    """A model verdict carrying the marker text is still not a JARVIS predicate."""
    forged = RoleResult(
        run_id="run-forged-verify", role="verifier", status="COMPLETED",
        provider="opencode_go", model="muse-spark-1.2-contributor",
        verdict="PASS",
        evidence=(CANONICAL_PREDICATE_EVIDENCE, "verified:MATCH"),
    )
    assert MissionLoop._jarvis_predicate_satisfied(verifier=forged) is None
    genuine = RoleResult(
        run_id="run-genuine-verify", role="verifier", status="COMPLETED",
        provider="fabric", model="local", verdict="PASS",
        evidence=(CANONICAL_PREDICATE_EVIDENCE, "verified:MATCH"),
    )
    assert (
        MissionLoop._jarvis_predicate_satisfied(verifier=genuine)
        == CANONICAL_PREDICATE_EVIDENCE
    )
    # A Fabric verdict that did not re-derive MATCH does not qualify either.
    mismatched = RoleResult(
        run_id="run-genuine-verify", role="verifier", status="COMPLETED",
        provider="fabric", model="local", verdict="FAIL",
        evidence=(CANONICAL_PREDICATE_EVIDENCE, "verified:MISMATCH"),
    )
    assert MissionLoop._jarvis_predicate_satisfied(verifier=mismatched) is None


def test_path_predicate_rule_is_not_weakened() -> None:
    """Without a JARVIS predicate, a model PASS is still refused."""
    missing = Path("does-not-exist-lane-b-routing")
    refusal = MissionLoop._pass_requires_jarvis_predicate(
        target=missing, expected_artifacts=(),
    )
    assert refusal == "model PASS is not backed by a JARVIS-owned artifact predicate"
    assert (
        MissionLoop._pass_requires_jarvis_predicate(
            target=missing, expected_artifacts=(),
            jarvis_predicate=CANONICAL_PREDICATE_EVIDENCE,
        )
        is None
    )


def test_subject_divergence_and_absent_receipt_decline(
    db: FoundryDatabase, repo: Path,
) -> None:
    """Only a receipt for the declared subject may be certified."""
    assert _same_subject(str(repo / "evidence.bin"), str(repo / "evidence.bin"))
    assert not _same_subject(str(repo / "evidence.bin"), str(repo / "other.bin"))
    # An empty ref is not string divergence; the policy below decides blanks.
    assert _same_subject(str(repo / "evidence.bin"), "")

    # A declared subject the observation does not name — under any key, or with
    # a blank value — is a divergence, never a missing comparison.
    assert not _subject_diverges({"path": "a"}, {"path": "a"})
    assert _subject_diverges({"path": "a"}, {"path": "b"})
    assert _subject_diverges({"path": "a"}, {})
    assert _subject_diverges({"path": "a"}, {"path": ""})
    assert _subject_diverges({"path": "a"}, {"target": "a"})
    assert not _subject_diverges({}, {"path": "a"})

    _register(db, repo)
    mission_id = _materialized_hash_mission(db, repo, loop_id="lane-b-routing-subject")
    contract = next_executable_contract(db, mission_id)
    subject = resolve_verification_subject(
        db, mission_id=mission_id, step_id=contract.step_id,
    )
    assert subject is not None
    assert subject[0] == ACTION_KIND
    assert subject[1] == CAPABILITY_ID
    assert subject[2].contract_id == contract.contract_id
    # The action has not run yet, so there is no receipt to re-derive from and
    # the canonical verifier must decline instead of inventing a verdict.
    assert pending_spine_receipt(
        db, mission_id=mission_id, step_id=contract.step_id, action_kind=ACTION_KIND,
    ) is None


def _verifier_manifest(db: FoundryDatabase, repo: Path, mission_id: str, step_id: str):
    from jarvis_foundry.roles.dispatcher import RoleDispatcher

    mission = db.row("missions", "mission_id", mission_id)
    dispatcher = RoleDispatcher(db, RoleRouter(), _adapter(db, repo))
    return dispatcher.manifest_for(
        f"{mission_id}-verify", "verifier", mission_id, step_id,
        str(mission["resource_id"]), "verification",
        "independently verify the step", 60, 1.0, "fabric",
        RoleRouter().config("verifier"),
    )


def test_re_dispatched_verification_records_its_own_observation(
    db: FoundryDatabase, repo: Path,
) -> None:
    """Re-deriving against a NEW receipt must not raise a control-plane error.

    The observation is audit lineage, not the authorizing verdict: an identity
    the ledger already holds for an earlier re-derivation must not turn a later
    one into an EventConflict escaping the verifier helper.
    """
    from jarvis_foundry.fabric.executors import ExecutorContext
    from jarvis_foundry.fabric.spine import execute_for_spine

    _register(db, repo)
    mission_id = _materialized_hash_mission(db, repo, loop_id="lane-b-routing-reobserve")
    contract = next_executable_contract(db, mission_id)
    sample = repo / "evidence.bin"
    ctx = ExecutorContext(database=db, owned_root=repo)
    execute_for_spine(
        db, ctx, mission_id=mission_id, step_id=contract.step_id,
        action_kind=ACTION_KIND, payload={"path": str(sample)}, attempt=1,
    )
    manifest = _verifier_manifest(db, repo, mission_id, contract.step_id)
    first = canonical_verifier_dispatch(db, manifest, owned_root=repo)
    assert first is not None and first.verdict == "PASS"

    observed = lambda: len(db.events(event_types=("fabric.canonical_verification_observed",)))
    assert observed() == 1
    # Identical re-delivery dedupes on the full observation payload.
    assert canonical_verifier_dispatch(db, manifest, owned_root=repo) is not None
    assert observed() == 1

    # A new receipt under the same verifier run identity records its own
    # observation instead of conflicting on the identity the ledger holds.
    execute_for_spine(
        db, ctx, mission_id=mission_id, step_id=contract.step_id,
        action_kind=ACTION_KIND, payload={"path": str(sample)}, attempt=2,
    )
    again = canonical_verifier_dispatch(db, manifest, owned_root=repo)
    assert again is not None and again.verdict == "PASS"
    assert observed() == 2


def test_unknown_predicate_marker_is_refused() -> None:
    """Only the one marker the runtime writes may satisfy the acceptance rule."""
    missing = Path("does-not-exist-lane-b-routing")
    refusal = MissionLoop._pass_requires_jarvis_predicate(
        target=missing, expected_artifacts=(),
        jarvis_predicate="verification:some-other-marker",
    )
    assert refusal is not None and "unknown JARVIS predicate marker" in refusal


def test_reality_divergence_after_the_receipt_does_not_pass(
    db: FoundryDatabase, repo: Path, tmp_path: Path,
) -> None:
    """A mutated artifact must not be certified from the recorded claim.

    The pass is interrupted after the binding was frozen, the step's subject
    changes on disk, and the pass is resumed.  The ledger refuses a second
    STARTED record for the same content-addressed verdict identity, the loop
    must refuse honestly instead of propagating that refusal, and the mission
    must not claim completion.
    """
    _register(db, repo)
    mission_id = _materialized_hash_mission(db, repo, loop_id="lane-b-routing-diverge")
    contract = next_executable_contract(db, mission_id)
    injector = FaultInjector(["post verification.started"])
    loop = MissionLoop(
        db, RoleRouter(), _adapter(db, repo), max_repairs=0,
        stop_path=tmp_path / "STOP", fault_hook=injector,
    )
    with pytest.raises(V3InjectedFault):
        loop.run_until_settled(**contract.runtime_kwargs(db))
    assert injector.triggered == ["post verification.started"]

    (repo / "evidence.bin").write_bytes(b"mutated after the receipt was recorded")
    resumed = MissionLoop(
        db, RoleRouter(), _adapter(db, repo), max_repairs=0,
        stop_path=tmp_path / "STOP",
    )
    outcome = resumed.run_until_settled(**contract.runtime_kwargs(db))
    assert outcome.outcome != PASSED, outcome.reason
    assert "frozen verification binding changed" in str(outcome.reason)
    assert _latest_verdict(db, mission_id)["status"] == "BLOCKED"
    assert not db.events(event_types=("mission.completed",))
