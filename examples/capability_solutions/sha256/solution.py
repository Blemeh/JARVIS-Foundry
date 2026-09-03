"""Selected bounded solution used by the V12 disposable trial.

The solution is deliberately tiny: it uses Python's standard ``hashlib``
implementation to hash one file under the caller-provided owned root.  The
generated V12 wrapper supplies the protocol envelope and path containment;
this module only supplies the capability behavior.
"""
from __future__ import annotations

import hashlib
from pathlib import Path


def readiness_probe() -> dict[str, object]:
    return {
        "ready": True,
        "protocol": "jarvis.capability.adapter.v1",
        "implementation": "python-stdlib-hashlib",
    }


def run(payload: dict[str, object]) -> dict[str, object]:
    raw_path = payload.get("path")
    allowed_root = payload.get("_allowed_root")
    if not isinstance(raw_path, str) or not isinstance(allowed_root, str):
        return {"claimed": "FAILED", "error_code": "path_contract_invalid"}
    path = Path(raw_path).resolve()
    root = Path(allowed_root).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return {"claimed": "FAILED", "error_code": "path_outside_owned_root"}
    if not path.is_file():
        return {"claimed": "FAILED", "error_code": "input_missing", "path": str(path)}
    data = path.read_bytes()
    return {
        "claimed": "COMPLETED",
        "path": str(path),
        "sha256": hashlib.sha256(data).hexdigest(),
        "bytes": len(data),
    }
