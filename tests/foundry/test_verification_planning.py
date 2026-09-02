from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from jarvis_foundry.mission.contracts import MissionStepContract
from jarvis_foundry.mission.verifier_binding import verifier_contract_digest
from jarvis_foundry.mission.verification_planning import (
    CURRENT_VERIFIER_CAPABILITIES,
    NEEDS_CONTEXT,
    PLAN_SCHEMA,
    READY,
    VerificationPlanningError,
    compile_verification_plan,
)
from jarvis_foundry.workers.contracts import WorkerStepContract


BASE = "00ee2e148c12151699a796649a1cb87ed892a69b"


@dataclass(frozen=True, slots=True)
class ProjectProfile:
    """Exact structural shape exposed by the parallel ProjectProfile lane."""

    value: dict[str, object]

    @property
    def root(self) -> str:
        return str(self.value["root"])

    def to_dict(self) -> dict[str, object]:
        return dict(self.value)


def _python_candidate() -> dict[str, object]:
    return {
        "candidate_id": "cand-v11-verification-plan",
        "objective_id": "initiative-objective:cand-v11-verification-plan",
        "objective": "Reject invalid verification candidates before A3 mission execution",
        "structured_outcome": {
            "summary": (
                "Invalid candidate contracts fail closed and valid Python changes "
                "produce a targeted verifier specification"
            ),
            "project_type": "python",
            "task_kind": "code",
            "changed_paths": [
                "src/jarvis_foundry/mission/verification_planning.py",
                "tests/foundry/test_verification_planning.py",
            ],
            "test_targets": [
                "tests/foundry/test_verification_planning.py::"
                "test_invalid_candidate_cannot_supply_fake_success_criteria"
            ],
            "postconditions": [{
                "kind": "behavior",
                "target": "compile_verification_plan",
                "assertion": (
                    "raises VerificationPlanningError before emitting verifier "
                    "criteria for an invalid candidate contract"
                ),
                "evidence": "targeted pytest receipt and independent source readback",
            }],
            "forbidden_side_effects": ["no network or provider access"],
            "rollback_cleanup_expectations": [
                "remove only the candidate module and its focused test if the lane is rejected"
            ],
        },
    }


def _python_profile() -> ProjectProfile:
    return ProjectProfile({
        "schema": "jarvis.project-profile.v1",
        "profile_id": "profile-jarvis-foundry",
        "profile_hash": "sha256:" + "a" * 64,
        "project_id": "JARVIS Foundry",
        "root": r"C:\temp\JARVIS-V11-Verification-Planning-20260902",
        "stack": {
            "classification": "PYTHON",
            "languages": {
                "status": "KNOWN",
                "values": [{"name": "Python", "evidence": ["file:pyproject.toml"]}],
            },
            "frameworks": {
                "status": "KNOWN",
                "values": [{"name": "pytest", "kind": "test_framework"}],
            },
            "components": [{
                "name": "jarvis-foundry", "kind": "python", "root": ".",
            }],
        },
        "important_manifests": [{"path": "pyproject.toml", "status": "READ"}],
        "repository_state": {
            "head": BASE,
            "branch": "candidate/v11-verification-planning-20260902",
            "dirty": False,
        },
        "commands": {
            "test": {
                "status": "KNOWN",
                "items": [{
                    "argv": ["python", "-m", "pytest", "tests/foundry"],
                    "cwd": ".",
                    "source": "file:pyproject.toml#[tool.pytest.ini_options]",
                    "execute": False,
                }],
            },
            "lint": {"status": "UNKNOWN", "items": []},
            "typecheck": {"status": "UNKNOWN", "items": []},
            "build": {"status": "UNKNOWN", "items": []},
        },
        "known_constraints": [{
            "text": "Never change live production or push from an isolated build lane.",
            "evidence": "file:AGENTS.md#L90",
        }],
        "forbidden_paths": [".env"],
    })


def test_python_plan_is_minimal_pure_and_deterministic() -> None:
    candidate = _python_candidate()
    profile = _python_profile()

    first = compile_verification_plan(
        candidate,
        # The compiler must not require this path to exist or inspect it.
        r"C:\does-not-exist\verification-planning",
        profile,
    )
    replay = compile_verification_plan(
        dict(reversed(list(candidate.items()))),
        r"C:\does-not-exist\verification-planning",
        profile,
    )

    assert first.status == READY
    assert first.project_context_status == "AVAILABLE"
    assert first.project_type == "python"
    assert len(first.tests_to_run) == 1
    assert first.tests_to_run[0].argv == (
        "python", "-m", "pytest",
        "tests/foundry/test_verification_planning.py::test_invalid_candidate_cannot_supply_fake_success_criteria",
    )
    assert [(item.kind, item.check_id) for item in first.checks_to_run] == [
        ("compile", "python-compile")
    ]
    assert "no network or provider access" in first.forbidden_side_effects
    assert ".env" in first.forbidden_paths
    assert any(
        item.startswith("project-profile:profile-jarvis-foundry:")
        for item in first.source_contract_refs
    )
    assert first.to_payload() == replay.to_payload()
    assert first.plan_id == replay.plan_id
    assert first.contract_hash == replay.contract_hash
    assert first.to_payload()["schema"] == PLAN_SCHEMA


