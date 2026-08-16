"""R9-G05: fresh verifier subprocess reproduction, no builder self-certification."""

from __future__ import annotations

import sys
import time

from common import (
    EVIDENCE_DIR,
    PYTHON,
    REPO_ROOT,
    TEMP_BASE,
    read_json,
    sha256_file,
    write_json,
)

VERIFIER_ID = "r9-fresh-verifier-subprocess"
WORK_PACKAGE_SHA256 = "F6582580BDD5ED27689957A4DF832A81D902CE82AD6AB8FE9364960D8AEFFC5C"


def main() -> int:
    started = time.monotonic()
    import subprocess

    mirror_root = TEMP_BASE / "gates" / "g05" / "mirror"
    if mirror_root.exists():
        import shutil

        for path in mirror_root.rglob("*"):
            if path.is_file():
                path.chmod(0o666)
        shutil.rmtree(mirror_root)
    mirror_root.mkdir(parents=True, exist_ok=True)
    completed = subprocess.run(
        [
            PYTHON,
            str(REPO_ROOT / "scripts" / "remaster" / "r9" / "verifier.py"),
            "--evidence-dir",
            str(EVIDENCE_DIR),
            "--mirror-root",
            str(mirror_root),
            "--worktree",
            str(REPO_ROOT),
            "--python",
            PYTHON,
            "--expected-work-package-sha",
            WORK_PACKAGE_SHA256,
            "--verifier-id",
            VERIFIER_ID,
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=3600,
        check=False,
    )
    verdict_path = EVIDENCE_DIR / "R9_VERIFIER_REPRODUCTION.json"
    verdict = read_json(verdict_path) if verdict_path.is_file() else {}
    no_self_certification = bool(
        verdict.get("verifier_id") == VERIFIER_ID
        and verdict.get("verifier_id") != "r9_builder_fresh"
    )
    passed = bool(
        completed.returncode == 0 and verdict.get("passed") is True and no_self_certification
    )
    payload = {
        "schema": "jarvis-r9-g05-verification-v1",
        "exit_code": 0 if passed else 1,
        "wall_seconds": round(time.monotonic() - started, 3),
        "passed": passed,
        "verifier_subprocess": {
            "exit_code": completed.returncode,
            "stdout": completed.stdout[-2000:],
            "stderr": completed.stderr[-2000:],
        },
        "verdict": verdict,
        "verdict_path": str(verdict_path),
        "verdict_sha256": sha256_file(verdict_path) if verdict_path.is_file() else None,
        "no_builder_self_certification": no_self_certification,
        "final_independent_adjudication": "PENDING; a fresh independent verifier must review the frozen package",
    }
    write_json(EVIDENCE_DIR / "R9_G05_VERIFICATION.json", payload)
    print(f"g05 verification: passed={passed} wall_seconds={payload['wall_seconds']}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
