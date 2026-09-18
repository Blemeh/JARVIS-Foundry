"""Lane B self-expansion proof: a real mission requirement with an absent but
safely acquirable local capability.

Sequence (all through existing runtime paths):
  1. owner GOAL carries a normalized need whose required action has no
     registered capability -> the runtime records the gap durably and refuses
     to materialize a mission (no false start).
  2. the runtime's canonical intent runner hits the same missing action,
     acquires the capability through the product's own acquisition recipe
     (candidate written under the owned root, focused verification, then
     registration), and re-executes the action.
  3. the same objective is re-processed: the intent now resolves to an action
     plan, a mission is materialized, MissionLoop executes the acquired
     capability, and the result is independently verified against an
     externally computed digest.

Disposable runtime only.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent


def _repo_root(start: Path) -> Path:
    current = start
    for _ in range(10):
        if (current / "src" / "jarvis_foundry").is_dir() and (current / "pyproject.toml").is_file():
            return current
        current = current.parent
    raise RuntimeError(f"could not locate JARVIS repo from {start}")


REPO = _repo_root(HERE)
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jarvis_foundry.control.registration import inspect_git_resource, registration_event  # noqa: E402
from jarvis_foundry.database import FoundryDatabase  # noqa: E402
from jarvis_foundry.executive.autonomy import (  # noqa: E402
    run_autonomy_cycle,
    sync_owner_goals,
    update_objective,
)
from jarvis_foundry.fabric.adapter import FabricRoleAdapter  # noqa: E402
from jarvis_foundry.fabric.executors import ExecutorContext  # noqa: E402
from jarvis_foundry.fabric.loop import run_intent, seed_builtins  # noqa: E402
from jarvis_foundry.fabric.model import CANONICAL_PREDICATE_EVIDENCE, TaskIntent  # noqa: E402
from jarvis_foundry.mission.contracts import next_executable_contract  # noqa: E402
from jarvis_foundry.mission.loop import PASSED, MissionLoop  # noqa: E402
from jarvis_foundry.owner_controls import OwnerControlService, sign_command  # noqa: E402
from jarvis_foundry.roles.adapter import FakeHermesAdapter  # noqa: E402
from jarvis_foundry.roles.router import RoleRouter  # noqa: E402

RUN = HERE / "run"
RESULT = HERE.parent / "SELF_EXPANSION_PROOF.json"
SECRET = b"purpose-recovery-ii-lane-b-expansion-secret"
GOAL_TEXT = "Compute and record the integrity digest of the supplied evidence file."
NEED_ACTION = "hash.file.sha256"


def _git(*args: str, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", timeout=60, check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    return completed.stdout.strip()


def _prior_proof_receipt() -> dict[str, Any] | None:
    """The last committed version of this proof, as repair lineage.

    The pre-repair artifact records the honest refusal the runtime produced
    before the verifier-routing repair: a model verifier step for a
    deterministic local action, FAIL, and the mission BLOCKED while the Fabric
    spine had already re-derived the effect.  It is read back from Git rather
    than retyped, so the lineage cannot drift from what was committed.
    """

    relative = str(RESULT.relative_to(REPO)).replace("\\", "/")
    try:
        commit = _git("log", "-1", "--format=%H", "--", relative, cwd=REPO)
        blob = _git("show", f"{commit}:{relative}", cwd=REPO)
    except Exception:  # noqa: BLE001 - lineage is optional, never fabricated
        return None
    if not commit or not blob:
        return None
    try:
        artifact = json.loads(blob)
    except ValueError:
        return None
    resume = artifact.get("resume") or {}
    return {
        "commit": commit,
        "mission_outcome": resume.get("mission_outcome"),
        "verdicts": resume.get("verdicts"),
    }


def _remove_readonly(function: Any, path: str, _error: Any) -> None:
    os.chmod(path, 0o700)
    function(path)


def main() -> int:
    if RUN.exists():
        shutil.rmtree(RUN, onexc=_remove_readonly)
    RUN.mkdir(parents=True)
    repo = RUN / "project"
    repo.mkdir()
    _git("init", "-b", "main", cwd=repo)
    _git("config", "user.name", "JARVIS Lane B Expansion", cwd=repo)
    _git("config", "user.email", "lane-b@example.invalid", cwd=repo)
    evidence = repo / "evidence.bin"
    evidence.write_bytes(b"lane-b-capability-expansion-evidence")
    _git("add", "evidence.bin", cwd=repo)
    _git("commit", "-m", "evidence", cwd=repo)
    expected_sha = hashlib.sha256(evidence.read_bytes()).hexdigest()

    database = FoundryDatabase(RUN / "foundry.db")
    database.initialize()
    seed_builtins(database)
    resource = inspect_git_resource(repo, resource_id="resource-lane-b-expansion")
    database.append(registration_event(resource, source_id="lane-b-expansion"))

    proof: dict[str, Any] = {}
    try:
        controls = OwnerControlService(database, secret=SECRET, stop_path=RUN / "STOP")
        applied = controls.apply(
            "GOAL", target="GLOBAL", exact_text=GOAL_TEXT, nonce="lane-b-expansion-1",
            signature=sign_command(SECRET, "GOAL", "GLOBAL", GOAL_TEXT, "lane-b-expansion-1"),
        )
        created = sync_owner_goals(database)
        if not created:
            raise RuntimeError("owner GOAL was not admitted")
        objective = created[0]
        update_objective(
            database, objective.objective_id,
            {
                "resource_id": "resource-lane-b-expansion",
                "required_capabilities": ["write_registered_worktree_files"],
                "success_criteria": ["the evidence digest is computed and independently verified"],
                "metadata": {
                    **dict(objective.metadata or {}),
                    "intent": {
                        "needs": ["compute.file_hash"],
                        "inputs": {"file_to_hash": str(evidence)},
                        "success_criteria": [
                            "the evidence digest is computed and independently verified",
                        ],
                    },
                },
            },
        )

        # 1. Runtime detects the real gap and refuses to invent a mission.
        cycle_one = run_autonomy_cycle(
            database, cycle_id="lane-b-expansion-cycle-1",
            resource_id="resource-lane-b-expansion",
            capability="write_registered_worktree_files",
        )
        gaps = [
            dict(event.payload) for event in database.events(event_types=("fabric.gap.recorded",))
            if str(event.payload.get("needed_action")) == NEED_ACTION
        ]
        unresolved = [
            dict(event.payload) for event in database.events(event_types=("portfolio.intent_unresolved",))
        ]

        # 2. Runtime intent runner hits the missing action and acquires it
        #    through the product's own acquisition recipe, then re-executes.
        ctx = ExecutorContext(database=database, owned_root=repo)
        acquisition_run = run_intent(
            database, ctx,
            TaskIntent(task_id="lane-b-expansion", required_actions=(NEED_ACTION,)),
            {NEED_ACTION: {"path": str(evidence)}},
        )
        acquisition_result = dict(acquisition_run["results"][0])
        gaps_after = [
            dict(event.payload) for event in database.events(event_types=("fabric.gap.recorded",))
            if str(event.payload.get("needed_action")) == NEED_ACTION
        ]

        # 3. Same objective resumes: the intent now resolves to an action plan.
        cycle_two = run_autonomy_cycle(
            database, cycle_id="lane-b-expansion-cycle-2",
            resource_id="resource-lane-b-expansion",
            capability="write_registered_worktree_files",
        )
        mission_id = str((cycle_two.materialization or {}).get("mission_id") or "")
        contract = next_executable_contract(database, mission_id) if mission_id else None
        mission_outcome = None
        verification = None
        if contract is not None:
            adapter = FabricRoleAdapter(
                database, owned_root=repo,
                inner=FakeHermesAdapter(working_root=repo),
                operator_executable_allowlist=(),
            )
            loop = MissionLoop(database, RoleRouter(), adapter, max_repairs=1, stop_path=RUN / "STOP")
            outcome = loop.run_until_settled(**contract.runtime_kwargs(database))
            mission_outcome = outcome.outcome
            with database.connect(readonly=True) as connection:
                verification = [
                    dict(row) for row in connection.execute(
                        "SELECT verdict_id, subject_id, status, verdict, "
                        "evidence_json FROM verification_verdicts "
                        "WHERE mission_id = ? ORDER BY rowid",
                        (mission_id,),
                    ).fetchall()
                ]
        predicate_evidence = None
        for row in verification or []:
            if str(row.get("status")) != "PASS":
                continue
            try:
                evidence = json.loads(str(row.get("evidence_json") or "{}"))
            except ValueError:
                continue
            predicate_evidence = {
                "jarvis_predicate": evidence.get("jarvis_predicate"),
                "verifier_answered_by": evidence.get("verifier_answered_by"),
            }
        canonical_events = [
            dict(event.payload)
            for event in database.events(
                event_types=("fabric.canonical_verification_observed",),
            )
            if str(event.payload.get("mission_id") or "") == mission_id
        ]
        verified = [
            dict(event.payload) for event in database.events(event_types=("fabric.capability.outcome",))
            if str(event.payload.get("capability_id")) == "cap.hash.file.sha256"
        ]

        proof = {
            "schema": "jarvis.purpose-recovery-ii.lane-b.self-expansion-proof.v1",
            "objective_id": objective.objective_id,
            "goal_text": GOAL_TEXT,
            "required_need": "compute.file_hash",
            "required_action": NEED_ACTION,
            "expected_digest": expected_sha,
            "gap_detected_by_runtime": {
                "cycle_status": cycle_one.status,
                "cycle_reason": cycle_one.reason,
                "materialized": bool((cycle_one.materialization or {}).get("materialized")),
                "gaps": gaps,
                "intent_unresolved": unresolved,
            },
            "acquisition": {
                "claimed": acquisition_result.get("claimed"),
                "verified": acquisition_result.get("verified"),
                "capability_id": acquisition_result.get("capability_id"),
                "observed_sha256": (acquisition_result.get("observed") or {}).get("sha256"),
                "gap_records_after": gaps_after,
            },
            "resume": {
                "cycle_status": cycle_two.status,
                "materialized": bool((cycle_two.materialization or {}).get("materialized")),
                "mission_id": mission_id,
                "contract_capability": contract.capability if contract else None,
                "contract_step": contract.step_id if contract else None,
                "mission_outcome": mission_outcome,
                "verdicts": verification,
            },
            "capability_outcomes": verified,
            "canonical_verification": {
                "evidence": predicate_evidence,
                "observations": canonical_events,
            },
            "checks": {
                "runtime_detected_gap_without_materializing": (
                    bool((cycle_one.materialization or {}).get("materialized")) is False
                    and bool(gaps)
                ),
                "gap_carries_the_required_action": bool(
                    gaps and gaps[0].get("needed_action") == NEED_ACTION),
                "runtime_acquired_capability": (
                    acquisition_result.get("claimed") == "COMPLETED"
                    and acquisition_result.get("verified") == "MATCH"
                    and acquisition_result.get("capability_id") == "cap.hash.file.sha256"
                ),
                "computed_digest_matches_independent_digest": (
                    (acquisition_result.get("observed") or {}).get("sha256") == expected_sha
                ),
                "same_objective_resumed": bool(
                    (cycle_two.materialization or {}).get("materialized")
                    and mission_id != ""
                    and (cycle_two.materialization or {}).get("mission_id") == mission_id
                ),
                "mission_bound_the_acquired_action": bool(
                    contract is not None and contract.capability == "cap.hash.file.sha256"),
                "resumed_action_verified_against_external_digest": bool(
                    any(
                        str(item.get("capability_id")) == "cap.hash.file.sha256"
                        and item.get("verified") == "MATCH"
                        and item.get("success") is True
                        for item in verified
                    )
                ),
                # The mission's own verification of a deterministic local action
                # must be the runtime's re-derived predicate, not a model
                # verdict, and the re-derivation must be durable and carry the
                # effect lineage.
                "resumed_mission_verified_by_jarvis_predicate": bool(
                    predicate_evidence is not None
                    and predicate_evidence.get("jarvis_predicate")
                    == CANONICAL_PREDICATE_EVIDENCE
                    and predicate_evidence.get("verifier_answered_by") == "fabric"
                    and canonical_events
                    and canonical_events[-1].get("reverified") == "MATCH"
                    and canonical_events[-1].get("effect_id")
                ),
                "no_false_success": bool(
                    mission_outcome != PASSED
                    or (
                        bool(verification)
                        and predicate_evidence is not None
                        and predicate_evidence.get("jarvis_predicate")
                        == CANONICAL_PREDICATE_EVIDENCE
                    )
                ),
            },
            "repair_lineage": {
                "supersedes": _prior_proof_receipt(),
                "repairs": [
                    "R6 verifier routing: the mission verifier step for a step whose "
                    "contract capability resolves to a Fabric action kind is answered by "
                    "the canonical JARVIS predicate over the recorded receipt "
                    "(fabric/adapter.py canonical_verifier_dispatch + "
                    "fabric/loop.py canonical_predicate_verdict), and the acceptance "
                    "rule 'Model PASS is insufficient: JARVIS must see an artifact "
                    "predicate' accepts that re-derived predicate as the predicate "
                    "(mission/loop.py).",
                    "R7 binding drift: a pass resumed after the frozen binding drifted "
                    "is refused honestly (BLOCKED with the ledger's own reason) instead "
                    "of propagating the ledger's EventConflict out of the loop.",
                ],
            },
        }
        proof["passed"] = all(proof["checks"].values())
    except Exception as error:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        proof = {
            "schema": "jarvis.purpose-recovery-ii.lane-b.self-expansion-proof.v1",
            "passed": False,
            "exception": f"{type(error).__name__}: {error}",
            "checks": {},
        }
    RESULT.parent.mkdir(parents=True, exist_ok=True)
    RESULT.write_text(json.dumps(proof, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print(json.dumps({
        "passed": proof.get("passed"), "checks": proof.get("checks"),
        "exception": proof.get("exception"),
        "gap": (proof.get("gap_detected_by_runtime") or {}).get("materialized"),
        "acquisition": (proof.get("acquisition") or {}).get("capability_id"),
        "mission": (proof.get("resume") or {}).get("mission_id"),
    }, indent=2, default=str))
    return 0 if proof.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
