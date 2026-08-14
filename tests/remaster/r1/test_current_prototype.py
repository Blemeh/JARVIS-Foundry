from __future__ import annotations

import json
import subprocess
from pathlib import Path

from acceptance import JOURNEY_IDS


EXPECTED_COMMIT = "bf8f1493b7fd5cd80d17a631a1fb23e82c2260f9"
INSTALLED_ENTRYPOINT = Path(
    r"C:\Users\b\Documents\ChatGPT\Autonomous AI powered by Hoowen\JARVIS-Foundry\.venv\Scripts\jarvis.exe"
)


def _run_probe(
    repo_root: Path,
    script_root: Path,
    python_executable: Path,
    run_root: Path,
    output: Path,
) -> dict:
    completed = subprocess.run(
        [
            str(python_executable),
            "-B",
            str(script_root / "current_prototype_probe.py"),
            "--repo",
            str(repo_root),
            "--expected-commit",
            EXPECTED_COMMIT,
            "--python",
            str(python_executable),
            "--installed-entrypoint",
            str(INSTALLED_ENTRYPOINT),
            "--run-root",
            str(run_root),
            "--output",
            str(output),
        ],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
        timeout=15,
    )
    assert completed.returncode == 1, completed.stderr
    return json.loads(output.read_text(encoding="utf-8"))


def test_exact_current_prototype_is_rejected_without_database_write(
    repo_root: Path,
    script_root: Path,
    python_executable: Path,
    tmp_path: Path,
) -> None:
    result = _run_probe(repo_root, script_root, python_executable, tmp_path / "probe", tmp_path / "current.json")
    assert result["status"] == "FAIL"
    assert result["frozen_source"]["actual_commit"] == EXPECTED_COMMIT
    assert result["frozen_source"]["identity_match"] is True
    assert result["product_identity"]["entrypoint"] == "jarvis_foundry.cli.main:main"
    assert result["product_identity"]["installed_entrypoint"]["observed"] is True
    assert result["product_identity"]["installed_entrypoint"]["invoked"] is False
    assert result["product_identity"]["installed_entrypoint"]["size"] > 0
    assert result["product_identity"]["protocol_endpoint_present"] is False
    assert result["mutation_guard"]["repository_status_unchanged"] is True
    assert result["mutation_guard"]["database_files_created"] == 0
    assert [item["journey_id"] for item in result["journeys"]] == list(JOURNEY_IDS)
    assert all(item["status"] == "FAIL" for item in result["journeys"])
    assert all(item["evidence"] for item in result["journeys"])
    assert not list(tmp_path.rglob("*.db"))
    assert not list(repo_root.glob("foundry.db"))
