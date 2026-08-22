"""Start Prompt 4 M11 seven-day unattended trust gate. Does not claim L6 PASS."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from jarvis_foundry.clock import isoformat
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.deployment.layout import default_live_foundry_home
from jarvis_foundry.deployment.release import read_current, read_previous
from jarvis_foundry.paths import FoundryPaths
from jarvis_foundry.proving import register_window, window_status
from jarvis_foundry.runtime.authority import PRODUCTION_EXECUTOR_COUNT

WINDOW_ID = "prompt4-m11-l6-20260819"
REQUIRED_DAYS = 7
REQUIRED_MISSIONS = 10
DURATION = 7 * 24 * 3600
EVIDENCE_LIVE = default_live_foundry_home() / "evidence" / "proving-window" / "prompt4-m11"
REPO_EVIDENCE = REPO / "docs" / "evidence" / "PROMPT4_RECOVERY"


def main() -> int:
    EVIDENCE_LIVE.mkdir(parents=True, exist_ok=True)
    REPO_EVIDENCE.mkdir(parents=True, exist_ok=True)
    home = default_live_foundry_home()
    database = FoundryDatabase(FoundryPaths(home).database)
    database.initialize()
    started = isoformat()
    register_window(
        database, WINDOW_ID,
        required_days=REQUIRED_DAYS,
        required_missions=REQUIRED_MISSIONS,
        started_at=started,
        mission_id=None,
        controller="supervisor",
    )
    current = read_current()
    previous = read_previous()
    end_at = (datetime.now(timezone.utc) + timedelta(seconds=DURATION)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    manifest = {
        "schema": "jarvis-proving-window-manifest-v1",
        "window_id": WINDOW_ID,
        "release_source_commit": current,
        "release_pointer": str(Path(r"C:\Users\b\AppData\Local\JARVIS") / "CURRENT"),
        "started_at": started,
        "required_end_at": end_at,
        "required_duration_seconds": DURATION,
        "max_heartbeat_gap_seconds": 300,
        "canonical_sha": subprocess.check_output(
            ["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True).strip(),
        "live_db": str(FoundryPaths(home).database),
        "live_schema": 34,
        "current_release": current,
        "previous_release": previous,
        "production_mission_path_count": PRODUCTION_EXECUTOR_COUNT,
        "created_by": "supervisor",
        "external_effects": "NOT_TESTED",
        "credential_values_recorded": False,
        "l6_claim_before_elapsed": "FORBIDDEN",
    }
    manifest_path = EVIDENCE_LIVE / "MANIFEST.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    monitor_src = REPO / "scripts" / "convergence" / "monitor_proving_window.py"
    monitor_dst = EVIDENCE_LIVE / "monitor_proving_window.py"
    shutil.copy2(monitor_src, monitor_dst)
    release_py = (
        Path(r"C:\Users\b\AppData\Local\JARVIS") / "releases" / current
        / ".venv" / "Scripts" / "python.exe"
    )
    checkpoint_path = EVIDENCE_LIVE / "CHECKPOINT.json"
    env = {k: v for k, v in __import__("os").environ.items()
           if k not in {"PYTHONPATH", "PYTHONHOME"}}
    first = subprocess.run(
        [str(release_py), str(monitor_dst),
         "--database", str(FoundryPaths(home).database),
         "--manifest", str(manifest_path),
         "--output", str(checkpoint_path)],
        capture_output=True, text=True, timeout=60, env=env,
    )
    task_name = r"\JARVIS-Prompt4-M11-Trust-Monitor"
    tr = (
        f"\"{release_py}\" \"{monitor_dst}\" "
        f"--database \"{FoundryPaths(home).database}\" "
        f"--manifest \"{manifest_path}\" "
        f"--output \"{checkpoint_path}\""
    )
    created = subprocess.run(
        ["schtasks", "/Create", "/TN", task_name, "/SC", "MINUTE", "/MO", "5",
         "/F", "/RL", "LIMITED", "/TR", tr],
        capture_output=True, text=True, timeout=60,
    )
    status = window_status(database, WINDOW_ID)
    resume = f"""# Prompt 4 M11 resume

Do not restart M9/M10.
Do not claim L6 until this window's real elapsed duration is complete.

window_id: {WINDOW_ID}
started_at: {started}
required_end_at: {end_at}
required_days: {REQUIRED_DAYS}
required_missions: {REQUIRED_MISSIONS}
CURRENT: {current}
PREVIOUS: {previous}
canonical SHA: {manifest["canonical_sha"]}
live DB: {manifest["live_db"]}
manifest: {manifest_path}
checkpoint: {checkpoint_path}
scheduled task: {task_name}

Next session:
1. Read CHECKPOINT.json status (must be WAITING_ELAPSED until 7 real days).
2. Confirm CURRENT/PREVIOUS and Supervisor RUNNING.
3. Only after required_end_at + READY_TO_CLOSE + proving window_status met:
   run affected tests, FULL suite once, one independent verifier.
"""
    (EVIDENCE_LIVE / "RESUME.md").write_text(resume, encoding="utf-8")
    (REPO_EVIDENCE / "M11_START.json").write_text(json.dumps({
        "window_id": WINDOW_ID,
        "started_at": started,
        "required_end_at": end_at,
        "current": current,
        "previous": previous,
        "canonical_sha": manifest["canonical_sha"],
        "monitor_first": {
            "returncode": first.returncode,
            "stdout": first.stdout,
            "stderr": first.stderr[-400:],
        },
        "schtasks": {
            "returncode": created.returncode,
            "stdout": created.stdout,
            "stderr": created.stderr,
        },
        "window_status": status,
        "manifest": str(manifest_path),
        "verdict": "RUNNING",
        "l6": "NOT YET COMPLETE",
    }, indent=2, default=str), encoding="utf-8")
    (REPO_EVIDENCE / "M11_RESUME.md").write_text(resume, encoding="utf-8")
    print(json.dumps({
        "verdict": "RUNNING",
        "started_at": started,
        "required_end_at": end_at,
        "monitor": first.stdout.strip(),
        "schtasks_rc": created.returncode,
        "window_verdict": status.get("verdict"),
    }, indent=2))
    return 0 if first.returncode == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