def test_nextjs_plan_runs_only_targeted_tests_and_justified_checks() -> None:
    candidate = {
        "candidate_id": "cand-owner-dashboard-route",
        "objective": "Add a typed owner-dashboard route with an honest empty state",
        "structured_outcome": {
            "summary": "The new route renders its empty state and survives a production build",
            "project_type": "nextjs",
            "task_kind": "code",
            "changed_paths": [
                "app/owner-dashboard/page.tsx",
                "app/owner-dashboard/page.test.tsx",
            ],
            "test_targets": ["app/owner-dashboard/page.test.tsx"],
            "required_checks": ["typecheck", "build"],
            "build_required": True,
            "build_justification": (
                "a new Next.js route crosses the route compiler and server/client boundary"
            ),
            "postconditions": [{
                "kind": "behavior",
                "target": "/owner-dashboard",
                "assertion": "renders the typed empty state when no owner work is available",
                "evidence": "targeted component test and production-build receipt",
            }],
        },
    }
    profile = {
        "schema": "jarvis.project-profile.v1",
        "profile_id": "profile-owner-dashboard",
        "profile_hash": "sha256:" + "b" * 64,
        "project_id": "Owner Dashboard",
        "root": r"C:\projects\owner-dashboard",
        "stack": {
            "classification": "NODE",
            "languages": {
                "status": "KNOWN", "values": [{"name": "TypeScript"}],
            },
            "frameworks": {
                "status": "KNOWN", "values": [{"name": "Next.js"}],
            },
        },
        "important_manifests": [
            {"path": "package.json"}, {"path": "next.config.ts"},
            {"path": "tsconfig.json"},
        ],
        "repository_state": {
            "head": "1" * 40,
            "branch": "candidate/owner-dashboard-route",
            "dirty": False,
        },
        "commands": {
            "test": {"status": "KNOWN", "items": [{
                "argv": ["npm", "run", "test"], "cwd": ".", "execute": False,
            }]},
            "typecheck": {"status": "KNOWN", "items": [{
                "argv": ["npm", "run", "typecheck"], "cwd": ".", "execute": False,
            }]},
            "build": {"status": "KNOWN", "items": [{
                "argv": ["npm", "run", "build"], "cwd": ".", "execute": False,
            }]},
            "lint": {"status": "KNOWN", "items": [{
                "argv": ["npm", "run", "lint"], "cwd": ".", "execute": False,
            }]},
        },
    }

    actionable_context = {
        "schema": "jarvis.actionable-project-context.v1",
        "candidate": {
            "candidate_id": candidate["candidate_id"],
            "target_project": profile["project_id"],
        },
        "profile_ref": {
            "profile_id": profile["profile_id"],
            "profile_hash": profile["profile_hash"],
        },
        "actionable_context": {
            key: value for key, value in profile.items()
            if key not in {"schema", "profile_id", "profile_hash", "project_id"}
        },
        "execution": {"allowed": False, "commands_executed": False},
    }
    plan = compile_verification_plan(
        candidate, profile["root"], actionable_context)

    assert plan.status == READY
    assert plan.tests_to_run[0].argv == (
        "npm", "run", "test", "--", "app/owner-dashboard/page.test.tsx")
    assert [item.kind for item in plan.checks_to_run] == ["typecheck", "build"]
    assert all(item.kind != "lint" for item in plan.checks_to_run)
    assert "route compiler" in plan.checks_to_run[-1].proves


