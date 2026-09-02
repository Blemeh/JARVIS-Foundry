"""Hard-exit child used by the V11 A4 disposable durability harness."""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.executive.autonomy import create_objective, run_autonomy_cycle
from jarvis_foundry.executive.continuous import schedule_wait_until
from jarvis_foundry.owner_controls import OwnerControlService, sign_command
from jarvis_foundry.paths import FoundryPaths
from jarvis_foundry.runtime.config import WorkerRuntimeConfig
from jarvis_foundry.runtime.worker_loop import run_cycle


SECRET = b"v11-a4-owner-secret-32-bytes!!"
HARD_EXIT = 86


def _service(database: FoundryDatabase, root: Path) -> OwnerControlService:
    return OwnerControlService(
        database,
        secret=SECRET,
        owner_id="owner",
        stop_path=root / "EMERGENCY_STOP",
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("materialize", "cycle", "wake", "stop", "resume"))
    parser.add_argument("root", type=Path)
    parser.add_argument("--fault", default="")
    args = parser.parse_args()
    root = args.root.resolve()
    database = FoundryDatabase(root / "foundry.db")

    if args.mode == "materialize":
        run_autonomy_cycle(
            database,
            cycle_id="a4-kill-after-mission-created",
            resource_id="resource-a4",
        )
        os._exit(HARD_EXIT)

    if args.mode == "wake":
        create_objective(
            database, "Wait durably", objective_id="wait-a4",
            resource_id="resource-a4",
        )
        due = datetime.now(timezone.utc) - timedelta(seconds=1)
        schedule_wait_until(
            database, "wait-a4",
            not_before=due.isoformat().replace("+00:00", "Z"),
        )
        os._exit(HARD_EXIT)

    if args.mode == "stop":
        text = "durable A4 owner stop"
        nonce = "a4-stop"
        _service(database, root).stop_now(
            reason=text,
            nonce=nonce,
            signature=sign_command(SECRET, "STOP_EVERYTHING", "GLOBAL", text, nonce),
        )
        os._exit(HARD_EXIT)

    if args.mode == "resume":
        text = "resume durable A4 work"
        nonce = "a4-resume"
        _service(database, root).resume(
            nonce=nonce,
            signature=sign_command(SECRET, "RESUME", "GLOBAL", text, nonce),
            exact_text=text,
        )
        os._exit(HARD_EXIT)

    config = WorkerRuntimeConfig(
        enabled=True,
        status="ACTIVE",
        resource_id="resource-a4",
        lab_scripted_hermes=True,
        executive_autonomy_enabled=True,
        runtime_state_root=str(root / "runtime"),
    )

    def hard_fault(point: str) -> None:
        if point == args.fault:
            os._exit(HARD_EXIT)

    report = {}
    for attempt in range(1, 7):
        report = run_cycle(
            database,
            FoundryPaths(root),
            config,
            loop_id=(
                "a4-child-" + args.fault.replace(" ", "-").replace(":", "-")
                + f"-{attempt}"
            ),
            instance_id="a4-child",
            fault_hook=hard_fault,
        )
    print(json.dumps(report, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
