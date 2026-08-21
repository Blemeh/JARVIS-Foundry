"""M2 promotion: build release, promote, validate, rollback drill, return.

Follows the canonical M10 pattern (scripts/prompt4_m10_promote.py):
build_release -> set_current -> record_generation -> validate ->
rollback_current -> validate -> set_current (return) -> validate.
Writes evidence to docs/evidence/M2_EXECUTIVE/M2_PROMOTE.json.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from jarvis_foundry.deployment.layout import (
    default_live_foundry_home,
    release_dir,
)
from jarvis_foundry.deployment.release import (
    build_release,
    read_current,
    read_previous,
    record_generation,
    rollback_current,
    set_current,
)
from jarvis_foundry.platform.windows_subprocess import headless_run_kwargs

SHA = sys.argv[1] if len(sys.argv) > 1 else None
HOME = default_live_foundry_home()
EVIDENCE = REPO / "docs" / "evidence" / "M2_EXECUTIVE"


def _current_release_pythonw() -> Path:
    sha = read_current()
    if not sha:
        raise FileNotFoundError("CURRENT pointer missing")
    pythonw = release_dir(sha) / ".venv" / "Scripts" / "pythonw.exe"
    if not pythonw.is_file():
        raise FileNotFoundError(f"release pythonw missing for CURRENT {sha}")
    return pythonw


def validate() -> dict:
    result = subprocess.run(
        [
            str(_current_release_pythonw()),
            "-m", "jarvis_foundry.deployment.headless_launcher",
            "--home", str(HOME), "--validate-only",
        ],
        capture_output=True, text=True, timeout=120,
        env={k: v for k, v in __import__("os").environ.items()
             if k not in {"PYTHONPATH", "PYTHONHOME"}},
        **headless_run_kwargs(),
    )
    body = (result.stdout or "") + (result.stderr or "")
    try:
        parsed = json.loads(result.stdout.strip().splitlines()[-1])
    except Exception:
        parsed = {"raw": body[-800:]}
    return {"returncode": result.returncode, "parsed": parsed}


def main() -> int:
    if SHA is None:
        print(json.dumps({"error": "usage: m2_promote.py <full-sha>"}))
        return 2
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    before = {"current": read_current(), "previous": read_previous()}
    built = build_release(source_repo=REPO, source_sha=SHA)
    promoted = set_current(SHA)
    record_generation(foundry_home=HOME)
    after_promote = validate()
    rolled = rollback_current(foundry_home=HOME)
    after_rollback = validate()
    returned = set_current(SHA)
    record_generation(foundry_home=HOME)
    after_return = validate()
    report = {
        "candidate": "Executive Intelligence M2 (goals/decisions/eligibility/"
                     "calibration/CC projection/live proof)",
        "builder_commit": SHA,
        "before": before,
        "built": built["manifest"],
        "promoted": promoted,
        "validate_promoted": after_promote,
        "rollback": rolled,
        "validate_rollback": after_rollback,
        "returned": returned,
        "validate_return": after_return,
    }
    ok = (
        after_promote["returncode"] == 0
        and after_rollback["returncode"] == 0
        and after_return["returncode"] == 0
        and read_current() == SHA
        and read_previous() == before["current"]
    )
    report["verdict"] = "PASS" if ok else "FAIL"
    report["after"] = {"current": read_current(), "previous": read_previous()}
    (EVIDENCE / "M2_PROMOTE.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps({
        "verdict": report["verdict"],
        "current": read_current(),
        "previous": read_previous(),
    }, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
