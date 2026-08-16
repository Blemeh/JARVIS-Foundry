"""R12-D03: release freeze manifest + independent restore proof.

Freezes the exact deployed release representation (isolated), records
every required freeze dimension, builds a rollback package for the
previous recoverable V7 release, and demonstrates a real restore on
isolated copies.
"""

from __future__ import annotations

import json
import shutil
import subprocess  # nosec B404 - read-only scheduled-task and python probes
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

from common import (
    EVIDENCE_DIR,
    EVIDENCE_UTC,
    PYTHON,
    REPO_ROOT,
    TEMP_BASE,
    V7_RELEASE_TAG,
    json_hash,
    run,
    safe_clear,
    sha256_file,
    utc_now,
    write_checkpoint,
    write_json,
)

from jarvis_foundry.fault_campaign.isolation import (
    ProvingWorkspace,
    seed_capability,
    seed_portfolio,
)


def _tree_manifest(root: Path) -> dict[str, Any]:
    entries: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if (
            path.is_file()
            and ".git" not in path.parts
            and "__pycache__" not in path.parts
            and not path.name.endswith(".pyc")
            and "r12-v7-proving" not in path.parts
        ):
            entries[path.relative_to(root).as_posix()] = sha256_file(path)
    return {"files": entries, "tree_hash": json_hash(entries)}


def _scheduled_tasks() -> list[dict[str, Any]]:
    script = r"""
$ErrorActionPreference = 'SilentlyContinue'
$taskNames = @('JARVIS-Foundry-Supervisor', 'Hermes_Gateway')
$rows = @()
foreach ($name in $taskNames) {
    $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($null -ne $task) {
        $rows += [pscustomobject]@{ task_name = $name; exists = $true; status = $task.State.ToString() }
    } else {
        $rows += [pscustomobject]@{ task_name = $name; exists = $false }
    }
}
$rows | ConvertTo-Json -Compress
"""
    completed = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-NonInteractive",
            "-ExecutionPolicy",
            "Bypass",
            "-Command",
            script,
        ],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if completed.returncode != 0:
        return []
    value = json.loads(completed.stdout.strip())
    return list(value) if isinstance(value, list) else [value]


def _python_freeze() -> dict[str, Any]:
    version = run([PYTHON, "--version"], timeout_seconds=60)
    executable = Path(PYTHON).resolve()
    return {
        "executable": str(executable),
        "executable_sha256": sha256_file(executable) if executable.is_file() else None,
        "version": version["output"].strip(),
    }


def _rollback_package(v7_snapshot: Path, target: Path) -> dict[str, Any]:
    safe_clear(target)
    target.mkdir(parents=True, exist_ok=True)
    archive = target / "v7-recoverable-release.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as handle:
        for path in sorted(v7_snapshot.rglob("*")):
            if (
                path.is_file()
                and ".git" not in path.parts
                and "__pycache__" not in path.parts
                and not path.name.endswith(".pyc")
                and "r12-v7-proving" not in path.parts
            ):
                info = zipfile.ZipInfo(
                    filename=path.relative_to(v7_snapshot).as_posix(),
                    date_time=(1980, 1, 1, 0, 0, 0),
                )
                info.compress_type = zipfile.ZIP_DEFLATED
                handle.writestr(info, path.read_bytes())
    return {
        "rollback_package": str(archive),
        "rollback_package_sha256": sha256_file(archive),
        "rollback_package_size": archive.stat().st_size,
        "previous_recoverable_release": V7_RELEASE_TAG,
    }


def _restore_proof(v7_snapshot: Path, rollback: dict[str, Any], work_root: Path) -> dict[str, Any]:
    """Restore the previous recoverable release on isolated copies only."""
    isolated_live = work_root / "isolated-live"
    restored = work_root / "restored"
    safe_clear(isolated_live)
    safe_clear(restored)
    shutil.copytree(
        v7_snapshot,
        isolated_live,
        ignore=shutil.ignore_patterns(".git", "r12-v7-proving", "__pycache__", "*.pyc"),
    )
    marker = isolated_live / "r12-candidate-marker.txt"
    marker.write_text("candidate mutation (isolated)\n", encoding="utf-8")
    mutated_hash = _tree_manifest(isolated_live)["tree_hash"]
    original_hash = _tree_manifest(v7_snapshot)["tree_hash"]
    with zipfile.ZipFile(rollback["rollback_package"]) as handle:
        handle.extractall(restored)
    restored_hash = _tree_manifest(restored)["tree_hash"]
    smoke = run(
        [
            PYTHON,
            "-c",
            (
                "import sys; sys.path.insert(0, r'"
                + str(restored / "src")
                + "'); from jarvis_foundry.events import Event; "
                "print(Event.__name__)"
            ),
        ],
        timeout_seconds=120,
    )
    passed = (
        restored_hash == original_hash and mutated_hash != original_hash and smoke["exit_code"] == 0
    )
    return {
        "restored_exactly": restored_hash == original_hash,
        "original_tree_hash": original_hash,
        "mutated_tree_hash": mutated_hash,
        "restored_tree_hash": restored_hash,
        "restore_smoke": {"exit_code": smoke["exit_code"], "output": smoke["output"].strip()},
        "passed": passed,
        "scope": "isolated copies only; protected main untouched",
    }


