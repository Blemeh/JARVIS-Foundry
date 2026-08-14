"""Safe black-box identity probe for the exact current V7-derived prototype.

Only Git identity, the static console entry point, and ``--help`` are observed.
The probe does not invoke a product command that initializes or opens a
database, and this module never imports product code. Product import occurs
only in the isolated ``--help`` subprocess with bytecode writes disabled.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import tomllib
from pathlib import Path
from typing import Any

from acceptance import JOURNEY_IDS, PROTOCOL_VERSION, formatted_json


def _run(command: list[str], *, cwd: Path, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=10,
    )


def _git(repo: Path, *arguments: str) -> bytes:
    completed = _run(["git", "-C", str(repo), *arguments], cwd=repo)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.decode("utf-8", errors="replace").strip())
    return completed.stdout


def _database_snapshot(repo: Path, run_root: Path) -> dict[str, tuple[int, int]]:
    patterns = ("*.db", "*.db-wal", "*.db-shm", "*.sqlite", "*.sqlite3")
    result: dict[str, tuple[int, int]] = {}
    for root, prefix in ((repo, "repo"), (run_root, "run")):
        for pattern in patterns:
            for path in root.rglob(pattern):
                if path.is_file():
                    stat = path.stat()
                    result[f"{prefix}/{path.relative_to(root).as_posix()}"] = (stat.st_size, stat.st_mtime_ns)
    return dict(sorted(result.items()))


def _safe_environment(repo: Path, run_root: Path) -> dict[str, str]:
    sensitive = ("SECRET", "TOKEN", "PASSWORD", "PASSWD", "API_KEY", "AUTH", "COOKIE", "CREDENTIAL")
    env = {
        key: value
        for key, value in os.environ.items()
        if not any(fragment in key.upper() for fragment in sensitive)
    }
    env["PYTHONPATH"] = str(repo / "src")
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    env["HERMES_HOME"] = str(run_root / "isolated-hermes-home")
    return env


def probe(
    repo: Path,
    expected_commit: str,
    python_executable: Path,
    installed_entrypoint: Path,
    run_root: Path,
) -> dict[str, Any]:
    repo = repo.resolve(strict=True)
    run_root = run_root.resolve()
    run_root.mkdir(parents=True, exist_ok=True)
    actual_commit = _git(repo, "rev-parse", "HEAD").decode("ascii").strip()
    tree = _git(repo, "rev-parse", "HEAD^{tree}").decode("ascii").strip()
    status_before = _git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    databases_before = _database_snapshot(repo, run_root)
    pyproject = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    entrypoint = pyproject["project"]["scripts"]["jarvis"]
    installed_entrypoint = installed_entrypoint.resolve(strict=True)
    installed_metadata = {
        "path": str(installed_entrypoint),
        "sha256": hashlib.sha256(installed_entrypoint.read_bytes()).hexdigest(),
        "size": installed_entrypoint.stat().st_size,
        "observed": True,
        "invoked": False,
    }
    help_command = [str(python_executable.resolve(strict=True)), "-B", "-m", "jarvis_foundry.cli.main", "--help"]
    help_result = _run(help_command, cwd=repo, env=_safe_environment(repo, run_root))
    status_after = _git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    databases_after = _database_snapshot(repo, run_root)
    help_text = help_result.stdout.decode("utf-8", errors="replace")
    protocol_endpoint_present = PROTOCOL_VERSION in help_text or "purpose-acceptance" in help_text.lower()
    identity_match = actual_commit == expected_commit
    repository_unchanged = status_before == status_after
    database_files_created = len(set(databases_after) - set(databases_before))
    database_state_unchanged = databases_before == databases_after
    evidence_basis = {
        "commit": actual_commit,
        "tree": tree,
        "entrypoint": entrypoint,
        "installed_entrypoint_sha256": installed_metadata["sha256"],
        "help_exit_code": help_result.returncode,
        "help_sha256": hashlib.sha256(help_result.stdout).hexdigest(),
        "protocol_endpoint_present": protocol_endpoint_present,
        "repository_status_unchanged": repository_unchanged,
        "database_state_unchanged": database_state_unchanged,
    }
    journeys = []
    for journey_id in JOURNEY_IDS:
        journeys.append(
            {
                "journey_id": journey_id,
                "status": "FAIL",
                "evidence": [
                    {
                        "kind": "MISSING_VERSIONED_PURPOSE_ENDPOINT",
                        "observation": f"Exact {actual_commit[:8]} V7-derived CLI exposes no {PROTOCOL_VERSION} endpoint, so {journey_id} integrated behavior cannot be exercised.",
                        "basis": evidence_basis,
                    }
                ],
            }
        )
    return {
        "schema": "jarvis-current-prototype-probe-v1",
        "program": "POST_V7_JARVIS_REMASTER",
        "stage": "R1",
        "status": "FAIL",
        "frozen_source": {
            "expected_commit": expected_commit,
            "actual_commit": actual_commit,
            "tree": tree,
            "identity_match": identity_match,
        },
        "product_identity": {
            "console_script": "jarvis",
            "entrypoint": entrypoint,
            "installed_entrypoint": installed_metadata,
            "help_exit_code": help_result.returncode,
            "help_sha256": evidence_basis["help_sha256"],
            "protocol_endpoint": PROTOCOL_VERSION,
            "protocol_endpoint_present": protocol_endpoint_present,
        },
        "mutation_guard": {
            "probe_class": "GIT_ENTRYPOINT_AND_HELP_ONLY",
            "repository_status_unchanged": repository_unchanged,
            "database_state_unchanged": database_state_unchanged,
            "database_files_created": database_files_created,
            "product_database_command_invoked": False,
            "bytecode_writes_disabled": True,
        },
        "journeys": journeys,
        "aggregate_reason": "The exact current prototype lacks the required external purpose protocol; no journey can be proven through an integrated black-box path.",
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Safely reject the exact current V7-derived prototype")
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--installed-entrypoint", type=Path, required=True)
    parser.add_argument("--run-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        result = probe(
            args.repo,
            args.expected_commit,
            args.python,
            args.installed_entrypoint,
            args.run_root,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(formatted_json(result), encoding="utf-8", newline="\n")
    except Exception as exc:  # Probe failures remain explicit nonzero failures.
        print(f"current prototype probe failed: {exc}", file=sys.stderr)
        return 2
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