def test_mixed_project_profile_selects_only_the_candidate_component() -> None:
    candidate = {
        "candidate_id": "cand-backend-validation",
        "objective": "Reject malformed API payloads in the Python backend",
        "structured_outcome": {
            "summary": "Malformed payloads return the typed validation response",
            "project_type": "python",
            "component_root": "backend",
            "task_kind": "code",
            "changed_paths": [
                "backend/app/validation.py",
                "backend/tests/test_validation.py",
            ],
            "test_targets": [
                "backend/tests/test_validation.py::test_malformed_payload",
            ],
            "postconditions": [{
                "kind": "behavior",
                "target": "POST /items",
                "assertion": "returns the typed validation response for a malformed payload",
                "evidence": "targeted backend pytest receipt and response assertion",
            }],
        },
    }
    profile = {
        "schema": "jarvis.project-profile.v1",
        "profile_id": "profile-mixed-owner-project",
        "profile_hash": "sha256:" + "d" * 64,
        "project_id": "owner-project",
        "root": r"C:\projects\owner-project",
        "stack": {
            "classification": "MIXED",
            "languages": {"status": "KNOWN", "values": [
                {"name": "Python"}, {"name": "TypeScript"},
            ]},
            "frameworks": {"status": "KNOWN", "values": [
                {"name": "FastAPI"}, {"name": "Next.js"},
            ]},
            "components": [
                {"name": "api", "kind": "python", "root": "backend"},
                {"name": "web", "kind": "node", "root": "frontend"},
            ],
        },
        "repository_state": {
            "head": "3" * 40, "branch": "candidate/backend-validation",
        },
        "commands": {
            "test": {"status": "KNOWN", "items": [
                {"argv": ["uv", "run", "pytest", "tests"], "cwd": "backend"},
                {"argv": ["pnpm", "run", "test"], "cwd": "frontend"},
            ]},
            "lint": {"status": "UNKNOWN", "items": []},
            "typecheck": {"status": "KNOWN", "items": [
                {"argv": ["pnpm", "run", "typecheck"], "cwd": "frontend"},
            ]},
            "build": {"status": "UNKNOWN", "items": []},
        },
    }

    plan = compile_verification_plan(candidate, profile["root"], profile)

    assert plan.status == READY
    assert plan.project_type == "python"
    assert plan.tests_to_run[0].cwd == "backend"
    assert plan.tests_to_run[0].argv == (
        "uv", "run", "pytest",
        "tests/test_validation.py::test_malformed_payload",
    )
    compile_check = next(item for item in plan.checks_to_run if item.kind == "compile")
    assert compile_check.cwd == "backend"
    assert compile_check.argv == (
        "uv", "run", "python", "-m", "py_compile",
        "app/validation.py", "tests/test_validation.py",
    )
    assert all(item.kind != "typecheck" for item in plan.checks_to_run)


def test_document_plan_requires_semantic_reopen_not_file_existence() -> None:
    candidate = {
        "candidate_id": "cand-decision-record",
        "objective": "Create the owner decision record as a DOCX artifact",
        "structured_outcome": {
            "summary": "A readable decision record contains the decision, rationale, and next action",
            "task_kind": "document",
            "expected_artifacts": ["artifacts/owner-decision.docx"],
            "postconditions": [{
                "kind": "content",
                "target": "artifacts/owner-decision.docx",
                "assertion": (
                    "reopened paragraphs contain the decision, rationale, and next-action sections"
                ),
                "evidence": "independent python-docx readback receipt",
            }],
        },
    }
    capabilities = set(CURRENT_VERIFIER_CAPABILITIES)
    capabilities.add("structured_document_readback")
    plan = compile_verification_plan(
        candidate,
        r"C:\temp\owner-decision",
        {
            "project_id": "Owner decision artifact",
            "root": r"C:\temp\owner-decision",
            "project_type": "document",
        },
        verifier_capabilities=capabilities,
    )

    assert plan.status == READY
    assert not plan.tests_to_run
    assert not plan.checks_to_run
    content = next(item for item in plan.independent_readback if item.kind == "content")
    assert content.verifier_capability == "structured_document_readback"
    assert "decision, rationale" in content.assertions[0]
    assert "file exists" not in " ".join(plan.acceptance_criteria).lower()


def test_git_task_compiles_git_truth_and_forbidden_remote_effects() -> None:
    candidate = {
        "candidate_id": "cand-local-git-checkpoint",
        "objective": "Create one local candidate checkpoint without publishing it",
        "structured_outcome": {
            "summary": "HEAD records one scoped local checkpoint above the pinned base",
            "task_kind": "git",
            "postconditions": [{
                "kind": "git",
                "target": "HEAD",
                "assertion": "is a descendant of the pinned base and contains only declared paths",
                "evidence": "independent rev-parse, status, and diff readback",
            }],
        },
    }
    profile = {
        "schema": "jarvis.project-profile.v1",
        "profile_id": "profile-local-candidate",
        "profile_hash": "sha256:" + "c" * 64,
        "project_id": "Local candidate",
        "root": r"C:\temp\local-candidate",
        "stack": {"classification": "UNKNOWN"},
        "repository_state": {
            "head": "2" * 40,
            "branch": "candidate/local-checkpoint",
            "dirty": False,
        },
    }

    plan = compile_verification_plan(candidate, profile["root"], profile)

    assert plan.status == READY
    assert [item.argv for item in plan.checks_to_run] == [
        ("git", "diff", "--check", f"{'2' * 40}...HEAD")
    ]
    git_readback = next(item for item in plan.independent_readback if item.kind == "git")
    assert any("exact base" in item for item in git_readback.assertions)
    assert any("checked-out branch" in item for item in git_readback.assertions)
    assert any("no merge, push, deploy" in item for item in plan.forbidden_side_effects)


