"""Prompt 4 M9: bounded real local multi-system mission on the production kernel."""
from __future__ import annotations

import json
import subprocess
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "scripts"))

from prompt3_capability_gates import (  # noqa: E402
    FOUNDRY_HOME,
    YAML_PATH,
    drive_until_complete,
    lab_config,
    make_worktree,
    register_kernel_mission,
    set_yaml_enabled,
)
from jarvis_foundry.command_center import CommandCenterServer, build_snapshot
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.owner_controls import OwnerControlService, sign_command
from jarvis_foundry.paths import FoundryPaths
from jarvis_foundry.runtime.authority import PRODUCTION_EXECUTOR_COUNT
from jarvis_foundry.supervisor.estop import EmergencyStop

STAMP = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
EVIDENCE = REPO / "docs" / "evidence" / "PROMPT4_RECOVERY"


def git_hash(worktree: Path, rel: str) -> str | None:
    path = worktree / rel
    if not path.is_file():
        return None
    result = subprocess.run(
        ["git", "hash-object", str(path)],
        cwd=worktree, capture_output=True, text=True, check=False, timeout=30,
    )
    return result.stdout.strip() or None


def main() -> int:
    EVIDENCE.mkdir(parents=True, exist_ok=True)
    paths = FoundryPaths(FOUNDRY_HOME)
    database = FoundryDatabase(paths.database)
    database.initialize()
    yaml_original = YAML_PATH.read_text(encoding="utf-8")
    set_yaml_enabled(False)
    time.sleep(2)
    report: dict = {"stamp": STAMP}
    try:
        secret = b"prompt4-m9-owner"
        nonce = uuid.uuid4().hex
        exact = (
            "M9 bounded local proving: write measured outcome files in an "
            "isolated git worktree; Foundry ledger and git are systems of record."
        )
        sig = sign_command(secret, "GOAL", "prompt4-m9", exact, nonce)
        owner = OwnerControlService(
            database, secret=secret, stop_path=FOUNDRY_HOME / "EMERGENCY_STOP",
            owner_id="prompt4-owner")
        goal = owner.apply(
            "GOAL", target="prompt4-m9", exact_text=exact, nonce=nonce, signature=sig)

        work = make_worktree(Path(r"C:\temp") / f"jarvis-prompt4-m9-{STAMP}")
        mission_id = f"mission-prompt4-m9-{STAMP[-8:]}"
        resource_id = f"resource-prompt4-m9-{STAMP[-8:]}"
        register_kernel_mission(
            database, mission_id=mission_id, resource_id=resource_id, worktree=work,
            objective=exact,
            next_action="create step_a.md containing STEP-A",
            steps=[("step-a", "step_a.md", "STEP-A"),
                   ("step-b", "step_b.md", "STEP-B"),
                   ("step-c", "step_c.md", "STEP-C")],
            created_from=f"owner-goal:{goal.directive_id}",
        )

        stop = EmergencyStop(FOUNDRY_HOME / "EMERGENCY_STOP")
        stop.activate(reason="prompt4 M9 STOP interrupt probe", source="prompt4-m9")
        frozen = drive_until_complete(
            database, paths, lab_config(FOUNDRY_HOME / "var" / "prompt4-m9"),
            mission_id, instance="m9-stop", limit=2)
        stop_blocked_progress = not all(
            (work / name).is_file() for name in ("step_a.md", "step_b.md", "step_c.md"))
        stop.clear()

        reports = drive_until_complete(
            database, paths, lab_config(FOUNDRY_HOME / "var" / "prompt4-m9"),
            mission_id, instance="m9")
        state = database.row("missions", "mission_id", mission_id)["state"]
        artifacts = {
            name: (work / name).read_text(encoding="utf-8") if (work / name).is_file() else None
            for name in ("step_a.md", "step_b.md", "step_c.md")
        }
        hashes = {name: git_hash(work, name) for name in artifacts}
        with database.connect() as connection:
            evidence_n = connection.execute(
                "SELECT COUNT(*) FROM evidence_bundles WHERE mission_id = ?",
                (mission_id,),
            ).fetchone()[0]
            pass_n = connection.execute(
                "SELECT COUNT(*) FROM verification_verdicts "
                "WHERE mission_id = ? AND status IN ('PASS', 'PASSED')",
                (mission_id,),
            ).fetchone()[0]
            fail_n = connection.execute(
                "SELECT COUNT(*) FROM verification_verdicts "
                "WHERE mission_id = ? AND status IN ('FAIL', 'FAILED')",
                (mission_id,),
            ).fetchone()[0]
        duplicate = drive_until_complete(
            database, paths, lab_config(FOUNDRY_HOME / "var" / "prompt4-m9-dup"),
            mission_id, instance="m9-dup", limit=2)
        after_dup = {
            name: (work / name).read_text(encoding="utf-8") if (work / name).is_file() else None
            for name in artifacts
        }
        snapshot = build_snapshot(database, stop_path=FOUNDRY_HOME / "EMERGENCY_STOP")
        visible = any(m.get("mission_id") == mission_id for m in snapshot.get("missions") or [])
        server = CommandCenterServer(
            database, stop_path=FOUNDRY_HOME / "EMERGENCY_STOP",
            host="127.0.0.1", port=0)
        server.start()
        import urllib.request
        base = f"http://{server.address[0]}:{server.address[1]}"
        with urllib.request.urlopen(f"{base}/api/command-center", timeout=10) as response:
            api = json.loads(response.read().decode("utf-8"))
        server.close()
        api_visible = any(
            m.get("mission_id") == mission_id for m in (api.get("missions") or []))
        report["gates"] = {
            "M9": {
                "goal_directive_id": goal.directive_id,
                "mission_id": mission_id,
                "state": state,
                "production_paths": PRODUCTION_EXECUTOR_COUNT,
                "stop_probe_reports": frozen,
                "stop_blocked_progress": stop_blocked_progress,
                "reports": reports,
                "artifacts": artifacts,
                "git_hashes": hashes,
                "evidence_bundles": evidence_n,
                "verifier_passed": pass_n,
                "verifier_failed": fail_n,
                "duplicate_reports": duplicate,
                "duplicate_artifacts_unchanged": after_dup == artifacts,
                "command_center_visible": visible and api_visible,
                "false_success_rejected": fail_n >= 1 and artifacts["step_b.md"] == "STEP-B\n",
                "verdict": "PASS" if (
                    state == "COMPLETE"
                    and artifacts["step_c.md"] == "STEP-C\n"
                    and hashes["step_c.md"]
                    and evidence_n >= 1
                    and pass_n >= 1
                    and fail_n >= 1
                    and after_dup == artifacts
                    and visible and api_visible
                    and stop_blocked_progress
                ) else "FAIL",
            }
        }
    finally:
        YAML_PATH.write_text(yaml_original, encoding="utf-8")
        EmergencyStop(FOUNDRY_HOME / "EMERGENCY_STOP").clear()
    out = EVIDENCE / "M9_LIVE.json"
    out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"wrote": str(out), "verdict": report.get("gates", {}).get("M9", {}).get("verdict")}, indent=2))
    return 0 if report.get("gates", {}).get("M9", {}).get("verdict") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
