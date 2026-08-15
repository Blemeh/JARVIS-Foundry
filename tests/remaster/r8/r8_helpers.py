"""Shared helpers for the R8 regression suite (no top-level conftest)."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from jarvis_foundry.orchestration.git_ops import run_git
from jarvis_foundry.orchestration.orchestrator import OrchestrationConfig, Orchestrator
from jarvis_foundry.orchestration.paths import copy_tree
from jarvis_foundry.orchestration.proving_project_data import (
    ALPHA_PYPROJECT_SOURCE,
    ALPHA_STORE_SOURCE,
    ALPHA_TEST_SOURCE,
    REPRO_TEST_SOURCE,
)
from jarvis_foundry.orchestration.repair import revert_repair_transform
from jarvis_foundry.orchestration.schemas import FileChange, TestRun, WorkerResult
from jarvis_foundry.orchestration.verify import Verifier

FIXED_UTC = "2026-08-15T20:30:00Z"
VENV_PYTHON = Path(sys.executable)
REPO_ROOT = Path(__file__).resolve().parents[3]


def write_files(root: Path, files: Mapping[str, str]) -> None:
    for relative, content in files.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def commit_all(root: Path, message: str = "baseline") -> None:
    run_git(["init", "-q"], root)
    run_git(["config", "user.email", "r8@test.local"], root)
    run_git(["config", "user.name", "R8 Test"], root)
    run_git(["add", "."], root)
    run_git(["commit", "-q", "-m", message], root)


def make_alpha_project(root: Path) -> Path:
    write_files(
        root,
        {
            "pyproject.toml": ALPHA_PYPROJECT_SOURCE,
            "src/alpha_ledger/__init__.py": '"""alpha_ledger event store."""\n',
            "src/alpha_ledger/store.py": ALPHA_STORE_SOURCE,
            "tests/test_store.py": ALPHA_TEST_SOURCE,
        },
    )
    commit_all(root)
    (root / "NOTES.md").write_text("# owner notes\n", encoding="utf-8")
    return root


def make_orchestration_candidate(root: Path) -> Path:
    copy_tree(REPO_ROOT / "src", root / "src")
    verify_path = root / "src" / "jarvis_foundry" / "orchestration" / "verify.py"
    source = verify_path.read_text(encoding="utf-8")
    try:
        reverted = revert_repair_transform(source, "verifier_recompute")
    except ValueError:
        reverted = source
    verify_path.write_text(reverted, encoding="utf-8")
    write_files(
        root,
        {"pyproject.toml": ALPHA_PYPROJECT_SOURCE, "tests/test_verify_repro.py": REPRO_TEST_SOURCE},
    )
    commit_all(root, "candidate with genuine verifier defect")
    return root


def make_worker_result(
    *,
    status: str = "PASS",
    files_changed: tuple[FileChange, ...] = (),
    commands_run: tuple[str, ...] = (),
    tests: tuple[TestRun, ...] = (),
) -> WorkerResult:
    return WorkerResult(
        status=status,
        claims={},
        files_changed=files_changed,
        commands_run=commands_run,
        tests=tests,
        evidence=(),
        artifacts=(),
        external_effects={},
        blockers=(),
        uncertainty="low",
        recommended_next_action="none",
    )


def asset_config(
    *,
    mission_id: str,
    source_project: Path,
    evidence_dir: Path,
    proving_root: Path,
    worker_module: str,
    kind: str = "asset",
    worker_spec: Mapping[str, Any] | None = None,
    required_tests: list[str] | tuple[str, ...] = ("pytest tests -q",),
    snapshot_untracked: bool = False,
    repair_finding_id: str = "",
    reproduced_test_command: str = "",
) -> OrchestrationConfig:
    return OrchestrationConfig(
        mission_id=mission_id,
        kind=kind,
        source_project=str(source_project),
        branch=f"r8/{mission_id}",
        worker_module=worker_module,
        worker_id=f"worker-{mission_id}",
        role="builder",
        objective=f"complete mission {mission_id}",
        known_verified_state={"source_head": "captured"},
        relevant_previous_failures=(),
        allowed_mutation=("tests/", "docs/", "src/"),
        forbidden_mutation=(".git/", "protected/**"),
        acceptance_criteria=("worker result PASS", "verifier PASS", "source unchanged"),
        required_tests=required_tests,
        required_evidence=("worker_result.json", "diff.json"),
        rollback_expectation="changes confined to clone; restore demonstrable",
        worker_spec=worker_spec or {"asset_type": "regression_suite"},
        snapshot_untracked=snapshot_untracked,
        candidate_id=f"candidate-{mission_id}",
        decision_id=f"decision-{mission_id}",
        goal_id=f"goal-{mission_id}",
        repair_finding_id=repair_finding_id,
        reproduced_test_command=reproduced_test_command,
        python=str(VENV_PYTHON),
        evidence_dir=str(evidence_dir),
        proving_root=str(proving_root),
        timeout_seconds=1800,
        next_action="",
    )


def verifier_factory(proving_root: Path):
    def factory(python: Path, clone: Path) -> Verifier:
        return Verifier(
            python=python,
            verifier_id="test-fresh-verifier",
            verified_at=FIXED_UTC,
            proving_root=proving_root,
        )

    return factory


def run_mission(config: OrchestrationConfig, proving_root: Path) -> dict[str, Any]:
    orchestrator = Orchestrator(config, verifier_factory=verifier_factory(proving_root))
    return orchestrator.run()
