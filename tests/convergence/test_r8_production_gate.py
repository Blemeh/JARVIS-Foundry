"""R8 production gate on canonical main.

This gate uses the canonical worker path:

    bounded contract -> deterministic route -> subprocess worker -> observed
    evidence reconciliation -> fresh verifier -> repair -> fresh verifier.

The end-to-end cases use disposable git repositories. One produces a useful
multi-input health index and report; the other repairs a real defect in an
existing manifest loader. No external provider readiness is claimed here.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path
from textwrap import dedent
from typing import Callable

import pytest

from jarvis_foundry.control.capabilities import set_capability_state
from jarvis_foundry.control.policy import ActionPolicy
from jarvis_foundry.control.registration import inspect_git_resource, registration_event
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.mission.loop import WAITING_OUTCOME, MissionLoop
from jarvis_foundry.mission.proposal import Proposal, ProposalError, Waiting, propose_next_action
from jarvis_foundry.roles.adapter import FakeHermesAdapter
from jarvis_foundry.roles.router import RoleRouter
from jarvis_foundry.workers.adapters.registry import WorkerRegistry
from jarvis_foundry.workers.contracts import WORKER_HERMES, WORKER_OPENHANDS, WorkerStepContract
from jarvis_foundry.workers.envelope import COMPLETED, FAILED, VERDICT_FAIL, VERDICT_PASS, ResultEnvelope
from jarvis_foundry.workers.evidence import EvidenceReconciler, git_changed_files, git_diff_hash
from jarvis_foundry.workers.isolation import DisposableWorkerState, HeldOutBoundary, IsolationError
from jarvis_foundry.workers.orchestrator import HybridStepController
from jarvis_foundry.workers.router import READY, WorkerRouter


def git(*args: str, cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def _commit(repository: Path, message: str) -> None:
    git("add", ".", cwd=repository)
    git("commit", "-m", message, cwd=repository)


def make_repository(tmp_path: Path, *, name: str = "repo") -> Path:
    repository = tmp_path / name
    repository.mkdir(parents=True)
    git("init", cwd=repository)
    git("config", "user.email", "jarvis-r8-gate@example.invalid", cwd=repository)
    git("config", "user.name", "JARVIS R8 Gate", cwd=repository)
    (repository / ".gitignore").write_text(
        "__pycache__/\n.pytest_cache/\n.mypy_cache/\n", encoding="utf-8"
    )
    (repository / "README.md").write_text("baseline\n", encoding="utf-8")
    _commit(repository, "baseline")
    return repository


def make_asset_repository(tmp_path: Path) -> Path:
    repository = make_repository(tmp_path, name="health-index-repo")
    (repository / "docs" / ".gitkeep").parent.mkdir(parents=True, exist_ok=True)
    (repository / "docs" / ".gitkeep").write_text("", encoding="utf-8")
    for name, content in {
        "alpha.md": "# Alpha Service\n\nOwner: platform\nStatus: healthy\n",
        "beta.md": "# Beta Service\n\nOwner: data\nStatus: watch\n",
        "gamma.md": "# Gamma Service\n\nOwner: security\nStatus: healthy\n",
    }.items():
        path = repository / "inputs" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    test_path = repository / "tests" / "test_inputs.py"
    test_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.write_text(
        """from pathlib import Path


def test_input_corpus_is_complete_and_sorted():
    root = Path(__file__).parents[1]
    paths = sorted((root / "inputs").glob("*.md"))
    assert [path.name for path in paths] == ["alpha.md", "beta.md", "gamma.md"]
    assert all(path.read_text(encoding="utf-8").strip() for path in paths)
""",
        encoding="utf-8",
    )
    _commit(repository, "commit health-index inputs and acceptance test")
    return repository


def make_maintenance_repository(tmp_path: Path) -> Path:
    repository = make_repository(tmp_path, name="maintenance-repo")
    package = repository / "src" / "catalog"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "manifest.py").write_text(
        """import json
from pathlib import Path