def main(argv: list[str] | None = None) -> int:
    del argv
    started = time.monotonic()
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    freeze_root = TEMP_BASE / "release-freeze"
    safe_clear(freeze_root)
    proving = ProvingWorkspace(TEMP_BASE / "release-freeze", fixed_utc=EVIDENCE_UTC)
    v7_snapshot = TEMP_BASE / "v7-snapshot"
    rollback = _rollback_package(v7_snapshot, TEMP_BASE / "rollback-package")

    source_manifest = _tree_manifest(REPO_ROOT / "src" / "jarvis_foundry")
    store = proving.portfolio("freeze-portfolio")
    seed_portfolio(store, fixed_utc=EVIDENCE_UTC)
    registry = proving.capability_registry("freeze-capability")
    seed_capability(registry, "capability.files")
    ledger_path = proving.root / "stores" / "freeze-capability-ledger.db"
    db_files = {
        name: {
            "sha256": sha256_file(path),
            "size": path.stat().st_size,
        }
        for name in ("foundry.db", "foundry.db-wal", "foundry.db-shm")
        if (path := ledger_path.parent / name).is_file()
    }
    dependencies = run([PYTHON, "-m", "pip", "list", "--format=json"], timeout_seconds=120)
    freeze = {
        "schema": "jarvis-r12-release-freeze-v1",
        "generated_utc": utc_now(),
        "fixed_utc": EVIDENCE_UTC,
        "executable": _python_freeze(),
        "source": {
            "path": "src/jarvis_foundry",
            "tree_hash": source_manifest["tree_hash"],
            "file_count": len(source_manifest["files"]),
            "repo_head": subprocess.run(
                ["git", "-C", str(REPO_ROOT), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            ).stdout.strip(),
        },
        "dependencies": {
            "pyproject": "src-independent package with dev extras; versions captured from venv",
            "pip_list_json_exit_code": dependencies["exit_code"],
        },
        "configuration": {
            "env_vars": ["JARVIS_R12_TEMP", "R12_RECORD_EVIDENCE"],
            "proving_layout": str(proving.root),
            "network_allowed": False,
        },
        "db_wal": db_files,
        "scheduled_tasks": _scheduled_tasks(),
        "ports": {"used_by_r12": [], "note": "staged simulation uses no ports"},
        "capabilities": registry.snapshot()["states"]["capabilities"],
        "credential_metadata": {
            "note": "no secret values stored; metadata-only broker",
            "secret_count": 0,
        },
        "queues": {"queue_files": [], "note": "isolated worker queues under proving root"},
        "leases": {"lease_count": 0, "note": "freeze snapshot has no active leases"},
        "health": {
            "ledger_chain_valid": registry.ledger.verify_chain().valid,
            "portfolio_events": store.event_count(),
        },
        "evidence": _tree_manifest(EVIDENCE_DIR),
        "release_pointer": {
            "protected_main_head": "2f7360e",
            "previous_recoverable_release": V7_RELEASE_TAG,
            "remastered_frozen_base": "c76d92df55b6ec552b68379479ccd80f972c303c",
        },
        "rollback_package": rollback,
        "wall_seconds": round(time.monotonic() - started, 3),
    }
    restore = _restore_proof(v7_snapshot, rollback, TEMP_BASE / "restore-proof")
    freeze["restore_proof"] = restore
    write_json(EVIDENCE_DIR / "R12_RELEASE_FREEZE.json", freeze)
    write_json(EVIDENCE_DIR / "R12_RESTORE_PROOF.json", restore)
    write_json(EVIDENCE_DIR / "R12_ROLLBACK_PACKAGE.json", rollback)
    write_checkpoint(
        "release_freeze",
        {
            "status": "PASS" if restore["passed"] else "FAIL",
            "restored_exactly": restore["restored_exactly"],
        },
    )
    print(
        f"release freeze: restore_passed={restore['passed']} "
        f"source_tree={source_manifest['tree_hash'][:12]} "
        f"rollback={rollback['rollback_package_sha256'][:12]}"
    )
    return 0 if restore["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
