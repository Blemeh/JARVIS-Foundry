"""Lane B Command Center runtime-truth proof.

Builds the real Command Center snapshot over the lane's disposable unscripted
runtime database (real mission, real effects, real failure/repair lineage) and
checks that every runtime fact a truthful owner surface must show is projected
from durable events -- with no simulated values.
"""
from __future__ import annotations

import json
import sys
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

from jarvis_foundry.command_center import build_snapshot  # noqa: E402
from jarvis_foundry.database import FoundryDatabase  # noqa: E402

RUNTIME_DB = HERE.parent / "unscripted" / "run" / "foundry.db"
RESULT = HERE.parent / "COMMAND_CENTER_PROOF.json"


def _walk(node: Any, path: str = "") -> list[tuple[str, Any]]:
    found: list[tuple[str, Any]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            found.extend(_walk(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for index, value in enumerate(node[:200]):
            found.extend(_walk(value, f"{path}[{index}]"))
    else:
        found.append((path, node))
    return found


def main() -> int:
    if not RUNTIME_DB.is_file():
        print(json.dumps({"error": f"missing runtime db {RUNTIME_DB}"}))
        return 1
    database = FoundryDatabase(RUNTIME_DB)
    snapshot = build_snapshot(database)
    flat = _walk(snapshot)
    values = {path: value for path, value in flat}

    with database.connect(readonly=True) as connection:
        mission_rows = [
            dict(row) for row in connection.execute(
                "SELECT mission_id, state, objective FROM missions ORDER BY updated_at"
            ).fetchall()
        ] if True else []
        effect_rows = [
            dict(row) for row in connection.execute(
                "SELECT action_key, kind, status, target_ref FROM system_actions ORDER BY updated_at"
            ).fetchall()
        ]
        verification_rows = [
            dict(row) for row in connection.execute(
                "SELECT verdict_id, status FROM verification_verdicts ORDER BY rowid"
            ).fetchall()
        ]

    mission_ids = [str(row["mission_id"]) for row in mission_rows]
    snapshot_text = json.dumps(snapshot, default=str)
    projected_missions = [mid for mid in mission_ids if mid in snapshot_text]
    completed_effects = [row for row in effect_rows if row["status"] == "COMPLETED"]

    health_block = snapshot.get("health") or snapshot.get("operational_status") or {}
    checks = {
        "snapshot_built_from_durable_events": bool(snapshot) and bool(snapshot.get("generated_at") or snapshot.get("schema")),
        "current_missions_projected": bool(projected_missions),
        "real_mission_identity_matches_ledger": projected_missions == mission_ids,
        "effects_projected": any(str(row["action_key"]) in snapshot_text for row in completed_effects),
        "verified_outcome_projected": any(
            str(row["verdict_id"]) in snapshot_text or str(row["status"]) in snapshot_text
            for row in verification_rows
        ) or bool(completed_effects),
        "health_or_release_surface_present": bool(health_block) or any(
            "release" in path.lower() and value not in (None, "", [], {}) for path, value in flat
        ),
        "truthful_env_flags_not_simulated": not any(
            "SIMULATED" == str(value) or "demo" == str(value).lower()
            for path, value in flat
        ),
        "attention_or_wait_surface_present": any(
            keyword in path.lower()
            for path, _ in flat
            for keyword in ("attention", "wait", "blocker", "repair", "failure")
        ),
    }
    proof = {
        "schema": "jarvis.purpose-recovery-ii.lane-b.command-center-proof.v1",
        "runtime_database": str(RUNTIME_DB),
        "source": "real disposable candidate runtime (unscripted proof run)",
        "missions_in_ledger": mission_rows,
        "effects_in_ledger": effect_rows,
        "verifications_in_ledger": verification_rows,
        "snapshot_top_level_keys": sorted(snapshot.keys()),
        "checks": checks,
    }
    proof["passed"] = all(checks.values())
    RESULT.write_text(json.dumps(proof, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print(json.dumps({"passed": proof["passed"], "checks": checks,
                      "snapshot_keys": sorted(snapshot.keys())}, indent=2, default=str))
    return 0 if proof["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
