"""Convergence adapter: observed candidate -> normalized MissionIntent -> admission.

This is the seam the A+B convergence adds.  It proves, at the seam level:

* a class the real discovery path emits (``project_staleness``) is admitted
  instead of being refused as "not a small owner-repo build";
* the admitted objective carries a normalized intent whose needs come from the
  candidate class, not from owner-text keywords and not from a recipe id;
* the derived intent binds through the existing deterministic resolver to a
  bounded action plan (no digital-operator four-need tuple, no 19-role block);
* a class with no deterministic binding is still refused honestly;
* a bounded-change class is admitted WITHOUT being downgraded to a read-only
  review it does not mean.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.discovery_candidates import ingest_observed_candidate
from jarvis_foundry.events import Event
from jarvis_foundry.executive.autonomy import rebuild_executive_state
from jarvis_foundry.executive.decisions import ExecutiveDecision
from jarvis_foundry.executive.owner_context import seed_owner_context
from jarvis_foundry.fabric.catalog import record_outcome as record_fabric_outcome
from jarvis_foundry.fabric.loop import seed_builtins
from jarvis_foundry.fabric.model import VerifyVerdict
from jarvis_foundry.initiative_a3 import (
    A3AdmissionPolicy,
    InitiativeAdmissionAuthority,
)
from jarvis_foundry.mission.intent import (
    NEED_REPOSITORY_INSPECT,
    CANDIDATE_NEEDS,
    intent_from_observation,
    resolve_mission_intent,
)


def _repo(root: Path) -> None:
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "conv@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Convergence Test"], check=True)
    (root / "work.py").write_text("# TODO: tighten the retry loop\nVALUE = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(root), "add", "work.py"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "-qm", "seed owner repo"], check=True)


def _ready_database(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "home" / "foundry.db")
    database.initialize()
    database.append(Event.create(
        "system.start_build_recorded",
        {"received_at": "2026-09-02T00:00:00Z", "standing_mandate": True},
        source_type="owner",
        source_id="convergence-owner-mandate",
        idempotency_key="system.start-build",
    ))
    seed_builtins(database)
    for capability in A3AdmissionPolicy().required_fabric_capabilities:
        record_fabric_outcome(
            database, capability, success=True, verified=VerifyVerdict.MATCH,
            note="convergence adapter readiness", at="2026-09-02T00:00:00Z",
        )
    seed_owner_context(
        database,
        goals=[{"text": "Improve owner repositories with bounded verified work",
                "source_ref": "IDEA.md", "source_hash": "a" * 64}],
        projects=[{"text": "OwnerRepo active project",
                   "source_ref": "docs/CURRENT_STATE.md", "source_hash": "b" * 64}],
    )
    return database


def _selected_candidate(
    database: FoundryDatabase, root: Path, token: str, *, candidate_type: str,
    evidence: list[dict] | None = None,
) -> ExecutiveDecision:
    candidate = ingest_observed_candidate(
        database,
        "convergence-observation-source",
        observation_id=f"observation-{token}",
        source_kind="project",
        source_identity=f"repo://owner/{token}",
        content_hash=f"sha256:{__import__('hashlib').sha256(token.encode()).hexdigest()}",
        objective="Review inactive project resource-ownerrepo",
        target_project="OwnerRepo",
        candidate_type=candidate_type,
        confidence=0.8,
        expires_at="2099-01-01T00:00:00Z",
        evidence=evidence or [{
            "ref": f"repo://owner/{token}",
            "required_capabilities": list(A3AdmissionPolicy().required_fabric_capabilities),
        }],
    )
    source = str(root)
    del source
    candidate_id = str(candidate["candidate_id"])
    decision_id = f"exec-convergence-{token}"
    database.append(Event.create(
        "executive.decision_recorded",
        {"decision_id": decision_id, "decision": "SELECT",
         "candidate_id": candidate_id, "reason": "convergence discovery selection"},
        source_type="supervisor", source_id="convergence-test",
        idempotency_key=f"executive.decision_recorded:{decision_id}",
    ))
    database.append(Event.create(
        "discovery.candidate_judged",
        {"judgment_id": f"judgment-{token}", "candidate_id": candidate_id,
         "fingerprint": candidate["fingerprint"], "decision_id": decision_id,
         "cycle_id": f"convergence-{token}", "stage": "FINAL", "outcome": "ELIGIBLE",
         "reason_code": "READY_FOR_ADMISSION", "reason_detail": "convergence selection",
         "score": 0.8,
         "factors": {"confidence": {"value": 0.8}, "reversibility": {"value": 0.9}},
         "evidence": [{"ref": f"repo://owner/{token}"}], "rank": 1},
        source_type="supervisor", source_id="v11-a2-judgment",
        idempotency_key=f"discovery.candidate_judged:judgment-{token}",
    ))
    return ExecutiveDecision(
        kind="SELECT", decision_id=decision_id,
        reason="convergence discovery selection", candidate_id=candidate_id,
    )


def _authority(database: FoundryDatabase, tmp_path: Path, root: Path):
    return InitiativeAdmissionAuthority(
        database,
        worktree_parent=tmp_path / "convergence-worktrees",
        project_roots={"OwnerRepo": root},
    )


def test_observed_project_class_admits_with_a_normalized_intent(tmp_path: Path) -> None:
    """The class real discovery emits must not be refused as an unbuildable task."""
    root = tmp_path / "repo"
    _repo(root)
    database = _ready_database(tmp_path)
    decision = _selected_candidate(database, root, "staleness", candidate_type="project_staleness")

    admission = _authority(database, tmp_path, root).admit(decision)

    assert admission.admitted, admission.reason
    assert "ACTION_CLASS_NOT_ALLOWED" not in admission.reason
    objective = rebuild_executive_state(database).objectives[admission.objective_id]
    assert not str(objective.objective_id).startswith("owner-goal:")
    intent = objective.metadata.get("intent")
    assert isinstance(intent, dict), "admitted objective carries no normalized intent"
    assert intent["needs"] == [NEED_REPOSITORY_INSPECT]
    # Provenance is the Executive's own observation, never a harness or a
    # keyword map onto an existing recipe.
    assert intent["source"] == "executive-observation"
    assert "recipe_id" not in intent and "recipe_params" not in intent
    assert intent["inputs"]["owned_repository_root"] == objective.metadata["worktree"]


def test_admitted_intent_binds_to_a_bounded_action_plan(tmp_path: Path) -> None:
    """The binder resolves the derived intent without a recipe id or 19 roles."""
    root = tmp_path / "repo"
    _repo(root)
    database = _ready_database(tmp_path)
    intent = intent_from_observation(
        candidate_id="cand-binder",
        candidate_type="project_staleness",
        objective="Review inactive project resource-ownerrepo",
        resource_id="resource-binder",
        supplied_inputs={"owned_repository_root": str(root), "repository_branch": "main"},
    )
    resolution = resolve_mission_intent(database, intent)

    assert resolution.resolved, resolution.reason
    assert resolution.kind == "ACTIONS"
    assert resolution.recipe_id is None
    action_kinds = [str(action.get("action_kind")) for action in resolution.actions]
    assert action_kinds == ["git.inspect"]
    assert [action["payload"]["worktree"] for action in resolution.actions] == [str(root)]


def test_class_without_a_binding_is_still_refused_honestly(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    _repo(root)
    database = _ready_database(tmp_path)
    decision = _selected_candidate(database, root, "watch", candidate_type="web_watch")

    admission = _authority(database, tmp_path, root).admit(decision)

    assert admission.status == "DEFERRED"
    assert not admission.admitted
    assert "web_watch" in admission.reason


def test_bounded_change_class_is_not_downgraded_to_a_review(tmp_path: Path) -> None:
    """A repository-change candidate keeps its build path and carries no review intent."""
    root = tmp_path / "repo"
    _repo(root)
    database = _ready_database(tmp_path)
    decision = _selected_candidate(database, root, "todo", candidate_type="tracked_work")

    admission = _authority(database, tmp_path, root).admit(decision)

    assert admission.admitted, admission.reason
    objective = rebuild_executive_state(database).objectives[admission.objective_id]
    assert "intent" not in objective.metadata
    assert "tracked_work" not in CANDIDATE_NEEDS
