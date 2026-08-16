"""R10-G02: canonical durable directives and STOP during UI failure."""

from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

from common import (
    EVIDENCE_DIR,
    EVIDENCE_UTC,
    PYTHON,
    REPO_ROOT,
    safe_clear,
    sha256_file,
    write_json,
)

from jarvis_foundry.command_center.directives import DirectiveService
from jarvis_foundry.command_center.ledger import DirectiveLedger
from jarvis_foundry.command_center.proving import ProvingLayout
from jarvis_foundry.command_center.seeding import seed_proving_stores
from jarvis_foundry.kernel.clock import FixedClock
from jarvis_foundry.kernel.portfolio.store import PortfolioStore

G02_TEMP = Path("C:/Users/b/AppData/Local/Temp/jarvis-r10-builder")
MAIN_ROOT = G02_TEMP / "g02-main"
UI_FAILURE_ROOT = G02_TEMP / "g02-ui-failure"
UI_PORT = 8793


def _service(layout: ProvingLayout) -> DirectiveService:
    return DirectiveService(
        PortfolioStore(layout.portfolio),
        DirectiveLedger(layout.directive_ledger),
        clock=FixedClock(EVIDENCE_UTC),
    )


def _read_ui_failure_state() -> dict[str, Any]:
    completed = subprocess.run(
        [
            PYTHON,
            str(Path(__file__).with_name("read_directive_state.py")),
            "--proving-root",
            str(UI_FAILURE_ROOT),
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(f"read_directive_state failed: {completed.stderr[:500]}")
    return json.loads(completed.stdout.strip())


def _stop_during_ui_failure() -> dict[str, Any]:
    if UI_FAILURE_ROOT.is_dir():
        safe_clear(UI_FAILURE_ROOT)
    UI_FAILURE_ROOT.mkdir(parents=True, exist_ok=True)
    layout = ProvingLayout(UI_FAILURE_ROOT)
    seed_proving_stores(layout, REPO_ROOT / "docs" / "remaster" / "evidence")
    process = subprocess.Popen(
        [
            PYTHON,
            "-m",
            "jarvis_foundry.command_center",
            "--proving-root",
            str(UI_FAILURE_ROOT),
            "--evidence-root",
            str(REPO_ROOT / "docs" / "remaster" / "evidence"),
            "--port",
            str(UI_PORT),
        ],
        cwd=str(REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    base = f"http://127.0.0.1:{UI_PORT}"
    try:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(f"{base}/api/health", timeout=2) as response:
                    health = json.loads(response.read())
                if health.get("status") == "OK":
                    break
            except (OSError, ValueError):
                time.sleep(1)
        else:
            raise RuntimeError("R10 UI server did not become healthy")
        body = {
            "command_type": "STOP",
            "target": "GLOBAL",
            "reason": "owner STOP through UI before UI failure",
            "nonce": "g02-ui-stop",
            "value": "",
        }
        request = urllib.request.Request(
            f"{base}/api/directives",
            data=json.dumps(body).encode("utf-8"),
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            applied = json.loads(response.read())
        before_kill = dict(applied.get("control", {}))
    finally:
        process.terminate()
        try:
            process.wait(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=10)

    after_kill = _read_ui_failure_state()
    return {
        "applied_status": applied.get("status"),
        "directive_id": applied.get("directive_id"),
        "control_before_kill": before_kill,
        "control_after_kill": after_kill,
        "ui_process_killed": True,
        "stop_survives_ui_failure": bool(after_kill.get("stop_active")),
        "server_pid": process.pid,
    }


def main(argv: list[str] | None = None) -> int:
    del argv
    started = time.monotonic()
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    if MAIN_ROOT.is_dir():
        safe_clear(MAIN_ROOT)
    layout = ProvingLayout(MAIN_ROOT)
    seed_proving_stores(layout, REPO_ROOT / "docs" / "remaster" / "evidence")
    service = _service(layout)
    before = service.state_dict()
    transitions: list[dict[str, Any]] = []
    cases = [
        ("STOP", "GLOBAL", "stop everything", "", "STOP"),
        ("VETO", "cand-11d1ef18f372", "veto candidate", "", "VETO"),
        ("PAUSE", "operators", "pause operators", "", "PAUSE"),
        ("RESUME", "GLOBAL", "resume all", "", "RESUME"),
        ("REPRIORITIZE", "cand-11d1ef18f372", "boost priority", "2", "REPRIORITIZE"),
        ("REDIRECT", "operators", "redirect target", "proving/operators", "REDIRECT"),
    ]
    for index, (command, target, reason, value, expected) in enumerate(cases):
        receipt = service.apply(
            command_type=command,
            target=target,
            reason=reason,
            nonce=f"g02-{index}",
            value=value,
        )
        transitions.append(
            {
                "command_type": command,
                "target": target,
                "reason": reason,
                "value": value,
                "receipt": {
                    "directive_id": receipt.directive_id,
                    "event_id": receipt.event_id,
                    "sequence": receipt.sequence,
                    "created": receipt.created,
                },
                "state_after": service.state_dict(),
                "command_matches": receipt.command_type == expected,
            }
        )
    after = service.state_dict()
    stop_applied = next(item for item in transitions if item["command_type"] == "STOP")
    service.apply(command_type="STOP", target="GLOBAL", reason="stop again", nonce="g02-stop-again")
    replay = service.apply(
        command_type="STOP", target="GLOBAL", reason="stop again", nonce="g02-stop-again"
    )
    idempotent = not replay.created
    service.apply(command_type="RESUME", target="GLOBAL", reason="resume", nonce="g02-resume-final")
    final = service.state_dict()

    ui_failure = _stop_during_ui_failure()
    checks = {
        "six_commands_recorded": len(transitions) == 6,
        "all_command_types_present": {item["command_type"] for item in transitions}
        == {"STOP", "VETO", "PAUSE", "RESUME", "REPRIORITIZE", "REDIRECT"},
        "control_state_changed": before != after,
        "stop_active_after_stop": bool(stop_applied["state_after"]["stop_active"]),
        "resume_clears_stop": final["stop_active"] is False,
        "replay_idempotent": idempotent,
        "ledger_count": service.ledger.count() == 8,
        "stop_survives_ui_failure": ui_failure["stop_survives_ui_failure"],
    }
    passed = all(checks.values())
    transitions_path = EVIDENCE_DIR / "R10_DIRECTIVE_TRANSITIONS.json"
    write_json(transitions_path, {"transitions": transitions, "ui_failure": ui_failure})
    payload = {
        "schema": "jarvis-r10-g02-directives-v1",
        "evidence_utc": EVIDENCE_UTC,
        "exit_code": 0 if passed else 1,
        "wall_seconds": round(time.monotonic() - started, 3),
        "passed": passed,
        "checks": checks,
        "before_control_hash": before.get("control_hash"),
        "after_control_hash": after.get("control_hash"),
        "final_control_hash": final.get("control_hash"),
        "transitions_output": "R10_DIRECTIVE_TRANSITIONS.json",
        "transitions_sha256": sha256_file(transitions_path),
        "ui_failure_summary": {
            "applied_status": ui_failure.get("applied_status"),
            "stop_survives_ui_failure": ui_failure["stop_survives_ui_failure"],
            "control_after_kill": ui_failure.get("control_after_kill"),
        },
        "evidence": [
            "R10_DIRECTIVE_TRANSITIONS.json",
            "R10_G02_DIRECTIVES.json",
        ],
    }
    write_json(EVIDENCE_DIR / "R10_G02_DIRECTIVES.json", payload)
    print(
        f"g02 directives: passed={passed} checks={sum(checks.values())}/{len(checks)} "
        f"ui_failure_stop={ui_failure['stop_survives_ui_failure']} "
        f"wall_seconds={payload['wall_seconds']}"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
