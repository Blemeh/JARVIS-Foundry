"""Lane B probe: what happens when a verification pass is resumed after the
frozen binding drifted (working tree changed while the pass was open)?

Reproduction (all through the normal runtime path):
  1. an owner-goal intent resolves to a mission step on a deterministic local
     capability;
  2. the pass is interrupted at ``post verification.started`` (the fault hook
     point the loop itself exposes, i.e. after the binding was frozen);
  3. the step's subject changes on disk;
  4. the same pass is resumed.

Writes its receipt to ``probe/VERIFIER_BINDING_DRIFT_PROBE.json`` so the
before/after of the repair stays durable.  Disposable runtime only.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import traceback
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
from jarvis_foundry.evolution.executive_portfolio import (  # noqa: E402
    PortfolioPolicy,
    configure_portfolio_policy,
    register_work_item,
    select_next_work,
)
from jarvis_foundry.executive.portfolio_bridge import materialize_portfolio_selection  # noqa: E402
from jarvis_foundry.fabric.adapter import FabricRoleAdapter  # noqa: E402
from jarvis_foundry.fabric.expansion import acquire_hash_capability  # noqa: E402
from jarvis_foundry.fabric.loop import seed_builtins  # noqa: E402
from jarvis_foundry.mission.contracts import next_executable_contract  # noqa: E402
from jarvis_foundry.mission.loop import PASSED, MissionLoop  # noqa: E402
from jarvis_foundry.roles.adapter import FakeHermesAdapter  # noqa: E402
from jarvis_foundry.roles.router import RoleRouter  # noqa: E402
from jarvis_foundry.v3_fault import FaultInjector, V3InjectedFault  # noqa: E402

RUN = HERE / "drift_run"
RESULT = HERE / "VERIFIER_BINDING_DRIFT_PROBE.json"
RESOURCE_ID = "resource-lane-b-drift"
GOAL = "Compute and record the integrity digest of the supplied evidence file."


def _git(*args: str, cwd: Path) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
        check=False, timeout=60,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    return completed.stdout.strip()


def _remove_readonly(function: Any, path: str, _error: Any) -> None:
    os.chmod(path, 0o700)
    function(path)


def _loop(db: FoundryDatabase, repo: Path, stop: Path, **kwargs: Any) -> MissionLoop:
    adapter = FabricRoleAdapter(
        db, owned_root=repo, inner=FakeHermesAdapter(working_root=repo),
        operator_executable_allowlist=(),
    )
    return MissionLoop(db, RoleRouter(), adapter, stop_path=stop, **kwargs)


def main() -> int:
    if RUN.exists():
        shutil.rmtree(RUN, onexc=_remove_readonly)
    RUN.mkdir(parents=True)
    repo = RUN / "project"
    repo.mkdir()
    _git("init", "-b", "main", cwd=repo)
    _git("config", "user.name", "Lane B Drift Probe", cwd=repo)
    _git("config", "user.email", "lane-b@example.invalid", cwd=repo)
    evidence = repo / "evidence.bin"
    evidence.write_bytes(b"lane-b-binding-drift-evidence")
    _git("add", "evidence.bin", cwd=repo)
    _git("commit", "-m", "evidence", cwd=repo)

    db = FoundryDatabase(RUN / "foundry.db")
    db.initialize()
    seed_builtins(db)
    payload = inspect_git_resource(repo, resource_id=RESOURCE_ID)
    db.append(registration_event(payload, source_id="lane-b-drift-probe"))

    receipt: dict[str, Any] = {"schema": "jarvis.lane-b.verifier-binding-drift-probe.v1"}
    try:
        configure_portfolio_policy(db, PortfolioPolicy())
        acquired = acquire_hash_capability(db, repo)
        receipt["acquired"] = acquired["status"]
        register_work_item(
            db, item_id="owner-goal:lane-b-drift", kind="OWNER_GOAL", objective=GOAL,
            expected_value=0.9, urgency=0.5, confidence=0.9, effort=0.2, dependencies=(),
            metadata={
                "goal_id": "owner-goal:lane-b-drift",
                "success_criteria": ["the evidence digest is computed and independently verified"],
                "intent": {
                    "needs": ["compute.file_hash"],
                    "inputs": {"file_to_hash": str(evidence)},
                },
            },
        )
        decision = select_next_work(db, loop_id="lane-b-drift")
        materialized = materialize_portfolio_selection(
            db, decision=decision.to_payload(), resource_id=RESOURCE_ID,
            source_id="lane-b-drift-probe",
        )
        mission_id = str(materialized["mission_id"])
        receipt["mission_id"] = mission_id
        contract = next_executable_contract(db, mission_id)
        receipt["contract"] = {
            "step_id": contract.step_id,
            "capability": contract.capability,
            "source": contract.source,
        }
        stop = RUN / "STOP"
        injector = FaultInjector(["post verification.started"])
        interrupted = False
        try:
            _loop(db, repo, stop, max_repairs=0, fault_hook=injector).run_until_settled(
                **contract.runtime_kwargs(db)
            )
        except V3InjectedFault as error:
            interrupted = True
            receipt["interrupted_at"] = error.point
        receipt["interrupted"] = interrupted

        evidence.write_bytes(b"changed while the verification pass was open")
        receipt["mutated_sha256"] = hashlib.sha256(evidence.read_bytes()).hexdigest()

        resumed_outcome: dict[str, Any] = {}
        try:
            outcome = _loop(db, repo, stop, max_repairs=0).run_until_settled(
                **contract.runtime_kwargs(db)
            )
            resumed_outcome = {
                "raised": None,
                "outcome": str(outcome.outcome),
                "reason": str(outcome.reason or ""),
            }
        except Exception as error:  # noqa: BLE001 - the probe records what happened
            resumed_outcome = {
                "raised": type(error).__name__,
                "detail": str(error),
                "traceback_tail": traceback.format_exc().strip().splitlines()[-6:],
            }
        receipt["resume"] = resumed_outcome
        with db.connect(readonly=True) as connection:
            rows = [
                dict(row) for row in connection.execute(
                    "SELECT verdict_id, status, verdict FROM verification_verdicts "
                    "WHERE mission_id = ? ORDER BY rowid",
                    (mission_id,),
                ).fetchall()
            ]
        receipt["verdicts"] = rows
        receipt["verdict_is_not_pass"] = not any(
            str(row["status"]) == "PASS" for row in rows
        )
    except Exception as error:  # noqa: BLE001
        receipt["probe_exception"] = f"{type(error).__name__}: {error}"
        traceback.print_exc()

    RESULT.write_text(json.dumps(receipt, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print(json.dumps(receipt, indent=2, sort_keys=True, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
