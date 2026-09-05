"""Opt-in Workforce envelopes for AI-delegated General Work steps."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from ..database import FoundryDatabase
from ..events import canonical_json
from ..fabric.catalog import get_spec
from ..fabric.contracts import build_adapter_request
from ..mission.contracts import latest_contract
from ..platform.windows_subprocess import headless_run_kwargs
from ..workforce.contracts import WorkPackage
from ..workforce.state import create_work_package, rebuild_workforce_state
from .ledger import load_recorded_plan, target_claims
from .materialization import MATERIALIZED_EVENT, MaterializedStep


WORKFORCE_DELEGATION_SCHEMA = "v13.general-work-workforce-delegation.v1"
_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


class GeneralWorkWorkforceError(ValueError):
    """An AI-delegated step cannot enter the Workforce authority."""


@dataclass(frozen=True, slots=True)
class GeneralWorkWorkforceBinding:
    plan_id: str
    plan_hash: str
    source_step_id: str
    materialized_step: MaterializedStep
    package: WorkPackage
    inserted: bool

    @property
    def replayed(self) -> bool:
        return not self.inserted


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
        **headless_run_kwargs(),
    )
    if result.returncode != 0:
        detail = result.stderr.strip() or f"git {' '.join(args)} failed"
        raise GeneralWorkWorkforceError(detail)
    return result.stdout.strip()


def _git_source(source_repository: str | Path, source_sha: str) -> Path:
    source = Path(source_repository).expanduser().resolve()
    if not source.is_dir():
        raise GeneralWorkWorkforceError(
            f"AI-delegated General Work requires a Git repository: {source}"
        )
    normalized_sha = str(source_sha).strip().lower()
    if not _SHA_PATTERN.fullmatch(normalized_sha):
        raise GeneralWorkWorkforceError(
            "AI-delegated General Work requires a full Git commit SHA"
        )
    try:
        top_level = Path(_git(source, "rev-parse", "--show-toplevel")).resolve()
        object_type = _git(top_level, "cat-file", "-t", normalized_sha)
    except GeneralWorkWorkforceError as error:
        raise GeneralWorkWorkforceError(
            f"AI-delegated General Work is not Git-backed: {error}"
        ) from error
    if object_type != "commit":
        raise GeneralWorkWorkforceError(
            f"General Work source is not a Git commit: {normalized_sha}"
        )
    return top_level


def _materialized_step(
    database: FoundryDatabase,
    *,
    plan_id: str,
    plan_hash: str,
    source_step_id: str,
) -> MaterializedStep:
    payloads = [
        event.payload
        for event in database.events(event_types=(MATERIALIZED_EVENT,))
        if event.payload.get("plan_id") == plan_id
        and event.payload.get("plan_hash") == plan_hash
    ]
    if not payloads:
        raise GeneralWorkWorkforceError(
            "General Work must be materialized before AI delegation"
        )
    raw_steps = payloads[-1].get("steps")
    if not isinstance(raw_steps, list):
        raise GeneralWorkWorkforceError(
            "General Work materialization has malformed step lineage"
        )
    for raw in raw_steps:
        if not isinstance(raw, Mapping):
            continue
        if raw.get("source_step_id") != source_step_id:
            continue
        try:
            return MaterializedStep(
                source_step_id=str(raw["source_step_id"]),
                runtime_step_id=str(raw["runtime_step_id"]),
                mission_id=str(raw["mission_id"]),
                child_id=str(raw["child_id"]),
                capability_id=str(raw["capability_id"]),
                target_relative_path=str(raw["target_relative_path"]),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise GeneralWorkWorkforceError(
                "General Work materialization has malformed step lineage"
            ) from error
    raise GeneralWorkWorkforceError(
        f"General Work step is not materialized: {source_step_id}"
    )


def general_work_work_package_id(plan_id: str, source_step_id: str) -> str:
    """Return the stable package identity reserved by an AI-delegated step."""

    identity = hashlib.sha256(
        canonical_json({
            "plan_id": str(plan_id),
            "source_step_id": source_step_id,
        }).encode("utf-8")
    ).hexdigest()[:24]
    return f"general-work-package-{identity}"


def create_general_work_work_package(
    database: FoundryDatabase,
    *,
    plan_id: str,
    source_step_id: str,
    ai_delegated: bool,
    source_repository: str | Path | None = None,
    source_sha: str | None = None,
    repository_id: str | None = None,
    branch_name: str | None = None,
    worktree_path: str | Path | None = None,
    allowed_paths: Sequence[str] = (),
    forbidden_paths: Sequence[str] = (),
    required_tests: Sequence[str] = (),
    duration_seconds: int = 900,
    lease_seconds: int = 1200,
    cost_budget_usd: float | None = None,
    deadline: str | None = None,
    stop_path: str | Path | None = None,
) -> GeneralWorkWorkforceBinding | None:
    """Create one WorkPackage only for an explicit Git-backed AI delegation."""

    if not ai_delegated:
        return None
    required = {
        "source_repository": source_repository,
        "source_sha": source_sha,
        "repository_id": repository_id,
        "branch_name": branch_name,
        "worktree_path": worktree_path,
    }
    missing = sorted(
        name for name, value in required.items()
        if value is None or not str(value).strip()
    )
    if missing:
        raise GeneralWorkWorkforceError(
            f"AI-delegated General Work is missing Git authority: {missing}"
        )

    plan = load_recorded_plan(database, str(plan_id))
    source_steps = {step.step_id: step for step in plan.steps}
    source_step = source_steps.get(str(source_step_id))
    if source_step is None:
        raise GeneralWorkWorkforceError(
            f"General Work plan has no step: {source_step_id}"
        )
    package_id = general_work_work_package_id(plan.plan_id, source_step.step_id)
    expected_resource_id = f"workforce-resource:{package_id}"
    if plan.resource_id != expected_resource_id:
        raise GeneralWorkWorkforceError(
            "AI-delegated General Work must be materialized in its reserved "
            f"Workforce resource: {expected_resource_id}"
        )
    materialized = _materialized_step(
        database,
        plan_id=plan.plan_id,
        plan_hash=plan.plan_hash,
        source_step_id=source_step.step_id,
    )
    contract = latest_contract(
        database,
        materialized.mission_id,
        step_id=materialized.runtime_step_id,
        allow_invalid=False,
    )
    if contract is None:
        raise GeneralWorkWorkforceError(
            "General Work step has no canonical MissionStepContract"
        )

    claims = {
        str(claim.get("target_ref") or "")
        for claim in target_claims(database, plan_id=plan.plan_id)
    }
    unclaimed = sorted(set(contract.expected_artifacts).difference(claims))
    if unclaimed:
        raise GeneralWorkWorkforceError(
            f"General Work outputs lack prior target claims: {unclaimed}"
        )

    source_root = _git_source(
        Path(str(source_repository)),
        str(source_sha),
    )
    target_worktree = Path(str(worktree_path)).expanduser()
    if not target_worktree.is_absolute():
        raise GeneralWorkWorkforceError(
            "AI-delegated General Work requires an absolute worktree path"
        )
    target_worktree = target_worktree.resolve(strict=False)

    spec = get_spec(database, contract.capability)
    if spec is None:
        raise GeneralWorkWorkforceError(
            f"General Work capability is not registered: {contract.capability}"
        )
    try:
        payload = json.loads(contract.instructions)
    except json.JSONDecodeError as error:
        raise GeneralWorkWorkforceError(
            "General Work contract instructions are not structured JSON"
        ) from error
    if not isinstance(payload, dict):
        raise GeneralWorkWorkforceError(
            "General Work contract instructions must be a JSON object"
        )
    request = build_adapter_request(
        mission_id=contract.mission_id,
        workflow_id=f"workflow:{contract.mission_id}",
        step_id=contract.step_id,
        step_version=contract.version,
        attempt=1,
        action_kind=spec.action_kind,
        payload=payload,
        spec=spec,
    )

    package_paths = tuple(dict.fromkeys(
        str(value)
        for value in (
            tuple(allowed_paths)
            or contract.expected_artifacts
            or (contract.target_relative_path,)
        )
        if str(value).strip()
    ))
    package_forbidden = tuple(dict.fromkeys((
        *contract.forbidden_paths,
        *(str(value) for value in forbidden_paths),
    )))
    package = WorkPackage(
        package_id=package_id,
        parent_objective_id=plan.objective_id,
        campaign_id=plan.campaign_id,
        mission_id=materialized.mission_id,
        step_id=materialized.runtime_step_id,
        role="builder",
        capability=contract.capability,
        bounded_goal=source_step.description,
        acceptance_criteria=contract.verification_criteria,
        source_sha=str(source_sha).strip().lower(),
        repository_id=str(repository_id),
        branch_name=str(branch_name),
        worktree_path=str(target_worktree),
        allowed_paths=package_paths,
        forbidden_paths=package_forbidden,
        mission_step_contract_id=contract.contract_id,
        mission_step_contract_hash=contract.content_hash(),
        expected_artifacts=contract.expected_artifacts,
        required_tests=tuple(str(value) for value in required_tests),
        evidence_requirements=(
            "General Work target claim predates Workforce lease",
            "MissionLoop retains final acceptance authority",
        ),
        risk_class=contract.risk_class,
        duration_seconds=duration_seconds,
        cost_budget_usd=cost_budget_usd,
        deadline=deadline,
        lease_seconds=lease_seconds,
        effect_action_key=request.effect_id,
        owner_constraints=(
            "preserve immutable General Work sources",
            "reconcile ambiguous effects before retry",
        ),
        lineage={
            "schema": WORKFORCE_DELEGATION_SCHEMA,
            "ai_delegated": True,
            "general_work_plan_id": plan.plan_id,
            "general_work_plan_hash": plan.plan_hash,
            "general_work_source_step_id": source_step.step_id,
            "general_work_mission_id": materialized.mission_id,
            "general_work_runtime_step_id": materialized.runtime_step_id,
            "general_work_resource_id": plan.resource_id,
            "source_repository": str(source_root),
        },
    )
    existing = rebuild_workforce_state(database).packages.get(package_id)
    state = create_work_package(
        database,
        package,
        stop_path=stop_path,
        source_id="v13-general-work",
    )
    return GeneralWorkWorkforceBinding(
        plan_id=plan.plan_id,
        plan_hash=plan.plan_hash,
        source_step_id=source_step.step_id,
        materialized_step=materialized,
        package=state.package,
        inserted=existing is None,
    )


__all__ = [
    "WORKFORCE_DELEGATION_SCHEMA",
    "GeneralWorkWorkforceBinding",
    "GeneralWorkWorkforceError",
    "create_general_work_work_package",
    "general_work_work_package_id",
]
