"""Lane B A/B probe: does the verifier-routing repair actually change behaviour?

Runs the *same* intent-resolved deterministic mission twice on disposable
runtime state:

  A. repair disabled — ``fabric.adapter.canonical_verifier_dispatch`` is
     patched out of the module namespace, i.e. exactly the pre-repair runtime
     behaviour (the verifier session falls through to the worker adapter);
  B. repair enabled  — the unpatched runtime path.

A regression that passes on both sides would prove nothing, so the probe
records both receipts and asserts they differ.  Nothing here edits the source
tree: the disabled side is an in-process module-attribute patch.

Disposable runtime only.
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

import jarvis_foundry.fabric.adapter as fabric_adapter  # noqa: E402
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

RESULT = HERE / "VERIFIER_ROUTING_AB_PROBE.json"
RUN = HERE / "ab_run"
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


def _one_run(label: str, *, disable_repair: bool) -> dict[str, Any]:
    run_dir = RUN / label
    if run_dir.exists():
        shutil.rmtree(run_dir, onexc=_remove_readonly)
    run_dir.mkdir(parents=True)
    repo = run_dir / "project"
    repo.mkdir()
    _git("init", "-b", "main", cwd=repo)
    _git("config", "user.name", f"Lane B AB {label}", cwd=repo)
    _git("config", "user.email", "lane-b@example.invalid", cwd=repo)
    evidence = repo / "evidence.bin"
    evidence.write_bytes(f"lane-b-ab-{label}".encode())
    _git("add", "evidence.bin", cwd=repo)
    _git("commit", "-m", "evidence", cwd=repo)

    receipt: dict[str, Any] = {"label": label, "repair_disabled": disable_repair}
    original = fabric_adapter.canonical_verifier_dispatch
    if disable_repair:
        fabric_adapter.canonical_verifier_dispatch = lambda *a, **k: None
    try:
        db = FoundryDatabase(run_dir / "foundry.db")
        db.initialize()
        seed_builtins(db)
        resource_id = f"resource-lane-b-ab-{label}"
        payload = inspect_git_resource(repo, resource_id=resource_id)
        db.append(registration_event(payload, source_id=f"lane-b-ab-{label}"))
        configure_portfolio_policy(db, PortfolioPolicy())
        receipt["acquired"] = acquire_hash_capability(db, repo)["status"]
        register_work_item(
            db, item_id=f"owner-goal:lane-b-ab-{label}", kind="OWNER_GOAL", objective=GOAL,
            expected_value=0.9, urgency=0.5, confidence=0.9, effort=0.2, dependencies=(),
            metadata={
                "goal_id": f"owner-goal:lane-b-ab-{label}",
                "success_criteria": ["the evidence digest is computed and independently verified"],
                "intent": {
                    "needs": ["compute.file_hash"],
                    "inputs": {"file_to_hash": str(evidence)},
                },
            },
        )
        decision = select_next_work(db, loop_id=f"lane-b-ab-{label}")
        materialized = materialize_portfolio_selection(
            db, decision=decision.to_payload(), resource_id=resource_id,
            source_id=f"lane-b-ab-{label}",
        )
        mission_id = str(materialized["mission_id"])
        receipt["mission_id"] = mission_id
        contract = next_executable_contract(db, mission_id)
        adapter = FabricRoleAdapter(
            db, owned_root=repo, inner=FakeHermesAdapter(working_root=repo),
            operator_executable_allowlist=(),
        )
        loop = MissionLoop(
            db, RoleRouter(), adapter, max_repairs=1, stop_path=run_dir / "STOP",
        )
        outcome = loop.run_until_settled(**contract.runtime_kwargs(db))
        receipt["outcome"] = str(outcome.outcome)
        receipt["reason"] = str(outcome.reason or "")
        with db.connect(readonly=True) as connection:
            verdicts = [
                dict(row) for row in connection.execute(
                    "SELECT verdict_id, subject_id, status, verdict, findings_json, "
                    "evidence_json FROM verification_verdicts WHERE mission_id = ? "
                    "ORDER BY rowid",
                    (mission_id,),
                ).fetchall()
            ]
            runs = [
                dict(row) for row in connection.execute(
                    "SELECT run_id, role, provider, model, status FROM agent_runs "
                    "WHERE mission_id = ? ORDER BY rowid",
                    (mission_id,),
                ).fetchall()
            ]
        receipt["verdicts"] = [
            {
                "status": row["status"],
                "verdict": row["verdict"],
                "findings": json.loads(str(row["findings_json"] or "[]")),
                "predicate": json.loads(str(row["evidence_json"] or "{}")).get(
                    "jarvis_predicate"
                ),
                "answered_by": json.loads(str(row["evidence_json"] or "{}")).get(
                    "verifier_answered_by"
                ),
            }
            for row in verdicts
        ]
        receipt["agent_runs"] = runs
        receipt["mission_completed"] = bool(db.events(event_types=("mission.completed",)))
        receipt["canonical_observations"] = len(
            db.events(event_types=("fabric.canonical_verification_observed",))
        )
        receipt["expected_digest"] = hashlib.sha256(evidence.read_bytes()).hexdigest()
    except Exception as error:  # noqa: BLE001
        receipt["exception"] = f"{type(error).__name__}: {error}"
        receipt["traceback_tail"] = traceback.format_exc().strip().splitlines()[-6:]
    finally:
        fabric_adapter.canonical_verifier_dispatch = original
    return receipt


def main() -> int:
    if RUN.exists():
        shutil.rmtree(RUN, onexc=_remove_readonly)
    RUN.mkdir(parents=True)
    before = _one_run("repair_disabled", disable_repair=True)
    after = _one_run("repair_enabled", disable_repair=False)
    proof = {
        "schema": "jarvis.lane-b.verifier-routing-ab-probe.v1",
        "before_repair_disabled": before,
        "after_repair_enabled": after,
        "discriminates": (
            before.get("outcome") != after.get("outcome")
            and after.get("outcome") == PASSED
            and not any(
                str(item.get("status")) == "PASS" for item in before.get("verdicts") or []
            )
        ),
    }
    RESULT.write_text(json.dumps(proof, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print(json.dumps({
        "discriminates": proof["discriminates"],
        "before": {
            "outcome": before.get("outcome"),
            "reason": (before.get("reason") or "")[:200],
            "verdicts": before.get("verdicts"),
        },
        "after": {
            "outcome": after.get("outcome"),
            "verdicts": after.get("verdicts"),
            "observations": after.get("canonical_observations"),
        },
    }, indent=2, default=str))
    return 0 if proof["discriminates"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
