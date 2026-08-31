"""Prompt 4 entry: residual mission record + optional immutable promotion."""
from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.deployment.layout import default_live_foundry_home, install_root
from jarvis_foundry.deployment.release import (
    build_release,
    read_current,
    read_previous,
    record_generation,
    set_current,
)
from jarvis_foundry.events import Event
from jarvis_foundry.paths import FoundryPaths

RESIDUAL = "mission-prompt3-m6a-T193243Z"
TARGET_SHA = "c4a0fd1b706f8c0ecac89143bc01b7c37d34191a"


def record_residual(database: FoundryDatabase) -> dict:
    row = database.row("missions", "mission_id", RESIDUAL)
    if row is None:
        return {"ok": False, "reason": "mission missing"}
    database.append(Event.create(
        "prompt4.residual.recorded",
        {
            "mission_id": RESIDUAL,
            "durable_state": row["state"],
            "next_action": row["next_action"],
            "reason": (
                "overlapping Prompt-3 live probe / lease collision. "
                "History preserved. Terminal state remains BLOCKED. "
                "Not a kernel defect; not reopened as Prompt 1/2 regression."
            ),
            "overlap": "concurrent Prompt-3 M6A stamps T193243Z vs later successful stamps",
        },
        source_type="supervisor",
        source_id="prompt4-entry",
        idempotency_key=f"prompt4.residual.recorded:{RESIDUAL}",
    ))
    after = database.row("missions", "mission_id", RESIDUAL)
    return {
        "ok": after["state"] == "BLOCKED",
        "state": after["state"],
        "next_action": after["next_action"],
        "updated_at": after["updated_at"],
    }


def main() -> int:
    home = default_live_foundry_home()
    paths = FoundryPaths(home)
    database = FoundryDatabase(paths.database)
    database.initialize()
    residual = record_residual(database)
    before = {"current": read_current(), "previous": read_previous()}
    built = None
    promoted = None
    if before["current"] != TARGET_SHA:
        built = build_release(source_repo=REPO, source_sha=TARGET_SHA)
        promoted = set_current(
            TARGET_SHA,
            database=database,
            reason="Prompt 4 entry reconcile: immutable release activation",
        )
        generation = record_generation(foundry_home=home)
    else:
        generation = None
    report = {
        "install_root": str(install_root()),
        "before": before,
        "after": {"current": read_current(), "previous": read_previous()},
        "residual": residual,
        "built": built["manifest"] if built else None,
        "promoted": promoted,
        "generation": generation,
        "production_runtime_in_prompt3": True,
        "reason": (
            "git diff b598367..c4a0fd1 includes worker_loop, _stack, "
            "mission_bridge, discovery_candidates, command_center"
        ),
    }
    out = REPO / "docs" / "evidence" / "PROMPT4_RECOVERY"
    out.mkdir(parents=True, exist_ok=True)
    (out / "ENTRY_RECONCILE.json").write_text(
        json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))
    return 0 if residual["ok"] and read_current() == TARGET_SHA else 1


if __name__ == "__main__":
    raise SystemExit(main())
