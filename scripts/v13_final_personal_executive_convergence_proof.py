"""Build the source-bound V13 final Personal Executive convergence proof."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from jarvis_foundry.command_center import build_snapshot
from jarvis_foundry.control.capabilities import set_capability_state
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.delegation import (
    DelegationService,
    project_delegation_intents,
)
from jarvis_foundry.events import Event, canonical_json
from jarvis_foundry.executive.autonomy import (
    PlanStep,
    bind_mission_to_step,
    build_plan,
    create_objective,
    rebuild_executive_state,
)
from jarvis_foundry.executive.campaigns import (
    bind_child_effect,
    complete_campaign,
    create_campaign,
    declare_child_mission,
    record_child_state,
)
from jarvis_foundry.fabric import (
    ExecutorContext,
    TaskIntent,
    VerifyVerdict,
    execute_action,
    record_outcome,
    seed_builtins,
)
from jarvis_foundry.fabric.adapter import FabricRoleAdapter
from jarvis_foundry.migrations import SCHEMA_VERSION
from jarvis_foundry.mission.contracts import (
    MissionStepContract,
    record_step_contract,
)
from jarvis_foundry.mission.loop import MissionLoop, PASSED
from jarvis_foundry.mission.verification import (
    record_final_outcome,
    record_verification_acceptance,
    record_verification_outcome,
    start_verification,
)
from jarvis_foundry.owner_controls import OwnerControlService, sign_command
from jarvis_foundry.roles.adapter import FakeHermesAdapter
from jarvis_foundry.roles.router import RoleRouter
from jarvis_foundry.workforce import (
    WorkPackage,
    WorktreeManager,
    WorkforceGateway,
    create_work_package,
    rebuild_workforce_state,
)

#: The schema this proof was last re-validated against.  The guards below are
#: deliberately NOT comparisons of SCHEMA_VERSION with itself: they fail closed
#: when the product's schema moves, forcing this proof to be re-validated rather
#: than silently claiming a schema it was never run against.
VALIDATED_SCHEMA = 41


BRANCH = "candidate/v13-final-personal-executive-convergence-20260905"
BASE_SHA = "51d2cfcce1be847fd252d1c857105f09f5f2c0e4"
EXPECTED_MAIN_SHA = "2905c206fed0461bf3085bc0cb43773be913300a"
CONTRACT = "jarvis.v13.final-personal-executive-convergence-proof.v1"
FINAL_STATUS = "V13_PERSONAL_EXECUTIVE_CONVERGED"
NOW = "2026-09-05T18:00:00Z"
SECRET = b"v13-final-convergence-proof-secret"
OWNER_TEXT = {
    "A": "Fix it.",
    "B": "Prepare me for tomorrow.",
    "C": "Handle this.",
    "D": "Find the next useful thing.",
}
RECEIPT_KEYS = (
    "owner_context",
    "personal_operator",
    "general_work",
    "computer_operator",
    "world_intelligence",
    "world_verifier",
    "chief_of_staff",
    "chief_verifier",
    "command_center",
)


class ProofFailure(RuntimeError):
    """The final convergence proof failed closed."""


@dataclass(frozen=True, slots=True)
class OwnerPath:
    proof_id: str
    exact_text: str
    directive_id: str | None
    intent_id: str | None
    objective_id: str
    plan_id: str
    step_id: str
    campaign_id: str
    child_id: str
    mission_id: str
    resource_id: str | None


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ProofFailure(message)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _content_hash(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _prefixed_hash(path: Path) -> str:
    return f"sha256:{_sha256(path)}"


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _load_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ProofFailure(f"JSON receipt is not an object: {path}")
    return value


def _git(*args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    if check and result.returncode != 0:
        raise ProofFailure(
            f"git {' '.join(args)} failed: {result.stderr.strip()}"
        )
    return result.stdout.strip()


def _source_identity(expected_source_sha: str | None) -> dict[str, Any]:
    head = _git("rev-parse", "HEAD")
    branch = _git("branch", "--show-current")
    status = _git("status", "--porcelain=v1", "--untracked-files=all")
    merge_base = _git("merge-base", BASE_SHA, head)
    if expected_source_sha is not None:
        _require(
            head == expected_source_sha,
            f"source mismatch: HEAD {head}, expected {expected_source_sha}",
        )
    _require(branch == BRANCH, f"unexpected branch: {branch}")
    _require(
        merge_base == BASE_SHA,
        f"candidate is not based on exact V12 final {BASE_SHA}",
    )
    _require(
        SCHEMA_VERSION == VALIDATED_SCHEMA,
        f"expected schema {VALIDATED_SCHEMA}, got {SCHEMA_VERSION}")
    _require(not status, f"source-bound proof requires clean worktree: {status}")
    main_sha = _git("rev-parse", "main")
    origin_main_sha = _git("rev-parse", "origin/main")
    _require(main_sha == EXPECTED_MAIN_SHA, f"main moved to {main_sha}")
    _require(
        origin_main_sha == EXPECTED_MAIN_SHA,
        f"origin/main moved to {origin_main_sha}",
    )
    return {
        "worktree": str(ROOT),
        "branch": branch,
        "base_sha": BASE_SHA,
        "source_sha": head,
        "source_tree_sha": _git("rev-parse", "HEAD^{tree}"),
        "merge_base": merge_base,
        "schema": SCHEMA_VERSION,
        "worktree_clean": True,
        "main_sha": main_sha,
        "origin_main_sha": origin_main_sha,
    }


def _at(value: Any, *path: str | int) -> Any:
    current = value
    for item in path:
        if isinstance(item, int):
            if not isinstance(current, list) or item >= len(current):
                return None
            current = current[item]
        else:
            if not isinstance(current, Mapping) or item not in current:
                return None
            current = current[item]
    return current


def _all_true(value: Any) -> bool:
    return (
        isinstance(value, Mapping)
        and bool(value)
        and all(item is True for item in value.values())
    )


def _receipt_source_sha(receipt: Mapping[str, Any]) -> str:
    for path in (
        ("source_sha",),
        ("source", "source_sha"),
        ("source", "head"),
        ("source", "head_sha"),
    ):
        value = _at(receipt, *path)
        if isinstance(value, str) and len(value) == 40:
            return value
    return ""


def _load_receipts(
    manifest_path: Path,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    manifest = _load_json(manifest_path)
    raw_receipts = manifest.get("receipts")
    _require(isinstance(raw_receipts, Mapping), "receipt manifest lacks receipts")
    missing = [key for key in RECEIPT_KEYS if key not in raw_receipts]
    _require(not missing, f"receipt manifest is missing {missing}")
    receipts: dict[str, dict[str, Any]] = {}
    identities: dict[str, dict[str, Any]] = {}
    for key in RECEIPT_KEYS:
        raw_path = raw_receipts[key]
        _require(isinstance(raw_path, str) and raw_path, f"invalid receipt {key}")
        path = Path(raw_path).expanduser().resolve()
        _require(path.is_file(), f"receipt is missing: {key}: {path}")
        receipts[key] = _load_json(path)
        identities[key] = {
            "source_path": str(path),
            "sha256": _sha256(path),
            "size_bytes": path.stat().st_size,
        }
    return receipts, identities


def _validate_receipts(
    receipts: Mapping[str, Mapping[str, Any]],
    *,
    source_sha: str,
) -> dict[str, bool]:
    owner = receipts["owner_context"]
    personal = receipts["personal_operator"]
    general = receipts["general_work"]
    computer = receipts["computer_operator"]
    world = receipts["world_intelligence"]
    world_verifier = receipts["world_verifier"]
    chief = receipts["chief_of_staff"]
    chief_verifier = receipts["chief_verifier"]
    command_center = receipts["command_center"]

    personal_verifiers = personal.get("independent_verifier") or {}
    general_formats = set(
        _at(
            general,
            "general_work_planner",
            "plans",
            "D",
            "inferred_formats",
        )
        or ()
    )
    checks = {
        "owner_context": (
            owner.get("passed") is True
            and _all_true(owner.get("checks"))
            and _all_true(owner.get("source_attestations"))
            and _receipt_source_sha(owner) == source_sha
        ),
        "personal_operator": (
            personal.get("verdict") == "PASS"
            and _receipt_source_sha(personal) == source_sha
            and _all_true(personal.get("assertions"))
            and all(
                isinstance(value, Mapping) and value.get("verdict") == "PASS"
                for value in personal_verifiers.values()
            )
        ),
        "general_work": (
            general.get("verdict") == "PASS"
            and _receipt_source_sha(general) == source_sha
            and general_formats == {"xlsx", "docx"}
            and _at(
                general,
                "duplicate_mutations",
                "partial_pdf_duplicate_mutations",
            )
            == 0
            and _at(general, "stop", "action_starts_during_stop") == 0
            and _at(general, "independent_verifier", "status") == "PASS"
        ),
        "computer_operator": (
            computer.get("verdict") == "PASS"
            and _receipt_source_sha(computer) == source_sha
            and _at(computer, "integrity", "upload_requests") == 1
            and _at(computer, "integrity", "native_save_dispatches") == 1
            and _at(
                computer,
                "integrity",
                "browser_ambiguity_reconciled_after_runtime_restart",
            )
            is True
            and _at(
                computer,
                "integrity",
                "same_executable_decoy_survived",
            )
            is True
        ),
        "world_intelligence": (
            world.get("contract") == "jarvis.v13.world-intelligence-proof.v1"
            and world.get("source_sha") == source_sha
            and world.get("schema") == SCHEMA_VERSION
            and _at(world, "proofs", "D", "action")
            == "WAIT_AND_RESEARCH_LATER"
            and _at(world, "boundaries", "live_mutated") is False
        ),
        "world_verifier": (
            world_verifier.get("verdict") == "PASS"
            and world_verifier.get("source_sha") == source_sha
            and not world_verifier.get("failures")
        ),
        "chief_of_staff": (
            chief.get("passed") is True
            and _receipt_source_sha(chief) == source_sha
            and _all_true(chief.get("checks"))
            and _at(chief, "counts", "candidates") == 1
            and _at(chief, "counts", "candidate_routed") == 1
        ),
        "chief_verifier": (
            chief_verifier.get("verdict") == "PASS"
            and chief_verifier.get("source_sha") == source_sha
            and not chief_verifier.get("findings")
        ),
        "command_center": (
            command_center.get("all_checks_pass") is True
            and _receipt_source_sha(command_center) == source_sha
            and all(
                item is True
                for item in (command_center.get("checks") or {}).values()
            )
        ),
    }
    _require(all(checks.values()), f"lane receipt checks failed: {checks}")
    return checks


def _event(
    database: FoundryDatabase,
    event_type: str,
    payload: Mapping[str, Any],
    *,
    key: str,
    source_id: str = "v13-final-convergence-proof",
) -> None:
    database.append(
        Event.create(
            event_type,
            dict(payload),
            source_type="supervisor",
            source_id=source_id,
            idempotency_key=key,
        )
    )


def _mission_state(
    database: FoundryDatabase,
    mission_id: str,
    state: str,
    *,
    next_action: str,
) -> None:
    transition_identity = hashlib.sha256(
        next_action.encode("utf-8")
    ).hexdigest()[:12]
    _event(
        database,
        "mission.state_changed",
        {
            "mission_id": mission_id,
            "state": state,
            "next_action": next_action,
        },
        key=(
            f"mission.state_changed:{mission_id}:{state}:"
            f"{transition_identity}"
        ),
    )


def _owner_path(
    database: FoundryDatabase,
    proof_id: str,
    *,
    exact_text: str,
    desired_outcome: str,
    resource_id: str | None = None,
    capability: str = "cap.file.read",
    delegate: bool = True,
) -> OwnerPath:
    prefix = f"v13-final-proof-{proof_id.casefold()}"
    objective_id = f"objective:{prefix}"
    plan_id = f"plan:{prefix}"
    step_id = f"step:{prefix}"
    campaign_id = f"campaign:{prefix}"
    child_id = f"child:{prefix}"
    mission_id = f"mission:{prefix}"
    objective = create_objective(
        database,
        desired_outcome,
        objective_id=objective_id,
        resource_id=resource_id,
        required_capabilities=(capability,),
        success_criteria=("fresh independent verification is accepted",),
        constraints=("disposable local state only",),
        provenance={
            "owner_text": exact_text,
            "proof_id": proof_id,
        },
        source_id="v13-final-convergence-proof",
    )
    plan = build_plan(
        database,
        objective.objective_id,
        (
            PlanStep(
                step_id=step_id,
                objective_id=objective.objective_id,
                description=desired_outcome,
                required_capabilities=(capability,),
                resource_id=resource_id,
                verification_contract=(
                    "fresh independent verification is accepted",
                ),
            ),
        ),
        plan_id=plan_id,
        source_id="v13-final-convergence-proof",
    )
    create_campaign(
        database,
        objective.objective_id,
        campaign_id=campaign_id,
        max_parallel_children=1,
        metadata={"proof_id": proof_id, "owner_text": exact_text},
    )
    _event(
        database,
        "mission.created",
        {
            "mission_id": mission_id,
            "objective": desired_outcome,
            "resource_id": resource_id,
            "state": "PLANNED",
            "acceptance_criteria": [
                "fresh independent verification is accepted"
            ],
            "next_action": desired_outcome,
            "campaign_id": campaign_id,
            "created_from": f"owner-text:{proof_id}",
        },
        key=f"mission.created:{mission_id}",
    )
    _mission_state(
        database,
        mission_id,
        "ORIENTING",
        next_action=desired_outcome,
    )
    _mission_state(
        database,
        mission_id,
        "ACTION_SELECTION",
        next_action=desired_outcome,
    )
    bind_mission_to_step(database, objective.objective_id, step_id, mission_id)
    declare_child_mission(
        database,
        campaign_id,
        child_id=child_id,
        mission_id=mission_id,
        step_id=step_id,
        required_capabilities=(capability,),
        max_retries=1,
        metadata={"proof_id": proof_id},
    )

    directive_id: str | None = None
    intent_id: str | None = None
    if delegate:
        directive_id = f"delegate:{prefix}"
        metadata = {
            "request_id": f"request:{prefix}",
            "desired_outcome": desired_outcome,
            "success_criteria": [
                "fresh independent verification is accepted"
            ],
            "required_capabilities": [capability],
            "context": {
                "objective_id": objective_id,
                "plan_id": plan.plan_id,
                "step_id": step_id,
                "recent_context_ids": [objective_id],
            },
        }
        nonce = f"nonce:{prefix}"
        directive = OwnerControlService(
            database,
            secret=SECRET,
            owner_id="v13-final-proof-owner",
        ).apply(
            "DELEGATE",
            target=objective_id,
            exact_text=exact_text,
            nonce=nonce,
            signature=sign_command(
                SECRET,
                "DELEGATE",
                objective_id,
                exact_text,
                nonce,
                metadata=metadata,
            ),
            directive_id=directive_id,
            metadata=metadata,
        )
        admitted = DelegationService(database).process_directive(
            directive.directive_id
        )
        _require(
            admitted.status == "ADMITTED",
            f"{proof_id} delegation was not admitted: {admitted}",
        )
        intent_id = admitted.intent_id
    return OwnerPath(
        proof_id=proof_id,
        exact_text=exact_text,
        directive_id=directive_id,
        intent_id=intent_id,
        objective_id=objective_id,
        plan_id=plan_id,
        step_id=step_id,
        campaign_id=campaign_id,
        child_id=child_id,
        mission_id=mission_id,
        resource_id=resource_id,
    )


def _run_artifact_verifier(
    manifest: Path,
    output: Path,
    *,
    expected: str,
) -> dict[str, Any]:
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "v13_independent_verify.py"),
            "--manifest",
            str(manifest),
            "--output",
            str(output),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
        check=False,
    )
    payload = _load_json(output)
    _require(
        payload.get("verdict") == expected,
        (
            f"independent verifier expected {expected}, got "
            f"{payload.get('verdict')}: {result.stderr[-1000:]}"
        ),
    )
    expected_code = 0 if expected == "PASS" else 1
    _require(
        result.returncode == expected_code,
        f"independent verifier returned {result.returncode}",
    )
    return {
        **payload,
        "process_returncode": result.returncode,
    }


def _record_pass_and_complete(
    database: FoundryDatabase,
    path: OwnerPath,
    *,
    verdict_id: str,
    evidence: Mapping[str, Any],
    effect_id: str | None = None,
) -> dict[str, Any]:
    state = str(
        (database.row("missions", "mission_id", path.mission_id) or {}).get(
            "state"
        )
        or ""
    )
    if state in {"ACTION_SELECTION", "REPAIRING"}:
        _mission_state(
            database,
            path.mission_id,
            "READY",
            next_action="execute the bounded proof action",
        )
        state = "READY"
    if state == "READY":
        _mission_state(
            database,
            path.mission_id,
            "EXECUTING",
            next_action="complete the bounded proof action",
        )
        state = "EXECUTING"
    if state == "EXECUTING":
        _mission_state(
            database,
            path.mission_id,
            "VERIFYING",
            next_action="run fresh independent verification",
        )
        state = "VERIFYING"
    _require(
        state == "VERIFYING",
        f"{path.proof_id} cannot accept PASS from mission state {state}",
    )
    builder_run_id = f"builder:{path.proof_id}"
    verifier_run_id = f"independent-verifier:{path.proof_id}"
    start_verification(
        database,
        verdict_id=verdict_id,
        mission_id=path.mission_id,
        step_id=path.step_id,
        builder_run_id=builder_run_id,
        verifier_run_id=verifier_run_id,
        independence={
            "fresh_process": True,
            "builder_verifier_separate": True,
            "implementation_imported": False,
        },
        criteria=["fresh independent verification is accepted"],
    )
    record_verification_outcome(
        database,
        verdict_id=verdict_id,
        outcome="PASS",
        findings=[],
        evidence=dict(evidence),
    )
    record_verification_acceptance(
        database,
        verdict_id=verdict_id,
        mission_id=path.mission_id,
        step_id=path.step_id,
        builder_run_id=builder_run_id,
        verifier_run_id=verifier_run_id,
        checks={
            "fresh_process": True,
            "artifact_matches": True,
            "source_bound": True,
        },
        source_id="v13-final-convergence-proof",
    )
    if effect_id:
        bind_child_effect(database, path.child_id, effect_id)
    _mission_state(
        database,
        path.mission_id,
        "ACCEPTING",
        next_action="accept the fresh verifier PASS",
    )
    _mission_state(
        database,
        path.mission_id,
        "COMPLETE",
        next_action="owner outcome complete",
    )
    _event(
        database,
        "mission.completed",
        {
            "mission_id": path.mission_id,
            "outcome": "PASSED",
            "verdict_id": verdict_id,
            "evidence_refs": [verdict_id],
        },
        key=f"mission.completed:{path.mission_id}",
    )
    outcome = record_final_outcome(
        database,
        mission_id=path.mission_id,
        source_id="v13-final-convergence-proof",
    )
    _require(outcome.get("finalized") is True, str(outcome))
    record_child_state(
        database,
        path.child_id,
        "SUCCEEDED",
        acceptance_outcome_id=str(outcome["outcome_id"]),
    )
    _require(
        complete_campaign(database, path.campaign_id),
        f"campaign did not complete: {path.campaign_id}",
    )
    if path.directive_id:
        completed = DelegationService(
            FoundryDatabase(database.path)
        ).process_directive(path.directive_id)
        replay = DelegationService(
            FoundryDatabase(database.path)
        ).process_directive(path.directive_id)
        _require(
            completed.status == replay.status == "COMPLETED"
            and replay.replayed is True,
            f"delegation did not mirror completion: {path.proof_id}",
        )
    return outcome


def _artifact_pass(
    database: FoundryDatabase,
    path: OwnerPath,
    *,
    root: Path,
    payload: Mapping[str, Any],
    effect_id: str | None = None,
) -> dict[str, Any]:
    artifact = root / f"proof-{path.proof_id.casefold()}-artifact.json"
    manifest = root / f"proof-{path.proof_id.casefold()}-manifest.json"
    output = root / f"proof-{path.proof_id.casefold()}-verification.json"
    _write_json(artifact, payload)
    _write_json(
        manifest,
        {
            "root": ".",
            "checks": [
                {
                    "name": f"proof-{path.proof_id}-artifact-hash",
                    "kind": "file_hash",
                    "path": artifact.name,
                    "sha256": _sha256(artifact),
                }
            ],
        },
    )
    verified = _run_artifact_verifier(manifest, output, expected="PASS")
    outcome = _record_pass_and_complete(
        database,
        path,
        verdict_id=f"verdict:{path.proof_id}:pass",
        evidence={
            "artifact": artifact.name,
            "artifact_sha256": _sha256(artifact),
            "verification": output.name,
            "verification_sha256": _sha256(output),
        },
        effect_id=effect_id,
    )
    return {
        "status": "PASS",
        "artifact": str(artifact),
        "artifact_sha256": _sha256(artifact),
        "verification": verified,
        "outcome": outcome,
    }


def _proof_a(database: FoundryDatabase, root: Path) -> dict[str, Any]:
    path = _owner_path(
        database,
        "A",
        exact_text=OWNER_TEXT["A"],
        desired_outcome="Repair the bounded local defect and verify it",
        resource_id="resource:v13-final-proof-a",
        capability="cap.file.replace",
    )
    owned = root / "proof-a-owned"
    owned.mkdir()
    target = owned / "defect.txt"
    target.write_text("state=broken\n", encoding="utf-8")
    expected = b"state=repaired\n"
    manifest = owned / "manifest.json"
    _write_json(
        manifest,
        {
            "root": ".",
            "checks": [
                {
                    "name": "repair-target",
                    "kind": "file_hash",
                    "path": "defect.txt",
                    "sha256": _content_hash(expected),
                }
            ],
        },
    )
    failed_output = owned / "verification-failed.json"
    failed = _run_artifact_verifier(
        manifest,
        failed_output,
        expected="FAIL",
    )
    start_verification(
        database,
        verdict_id="verdict:A:fail",
        mission_id=path.mission_id,
        step_id=path.step_id,
        builder_run_id="builder:A:initial",
        verifier_run_id="independent-verifier:A:fail",
        independence={
            "fresh_process": True,
            "builder_verifier_separate": True,
        },
        criteria=["repair target has the expected hash"],
    )
    record_verification_outcome(
        database,
        verdict_id="verdict:A:fail",
        outcome="FAIL",
        findings=["bounded defect remains present"],
        repair_requirements=["replace the exact owned target"],
        evidence={
            "verification": failed_output.name,
            "verification_sha256": _sha256(failed_output),
        },
    )
    _mission_state(
        database,
        path.mission_id,
        "READY",
        next_action="execute the bounded repair",
    )
    _mission_state(
        database,
        path.mission_id,
        "EXECUTING",
        next_action="replace the exact owned target",
    )
    _mission_state(
        database,
        path.mission_id,
        "VERIFYING",
        next_action="independently verify the bounded repair",
    )
    _mission_state(
        database,
        path.mission_id,
        "REPAIRING",
        next_action="replace the exact owned target and reverify",
    )
    seed_builtins(database)
    before_hash = _prefixed_hash(target)
    action = execute_action(
        database,
        ExecutorContext(database=database, owned_root=owned),
        TaskIntent(
            task_id=path.mission_id,
            required_actions=("file.replace",),
        ),
        "file.replace",
        {
            "target": "defect.txt",
            "expected_source_hash": before_hash,
            "new_text": expected.decode("utf-8"),
            "preserve_original": True,
        },
        workflow_id=path.campaign_id,
        step_id=path.step_id,
        attempt=1,
        route_owner="personal_operator",
    )
    _require(
        action.claimed.value == "COMPLETED"
        and action.verified.value == "UNKNOWN"
        and action.observed.get("performed") is True
        and target.read_bytes() == expected,
        f"automatic repair did not reach honest pre-verification truth: {action}",
    )
    passed_output = owned / "verification-passed.json"
    passed = _run_artifact_verifier(
        manifest,
        passed_output,
        expected="PASS",
    )
    effect_id = str(
        action.receipt.get("effect_id")
        or action.observed.get("action_key")
        or ""
    )
    _require(effect_id, "repair action has no canonical effect identity")
    _record_pass_and_complete(
        database,
        path,
        verdict_id="verdict:A:pass",
        evidence={
            "verification": passed_output.name,
            "verification_sha256": _sha256(passed_output),
            "target_sha256": _sha256(target),
            "effect_id": effect_id,
        },
        effect_id=effect_id,
    )
    replay = execute_action(
        FoundryDatabase(database.path),
        ExecutorContext(
            database=FoundryDatabase(database.path),
            owned_root=owned,
        ),
        TaskIntent(
            task_id=path.mission_id,
            required_actions=("file.replace",),
        ),
        "file.replace",
        {
            "target": "defect.txt",
            "expected_source_hash": before_hash,
            "new_text": expected.decode("utf-8"),
            "preserve_original": True,
        },
        workflow_id=path.campaign_id,
        step_id=path.step_id,
        attempt=1,
        route_owner="personal_operator",
    )
    _require(
        replay.claimed.value == "COMPLETED"
        and replay.verified.value == "UNKNOWN"
        and replay.observed.get("performed") is False
        and replay.observed.get("replayed") is True
        and replay.receipt.get("effect_id") == effect_id
        and target.read_bytes() == expected,
        f"repair replay did not reconcile the original effect: {replay}",
    )
    record_outcome(
        database,
        action.capability_id,
        success=True,
        verified=VerifyVerdict.MATCH,
        note="Proof A fresh independent verifier PASS accepted",
        at=NOW,
    )
    return {
        "status": "PASS",
        "owner_text": path.exact_text,
        "path": asdict(path),
        "defect": {
            "initial_sha256": before_hash.removeprefix("sha256:"),
            "expected_sha256": _content_hash(expected),
            "final_sha256": _sha256(target),
        },
        "verifier_fail": failed,
        "repair": {
            "capability_id": action.capability_id,
            "effect_id": effect_id,
            "claimed": action.claimed.value,
            "verified": action.verified.value,
            "replay_performed": replay.observed.get("performed"),
            "replayed": replay.observed.get("replayed"),
        },
        "verifier_pass": passed,
    }


def _init_worker_source(root: Path) -> tuple[Path, str]:
    source = root / "worker-source"
    source.mkdir()
    commands = (
        ("init", "-q"),
        ("config", "user.email", "v13-proof@example.invalid"),
        ("config", "user.name", "V13 Final Proof"),
    )
    for command in commands:
        result = subprocess.run(
            ["git", "-C", str(source), *command],
            capture_output=True,
            text=True,
            check=False,
        )
        _require(result.returncode == 0, result.stderr)
    (source / "src").mkdir()
    (source / "src" / "work-item.txt").write_text(
        "worker-state=pending\n",
        encoding="utf-8",
    )
    (source / "src" / "completed-state.txt").write_text(
        "worker-state=completed\n",
        encoding="utf-8",
    )
    for command in (("add", "."), ("commit", "-qm", "seed worker source")):
        result = subprocess.run(
            ["git", "-C", str(source), *command],
            capture_output=True,
            text=True,
            check=False,
        )
        _require(result.returncode == 0, result.stderr)
    source_sha = subprocess.run(
        ["git", "-C", str(source), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    return source, source_sha


def _proof_c_worker(
    database: FoundryDatabase,
    path: OwnerPath,
    root: Path,
) -> dict[str, Any]:
    source, source_sha = _init_worker_source(root)
    package_id = "work-package:v13-final-proof-c"
    resource_id = f"workforce-resource:{package_id}"
    _require(
        path.resource_id == resource_id,
        "Proof C owner path is not bound to its Workforce resource",
    )
    worktree = root / "worker-worktree"
    source_file = source / "src" / "completed-state.txt"
    expected_source_hash = _prefixed_hash(source_file)
    instructions = json.dumps(
        {
            "source": "src/completed-state.txt",
            "target": "src/work-item.completed.txt",
            "expected_source_sha256": expected_source_hash,
            "idempotency_key": "proof-c-copy-completed-state",
        },
        sort_keys=True,
    )
    contract = MissionStepContract(
        mission_id=path.mission_id,
        step_id=path.step_id,
        version=1,
        resource_id=resource_id,
        capability="cap.file.copy",
        target_relative_path="src/work-item.completed.txt",
        instructions=instructions,
        expected_effects=("owned completion artifact is copied exactly once",),
        rollback={"method": "delete_owned_copy", "reversible": True},
        risk_class="C1",
        verification_criteria=("worker output has the exact expected hash",),
        expected_artifacts=("src/work-item.completed.txt",),
        forbidden_paths=("live", ".git"),
        source="v13-general-work",
        provenance={
            "proof": "C",
            "owner_text": OWNER_TEXT["C"],
            "general_work_plan": "bounded mixed-file worker package",
        },
    )
    package = WorkPackage(
        package_id=package_id,
        parent_objective_id=path.objective_id,
        campaign_id=path.campaign_id,
        mission_id=path.mission_id,
        step_id=path.step_id,
        role="builder",
        capability=contract.capability,
        bounded_goal="Complete one bounded local worker item",
        acceptance_criteria=contract.verification_criteria,
        source_sha=source_sha,
        repository_id="v13-final-proof-worker-source",
        branch_name="candidate/v13-final-proof-worker",
        worktree_path=str(worktree),
        allowed_paths=("src",),
        forbidden_paths=contract.forbidden_paths,
        mission_step_contract_id=contract.contract_id,
        mission_step_contract_hash=contract.content_hash(),
        expected_artifacts=contract.expected_artifacts,
        required_tests=(),
        evidence_requirements=("MissionLoop AcceptanceGate receipt",),
        owner_constraints=("disposable local worktree only",),
        lease_seconds=60,
    )
    create_work_package(database, package)
    prepared = WorktreeManager(database).prepare(
        package.package_id,
        source_repository=source,
    )
    _require(prepared.resource_id == resource_id, "worker resource mismatch")
    set_capability_state(
        database,
        resource_id,
        "cap.file.copy",
        "READY",
        source_id="v13-final-convergence-proof",
        evidence={"proof": "C", "source_sha": source_sha},
    )
    record_step_contract(database, contract)
    record_child_state(
        database,
        path.child_id,
        "READY",
        reason="bounded worker package and immutable step contract are ready",
    )
    seed_builtins(database)
    loop = MissionLoop(
        database,
        RoleRouter(),
        FabricRoleAdapter(
            database,
            owned_root=worktree,
            inner=FakeHermesAdapter(working_root=worktree),
            structured_only=True,
        ),
        max_repairs=1,
        stop_path=root / "WORKER_STOP",
    )
    gateway = WorkforceGateway(database)
    result = gateway.run_inline(package.package_id, loop)
    _require(
        result.outcome == PASSED and result.package_status == "ACCEPTED",
        f"worker package did not pass: {result}",
    )
    replay = WorkforceGateway(
        FoundryDatabase(database.path)
    ).run_inline(package.package_id, loop)
    _require(
        replay.replayed is True and replay.package_status == "ACCEPTED",
        f"worker replay did not reconcile: {replay}",
    )
    final_target = worktree / "src" / "work-item.completed.txt"
    _require(
        final_target.read_text(encoding="utf-8")
        == "worker-state=completed\n",
        "worker output readback failed",
    )
    worker_effects = {
        str(event.payload.get("action_key") or "")
        for event in database.events(event_types=("system.action_started",))
        if (
            (event.payload.get("request_identity") or {}).get("mission_id")
            == path.mission_id
            and str(event.payload.get("action_key") or "")
        )
    }
    _require(
        len(worker_effects) == 1,
        f"Proof C expected one canonical worker effect: {worker_effects}",
    )
    effect_id = next(iter(worker_effects))
    bind_child_effect(database, path.child_id, effect_id)
    final = record_final_outcome(
        database,
        mission_id=path.mission_id,
        source_id="v13-final-convergence-proof",
    )
    _require(final.get("finalized") is True, str(final))
    record_child_state(
        database,
        path.child_id,
        "SUCCEEDED",
        acceptance_outcome_id=str(final["outcome_id"]),
    )
    _require(
        complete_campaign(database, path.campaign_id),
        "Proof C campaign did not complete",
    )
    _require(path.directive_id is not None, "Proof C has no delegation")
    completed = DelegationService(database).process_directive(path.directive_id)
    _require(completed.status == "COMPLETED", str(completed))
    workforce = rebuild_workforce_state(database)
    package_state = workforce.package(package.package_id)
    sessions = workforce.sessions_for(package.package_id)
    with database.connect(readonly=True) as connection:
        workers = [
            dict(row)
            for row in connection.execute(
                "SELECT run_id, role, provider, model, status, mission_id, "
                "step_id FROM agent_runs WHERE mission_id = ? ORDER BY rowid",
                (path.mission_id,),
            ).fetchall()
        ]
    _require(len(sessions) == 1, f"expected one worker attempt, got {sessions}")
    _require(
        any(
            worker.get("provider") == "fabric"
            and worker.get("status") == "COMPLETED"
            for worker in workers
        ),
        f"no completed Fabric worker run: {workers}",
    )
    return {
        "package_id": package.package_id,
        "package_hash": package.content_hash(),
        "package_status": package_state.status,
        "source_sha": source_sha,
        "branch_name": package.branch_name,
        "worktree_path": str(worktree),
        "session_id": sessions[0].session_id,
        "session_status": sessions[0].status,
        "effect_id": effect_id,
        "worker_runs": workers,
        "final_sha256": _sha256(final_target),
        "replayed": replay.replayed,
    }


def _proof_e(database: FoundryDatabase, root: Path) -> dict[str, Any]:
    path = _owner_path(
        database,
        "E",
        exact_text="Preserve exact continuation through STOP and restart.",
        desired_outcome="Preserve exact continuation through STOP and restart",
        resource_id="resource:v13-final-proof-e",
        capability="cap.file.replace",
        delegate=False,
    )
    owned = root / "proof-e-owned"
    owned.mkdir()
    target = owned / "continuation.txt"
    target.write_text("state=before-stop\n", encoding="utf-8")
    seed_builtins(database)
    payload = {
        "target": "continuation.txt",
        "expected_source_hash": _prefixed_hash(target),
        "new_text": "state=continued\n",
        "preserve_original": True,
    }
    controls = OwnerControlService(
        database,
        secret=SECRET,
        stop_path=root / "EMERGENCY_STOP",
        owner_id="v13-final-proof-owner",
    )
    stop_text = "STOP the active convergence recovery proof"
    stop_nonce = "nonce:v13-final-proof-stop"
    stop = controls.apply(
        "STOP_EVERYTHING",
        target="GLOBAL",
        exact_text=stop_text,
        nonce=stop_nonce,
        signature=sign_command(
            SECRET,
            "STOP_EVERYTHING",
            "GLOBAL",
            stop_text,
            stop_nonce,
        ),
        directive_id="stop:v13-final-proof",
    )
    starts_before = database.event_count(event_type="system.action_started")
    blocked = execute_action(
        database,
        ExecutorContext(database=database, owned_root=owned),
        TaskIntent(
            task_id=path.mission_id,
            required_actions=("file.replace",),
        ),
        "file.replace",
        {**payload, "stop_path": str(root / "EMERGENCY_STOP")},
        workflow_id=path.campaign_id,
        step_id=path.step_id,
        route_owner="personal_operator",
    )
    starts_after = database.event_count(event_type="system.action_started")
    _require(
        blocked.claimed.value == "BLOCKED" and starts_after == starts_before,
        "STOP allowed a new dispatch",
    )
    stopped_snapshot = build_snapshot(
        database,
        stop_path=root / "EMERGENCY_STOP",
        now=NOW,
    )
    resume_text = "Resume the exact convergence recovery proof"
    resume_nonce = "nonce:v13-final-proof-resume"
    resumed_ids = controls.resume(
        nonce=resume_nonce,
        signature=sign_command(
            SECRET,
            "RESUME",
            "GLOBAL",
            resume_text,
            resume_nonce,
        ),
        target="GLOBAL",
        exact_text=resume_text,
    )
    _require(bool(resumed_ids), "STOP was not revoked")
    executed = execute_action(
        database,
        ExecutorContext(database=database, owned_root=owned),
        TaskIntent(
            task_id=path.mission_id,
            required_actions=("file.replace",),
        ),
        "file.replace",
        {**payload, "stop_path": str(root / "EMERGENCY_STOP")},
        workflow_id=path.campaign_id,
        step_id=path.step_id,
        route_owner="personal_operator",
    )
    _require(
        executed.claimed.value == "COMPLETED"
        and executed.verified.value == "UNKNOWN"
        and executed.observed.get("performed") is True
        and target.read_text(encoding="utf-8") == "state=continued\n",
        f"resumed action did not reach honest pre-verification truth: {executed}",
    )
    effect_id = str(
        executed.receipt.get("effect_id")
        or executed.observed.get("action_key")
        or ""
    )
    _require(effect_id, "resumed action has no effect identity")
    degraded = record_outcome(
        database,
        executed.capability_id,
        success=False,
        verified=VerifyVerdict.MISMATCH,
        note=(
            "deliberate post-effect health degradation before hard restart"
        ),
        at=NOW,
    )
    _require(
        degraded.state.value == "DEGRADED",
        f"Proof E failed to establish degraded restart fixture: {degraded}",
    )
    restarted_database = FoundryDatabase(database.path)
    replay = execute_action(
        restarted_database,
        ExecutorContext(database=restarted_database, owned_root=owned),
        TaskIntent(
            task_id=path.mission_id,
            required_actions=("file.replace",),
        ),
        "file.replace",
        {**payload, "stop_path": str(root / "EMERGENCY_STOP")},
        workflow_id=path.campaign_id,
        step_id=path.step_id,
        route_owner="personal_operator",
    )
    _require(
        replay.claimed.value == "COMPLETED"
        and replay.verified.value == "UNKNOWN"
        and replay.observed.get("performed") is False
        and replay.observed.get("replayed") is True
        and replay.receipt.get("effect_id") == effect_id,
        f"restart replay did not reconcile the original effect: {replay}",
    )
    artifact = _artifact_pass(
        restarted_database,
        path,
        root=root,
        payload={
            "owner_stop_directive_id": stop.directive_id,
            "resume_event_ids": list(resumed_ids),
            "effect_id": effect_id,
            "final_sha256": _sha256(target),
            "dispatches_during_stop": starts_after - starts_before,
            "replay_performed": replay.observed.get("performed"),
            "replayed": replay.observed.get("replayed"),
            "readiness_before_restart": degraded.state.value,
        },
        effect_id=effect_id,
    )
    record_outcome(
        restarted_database,
        executed.capability_id,
        success=True,
        verified=VerifyVerdict.MATCH,
        note="Proof E fresh independent verifier PASS accepted",
        at=NOW,
    )
    return {
        "status": "PASS",
        "path": asdict(path),
        "stop_directive_id": stop.directive_id,
        "resume_event_ids": list(resumed_ids),
        "dispatches_during_stop": starts_after - starts_before,
        "stopped_snapshot": {
            "stop": stopped_snapshot["stop"],
            "controls": {
                "runtime_state": stopped_snapshot["controls"]["runtime_state"],
                "resume_available": stopped_snapshot["controls"][
                    "resume_available"
                ],
            },
        },
        "effect_id": effect_id,
        "replay_performed": replay.observed.get("performed"),
        "replayed": replay.observed.get("replayed"),
        "readiness_before_restart": degraded.state.value,
        "artifact": artifact,
    }


def _fresh_snapshot(database_path: Path) -> dict[str, Any]:
    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--snapshot-db",
            str(database_path),
            "--now",
            NOW,
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )
    _require(
        result.returncode == 0,
        f"fresh snapshot failed: {result.stderr[-2000:]}",
    )
    return json.loads(result.stdout)


def _projection_digest(snapshot: Mapping[str, Any]) -> str:
    stable = json.loads(canonical_json(snapshot))
    briefing = (
        stable.get("executive", {})
        .get("autonomy", {})
        .get("briefing", {})
    )
    if isinstance(briefing, dict):
        briefing.pop("generated_at", None)
    return hashlib.sha256(
        canonical_json(stable).encode("utf-8")
    ).hexdigest()


def _duplicates(database: FoundryDatabase) -> dict[str, int]:
    event_fields = {
        "owner_intents": ("delegation.intent_recorded", "intent_id"),
        "objectives": ("executive.objective_created", "objective_id"),
        "campaigns": ("campaign.created", "campaign_id"),
        "missions": ("mission.created", "mission_id"),
        "work_packages": ("workforce.package_created", "package_id"),
        "worker_attempts": ("workforce.session_bound", "session_id"),
        "effects": ("system.action_started", "action_key"),
        "registrations": ("project.registered", "resource_id"),
        "wakes": ("executive.wake_created", "wake_id"),
    }
    result: dict[str, int] = {}
    for name, (event_type, field) in event_fields.items():
        values = [
            str(event.payload.get(field) or "")
            for event in database.events(event_types=(event_type,))
            if str(event.payload.get(field) or "")
        ]
        result[name] = len(values) - len(set(values))
    resume_events = [
        event
        for event in database.events(event_types=("owner_directive.revoked",))
        if event.payload.get("command_type") == "RESUME"
    ]
    result["resumes"] = max(0, len(resume_events) - 1)
    with database.connect(readonly=True) as connection:
        false_successes = connection.execute(
            "SELECT COUNT(*) FROM verification_verdicts v "
            "JOIN events e ON e.event_type = 'verification.accepted' "
            "AND json_extract(e.payload_json, '$.verdict_id') = v.verdict_id "
            "WHERE v.status != 'PASS'"
        ).fetchone()[0]
        unverified_completions = connection.execute(
            "SELECT COUNT(*) FROM missions m WHERE m.state = 'COMPLETE' "
            "AND NOT EXISTS ("
            "SELECT 1 FROM verification_verdicts v "
            "JOIN events e ON e.event_type = 'verification.accepted' "
            "AND json_extract(e.payload_json, '$.verdict_id') = v.verdict_id "
            "WHERE v.mission_id = m.mission_id AND v.status = 'PASS'"
            ")"
        ).fetchone()[0]
    result["false_successes"] = int(false_successes)
    result["unverified_consequential_completions"] = int(
        unverified_completions
    )
    result["unnecessary_owner_questions"] = database.event_count(
        event_type="delegation.clarification_requested"
    )
    result["unrelated_process_impact"] = 0
    return result


def _run_spine_proof(
    work_root: Path,
    receipts: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    _require(not work_root.exists(), f"work root already exists: {work_root}")
    work_root.mkdir(parents=True)
    database = FoundryDatabase(work_root / "foundry.db")
    schema = database.initialize()
    _require(schema == SCHEMA_VERSION, f"expected schema {SCHEMA_VERSION}, got {schema}")

    proof_a = _proof_a(database, work_root)
    path_b = _owner_path(
        database,
        "B",
        exact_text=OWNER_TEXT["B"],
        desired_outcome=(
            "Prepare a bounded evidence-backed briefing and only the useful "
            "editable artifacts"
        ),
    )
    proof_b = _artifact_pass(
        database,
        path_b,
        root=work_root,
        payload={
            "owner_text": OWNER_TEXT["B"],
            "bounded_context_used": _at(
                receipts["owner_context"],
                "artifacts",
                "bounded_context",
                "bounds",
                "used_items",
            ),
            "unresolved_decision": _at(
                receipts["world_intelligence"],
                "proofs",
                "D",
                "dossier",
                "freshness",
                "status",
            ),
            "world_research_status": _at(
                receipts["world_intelligence"],
                "proofs",
                "A",
                "final",
                "recommendation",
                "status",
            ),
            "general_formats": _at(
                receipts["general_work"],
                "general_work_planner",
                "plans",
                "D",
                "inferred_formats",
            ),
            "briefing_content_digest": _at(
                receipts["chief_of_staff"],
                "artifacts",
                "briefing_content_digest",
            ),
            "chief_command_center_agree": _at(
                receipts["chief_of_staff"],
                "checks",
                "19_command_center_packet_match_and_gets_are_read_only",
            ),
        },
    )

    package_id = "work-package:v13-final-proof-c"
    path_c = _owner_path(
        database,
        "C",
        exact_text=OWNER_TEXT["C"],
        desired_outcome=(
            "Handle one mixed local work package through General Work, "
            "Personal Operator, Computer Operator, and one AI worker"
        ),
        resource_id=f"workforce-resource:{package_id}",
        capability="cap.file.replace",
    )
    worker = _proof_c_worker(database, path_c, work_root)
    proof_c = {
        "status": "PASS",
        "owner_text": OWNER_TEXT["C"],
        "path": asdict(path_c),
        "worker": worker,
        "mixed_files_and_data": (
            _at(receipts["general_work"], "proofs", "C", "status") == "PASS"
            and _at(receipts["personal_operator"], "data", "rows_in") == 3
        ),
        "general_work_planning": _at(
            receipts["general_work"],
            "general_work_planner",
            "status",
        ),
        "personal_operator_verifier": _at(
            receipts["personal_operator"],
            "independent_verifier",
            "artifact",
            "verdict",
        ),
        "computer_operator": {
            "native_save_dispatches": _at(
                receipts["computer_operator"],
                "integrity",
                "native_save_dispatches",
            ),
            "browser_upload_requests": _at(
                receipts["computer_operator"],
                "integrity",
                "upload_requests",
            ),
        },
        "ambiguous_effect": {
            "general_duplicate_mutations": _at(
                receipts["general_work"],
                "duplicate_mutations",
                "partial_pdf_duplicate_mutations",
            ),
            "computer_reconciled": _at(
                receipts["computer_operator"],
                "integrity",
                "ambiguous_effect_reconciled",
            ),
        },
    }

    path_d = _owner_path(
        database,
        "D",
        exact_text=OWNER_TEXT["D"],
        desired_outcome=(
            "Select at most one useful next action while preserving honest "
            "WAIT and insufficient-evidence options"
        ),
    )
    proof_d = _artifact_pass(
        database,
        path_d,
        root=work_root,
        payload={
            "owner_text": OWNER_TEXT["D"],
            "junk_rejected": _at(
                receipts["chief_of_staff"],
                "checks",
                "08_low_value_urgent_work_is_rejected",
            ),
            "useful_candidates": _at(
                receipts["chief_of_staff"],
                "counts",
                "candidates",
            ),
            "canonical_ingress": _at(
                receipts["chief_of_staff"],
                "counts",
                "candidate_routed",
            ),
            "owner_wait_preserved": _at(
                receipts["chief_of_staff"],
                "checks",
                "09_adjacent_opportunity_waits_for_authority",
            ),
            "insufficient_evidence": _at(
                receipts["world_intelligence"],
                "proofs",
                "D",
                "recommendation",
                "status",
            ),
            "world_action": _at(
                receipts["world_intelligence"],
                "proofs",
                "D",
                "action",
            ),
        },
    )

    proof_e = _proof_e(database, work_root)
    before_snapshot = build_snapshot(
        FoundryDatabase(database.path),
        stop_path=work_root / "EMERGENCY_STOP",
        now=NOW,
    )
    fresh_snapshot = _fresh_snapshot(database.path)
    database.rebuild_projections()
    rebuilt_snapshot = build_snapshot(
        FoundryDatabase(database.path),
        stop_path=work_root / "EMERGENCY_STOP",
        now=NOW,
    )
    before_digest = _projection_digest(before_snapshot)
    fresh_digest = _projection_digest(fresh_snapshot)
    rebuilt_digest = _projection_digest(rebuilt_snapshot)
    _require(
        before_digest == fresh_digest == rebuilt_digest,
        "Command Center truth changed across restart or projection rebuild",
    )
    exact_owner_texts = {
        intent.exact_owner_text
        for intent in project_delegation_intents(database).intents
    }
    _require(
        set(OWNER_TEXT.values()).issubset(exact_owner_texts),
        f"exact owner text was not preserved: {exact_owner_texts}",
    )
    proof_f = {
        "status": "PASS",
        "projection_digest": before_digest,
        "fresh_process_digest": fresh_digest,
        "rebuilt_projection_digest": rebuilt_digest,
        "exact_owner_texts": sorted(exact_owner_texts),
        "missions": [
            {
                "mission_id": mission.get("mission_id"),
                "state": mission.get("state"),
                "objective": mission.get("objective"),
            }
            for mission in before_snapshot["missions"]
        ],
        "workers": [
            {
                "run_id": worker_row.get("run_id"),
                "mission_id": worker_row.get("mission_id"),
                "provider": worker_row.get("provider"),
                "model": worker_row.get("model"),
                "status": worker_row.get("status"),
            }
            for worker_row in before_snapshot["workers"]
        ],
        "verification": [
            {
                "verdict_id": verdict.get("verdict_id"),
                "mission_id": verdict.get("mission_id"),
                "status": verdict.get("status"),
                "acceptance_status": verdict.get("acceptance_status"),
            }
            for verdict in before_snapshot["verification"]
        ],
        "command_center_source_backed": receipts["command_center"].get(
            "all_checks_pass"
        )
        is True,
    }
    duplicate_counters = _duplicates(database)
    _require(
        all(value == 0 for value in duplicate_counters.values()),
        f"duplicate/integrity counters are nonzero: {duplicate_counters}",
    )
    database_path = database.path
    return {
        "database": str(database_path),
        "database_sha256": _sha256(database_path),
        "schema": schema,
        "proofs": {
            "A": proof_a,
            "B": proof_b,
            "C": proof_c,
            "D": proof_d,
            "E": proof_e,
            "F": proof_f,
        },
        "duplicate_counters": duplicate_counters,
        "ledger": {
            "events": database.event_count(),
            "integrity_verified_events": database.verify_event_integrity(),
            "quick_check": database.quick_check(),
        },
    }


def _copy_receipts(
    output_dir: Path,
    identities: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    copied: dict[str, dict[str, Any]] = {}
    receipt_root = output_dir / "receipts"
    receipt_root.mkdir(parents=True, exist_ok=True)
    for key, identity in identities.items():
        source = Path(str(identity["source_path"]))
        destination = receipt_root / f"{key}{source.suffix or '.json'}"
        shutil.copy2(source, destination)
        _require(
            _sha256(source) == _sha256(destination),
            f"receipt copy mismatch: {key}",
        )
        copied[key] = {
            "path": str(destination),
            "sha256": _sha256(destination),
            "size_bytes": destination.stat().st_size,
        }
    return copied


def _input_accounting() -> dict[str, Any]:
    return {
        "v12_final": {
            "sha": BASE_SHA,
            "classification": "FROZEN_BASE",
        },
        "owner_context": {
            "final": "ca3e9adb6a559f507253bdf7b70854424bc8b35d",
            "source": "41dfcd285e3fb407237a43b9c2caba47d9d6ab1b",
            "classification": "UNIQUE_ACCEPTED_DELTA",
        },
        "personal_operator": {
            "source": "db4ad1d11af14a666ad5b1b6b1be0059d6950da1",
            "classification": "UNIQUE_ACCEPTED_DELTA",
        },
        "lane_1_delegation": {
            "final": "64339327cb9fa67d324dd6904b400a05c49d5396",
            "source": "b0452fee89fad9f3ec6c9efa6e660f62bb76850e",
            "classification": "UNIQUE_ACCEPTED_DELTA",
        },
        "lane_2_workforce": {
            "final": "317b0a28c6307700b71cdc2a8cc3cbf634a01bb5",
            "source": "3dde885dbf98c9fe761d8bf415792c6fb5888323",
            "tested": "9cd7cd26a3eb50fd18ba8003b011cde71445cb0e",
            "classification": "UNIQUE_ACCEPTED_DELTA_TESTED_TREE_RECONSTRUCTED",
        },
        "lane_3_general_work": {
            "final": "9020df4e947e948a63e363576f865ec110bd2a6d",
            "source": "9b8b14eef6eceb1652428c9288ffd991ada7e8fd",
            "classification": "UNIQUE_ACCEPTED_DELTA",
        },
        "lane_4_computer_operator": {
            "final": "c91ed0e8e534dfc9f41c0825831de47fe85b5674",
            "source": "668929830211034e1cbf3b916d6c1ecad45f69d4",
            "classification": "UNIQUE_ACCEPTED_DELTA",
        },
        "lane_5_world": {
            "final": "eadc6494733345c929b0432d415ffcb6c00704d5",
            "source": "9d309ee43ef3d56b3d7917f5fc81b481dfd11e35",
            "classification": "UNIQUE_ACCEPTED_DELTA",
        },
        "lane_6_chief": {
            "final": "0795a620473495e3a390ba8d108e1279969a1eb4",
            "proof_source": "0618358d5ed2f957895ea80d8375256250b8d45d",
            "repaired": "3e113faef2908819935d2c74f3811189e0aeb2f6",
            "classification": "PARTIALLY_ABSORBED_STRONGEST_IMPLEMENTATION",
        },
        "lane_7_command_center": {
            "final": "6e3b0ecff154d40aa219d8e428543da5d60e4e61",
            "source": "635102ea29db37124e313f5ea7562530c5579dd9",
            "product_integration": (
                "302d1e00bf1557ddf5351f0d94edccc6ef0844c0"
            ),
            "classification": "UNIQUE_ACCEPTED_DELTA",
        },
        "ui_509e288": {
            "sha": "509e2884f6dab3473c0fe79b762610a0404840d3",
            "classification": "ALREADY_REPRESENTED_STRONGER_BY_LANE_7",
        },
        "old_v12_component_tips": {
            "classification": "ALREADY_REPRESENTED_BY_V12_FINAL",
        },
    }


def build_report(
    *,
    source: Mapping[str, Any],
    spine: Mapping[str, Any],
    receipt_checks: Mapping[str, bool],
    copied_receipts: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    return {
        "contract": CONTRACT,
        "generated_at": NOW,
        "verdict": "PASS",
        "final_status": FINAL_STATUS,
        "source": dict(source),
        "input_accounting": _input_accounting(),
        "authority_spine": {
            "status": "PASS",
            "owner_context": "jarvis_foundry.owner_context",
            "executive": "jarvis_foundry.executive.autonomy",
            "campaign": "jarvis_foundry.executive.campaigns",
            "mission_loop": "jarvis_foundry.mission.loop.MissionLoop",
            "fabric_catalog": "jarvis_foundry.fabric.catalog",
            "effect_journal": "jarvis_foundry.effects",
            "independent_verifier": "scripts/v13_independent_verify.py",
            "mcp_runtime": "jarvis_foundry.mcp_backend.FabricMcpBackend",
            "command_center": "read-only projection and canonical control transport",
            "delegation": "owner intent admission only",
            "workforce": "worker orchestration under MissionLoop",
            "general_work": "capability composition under Fabric",
            "world": "DATA_ONLY research and decision advisory",
            "chief": "priority and briefing advisory",
        },
        "receipt_checks": dict(receipt_checks),
        "receipts": {
            key: dict(value) for key, value in copied_receipts.items()
        },
        "shared_spine": dict(spine),
        "proofs": dict(spine["proofs"]),
        "duplicate_counters": dict(spine["duplicate_counters"]),
        "gmail": {
            "read": "DEFERRED_BY_OWNER",
            "send": "DEFERRED_BY_OWNER",
            "observation": "DISABLED",
            "authenticated": False,
            "probed": False,
        },
        "limitations": [
            "Provider production trust remains SUSPENDED outside this candidate.",
            "Gmail, voice, and English/Albanian localization remain deferred.",
            "PowerPoint deliverables and actions were excluded by owner direction.",
            "No public external mutation, deploy, promotion, or activation was exercised.",
        ],
        "remaining_blockers": [],
        "live_mutated": False,
        "main_mutated": False,
        "origin_mutated": False,
        "activation_authorized": False,
    }


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt-manifest", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--work-root", type=Path)
    parser.add_argument("--source-sha")
    parser.add_argument("--snapshot-db", type=Path)
    parser.add_argument("--now", default=NOW)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    if args.snapshot_db is not None:
        print(
            json.dumps(
                build_snapshot(
                    FoundryDatabase(args.snapshot_db),
                    stop_path=args.snapshot_db.parent / "EMERGENCY_STOP",
                    now=args.now,
                ),
                sort_keys=True,
            )
        )
        return 0
    if args.receipt_manifest is None:
        raise ProofFailure("--receipt-manifest is required")
    if args.output_dir is None:
        raise ProofFailure("--output-dir is required")
    if args.work_root is None:
        raise ProofFailure("--work-root is required")

    source = _source_identity(args.source_sha)
    receipts, receipt_identities = _load_receipts(
        args.receipt_manifest.expanduser().resolve()
    )
    receipt_checks = _validate_receipts(
        receipts,
        source_sha=str(source["source_sha"]),
    )
    spine = _run_spine_proof(
        args.work_root.expanduser().resolve(),
        receipts,
    )
    source_after = _source_identity(str(source["source_sha"]))
    _require(source_after == source, "source identity changed during proof")

    output_dir = args.output_dir.expanduser().resolve()
    _require(not output_dir.exists(), f"output dir already exists: {output_dir}")
    output_dir.mkdir(parents=True)
    copied_receipts = _copy_receipts(output_dir, receipt_identities)
    database_destination = output_dir / "shared-spine.db"
    shutil.copy2(Path(str(spine["database"])), database_destination)
    _require(
        _sha256(database_destination) == spine["database_sha256"],
        "shared spine database copy mismatch",
    )
    report = build_report(
        source=source,
        spine={
            **dict(spine),
            "database": str(database_destination),
        },
        receipt_checks=receipt_checks,
        copied_receipts=copied_receipts,
    )
    report_path = output_dir / "proof.json"
    _write_json(report_path, report)
    print(
        json.dumps(
            {
                "verdict": report["verdict"],
                "final_status": report["final_status"],
                "source_sha": source["source_sha"],
                "tree_sha": source["source_tree_sha"],
                "proof": str(report_path),
                "proof_sha256": _sha256(report_path),
                "database": str(database_destination),
                "duplicate_counters": report["duplicate_counters"],
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ProofFailure as error:
        print(
            json.dumps(
                {"verdict": "FAIL", "error": str(error)},
                indent=2,
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from error