def test_invalid_candidate_cannot_supply_fake_success_criteria() -> None:
    with pytest.raises(VerificationPlanningError, match="structured_outcome"):
        compile_verification_plan(
            {
                "candidate_id": "cand-fake",
                "objective": "Pretend the change succeeded",
                "expected_outcome": "success",
                "acceptance_criteria": ["builder says PASS"],
            },
            "project",
            {"project_type": "python"},
        )

    with pytest.raises(VerificationPlanningError, match="builder/candidate claim"):
        compile_verification_plan(
            {
                "candidate_id": "cand-self-certified",
                "objective": "Self-certify a result",
                "structured_outcome": {
                    "summary": "The result has an observable behavior",
                    "project_type": "python",
                    "postconditions": [{
                        "kind": "behavior",
                        "target": "result",
                        "assertion": "returns the expected typed value for the invalid-input case",
                        "evidence": "builder claim",
                    }],
                },
            },
            "project",
            {"project_type": "python"},
        )

    with pytest.raises(VerificationPlanningError, match="success claim"):
        compile_verification_plan(
            {
                "candidate_id": "cand-file-only",
                "objective": "Create a document",
                "structured_outcome": {
                    "summary": "The document records a decision",
                    "task_kind": "document",
                    "expected_artifacts": ["decision.docx"],
                    "postconditions": [{
                        "kind": "content",
                        "target": "decision.docx",
                        "assertion": "file exists",
                        "evidence": "filesystem stat",
                    }],
                },
            },
            "project",
            {"project_type": "document"},
        )

    poisoned = _python_candidate()
    poisoned_outcome = poisoned["structured_outcome"]
    assert isinstance(poisoned_outcome, dict)
    poisoned_outcome["test_targets"] = [
        "tests/foundry/test_verification_planning.py\n--collect-only",
    ]
    with pytest.raises(VerificationPlanningError, match="control character"):
        compile_verification_plan(poisoned, _python_profile().root, _python_profile())


def test_missing_project_context_is_explicit_and_blocks_verifier_dispatch() -> None:
    plan = compile_verification_plan(
        _python_candidate(),
        r"C:\unknown\owner-project",
    )

    assert plan.status == NEEDS_CONTEXT
    assert plan.project_context_status == "MISSING"
    assert any("ProjectProfile" in item for item in plan.context_gaps)
    assert any("targeted test runner" in item for item in plan.context_gaps)
    with pytest.raises(VerificationPlanningError, match="needs context"):
        plan.to_verifier_input()
    with pytest.raises(VerificationPlanningError, match="needs context"):
        plan.mission_verifier_kwargs()


def test_existing_mission_contract_and_verifier_consume_compiled_plan() -> None:
    contract = MissionStepContract(
        mission_id="mission-v11-plan",
        step_id="02-execute",
        version=1,
        resource_id="resource-v11-plan",
        capability="write_registered_worktree_files",
        target_relative_path="src/jarvis_foundry/mission/verification_planning.py",
        instructions="Implement only the admitted candidate in the registered worktree.",
        expected_effects=("verification planner source changes",),
        rollback={"method": "revert candidate diff", "reversible": True},
        verification_criteria=(
            "mission contract remains subordinate to AcceptanceGate",
        ),
        expected_artifacts=("evidence/verification-plan.json",),
        forbidden_paths=(".env",),
    )
    plan = compile_verification_plan(
        _python_candidate(),
        _python_profile().root,
        _python_profile(),
        mission_contracts=(contract,),
    )

    assert plan.status == READY
    assert contract.contract_id in plan.source_contract_refs
    assert "mission contract remains subordinate to AcceptanceGate" \
        in plan.required_postconditions
    assert "evidence/verification-plan.json" in {
        item.path for item in plan.expected_artifacts
    }
    verifier_input = plan.to_verifier_input()
    kwargs = plan.mission_verifier_kwargs()
    worker_contract = WorkerStepContract(
        run_id="verify-v11-plan",
        mission_id=contract.mission_id,
        step_id=contract.step_id,
        attempt=1,
        role="verifier",
        objective=_python_candidate()["objective"],
        instructions="Read-only independent verification.",
        working_root=Path(_python_profile().root),
        forbidden_paths=tuple(Path(item) for item in kwargs["forbidden_paths"]),
        expected_artifacts=kwargs["expected_artifacts"],
        verification_criteria=tuple(kwargs["verification_criteria"]),
    )

    assert WorkerStepContract.from_payload(worker_contract.to_payload()).content_hash() \
        == worker_contract.content_hash()
    assert verifier_contract_digest(verifier_input) == verifier_contract_digest(
        plan.to_verifier_input())
    assert plan.a3_plan_step_fields()["verification_contract"] \
        == plan.acceptance_criteria
