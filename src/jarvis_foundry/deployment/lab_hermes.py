"""LAB-only Hermes-named adapter that performs bounded file writes.

Used so an installed Supervisor can prove the CASE-A mission cycle without
calling the owner's live Hermes profile or external provider. The adapter
registers as ``hermes`` on the HybridWorkerRoleAdapter path.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from ..supervisor.worker import WorkerController
from ..workers.adapters.fakes import ScriptedOutcome, ScriptedWorkerAdapter
from ..workers.contracts import WORKER_HERMES

DEFAULT_EXPECTED = {
    "step-a": ("step_a.md", "STEP-A\n"),
    "step-b": ("step_b.md", "STEP-B\n"),
    "step-c": ("step_c.md", "STEP-C\n"),
}

OWNED_PID_NAME = ".jarvis-owned-worker.pid"
PARTIAL_BYTES = "PARTIAL\n"


class WorkerInterrupted(RuntimeError):
    """Owned worker process died after a real partial worktree mutation."""


class LabHermesAdapter(ScriptedWorkerAdapter):
    """Hermes-pinned scripted worker: builder claims are not verifier truth."""

    def __init__(
        self,
        expected: dict[str, tuple[str, str]] | None = None,
        *,
        crash_after_write: bool = False,
        crash_step_id: str = "step-a",
        workers: WorkerController | None = None,
    ) -> None:
        super().__init__(WORKER_HERMES)
        self.expected = expected or DEFAULT_EXPECTED
        self.crash_after_write = crash_after_write
        self.crash_step_id = crash_step_id
        self.workers = workers
        self.last_owned_pid: int | None = None

    def dispatch(self, contract, *, heartbeat):
        if contract.step_id not in self.expected:
            return self._dispatch_dynamic(contract, heartbeat=heartbeat)
        artifact, expected = self.expected[contract.step_id]
        path = Path(contract.working_root) / artifact
        crash = (
            self.crash_after_write
            and contract.role == "builder"
            and contract.step_id == self.crash_step_id
            and int(contract.attempt) == 1
        )
        if crash:
            self._mutate_then_hold(contract, path, heartbeat)
        if contract.role in ("builder", "repair"):
            defect = (
                contract.role == "builder"
                and contract.step_id == "step-b"
                and int(contract.attempt) == 1
                and not crash
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("DEFECT\n" if defect else expected, encoding="utf-8")
            self.outcome = ScriptedOutcome(
                files_changed=(artifact,),
                observed_touched_paths=(artifact,),
                summary=f"{contract.role} wrote {artifact}",
            )
        else:
            actual = path.read_text(encoding="utf-8") if path.is_file() else ""
            passed = actual == expected
            self.outcome = ScriptedOutcome(
                files_changed=(),
                observed_touched_paths=(),
                verdict="PASS" if passed else "FAIL",
                summary="artifact matches" if passed else "artifact mismatch",
            )
        return super().dispatch(contract, heartbeat=heartbeat)

    def _dispatch_dynamic(self, contract, *, heartbeat):
        """Handle executive and other dynamic contract steps."""
        artifacts = tuple(contract.expected_artifacts or ())
        if contract.role in ("builder", "repair"):
            defect = (
                self.crash_after_write
                and contract.step_id == self.crash_step_id
                and int(contract.attempt) == 1
            )
            for rel in artifacts:
                path = Path(contract.working_root) / rel
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(
                    "DEFECT\n" if defect else
                    "Objective report\nVERIFIED_BY executive-worker\n",
                    encoding="utf-8",
                )
            self.outcome = ScriptedOutcome(
                files_changed=artifacts,
                observed_touched_paths=artifacts,
                summary=f"{contract.role} wrote executive artifacts",
            )
        else:
            missing = []
            for rel in artifacts:
                path = Path(contract.working_root) / rel
                if not path.is_file():
                    missing.append(rel)
                elif "VERIFIED_BY executive-worker" not in path.read_text(encoding="utf-8"):
                    missing.append(rel)
            passed = not missing
            self.outcome = ScriptedOutcome(
                files_changed=(),
                observed_touched_paths=(),
                verdict="PASS" if passed else "FAIL",
                summary="verified" if passed else f"missing {missing}",
            )
        return super().dispatch(contract, heartbeat=heartbeat)

    def _mutate_then_hold(self, contract, path: Path, heartbeat) -> None:
        """Write a real partial artifact, then wait in an owned child.

        Result submission happens only after ``wait`` returns. A kill of
        that child therefore happens after mutation and before a success
        envelope.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(PARTIAL_BYTES, encoding="utf-8")
        holder = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(600)"],
        )
        self.last_owned_pid = holder.pid
        pid_path = Path(contract.working_root) / OWNED_PID_NAME
        pid_path.write_text(str(holder.pid), encoding="utf-8")
        run_id = f"lab-crash:{contract.run_id}"
        if self.workers is not None:
            self.workers.attach(
                run_id, holder, acquire_lease=True,
                mission_id=contract.mission_id, step_id=contract.step_id,
                worker_type="lab_hermes",
                working_directory=contract.working_root,
            )
        heartbeat()
        holder.wait()
        if self.workers is not None:
            self.workers.reap()
        raise WorkerInterrupted(
            f"owned worker pid={holder.pid} terminated after partial write "
            f"{path.name}"
        )