def load_manifest(path: str | Path) -> list[dict[str, object]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    # Deliberate maintenance defect: disabled records are part of the
    # canonical manifest and must not disappear during a read.
    return [item for item in payload["items"] if item.get("enabled", True)]
""",
        encoding="utf-8",
    )
    (repository / "manifest.json").write_text(
        json.dumps(
            {
                "schema": "catalog-manifest/v1",
                "items": [
                    {"id": "alpha", "enabled": True},
                    {"id": "beta", "enabled": False},
                    {"id": "gamma", "enabled": True},
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    test_path = repository / "tests" / "test_manifest.py"
    test_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.write_text(
        """import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[1] / "src"))

from catalog.manifest import load_manifest


def test_manifest_read_preserves_order_and_disabled_records():
    root = Path(__file__).parents[1]
    records = load_manifest(root / "manifest.json")
    assert [record["id"] for record in records] == ["alpha", "beta", "gamma"]
    assert records[1]["enabled"] is False
""",
        encoding="utf-8",
    )
    _commit(repository, "commit canonical manifest asset and regression test")
    return repository


def mission_fixture(
    tmp_path: Path,
    *,
    repository: Path | None = None,
    next_action: str | None = "deliver useful asset",
    mission_id: str = "mission-1",
    objective: str = "r8 production gate mission",
):
    """Create the durable mission/resource surface used by canonical loops."""
    repository = repository or make_repository(tmp_path)
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    database.append(
        Event.create(
            "system.start_build_recorded",
            {"received_at": "2026-08-18T00:00:00Z", "standing_mandate": True},
            source_type="owner",
            source_id="local-owner",
            idempotency_key=f"system.start-build:{mission_id}",
        )
    )
    database.append(
        registration_event(inspect_git_resource(repository, resource_id="resource-1"))
    )
    database.append(
        Event.create(
            "mission.created",
            {
                "mission_id": mission_id,
                "objective": objective,
                "resource_id": "resource-1",
                "state": "PLANNED",
                "acceptance_criteria": ["actual artifact is inspected and verified"],
                "next_action": next_action,
            },
            source_type="test",
            source_id="r8-gate",
            idempotency_key=f"mission.created:{mission_id}",
        )
    )
    for state in ("ORIENTING", "ACTION_SELECTION"):
        database.append(
            Event.create(
                "mission.state_changed",
                {"mission_id": mission_id, "state": state, "next_action": next_action},
                source_type="test",
                source_id="r8-gate",
                idempotency_key=f"mission.state_changed:{mission_id}:{state}",
            )
        )
    set_capability_state(database, "resource-1", "git_local_commit", "READY",
                         evidence={"source": "r8-gate"})
    return database, repository, ActionPolicy(database, tmp_path / "STOP")


def hybrid_controller(
    tmp_path: Path,
    repository: Path,
    *,
    mission_id: str,
    objective: str,
    openhands,
    hermes,
):
    database, _, policy = mission_fixture(
        tmp_path, repository=repository, mission_id=mission_id, objective=objective,
        next_action="inspect the next useful change"
    )
    router = WorkerRouter()
    router.mark_ready(
        WORKER_OPENHANDS, state=READY,
        note="disposable local containment proven by this gate",
        evidence=("temporary git repository", "subprocess boundary", "scope evidence"),
    )
    controller = HybridStepController(
        database=database,
        router=router,
        registry=WorkerRegistry({WORKER_OPENHANDS: openhands, WORKER_HERMES: hermes}),
        policy=policy,
        stop_path=tmp_path / "STOP",
        reconciler=EvidenceReconciler(test_timeout_seconds=120),
        max_repairs=2,
    )
    return controller, database


def contract_for(
    repository: Path,
    *,
    mission_id: str,
    step_id: str,
    objective: str,
    expected_artifacts: tuple[str, ...],
) -> WorkerStepContract:
    return WorkerStepContract(
        run_id=f"{mission_id}-run-1",
        mission_id=mission_id,
        step_id=step_id,
        attempt=1,
        role="builder",
        objective=objective,
        instructions=(
            "Inspect committed inputs, produce declared artifacts, run deterministic "
            "acceptance tests, and report actual paths."
        ),
        working_root=repository,
        allowed_paths=(repository,),
        forbidden_paths=(repository / "controller-private",),
        provider="opencode_go",
        model="deepseek-v4-flash",
        duration_seconds=120,
        budget_usd=0.25,
        task_signals={
            "bounded": True,
            "explicit_acceptance": True,
            "tests_available": True,
            "narrow_scope": True,
            "reversible": True,
        },
        expected_artifacts=expected_artifacts,
        verification_criteria=(
            "all expected artifacts exist",
            "actual source hashes and paths are recorded",
            "fresh verifier runs the acceptance tests",
        ),
    )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _run_script(script: str, root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-c", dedent(script), str(root)],
        cwd=root, capture_output=True, text=True, check=False, timeout=120,
    )


def _pytest(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q"],
        cwd=root, capture_output=True, text=True, check=False, timeout=120,
    )


def _envelope(
    contract: WorkerStepContract,
    *,
    framework: str,
    result: subprocess.CompletedProcess[str],
    files_changed: tuple[str, ...],
    worker_commands: tuple[str, ...],
    verdict: str | None = None,
    worker_status: str = COMPLETED,
    summary: str = "",
    artifact_paths: tuple[str, ...] = (),
) -> ResultEnvelope:
    hashes = tuple(
        _sha256(contract.working_root / rel)
        for rel in artifact_paths
        if (contract.working_root / rel).is_file()
    )
    stdout = result.stdout.strip().replace("\n", " ")[-500:]
    stderr = result.stderr.strip().replace("\n", " ")[-500:]
    results = (f"exit={result.returncode}",) + tuple(
        part for part in (stdout, stderr) if part
    )
    return ResultEnvelope.from_contract(
        contract,
        framework=framework,
        worker_status=worker_status,
        verdict=verdict,
        claimed_outcome=summary,
        summary=summary,
        files_changed=files_changed,
        observed_touched_paths=files_changed,
        diff_hash=git_diff_hash(contract.working_root),
        worker_commands=worker_commands,
        worker_results=results,
        worker_raw_artifacts=results[1:],
        worker_artifact_hashes=hashes,
    )


ASSET_BUILDER = r"""
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
sources = []
for path in sorted((root / "inputs").glob("*.md")):
    text = path.read_text(encoding="utf-8")
    sources.append({
        "path": path.relative_to(root).as_posix(),
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "bytes": len(text.encode("utf-8")),
        "lines": len(text.splitlines()),
        "headings": [
            line[2:].strip() for line in text.splitlines() if line.startswith("# ")
        ],
    })
