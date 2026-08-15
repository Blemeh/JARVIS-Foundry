"""R7 privacy boundary: metadata-only staging, no private categories/bodies."""

from __future__ import annotations

import sys
import time
from typing import Any

from common import (
    EVIDENCE_DIR,
    EXCLUDED_VAULT_CATEGORIES,
    STAGED_ROOT,
    read_json,
    write_json,
)


def main(argv: list[str] | None = None) -> int:
    started = time.monotonic()
    manifest_path = STAGED_ROOT / "staging_manifest.json"
    manifest = read_json(manifest_path)
    vault = manifest["vault"]
    projects = manifest["projects"]
    excluded_present = False
    for entry in projects.values():
        path = entry["path"]
        for category in EXCLUDED_VAULT_CATEGORIES:
            if category.lower() in str(path).lower():
                excluded_present = True

    forbidden_bodies: list[str] = []
    for item in vault.get("files", []):
        if any(part in EXCLUDED_VAULT_CATEGORIES for part in item["rel_path"].split("/")):
            forbidden_bodies.append(item["rel_path"])
        if item["body_staged"]:
            name = item["rel_path"].rsplit("/", 1)[-1]
            if not (
                name == "README.md" or name.endswith(("Map.md", "North Star.md", "Welcome.md"))
            ):
                forbidden_bodies.append(item["rel_path"])

    staged_body_bytes = 0
    for path in (STAGED_ROOT / "vault").rglob("*"):
        if path.is_file():
            staged_body_bytes += path.stat().st_size

    evidence_text = ""
    evidence_files = sorted(EVIDENCE_DIR.glob("*.json"))
    for path in evidence_files:
        if path.name == "R7_PRIVACY.json":
            continue
        evidence_text += path.read_text(encoding="utf-8", errors="replace")
    full_body_leaks: list[str] = []
    for item in vault.get("files", []):
        if not item["body_staged"]:
            continue
        staged = STAGED_ROOT / "vault" / item["rel_path"]
        body = staged.read_bytes()
        if body and body in evidence_text.encode("utf-8"):
            full_body_leaks.append(item["rel_path"])

    private_category_mentions = [
        part for part in EXCLUDED_VAULT_CATEGORIES if part.lower() in evidence_text.lower()
    ]
    passed = bool(
        vault.get("available")
        and not excluded_present
        and not forbidden_bodies
        and not full_body_leaks
        and not private_category_mentions
    )
    payload: dict[str, Any] = {
        "schema": "jarvis-r7-privacy-v1",
        "exit_code": 0 if passed else 1,
        "wall_seconds": 0.0,
        "passed": passed,
        "excluded_categories": list(EXCLUDED_VAULT_CATEGORIES),
        "vault": {
            "available": vault.get("available"),
            "file_count": vault.get("file_count"),
            "body_staged_count": vault.get("body_staged_count"),
            "staged_body_bytes": staged_body_bytes,
            "excluded_present": excluded_present,
            "forbidden_bodies": forbidden_bodies,
        },
        "evidence": {
            "file_count": len(evidence_files),
            "full_body_leaks": full_body_leaks,
            "private_category_mentions": private_category_mentions,
        },
        "projects_staged": sorted(projects),
    }
    output = EVIDENCE_DIR / "R7_PRIVACY.json"
    write_json(output, payload)
    print(
        f"privacy: passed={passed} vault_files={vault.get('file_count')} "
        f"bodies={vault.get('body_staged_count')} leaks={len(full_body_leaks)} "
        f"wall_seconds={round(time.monotonic() - started, 3)}"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
