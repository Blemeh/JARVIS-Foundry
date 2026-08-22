"""B3-equivalent clean diagnosis harness (Hybrid Worker Integration §13).

The contaminated External Fusion B3 compared frameworks on a diagnosis task
where the builder could read the held-out criterion. This harness replaces
it with a genuinely private boundary:

- worker-visible: worktree + task contract + PUBLIC tests (pass trivially);
- controller-private: held-out tests + expected properties, unreachable
  from the worker's home, worktree, and environment;
- both frameworks receive equivalent visible information and mutate the
  same scoped worktree;
- success is judged ONLY by the held-out evidence, after the run.

The fixture mirrors B3's structure: plausible but incomplete symptoms
(a "header row" hypothesis) where the public tests pass either way and the
held-out criterion distinguishes root-cause correctness.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..clock import isoformat
from .contracts import WORKER_HERMES, WORKER_OPENHANDS, WorkerStepContract
from .envelope import ResultEnvelope
from .isolation import HeldOutBoundary


ROWS_SOURCE = '''\
"""Row loader with a misleading symptom (B3-equivalent fixture).

Observed symptom: load_rows seems to include the header row in its output.
Public tests cover the header-present case. The root cause is that the
loader skips the first non-blank line unconditionally, which also eats a
real data row when no header exists.
"""

def load_rows(text: str):
    lines = [line for line in text.splitlines() if line.strip()]
    return [line.split(",") for line in lines[1:]]
'''

PUBLIC_TEST = '''\
from rows import load_rows

def test_header_row_is_not_data():
    rows = load_rows("name,age\\nAlice,30\\nBob,40\\n")
    assert rows == [["Alice", "30"], ["Bob", "40"]]
'''

HELD_OUT_TEST = '''\
from rows import load_rows

def test_no_header_input_keeps_first_data_row():
    rows = load_rows("Alice,30\\nBob,40\\n")
    assert rows == [["Alice", "30"], ["Bob", "40"]]
'''

TASK_CONTRACT = """\
Task: fix rows.py so load_rows returns only DATA rows.

