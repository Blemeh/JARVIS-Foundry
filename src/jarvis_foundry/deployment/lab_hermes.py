"""LAB-only Hermes-named adapter that performs bounded file writes.

Used so an installed Supervisor can prove the CASE-A mission cycle without
calling the owner's live Hermes profile or external provider. The adapter
registers as ``hermes`` on the HybridWorkerRoleAdapter path.
"""
from __future__ import annotations

from pathlib import Path

from ..workers.adapters.fakes import ScriptedOutcome, ScriptedWorkerAdapter
from ..workers.contracts import WORKER_HERMES

DEFAULT_EXPECTED = {
    "step-a": ("step_a.md", "STEP-A\n"),
    "step-b": ("step_b.md", "STEP-B\n"),
    "step-c": ("step_c.md", "STEP-C\n"),
}


class LabHermesAdapter(ScriptedWorkerAdapter):
    """Hermes-pinned scripted worker: builder claims are not verifier truth."""

    def __init__(self, expected: dict[str, tuple[str, str]] | None = None) -> None:
        super().__init__(WORKER_HERMES)
        self.expected = expected or DEFAULT_EXPECTED

    def dispatch(self, contract, *, heartbeat):
        artifact, expected = self.expected[contract.step_id]
        path = Path(contract.working_root) / artifact
        if contract.role in ("builder", "repair"):
            defect = (
                contract.role == "builder"
                and contract.step_id == "step-b"
                and int(contract.attempt) == 1
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
