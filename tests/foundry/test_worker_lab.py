"""B3-equivalent clean diagnosis harness (H14, §13): genuinely private
held-out data, equivalent visible information, held-out-only judgement."""
from __future__ import annotations

from pathlib import Path

from jarvis_foundry.workers.adapters.fakes import ScriptedWorkerAdapter
from jarvis_foundry.workers.contracts import WORKER_HERMES, WORKER_OPENHANDS
from jarvis_foundry.workers.lab import DiagnosisLab, LabRunner


CORRECT_FIX = '''\
def _is_number(cell: str) -> bool:
    try:
        float(cell)
        return True
    except ValueError:
        return False

def load_rows(text: str):
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    first = [c.strip() for c in lines[0].split(",")]
    body = lines[1:] if all(not _is_number(c) for c in first) else lines
    return [line.split(",") for line in body]
'''


class FixingAdapter(ScriptedWorkerAdapter):
    def __init__(self, framework: str, source: str):
        super().__init__(framework)
        self.source = source

    def dispatch(self, contract, *, heartbeat):
        (contract.working_root / "rows.py").write_text(
            self.source, encoding="utf-8")
        return super().dispatch(contract, heartbeat=heartbeat)


def adapter_for(fixtures: dict[str, FixingAdapter]):
    def factory(framework: str):
        return fixtures[framework]
    return factory


def test_naive_header_fix_passes_public_but_fails_held_out(tmp_path):
    """The misleading-symptom trap: public tests pass, held-out refutes."""
    lab = DiagnosisLab.create(mission_id="m1", lab_root=tmp_path)
    naive = FixingAdapter(
        WORKER_HERMES, "def load_rows(text):\n"
                       "    lines = [l for l in text.splitlines() if l.strip()]\n"
                       "    return [l.split(',') for l in lines[1:]]\n")
    runner = LabRunner(adapter_for=adapter_for({WORKER_HERMES: naive}))
    result = runner.run(lab, WORKER_HERMES, run_id="run-naive")

    assert result.public_passed is True
    assert result.held_out_passed is False
    assert result.isolation_failures == ()


def test_root_cause_fix_passes_both_public_and_held_out(tmp_path):
    lab = DiagnosisLab.create(mission_id="m2", lab_root=tmp_path)
    correct = FixingAdapter(WORKER_HERMES, CORRECT_FIX)
    runner = LabRunner(adapter_for=adapter_for({WORKER_HERMES: correct}))
    result = runner.run(lab, WORKER_HERMES, run_id="run-correct")

    assert result.public_passed is True
    assert result.held_out_passed is True


def test_held_out_criterion_genuinely_private(tmp_path):
    lab = DiagnosisLab.create(mission_id="m3", lab_root=tmp_path)
    boundary = lab.boundary
    # Private files exist only under controller-private.
    held_out_file = boundary.held_out_tests / "test_held_out.py"
    assert held_out_file.exists()
    assert not (boundary.worker_visible / "controller-private").exists()
    assert boundary.assert_private_unreachable(
        boundary.worktree, boundary.task_contract,
        boundary.public_tests) == ()


def test_equivalent_visible_information_for_both_frameworks(tmp_path):
    """Both frameworks receive the same contract (same content hash)."""
    lab = DiagnosisLab.create(mission_id="m4", lab_root=tmp_path)
    hermes_contract = lab.build_contract(run_id="h", framework=WORKER_HERMES)
    openhands_contract = lab.build_contract(run_id="o", framework=WORKER_OPENHANDS)
    from jarvis_foundry.events import canonical_json
    hermes_payload = hermes_contract.to_payload()
    openhands_payload = openhands_contract.to_payload()
    hermes_payload["run_id"] = openhands_payload["run_id"] = "same"
    assert canonical_json(hermes_payload) == canonical_json(openhands_payload)