if len(sources) < 3:
    raise SystemExit("health index requires at least three committed inputs")
payload = {
    "schema": "jarvis.health-index/v1",
    "source_count": len(sources),
    "sources": sources,
    "summary": {
        "total_bytes": sum(item["bytes"] for item in sources),
        "total_lines": sum(item["lines"] for item in sources),
        "healthy_sources": sum(
            "Status: healthy" in (root / item["path"]).read_text(encoding="utf-8")
            for item in sources
        ),
    },
}
docs = root / "docs"
docs.mkdir(parents=True, exist_ok=True)
(docs / "health.json").write_text(
    json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
)
lines = [
    "# Project Health Index",
    "",
    f"This report covers {payload['source_count']} committed input documents.",
    "",
    "| Source | SHA-256 | Lines | Bytes |",
    "| --- | --- | ---: | ---: |",
]
for item in sources:
    lines.append(
        f"| {item['path']} | {item['sha256']} | {item['lines']} | {item['bytes']} |"
    )
lines.extend([
    "",
    "## Acceptance",
    "",
    "- Every committed input is represented exactly once.",
    "- Hashes and counts are computed from inspected source bytes.",
    "- The JSON index and this report are generated together.",
])
(docs / "health.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
"""

ASSET_VERIFIER = r"""
import hashlib
import json
import sys
from pathlib import Path

root = Path(sys.argv[1])
payload = json.loads((root / "docs" / "health.json").read_text(encoding="utf-8"))
paths = sorted((root / "inputs").glob("*.md"))
assert payload["schema"] == "jarvis.health-index/v1"
assert payload["source_count"] == len(paths) == 3
records = {item["path"]: item for item in payload["sources"]}
assert set(records) == {path.relative_to(root).as_posix() for path in paths}
for path in paths:
    text = path.read_text(encoding="utf-8")
    record = records[path.relative_to(root).as_posix()]
    assert record["sha256"] == hashlib.sha256(text.encode("utf-8")).hexdigest()
    assert record["bytes"] == len(text.encode("utf-8"))
report = (root / "docs" / "health.md").read_text(encoding="utf-8")
assert "# Project Health Index" in report
for path in paths:
    assert f"| {path.relative_to(root).as_posix()} |" in report
"""

MANIFEST_VERIFIER = r"""
import sys
from pathlib import Path

root = Path(sys.argv[1])
sys.path.insert(0, str(root / "src"))
from catalog.manifest import load_manifest

records = load_manifest(root / "manifest.json")
assert [record["id"] for record in records] == ["alpha", "beta", "gamma"]
assert records[1]["enabled"] is False
"""

MANIFEST_REPAIR = r"""
import sys
from pathlib import Path

path = Path(sys.argv[1]) / "src" / "catalog" / "manifest.py"
text = path.read_text(encoding="utf-8")
old = 'return [item for item in payload["items"] if item.get("enabled", True)]'
new = "return list(payload['items'])"
if old not in text:
    raise SystemExit("canonical manifest defect was not present; refusing repair")
path.write_text(text.replace(old, new), encoding="utf-8")
"""


class SubprocessWorkerAdapter:
    """Narrow adapter with a real process boundary.

    JARVIS owns routing, evidence reconciliation, verification and acceptance.
    This adapter performs only the bounded worker operation.
    """

    framework: str

    def __init__(self, framework: str) -> None:
        self.framework = framework
        self.received: list[WorkerStepContract] = []

    @staticmethod
    def _mode(contract: WorkerStepContract) -> str:
        # The canonical controller intentionally keeps verifier contracts
        # focused on criteria and omits builder expected_artifacts. Step
        # identity therefore remains the stable mode discriminator.
        if (
            "docs/health.json" in contract.expected_artifacts
            or contract.step_id == "health-index"
            or "health index" in contract.objective.lower()
        ):
            return "asset"
        if (
            "src/catalog/manifest.py" in contract.expected_artifacts
            or contract.step_id == "manifest-maintenance"
            or "manifest" in contract.objective.lower()
        ):
            return "maintenance"
        return "unknown"

    def dispatch(
        self,
        contract: WorkerStepContract,
        *,
        heartbeat: Callable[[], None],
    ) -> ResultEnvelope:
        self.received.append(contract)
        heartbeat()
        mode = self._mode(contract)
        root = contract.working_root

        if self.framework == WORKER_OPENHANDS and contract.role == "builder":
            if mode == "asset":
                result = _run_script(ASSET_BUILDER, root)
                changed = git_changed_files(root)
                return _envelope(
                    contract, framework=self.framework, result=result,
                    files_changed=changed,
                    worker_commands=(f"{sys.executable} -c <health-index-builder>",),
                    verdict=VERDICT_PASS if result.returncode == 0 else VERDICT_FAIL,
                    worker_status=COMPLETED if result.returncode == 0 else FAILED,
                    summary="built multi-input health index and report",
                    artifact_paths=("docs/health.json", "docs/health.md"),
                )
            result = subprocess.CompletedProcess([], 0, "inspected existing asset", "")
            return _envelope(
                contract, framework=self.framework, result=result, files_changed=(),
                worker_commands=("inspect existing manifest asset",),
                verdict=VERDICT_PASS,
                summary="inspected existing manifest; no mutation claimed",
                artifact_paths=("src/catalog/manifest.py",),
            )

        if self.framework == WORKER_HERMES and contract.role == "repair":
            result = (
                _run_script(MANIFEST_REPAIR, root)
                if mode == "maintenance"
                else subprocess.CompletedProcess([], 1, "unsupported repair mode", "")
            )
            changed = git_changed_files(root)
            return _envelope(
                contract, framework=self.framework, result=result,
                files_changed=changed,
                worker_commands=(f"{sys.executable} -c <manifest-repair>",),
                verdict=VERDICT_PASS if result.returncode == 0 else VERDICT_FAIL,
                worker_status=COMPLETED if result.returncode == 0 else FAILED,
                summary="repaired canonical manifest loader at its root cause",
                artifact_paths=("src/catalog/manifest.py",),
            )

        if self.framework == WORKER_HERMES and contract.role == "verifier":
            script = ASSET_VERIFIER if mode == "asset" else MANIFEST_VERIFIER
            inspection = _run_script(script, root)
            tests = _pytest(root)
            aggregate = subprocess.CompletedProcess(
                [],
                0 if inspection.returncode == 0 and tests.returncode == 0 else 1,
                f"inspection={inspection.returncode}; tests={tests.returncode}",
                inspection.stderr[-300:] + tests.stderr[-300:],
            )
            return _envelope(
                contract, framework=self.framework, result=aggregate, files_changed=(),
                worker_commands=(
                    f"{sys.executable} -c <independent-inspection>",
                    f"{sys.executable} -m pytest -q",
                ),
                verdict=VERDICT_PASS if aggregate.returncode == 0 else VERDICT_FAIL,
                summary="fresh verifier inspected actual files and ran tests",
                artifact_paths=contract.expected_artifacts,
            )

        result = subprocess.CompletedProcess([], 1, "unsupported worker role", "")
        return _envelope(
            contract, framework=self.framework, result=result, files_changed=(),
            worker_commands=("unsupported worker role",),
            verdict=VERDICT_FAIL, worker_status=FAILED,
            summary="unsupported worker role",
        )


def test_r8_bounded_worker_context_and_single_architecture(tmp_path: Path) -> None:
    RoleRouter().enforce_directive_routing()
    contract = contract_for(
        tmp_path, mission_id="mission-1", step_id="health-index",
        objective="build a useful health index from committed inputs",
        expected_artifacts=("docs/health.json", "docs/health.md"),
    )
    payload = contract.to_payload()
    assert WorkerStepContract.from_payload(payload).to_payload() == payload
    assert contract.content_hash() == contract.content_hash()
    budgeted = WorkerStepContract.from_payload({**payload, "budget_usd": 2.0})
    assert budgeted.content_hash() != contract.content_hash()
    assert budgeted.budget_usd == 2.0
    assert contract.allowed_paths and contract.forbidden_paths
    root = Path(__file__).resolve().parents[2] / "src" / "jarvis_foundry"
    assert not (root / "orchestration").exists()
    assert not (root / "kernel").exists()
    assert HybridStepController.__name__ == "HybridStepController"


def test_r8_isolated_worktree_keeps_private_boundary(tmp_path: Path) -> None:
    boundary = HeldOutBoundary.create(mission_id="mission-1", lab_root=tmp_path)
    (boundary.controller_private / "verifier-evidence" / "held.txt").write_text(
        "controller-private", encoding="utf-8"
    )
    assert boundary.worker_visible.is_dir()
    assert boundary.worktree.is_dir()
    assert boundary.assert_private_unreachable(boundary.worktree, boundary.task_contract) == ()
    assert boundary.cleanup() is True
    assert boundary.cleanup() is False
    with pytest.raises(IsolationError, match="protected owner root"):
        DisposableWorkerState.create(
            run_id="run-1", framework="hermes",
            lab_root=tmp_path, protected_roots=(tmp_path,),
        )
    disposable = DisposableWorkerState.create(
        run_id="run-1", framework="hermes", lab_root=tmp_path / "lab"
    )
    assert disposable.hermes_home.is_dir()
    assert disposable.cleanup() is True
    assert disposable.cleanup() is False


def test_r8_dirty_owner_work_is_preserved_by_delta_reconciliation(tmp_path: Path) -> None:
    repository = make_asset_repository(tmp_path)
    owner_notes = repository / "owner-notes.md"
    owner_notes.write_text("owner draft must survive\n", encoding="utf-8")
    contract = contract_for(
        repository, mission_id="dirty-mission", step_id="health-index",
        objective="build health index without touching owner draft",
        expected_artifacts=("docs/health.json", "docs/health.md"),
    )
    reconciler = EvidenceReconciler()
    before = reconciler.capture_before(contract)
    envelope = SubprocessWorkerAdapter(WORKER_OPENHANDS).dispatch(
        contract, heartbeat=lambda: None
    )
    # The adapter reports the complete status surface, including the pre-
    # existing owner draft. Delta reconciliation makes the worker accountable
    # only for the two files it introduced.
    envelope = replace(
        envelope,
        files_changed=("docs/health.json", "docs/health.md"),
        observed_touched_paths=("docs/health.json", "docs/health.md"),
    )
    report = reconciler.reconcile(
        contract, envelope, before=before, delta_baseline=before.changed_files
    )
    assert report.agree is True
    assert owner_notes.read_text(encoding="utf-8") == "owner draft must survive\n"
    assert "owner-notes.md" in git_changed_files(repository)
    assert set(report.observed_changed_files) == {
        "owner-notes.md", "docs/health.json", "docs/health.md"
    }


def test_r8_actual_result_reconciliation_rejects_false_success(tmp_path: Path) -> None:
    repository = make_repository(tmp_path)
    contract = contract_for(
        repository, mission_id="false-success", step_id="health-index",
        objective="build health index", expected_artifacts=("docs/health.md",),
    )
    reconciler = EvidenceReconciler()
    before = reconciler.capture_before(contract)
    envelope = ResultEnvelope.from_contract(
        contract, framework=WORKER_OPENHANDS,
        claimed_outcome="health report complete", verdict=VERDICT_PASS,
        files_changed=("docs/health.md",),
        observed_touched_paths=("docs/health.md",),
    )
    report = reconciler.reconcile(contract, envelope, before=before)
    assert report.agree is False
    assert any("expected artifacts missing" in item for item in report.mismatches)
    assert any("disagree" in item for item in report.mismatches)


def test_r8_real_asset_is_built_inspected_and_accepted(tmp_path: Path) -> None:
    repository = make_asset_repository(tmp_path)
    openhands = SubprocessWorkerAdapter(WORKER_OPENHANDS)
    hermes = SubprocessWorkerAdapter(WORKER_HERMES)
    controller, database = hybrid_controller(
        tmp_path, repository, mission_id="health-index-mission",
        objective="turn three committed service notes into a useful health index",
        openhands=openhands, hermes=hermes,
    )
    contract = contract_for(
        repository, mission_id="health-index-mission", step_id="health-index",
        objective="turn three committed service notes into a useful health index",
        expected_artifacts=("docs/health.json", "docs/health.md"),
    )
    outcome = controller.run_step(
        contract, test_commands=(f"{sys.executable} -m pytest -q",)
    )
    assert outcome.outcome == "PASSED"
    assert outcome.repair_run_id is None
    assert outcome.envelope is not None
    assert outcome.envelope.framework == WORKER_OPENHANDS
    assert outcome.evidence is not None and outcome.evidence.agree is True
    assert (repository / "docs" / "health.json").is_file()
    assert (repository / "docs" / "health.md").is_file()
    payload = json.loads((repository / "docs" / "health.json").read_text(encoding="utf-8"))
    assert payload["source_count"] == 3
    assert [item["path"] for item in payload["sources"]] == [
        "inputs/alpha.md", "inputs/beta.md", "inputs/gamma.md"
    ]
    assert "Acceptance" in (repository / "docs" / "health.md").read_text(encoding="utf-8")
    assert set(git_changed_files(repository)) == {"docs/health.json", "docs/health.md"}
    assert outcome.verdict_id is not None
    assert database.row("missions", "mission_id", "health-index-mission")["state"] == "ACTION_SELECTION"
    events = {event.event_type for event in database.events()}
    assert {"worker.route_decided", "worker.run_recorded",
            "worker.evidence_reconciled", "verification.passed"} <= events
    assert len(openhands.received) == 1
    assert [item.role for item in hermes.received] == ["verifier"]


def test_r8_real_maintenance_repair_reconciles_and_reverifies(tmp_path: Path) -> None:
    repository = make_maintenance_repository(tmp_path)
    manifest = repository / "src" / "catalog" / "manifest.py"
    before_hash = _sha256(manifest)
    baseline = _pytest(repository)
    assert baseline.returncode != 0, "the maintenance defect must be reproduced first"
    openhands = SubprocessWorkerAdapter(WORKER_OPENHANDS)
    hermes = SubprocessWorkerAdapter(WORKER_HERMES)
    controller, database = hybrid_controller(
        tmp_path, repository, mission_id="manifest-maintenance-mission",
        objective="repair the canonical manifest loader while preserving record order",
        openhands=openhands, hermes=hermes,
    )
    contract = contract_for(
        repository, mission_id="manifest-maintenance-mission",
        step_id="manifest-maintenance",
        objective="repair the canonical manifest loader while preserving record order",
        expected_artifacts=("src/catalog/manifest.py",),
    )
    outcome = controller.run_step(
        contract, test_commands=(f"{sys.executable} -m pytest -q",)
    )
    assert outcome.outcome == "PASSED"
    assert outcome.repair_run_id == "manifest-maintenance-mission-run-1-repair-r1"
    assert outcome.envelope is not None and outcome.envelope.role == "repair"
    assert outcome.evidence is not None and outcome.evidence.agree is True
    assert _sha256(manifest) != before_hash
    assert set(git_changed_files(repository)) == {"src/catalog/manifest.py"}
    assert _pytest(repository).returncode == 0
    assert len(openhands.received) == 1
    assert [item.role for item in hermes.received] == ["verifier", "repair", "verifier"]
    evidence_events = [
        event for event in database.events()
        if event.event_type == "worker.evidence_reconciled"
    ]
    assert any(not event.payload["agree"] for event in evidence_events)
    assert any(event.payload["agree"] for event in evidence_events)
    assert any(event.event_type == "verification.failed" for event in database.events())
    assert any(event.event_type == "verification.passed" for event in database.events())
    assert database.row("missions", "mission_id", "manifest-maintenance-mission")["state"] == "ACTION_SELECTION"


def test_r8_next_action_selection_and_honest_waiting(tmp_path: Path) -> None:
    database, _, _ = mission_fixture(tmp_path, next_action="deliver useful asset")
    proposal = propose_next_action(database, "mission-1")
    assert isinstance(proposal, Proposal)
    assert proposal.action_requirement == "deliver useful asset"
    assert propose_next_action(database, "mission-1").action_requirement == proposal.action_requirement

    waiting_db, waiting_repo, waiting_policy = mission_fixture(
        tmp_path / "waiting", next_action=None
    )
    waiting = propose_next_action(waiting_db, "mission-1")
    assert isinstance(waiting, Waiting)
    adapter = FakeHermesAdapter()
    loop = MissionLoop(waiting_db, RoleRouter(), adapter, policy=waiting_policy)
    outcome = loop.run_step(
        mission_id="mission-1", step_id="health-index",
        capability="write_registered_worktree_files",
        target=waiting_repo / "docs" / "health.md",
        instructions="build the next useful asset",
        expected_effects=("create a health report",),
        rollback={"kind": "delete_created_files"}, risk_class="C1",
    )
    assert outcome.outcome == WAITING_OUTCOME
    assert len(adapter.received) == 0
    with waiting_db.connect() as connection:
        runs = connection.execute("SELECT COUNT(*) FROM agent_runs").fetchone()[0]
    assert runs == 0
    assert waiting_db.row("missions", "mission_id", "mission-1")["state"] == "ACTION_SELECTION"
    with pytest.raises(ProposalError):
        propose_next_action(waiting_db, "mission-forged")
