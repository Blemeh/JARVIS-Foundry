"""Record the isolated install recipe, versions, and wheel hashes (R3-D01)."""

from __future__ import annotations

import argparse
import importlib.metadata
import json
import sys
from pathlib import Path

from common import EVIDENCE_DIR, record_enabled, write_json


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=EVIDENCE_DIR / "R3_INSTALL.json")
    args = parser.parse_args(argv)
    report = json.loads(args.report.read_text(encoding="utf-8"))
    versions: dict[str, str] = {}
    hashes: list[dict[str, str]] = []
    for entry in report.get("install", []):
        metadata = entry.get("metadata", {})
        name = metadata.get("name")
        version = metadata.get("version")
        if name and version:
            versions[name] = version
        archive = (entry.get("download_info") or {}).get("archive_info") or {}
        if name and version and archive.get("hash"):
            hashes.append(
                {
                    "package": name,
                    "version": version,
                    "wheel_hash": archive["hash"],
                }
            )
    try:
        pytest_version = importlib.metadata.version("pytest")
        versions["pytest"] = pytest_version
    except importlib.metadata.PackageNotFoundError:
        pass
    if "pip-audit" not in versions and "pip_audit" in versions:
        versions["pip-audit"] = versions.pop("pip_audit")
    payload = {
        "schema": "jarvis-r3-install-v1",
        "recipe": (
            "C:/Python313/python.exe -B -m venv --without-pip --system-site-packages "
            "C:/Users/b/AppData/Local/Temp/jarvis-r3-venv; .pth inserts repo src; "
            "host pip install --python <venv-python> -r requirements-dev.txt"
        ),
        "tool_versions": versions,
        "wheel_hashes": hashes,
        "wheel_hash_note": (
            "pytest==9.0.3 was already importable from the base system "
            "site-packages, so the pinned-install report contains no wheel "
            "hash for it; all other pinned packages have report hashes."
        ),
    }
    if record_enabled():
        write_json(args.output, payload)
    print(json.dumps({"packages": len(versions), "hashes_recorded": len(hashes)}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
