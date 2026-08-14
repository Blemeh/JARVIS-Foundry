"""Capture the Post-V7 R0 baseline without mutating the live Foundry.

The script uses read-only SQLite source connections and writes every artifact
under the isolated remaster worktree.  It intentionally records secret file
metadata and key names only; it never copies secret values.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


BASELINE_COMMIT = "93a3ddc0965dfaf3e4cb9dc0952467bd5250520a"
SECRET_KEY = re.compile(
    r"(?i)(?:api[_-]?key|token|secret|password|credential|authorization|cookie)"
)
SECRET_VALUE_KEY = re.compile(
    r"(?i)^(?:access_token|refresh_token|agent_key|api[_-]?key|client_secret|password|authorization)$"
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def metadata(path: Path, *, include_hash: bool = True) -> dict[str, Any]:
    stat = path.stat()
    result: dict[str, Any] = {
        "path": str(path),
        "size": stat.st_size,
        "mtime_utc": datetime.fromtimestamp(
            stat.st_mtime, tz=timezone.utc
        ).isoformat(),
    }
    if include_hash:
        result["sha256"] = sha256(path)
    return result


def run(command: list[str], *, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        command,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.stdout.strip()


def sqlite_uri(path: Path, *, immutable: bool = False) -> str:
    suffix = "?mode=ro&immutable=1" if immutable else "?mode=ro"
    return path.resolve().as_uri() + suffix


def table_counts(connection: sqlite3.Connection) -> dict[str, int]:
    names = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    wanted = (
        "events",
        "missions",
        "agent_runs",
        "monitors",
        "candidates",
        "research_queue",
        "knowledge_claims",
        "system_actions",
        "action_decisions",
        "cycles",
        "promotions",
        "canaries",
        "release_manifests",
        "briefing_outbox",
        "voice_interactions",
        "cost_measurements",
        "budgets",
        "acceptance_runs",
        "restore_points",
        "directives",
    )
    return {
        name: int(connection.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
        for name in wanted
        if name in names
    }


def backup_database(source: Path, destination: Path) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(f"refusing to overwrite restore point: {destination}")

    source_db = sqlite3.connect(sqlite_uri(source), uri=True)
    try:
        source_db.execute("PRAGMA query_only = ON")
        if source_db.execute("PRAGMA query_only").fetchone()[0] != 1:
            raise RuntimeError(f"query_only did not engage for {source}")
        source_quick = source_db.execute("PRAGMA quick_check").fetchone()[0]
        source_counts = table_counts(source_db)
        destination_db = sqlite3.connect(destination)
        try:
            source_db.backup(destination_db)
        finally:
            destination_db.close()
    finally:
        source_db.close()

    # A completed backup is a frozen standalone artifact. immutable=1 avoids
    # SQLite creating mutable -wal/-shm sidecars merely to validate it.
    check_db = sqlite3.connect(sqlite_uri(destination, immutable=True), uri=True)
    try:
        check_db.execute("PRAGMA query_only = ON")
        backup_quick = check_db.execute("PRAGMA quick_check").fetchone()[0]
        backup_integrity = check_db.execute("PRAGMA integrity_check").fetchone()[0]
        backup_counts = table_counts(check_db)
    finally:
        check_db.close()

    if source_quick != "ok" or backup_quick != "ok" or backup_integrity != "ok":
        raise RuntimeError(f"SQLite verification failed for {source}")
    if source_counts != backup_counts:
        raise RuntimeError(f"table-count mismatch for backup of {source}")

    result = metadata(destination)
    result.update(
        {
            "source": str(source),
            "source_quick_check": source_quick,
            "backup_quick_check": backup_quick,
            "backup_integrity_check": backup_integrity,
            "table_counts": backup_counts,
        }
    )
    return result


def env_key_names(path: Path) -> list[str]:
    names: set[str] = set()
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", line)
        if match:
            names.add(match.group(1))
    return sorted(names)


def sensitive_values(env_path: Path, auth_path: Path) -> set[str]:
    """Collect sensitive values in memory without returning names or values.

    The caller uses the returned set only for exact byte-presence checks and
    records counts/fingerprints, never plaintext.
    """
    values: set[str] = set()
    for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
        match = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)\s*$", line)
        if not match or not SECRET_KEY.search(match.group(1)):
            continue
        value = match.group(2).strip().strip("'\"")
        if len(value) >= 12:
            values.add(value)

    def visit(item: Any) -> None:
        if isinstance(item, dict):
            for key, value in item.items():
                if SECRET_VALUE_KEY.fullmatch(str(key)) and isinstance(value, str):
                    if len(value) >= 12:
                        values.add(value)
                else:
                    visit(value)
        elif isinstance(item, list):
            for value in item:
                visit(value)

    visit(json.loads(auth_path.read_text(encoding="utf-8")))
    return values


def secret_value_scan(values: set[str], paths: list[Path]) -> dict[str, Any]:
    matches: list[dict[str, str]] = []
    for path in sorted(set(paths)):
        data = path.read_bytes()
        for value in values:
            encoded_forms = (value.encode("utf-8"), value.encode("utf-16-le"))
            if any(encoded and encoded in data for encoded in encoded_forms):
                matches.append(
                    {
                        "artifact": str(path),
                        "value_sha256_prefix": hashlib.sha256(
                            value.encode("utf-8")
                        ).hexdigest()[:16],
                    }
                )
    return {
        "candidate_secret_values": len(values),
        "artifacts_scanned": len(set(paths)),
        "matches": matches,
        "match_count": len(matches),
        "plaintext_emitted": False,
    }


def safe_config_summary(path: Path) -> dict[str, Any]:
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    wanted: dict[str, str] = {}
    parents: list[tuple[int, str]] = []
    for raw in lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        key, separator, value = raw.strip().partition(":")
        if not separator:
            continue
        while parents and parents[-1][0] >= indent:
            parents.pop()
        full_key = ".".join([parent for _, parent in parents] + [key])
        if SECRET_KEY.search(full_key):
            continue
        if full_key in {
            "model.default",
            "model.provider",
            "model.base_url",
            "model.api_mode",
            "agent.reasoning_effort",
            "delegation.reasoning_effort",
            "tts.provider",
            "tts.use_gateway",
            "stt.enabled",
            "stt.provider",
            "stt.language",
            "proxy.enabled",
        }:
            wanted[full_key] = value.strip()
        if not value.strip():
            parents.append((indent, key))
    return wanted


def copy_with_metadata(source: Path, destination: Path) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    result = metadata(destination)
    result["source"] = str(source)
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--main-repo", type=Path, required=True)
    parser.add_argument("--live-home", type=Path, required=True)
    parser.add_argument("--hermes-home", type=Path, required=True)
    args = parser.parse_args()

    repo = args.repo.resolve()
    main_repo = args.main_repo.resolve()
    live_home = args.live_home.resolve()
    hermes_home = args.hermes_home.resolve()
    restore_root = repo / "var" / "remaster" / "r0" / "restore"
    tracked_root = repo / "docs" / "remaster" / "evidence" / "r0" / "raw"
    restore_root.mkdir(parents=True, exist_ok=True)
    tracked_root.mkdir(parents=True, exist_ok=True)

    if run(["git", "rev-parse", "HEAD"], cwd=repo) != BASELINE_COMMIT:
        raise RuntimeError("remaster worktree is not at the frozen V7 baseline")
    if run(["git", "rev-parse", "refs/heads/main"], cwd=repo) != BASELINE_COMMIT:
        raise RuntimeError("main ref is not at the frozen V7 baseline")

    manifest: dict[str, Any] = {
        "schema": "jarvis-remaster-r0-baseline-v1",
        "captured_at_utc": datetime.now(timezone.utc).isoformat(),
        "baseline_commit": BASELINE_COMMIT,
        "baseline_tree": run(["git", "rev-parse", f"{BASELINE_COMMIT}^{{tree}}"], cwd=repo),
        "branch": run(["git", "branch", "--show-current"], cwd=repo),
        "artifacts": {},
    }

    bundle = restore_root / f"jarvis-foundry-{BASELINE_COMMIT[:7]}.bundle"
    run(
        ["git", "bundle", "create", str(bundle), "refs/heads/main"],
        cwd=repo,
    )
    verify_text = run(["git", "bundle", "verify", str(bundle)], cwd=repo)
    bundle_heads = run(["git", "bundle", "list-heads", str(bundle)], cwd=repo)
    if BASELINE_COMMIT not in bundle_heads:
        raise RuntimeError("source bundle does not contain the frozen baseline")
    manifest["artifacts"]["source_bundle"] = {
        **metadata(bundle),
        "verify": verify_text or "exit_0",
        "heads": bundle_heads.splitlines(),
    }

    manifest["artifacts"]["live_database"] = backup_database(
        live_home / "foundry.db", restore_root / "live-foundry-r0.db"
    )
    manifest["artifacts"]["isolated_v7_database"] = backup_database(
        main_repo / "var" / "v7-runtime" / "v7.db",
        restore_root / "isolated-v7-r0.db",
    )

    profile = live_home / "foundry-profile.yaml"
    manifest["artifacts"]["foundry_profile"] = copy_with_metadata(
        profile, restore_root / profile.name
    )

    task_xml = run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            "Export-ScheduledTask -TaskName 'JARVIS-Foundry-Supervisor'",
        ]
    )
    task_path = restore_root / "JARVIS-Foundry-Supervisor.xml"
    # Export-ScheduledTask declares UTF-16. Preserve that encoding so the
    # restore artifact remains parseable by strict XML readers.
    task_path.write_text(task_xml + "\n", encoding="utf-16")
    manifest["artifacts"]["scheduled_task"] = metadata(task_path)

    python_exe = main_repo / ".venv" / "Scripts" / "python.exe"
    manifest["live_executable_metadata"] = metadata(python_exe)
    pth_files = sorted((main_repo / ".venv" / "Lib" / "site-packages").glob("*.pth"))
    manifest["editable_path_files"] = [
        {
            **metadata(path),
            "content": path.read_text(encoding="utf-8", errors="replace").strip(),
        }
        for path in pth_files
        if "jarvis" in path.name.lower()
    ]
    freeze = run([str(python_exe), "-m", "pip", "freeze"])
    freeze_path = restore_root / "live-venv-pip-freeze.txt"
    freeze_path.write_text(freeze + "\n", encoding="utf-8")
    manifest["artifacts"]["live_dependency_freeze"] = metadata(freeze_path)

    env_path = hermes_home / ".env"
    auth_path = hermes_home / "auth.json"
    config_path = hermes_home / "config.yaml"
    manifest["secret_metadata"] = {
        "env": {**metadata(env_path), "key_names": env_key_names(env_path)},
        "auth": metadata(auth_path),
        "values_copied": False,
    }
    manifest["provider_configuration"] = {
        "config_metadata": metadata(config_path),
        "selected_non_secret_values": safe_config_summary(config_path),
    }

    protected = [
        main_repo / "docs" / "handoffs" / "V5_SOL_HIGH_HARDENING_PROMPT.md",
        main_repo / "docs" / "handoffs" / "V5_SOL_HIGH_HARDENING_REPORT.md",
        main_repo / "docs" / "planning" / "POST_V7_CODEX_SOL_REMASTER_GOAL.md",
        main_repo / "docs" / "planning" / "V5_MASTER_PROMPT.md",
        main_repo / "docs" / "planning" / "V5_READ_FIRST.md",
        main_repo / "docs" / "planning" / "V7_MASTER_PROMPT.md",
    ]
    manifest["protected_owner_files"] = [metadata(path) for path in protected]

    temp_root = Path(tempfile.gettempdir())
    verdicts: list[dict[str, Any]] = []
    for source in sorted(temp_root.glob("v7s*-verdict*.json")):
        verdicts.append(
            copy_with_metadata(source, tracked_root / "v7-verdicts" / source.name)
        )
    manifest["artifacts"]["v7_verdicts"] = verdicts

    transcript_sources = [
        hermes_home
        / "cache"
        / "delegation"
        / "live"
        / "deleg_27d3c23e"
        / "task-0.log",
        hermes_home
        / "cache"
        / "delegation"
        / "subagent-summary-0-20260814_145047_691636.txt",
    ]
    manifest["artifacts"]["final_verifier_transcript"] = [
        copy_with_metadata(source, tracked_root / "v7-final-verifier" / source.name)
        for source in transcript_sources
    ]

    copied_artifacts = [path for path in restore_root.iterdir() if path.is_file()]
    copied_artifacts.extend(path for path in tracked_root.rglob("*") if path.is_file())
    scan = secret_value_scan(
        sensitive_values(env_path, auth_path), copied_artifacts
    )
    if scan["match_count"]:
        raise RuntimeError(
            "sensitive value found in R0 artifact; inspect fingerprints without "
            "printing plaintext"
        )
    manifest["secret_metadata"]["artifact_value_scan"] = scan
    manifest["restore_directory_inventory"] = [
        metadata(path) for path in sorted(restore_root.iterdir()) if path.is_file()
    ]

    manifest_path = repo / "docs" / "remaster" / "evidence" / "r0" / "R0_BASELINE_MANIFEST.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(json.dumps({"manifest": str(manifest_path), "sha256": sha256(manifest_path)}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
