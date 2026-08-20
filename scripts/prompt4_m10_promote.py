"""Prompt 4 M10: build/promote/rollback/return for the Command Center truth fix."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from jarvis_foundry.command_center import _CommandCenterHandler, build_snapshot
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.deployment.layout import (
    current_pointer,
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
from jarvis_foundry.paths import FoundryPaths
from jarvis_foundry.platform.windows_subprocess import headless_run_kwargs

SHA = "52ee917208e5964992125f478508460cd9893514"
HOME = default_live_foundry_home()
EVIDENCE = REPO / "docs" / "evidence" / "PROMPT4_RECOVERY"


def _current_release_pythonw() -> Path:
    sha = current_pointer().read_text(encoding="utf-8").strip().splitlines()[0].strip()
    pythonw = release_dir(sha) / ".venv" / "Scripts" / "pythonw.exe"
    if not pythonw.is_file():
        raise FileNotFoundError(f"release pythonw missing for CURRENT {sha}: {pythonw}")
    return pythonw


def validate() -> dict:
    result = subprocess.run(
        [
            str(_current_release_pythonw()),
            "-m", "jarvis_foundry.deployment.headless_launcher",
            "--home", str(HOME), "--validate-only",
        ],
        capture_output=True, text=True, timeout=60,
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


def html_probe() -> dict:
    database = FoundryDatabase(FoundryPaths(HOME).database)
    database.initialize()
    snapshot = build_snapshot(database, stop_path=HOME / "EMERGENCY_STOP")
    html = _CommandCenterHandler._html(snapshot)
    return {
        "has_local_candidate": "LOCAL CANDIDATE" in html,
        "has_live_foundry": "LIVE FOUNDRY" in html,
        "has_current": "CURRENT" in html,
        "snapshot_current": (snapshot.get("release_identity") or {}).get("current"),
        "snapshot_previous": (snapshot.get("release_identity") or {}).get("previous"),
    }


def main() -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    before = {"current": read_current(), "previous": read_previous()}
    built = build_release(source_repo=REPO, source_sha=SHA)
    promoted = set_current(SHA)
    record_generation(foundry_home=HOME)
    after_promote = validate()
    html_after = html_probe()
    rolled = rollback_current(foundry_home=HOME)
    after_rollback = validate()
    returned = set_current(SHA)
    record_generation(foundry_home=HOME)
    after_return = validate()
    html_return = html_probe()
    report = {
        "candidate": "Command Center HTML labeled live Foundry as LOCAL CANDIDATE",
        "builder_commit": SHA,
        "before": before,
        "built": built["manifest"],
        "promoted": promoted,
        "validate_promoted": after_promote,
        "html_promoted": html_after,
        "rollback": rolled,
        "validate_rollback": after_rollback,
        "returned": returned,
        "validate_return": after_return,
        "html_return": html_return,
        "measured_improvement": (
            html_after.get("has_live_foundry")
            and not html_after.get("has_local_candidate")
            and html_return.get("has_live_foundry")
            and not html_return.get("has_local_candidate")
        ),
    }
    ok = (
        report["measured_improvement"]
        and after_promote["returncode"] == 0
        and after_rollback["returncode"] == 0
        and after_return["returncode"] == 0
        and read_current() == SHA
        and read_previous() == before["current"]
    )
    report["verdict"] = "PASS" if ok else "FAIL"
    report["after"] = {"current": read_current(), "previous": read_previous()}
    (EVIDENCE / "M10_PROMOTE.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps({
        "verdict": report["verdict"],
        "current": read_current(),
        "previous": read_previous(),
        "measured_improvement": report["measured_improvement"],
    }, indent=2))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
