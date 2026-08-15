"""Fresh repair worker: reproduces a real defect and applies a bounded fix."""

from __future__ import annotations

import argparse
import json
import os
import subprocess  # nosec B404 - bounded test execution with a fixed python path
import sys
from pathlib import Path
from typing import Any

from jarvis_foundry.orchestration.mission_context import MissionContext
from jarvis_foundry.orchestration.paths import sha256_file
from jarvis_foundry.orchestration.repair import apply_repair_transform
from jarvis_foundry.orchestration.schemas import FileChange, TestRun, WorkerResult


def _run(clone: Path, python: Path, command: list[str]) -> dict[str, Any]:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(clone / "src")
    argv = ["-m", *command] if command and command[0] == "pytest" else command
    completed = subprocess.run(  # nosec B603 B607 - no shell, fixed python path
        [str(python), *argv],
        cwd=str(clone),
        capture_output=True,
        text=True,
        timeout=1200,
        env=env,
        check=False,
    )
    return {
        "exit_code": completed.returncode,
        "output": completed.stdout + completed.stderr,
    }


def _apply_fix(clone: Path, spec: dict[str, Any]) -> Path:
    target = clone / str(spec["target_file"])
    if not target.is_file():
        raise RuntimeError(f"repair target missing: {target}")
    before = target.read_text(encoding="utf-8")
    after = apply_repair_transform(before, str(spec["transform_id"]))
    if after == before:
        raise RuntimeError("repair transform produced no change")
    target.write_text(after, encoding="utf-8", newline="\n")
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mission", required=True, type=Path)
    parser.add_argument("--clone", required=True, type=Path)
    parser.add_argument("--result", required=True, type=Path)
    args = parser.parse_args(argv)
    mission = MissionContext.from_dict(json.loads(args.mission.read_text(encoding="utf-8")))
    spec = dict(mission.worker_spec)
    clone = args.clone.resolve()
    python = Path(sys.executable)
    reproduce = list(spec["reproduce_command"])
    regression = list(spec["regression_command"])

    before_reproduce = _run(clone, python, reproduce)
    reproduced = before_reproduce["exit_code"] != 0
    target = _apply_fix(clone, spec) if reproduced else clone / str(spec["target_file"])
    after_reproduce = _run(clone, python, reproduce) if reproduced else before_reproduce
    regression_run = _run(clone, python, regression)
    passed = reproduced and after_reproduce["exit_code"] == 0 and regression_run["exit_code"] == 0
    change = FileChange(
        path=target.relative_to(clone).as_posix(),
        action="modified",
        sha256=sha256_file(target),
        size=target.stat().st_size,
    )
    tests = (
        TestRun(
            name=" ".join(reproduce),
            passed=after_reproduce["exit_code"] == 0,
            exit_code=after_reproduce["exit_code"],
            summary=after_reproduce["output"].strip().splitlines()[-1]
            if after_reproduce["output"].strip()
            else "",
            evidence_path="repair-reproduce.txt",
        ),
        TestRun(
            name=" ".join(regression),
            passed=regression_run["exit_code"] == 0,
            exit_code=regression_run["exit_code"],
            summary=regression_run["output"].strip().splitlines()[-1]
            if regression_run["output"].strip()
            else "",
            evidence_path="repair-regression.txt",
        ),
    )
    result = WorkerResult(
        status="PASS" if passed else "FAIL",
        claims={
            "defect_reproduced": reproduced,
            "transform_id": spec.get("transform_id"),
            "target_file": spec.get("target_file"),
        },
        files_changed=(change,),
        commands_run=(
            f"{python} {' '.join(reproduce)}",
            f"{python} {' '.join(regression)}",
        ),
        tests=tests,
        evidence=("repair-reproduce.txt", "repair-regression.txt"),
        artifacts=(),
        external_effects={"protected_repos": 0, "owner_projects": 0, "live_state": 0},
        blockers=() if passed else ("repair did not make all required tests pass",),
        uncertainty="low",
        recommended_next_action="machinery reverification and acceptance",
    )
    (clone / "repair-reproduce.txt").write_text(before_reproduce["output"], encoding="utf-8")
    (clone / "repair-regression.txt").write_text(regression_run["output"], encoding="utf-8")
    args.result.write_text(
        json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result.to_dict(), sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