Observed symptom: the header row appears in the output when input has a
header. Public tests (public-tests/) cover the header-present case and pass
trivially today. Find the root cause and make the loader robust: when input
has NO header, every non-blank line is a data row and must be returned.
Run the public tests with: python -m pytest public-tests -q
"""


@dataclass(frozen=True, slots=True)
class DiagnosisLab:
    mission_id: str
    boundary: HeldOutBoundary
    created_at: str = field(default_factory=isoformat)

    @classmethod
    def create(
        cls,
        *,
        mission_id: str,
        lab_root: Path,
        protected_roots: tuple[Path, ...] = (),
    ) -> "DiagnosisLab":
        boundary = HeldOutBoundary.create(
            mission_id=mission_id, lab_root=lab_root,
            protected_roots=protected_roots,
        )
        worktree = boundary.worktree
        (worktree / "rows.py").write_text(ROWS_SOURCE, encoding="utf-8")
        (boundary.task_contract / "task.md").write_text(
            TASK_CONTRACT, encoding="utf-8")
        (boundary.public_tests / "test_public.py").write_text(
            PUBLIC_TEST, encoding="utf-8")
        (boundary.held_out_tests / "test_held_out.py").write_text(
            HELD_OUT_TEST, encoding="utf-8")
        (boundary.expected_properties / "expected.json").write_text(
            json.dumps({"handles_no_header_input": True,
                        "public_tests_pass": True}),
            encoding="utf-8")
        return cls(mission_id=mission_id, boundary=boundary)

    def build_contract(
        self,
        *,
        run_id: str,
        framework: str,
        attempt: int = 1,
        role: str = "builder",
        provider: str = "opencode_go",
        model: str = "deepseek-v4-flash",
    ) -> WorkerStepContract:
        boundary = self.boundary
        task_text = (boundary.task_contract / "task.md").read_text(encoding="utf-8")
        public_path = boundary.public_tests
        return WorkerStepContract(
            run_id=run_id,
            mission_id=self.mission_id,
            step_id="diagnose-rows",
            attempt=attempt,
            role=role,
            objective="repair rows.load_rows so no-header input keeps data rows",
            instructions=(
                f"{task_text}\n\nWork ONLY inside the worktree. The public "
                f"tests live at {public_path} and are safe to read and run. "
                "Do not read anything outside the worktree and the task "
                "contract directory."
            ),
            working_root=boundary.worktree,
            allowed_paths=(boundary.worktree,),
            forbidden_paths=(boundary.root,),
            provider=provider,
            model=model,
            duration_seconds=900,
            task_signals={
                "bounded": True,
                "explicit_acceptance": True,
                "tests_available": True,
                "narrow_scope": True,
                "ambiguous": False,
                "root_cause_required": True,
                "misleading_symptoms": True,
            },
            expected_artifacts=("rows.py",),
            verification_criteria=(
                "no-header input keeps every data row",
                "header-present input keeps only data rows",
            ),
        )

    def judge_held_out(self, *, worktree: Path | None = None) -> dict[str, object]:
        """Run the controller-private held-out test against the worktree."""
        target = worktree or self.boundary.worktree
        import sys

        from ..platform.windows_subprocess import headless_run_kwargs

        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(self.boundary.held_out_tests),
             "-q", "--rootdir", str(target)],
            cwd=str(target),
            capture_output=True, text=True, timeout=120, check=False,
            **headless_run_kwargs(),
        )
        return {
            "exit": result.returncode,
            "passed": result.returncode == 0,
            "stdout_tail": result.stdout[-1500:],
            "stderr_tail": result.stderr[-1500:],
        }

    def judge_public(self, *, worktree: Path | None = None) -> dict[str, object]:
        """Run the worker-visible public tests against the worktree."""
        target = worktree or self.boundary.worktree
        import sys

        from ..platform.windows_subprocess import headless_run_kwargs

        result = subprocess.run(
            [sys.executable, "-m", "pytest", str(self.boundary.public_tests),
             "-q", "--rootdir", str(target)],
            cwd=str(target),
            capture_output=True, text=True, timeout=120, check=False,
            **headless_run_kwargs(),
        )
        return {
            "exit": result.returncode,
            "passed": result.returncode == 0,
            "stdout_tail": result.stdout[-1500:],
            "stderr_tail": result.stderr[-1500:],
        }


@dataclass(frozen=True, slots=True)
class LabRunResult:
    framework: str
    run_id: str
    worker_status: str
    worker_verdict: str | None
    held_out_passed: bool
    public_passed: bool
    isolation_failures: tuple[str, ...]
    held_out_exit: int | None = None
    summary: str = ""


class LabRunner:
    """Dispatches both frameworks on equivalent visible information and
    judges success by held-out evidence only."""

    def __init__(self, *, adapter_for: Callable[[str], object]) -> None:
        self.adapter_for = adapter_for

    def run(
        self,
        lab: DiagnosisLab,
        framework: str,
        *,
        run_id: str,
    ) -> LabRunResult:
        adapter = self.adapter_for(framework)
        contract = lab.build_contract(run_id=run_id, framework=framework)
        isolation_failures = lab.boundary.assert_private_unreachable(
            lab.boundary.worktree, contract.working_root)
        envelope = adapter.dispatch(
            contract, heartbeat=lambda: None)  # type: ignore[attr-defined]
        public = lab.judge_public()
        held_out = lab.judge_held_out()
        return LabRunResult(
            framework=framework,
            run_id=run_id,
            worker_status=getattr(envelope, "worker_status", "UNKNOWN"),
            worker_verdict=getattr(envelope, "verdict", None),
            held_out_passed=bool(held_out["passed"]),
            public_passed=bool(public["passed"]),
            isolation_failures=isolation_failures,
            held_out_exit=int(held_out["exit"]),
            summary=str(held_out["stdout_tail"])[-300:],
        )
