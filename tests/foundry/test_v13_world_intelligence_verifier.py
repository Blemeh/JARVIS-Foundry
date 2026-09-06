from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _python(repo_root: Path) -> Path:
    project_python = repo_root / ".venv" / "Scripts" / "python.exe"
    return project_python if project_python.is_file() else Path(sys.executable)


def _branch(repo_root: Path) -> str:
    return subprocess.run(
        ["git", "-C", str(repo_root), "branch", "--show-current"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    ).stdout.strip()


def _run(
    repo_root: Path,
    *args: str,
    timeout: int = 240,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(_python(repo_root)), *args],
        cwd=repo_root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def test_separate_process_verifier_recomputes_v13_proof(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    proof_path = tmp_path / "proof.json"
    verifier_path = tmp_path / "verifier.json"
    proof = _run(
        repo_root,
        str(repo_root / "scripts" / "v13_world_intelligence_proof.py"),
        "--repo-root",
        str(repo_root),
        "--output",
        str(proof_path),
    )
    assert proof.returncode == 0, proof.stderr

    source_sha = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    ).stdout.strip()
    expected_branch = _branch(repo_root)
    assert expected_branch
    verifier = _run(
        repo_root,
        str(
            repo_root
            / "scripts"
            / "v13_world_intelligence_verifier.py"
        ),
        "--repo-root",
        str(repo_root),
        "--source-sha",
        source_sha,
        "--expected-branch",
        expected_branch,
        "--proof",
        str(proof_path),
        "--output",
        str(verifier_path),
    )
    assert verifier.returncode == 0, verifier.stderr
    report = json.loads(verifier_path.read_text(encoding="utf-8"))
    assert report["verdict"] == "PASS"
    assert report["assurance"] == {
        "kind": "DETERMINISTIC_SEPARATE_PROCESS",
        "fresh_source_checkout": True,
        "implementation_imported": False,
        "human_or_model_independence_claimed": False,
    }
    assert report["fresh_replay"]["status"] == "PASS"
    assert report["fresh_replay"]["semantic_match"] is True
    assert not report["failures"]


def test_verifier_rejects_tampered_decision_evidence(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[2]
    proof_path = tmp_path / "proof.json"
    report_path = tmp_path / "tampered-verifier.json"
    proof = _run(
        repo_root,
        str(repo_root / "scripts" / "v13_world_intelligence_proof.py"),
        "--repo-root",
        str(repo_root),
        "--output",
        str(proof_path),
    )
    assert proof.returncode == 0, proof.stderr
    payload = json.loads(proof_path.read_text(encoding="utf-8"))
    payload["proofs"]["A"]["final"]["recommendation"][
        "selected_option_id"
    ] = "transaction-begin-immediate"
    proof_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    expected_branch = _branch(repo_root)
    assert expected_branch

    verifier = _run(
        repo_root,
        str(
            repo_root
            / "scripts"
            / "v13_world_intelligence_verifier.py"
        ),
        "--repo-root",
        str(repo_root),
        "--expected-branch",
        expected_branch,
        "--proof",
        str(proof_path),
        "--skip-fresh-replay",
        "--output",
        str(report_path),
    )
    assert verifier.returncode == 1
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["verdict"] == "FAIL"
    assert any(
        row["check_id"] == "proof.A.changed-mind"
        for row in report["failures"]
    )
    assert all(
        row["check_id"] != "proof.branch"
        for row in report["failures"]
    )
