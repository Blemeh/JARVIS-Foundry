"""Record the canonical convergence boundary without mutating either repo.

The remaster is evidence and source material only. This script inventories its
current dirty files, binds them to the certified remaster commit, and records
which material is historical R6, convergence evidence, or a semantic delta
that must be ported deliberately into a clean current-main worktree.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


R6_PREFIXES = (
    "docs/remaster/evidence/r6/",
    "docs/remaster/work_packages/R6_WORK_PACKAGE.json",
    "scripts/remaster/r6/",
    "src/jarvis_foundry/kernel/memory/",
    "tests/remaster/r6/",
)
CONVERGENCE_PREFIXES = ("docs/convergence/", "tests/convergence/")
PORTABLE_SOURCE = {
    "src/jarvis_foundry/control/broker.py",
    "src/jarvis_foundry/evolution/builder.py",
    "src/jarvis_foundry/evolution/lab.py",
    "src/jarvis_foundry/evolution/registry.py",
    "src/jarvis_foundry/evolution/shadow.py",
    "src/jarvis_foundry/evolution/verify.py",
    "tests/foundry/test_evolution_builder.py",
    "tests/foundry/test_evolution_lab.py",
    "tests/foundry/test_evolution_promote.py",
    "tests/foundry/test_evolution_registry.py",
    "tests/foundry/test_evolution_shadow.py",
}


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def git_status(repo: Path) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain=v1"],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.rstrip("\r\n")


def sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def classify(path: str, status: str) -> tuple[str, str]:
    normalized = path.replace("\\", "/")
    if any(normalized == prefix or normalized.startswith(prefix)
           for prefix in R6_PREFIXES):
        return "HISTORICAL_R6_DEFERRED", "preserve untouched; owner skipped R6"
    if any(normalized.startswith(prefix) for prefix in CONVERGENCE_PREFIXES):
        return "CONVERGENCE_EVIDENCE", "adapt or verify; never treat as implementation proof"
    if normalized in PORTABLE_SOURCE:
        return "POST_CERTIFICATION_SEMANTIC_DELTA", "port only after current-main regression and fresh verification"
    if status.startswith("M") or status.startswith("A"):
        return "POST_CERTIFICATION_TEST_OR_SOURCE_DELTA", "inspect against newer canonical architecture"
    return "UNCLASSIFIED", "requires explicit review before promotion"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--remaster", type=Path, required=True)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()

    remaster = args.remaster.resolve()
    candidate = args.candidate.resolve()
    candidate_head = git(candidate, "rev-parse", "HEAD")
    status_result = subprocess.run(
        ["git", "-C", str(remaster), "status", "--porcelain=v1",
         "--untracked-files=all"],
        check=True,
        capture_output=True,
        text=True,
    )
    status_lines = status_result.stdout.splitlines()
    certified_head = git(remaster, "rev-parse", "6be9185")
    certified_files = set(git(remaster, "ls-tree", "-r", "--name-only",
                              certified_head).splitlines())
    entries: list[dict[str, object]] = []
    for line in status_lines:
        if not line:
            continue
        status = line[:2]
        path = line[3:]
        category, disposition = classify(path, status)
        entries.append({
            "path": path.replace("\\", "/"),
            "status": status,
            "category": category,
            "disposition": disposition,
            "tracked_at_certified_head": path in certified_files,
            "sha256": sha256(remaster / path),
        })
    entries.sort(key=lambda item: str(item["path"]))
    payload = {
        "schema": "jarvis-canonical-convergence-orientation-v1",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "canonical": {
            "path": str(candidate),
            "branch": git(candidate, "branch", "--show-current"),
            "head": candidate_head,
            "status": git_status(candidate),
        },
        "remaster": {
            "path": str(remaster),
            "branch": git(remaster, "branch", "--show-current"),
            "head": git(remaster, "rev-parse", "HEAD"),
            "certified_head": certified_head,
            "merge_base_with_canonical": git(
                remaster, "merge-base", "HEAD", candidate_head),
            "dirty_file_count": len(entries),
            "entries": entries,
        },
        "owner_state": {
            "r6": "OWNER_REDIRECT_R6_DEFERRED",
            "eyes": "OUT_OF_SCOPE_READ_ONLY",
            "main_checkout": "preserve owner docs; candidate is isolated",
        },
        "acceptance": {
            "dirty_remaster_is_not_canonical_truth": True,
            "unclassified_entries_block_promotion": True,
            "semantic_deltas_require_fresh_verification": True,
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2) + "\n",
                           encoding="utf-8")
    print(json.dumps({
        "output": str(args.output),
        "dirty_file_count": len(entries),
        "categories": {
            category: sum(item["category"] == category for item in entries)
            for category in sorted({str(item["category"]) for item in entries})
        },
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
