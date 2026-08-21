"""WP-24H-V2KB final gate script.

Runs, from the candidate worktree:
1. secret scan (canonical contains_secret_like) over EVERY changed file
   vs the base commit 7ac0b2373c8d4c74924fcea15c028c4f6e9cdc3c;
2. changed-file inventory + per-file line counts;
3. writes evidence/24h/v2-knowledge-brain/diff_review.json.

Read-only over the tree except for its own evidence output.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from jarvis_foundry.control.redaction import contains_secret_like  # noqa: E402

BASE = "7ac0b2373c8d4c74924fcea15c028c4f6e9cdc3c"


def git(*args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args],
        capture_output=True, text=True, check=True).stdout.strip()


def main() -> int:
    changed = git("diff", "--name-only", BASE).splitlines()
    staged = git("diff", "--cached", "--name-only", BASE).splitlines()
    untracked = [line.split(maxsplit=1)[1]
                 for line in git("status", "--porcelain").splitlines()
                 if line.startswith("??")]

    inventory = sorted(set(changed) | set(staged))
    scan_rows = []
    failures = []
    for rel in inventory:
        path = ROOT / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        dirty = contains_secret_like(text)
        scan_rows.append({"file": rel, "bytes": len(text.encode("utf-8")),
                          "secret_like": dirty})
        if dirty:
            failures.append(rel)

    # untracked files are scanned too (new modules, tests, evidence)
    for rel in untracked:
        path = ROOT / rel
        if path.is_file():
            dirty = contains_secret_like(
                path.read_text(encoding="utf-8", errors="replace"))
            scan_rows.append({"file": rel, "untracked": True,
                              "secret_like": dirty})
            if dirty:
                failures.append(rel)

    report = {
        "base_commit": BASE,
        "changed_files": inventory,
        "untracked_lane_files": untracked,
        "scan": scan_rows,
        "verdict": "PASS" if not failures else "FAIL",
        "failures": failures,
    }
    out_dir = ROOT / "evidence" / "24h" / "v2-knowledge-brain"
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "diff_review.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({
        "files_scanned": len(scan_rows),
        "verdict": report["verdict"],
        "failures": failures,
    }))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
