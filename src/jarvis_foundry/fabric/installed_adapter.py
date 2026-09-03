"""Execution of a locally installed V12 capability adapter.

The trial pipeline installs a tiny, hash-bound wrapper and a selected
solution module in a disposable workspace.  This module is the standard
Fabric-side invocation seam used both during the trial and after the
capability is registered.  It deliberately has no catalog writes or effect
journal of its own; mutating actions still cross the existing Fabric/effects
path in :mod:`jarvis_foundry.fabric.loop`.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping

from ..control.redaction import contains_secret_like, redact_text
from ..platform.windows_subprocess import headless_run_kwargs, sanitized_env

ADAPTER_PROTOCOL = "jarvis.capability.adapter.v1"
MAX_OUTPUT_BYTES = 128 * 1024


class AdapterInvocationError(ValueError):
    """The installed adapter is malformed, unsafe, or failed closed."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _manifest(path: Path) -> tuple[dict[str, Any], Path]:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise AdapterInvocationError(f"adapter manifest is missing: {resolved}")
    try:
        raw = json.loads(resolved.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise AdapterInvocationError("adapter manifest is not valid JSON") from error
    if not isinstance(raw, dict) or raw.get("protocol") != ADAPTER_PROTOCOL:
        raise AdapterInvocationError("adapter manifest protocol is unsupported")
    wrapper_rel = raw.get("wrapper_path")
    solution_rel = raw.get("solution_path")
    if not isinstance(wrapper_rel, str) or not isinstance(solution_rel, str):
        raise AdapterInvocationError("adapter manifest paths are required")
    wrapper = (resolved.parent / wrapper_rel).resolve()
    solution = (resolved.parent / solution_rel).resolve()
    try:
        wrapper.relative_to(resolved.parent.resolve())
        solution.relative_to(resolved.parent.resolve())
    except ValueError as error:
        raise AdapterInvocationError("adapter manifest path escapes its workspace") from error
    if not wrapper.is_file() or not solution.is_file():
        raise AdapterInvocationError("adapter manifest references missing adapter files")
    if raw.get("wrapper_sha256") != _sha256(wrapper):
        raise AdapterInvocationError("adapter wrapper hash does not match manifest")
    if raw.get("solution_sha256") != _sha256(solution):
        raise AdapterInvocationError("installed solution hash does not match manifest")
    return raw, resolved


def _safe_environment() -> dict[str, str]:
    env = sanitized_env()
    # A trial adapter must not inherit provider credentials or a caller's
    # source injection.  The wrapper imports the hash-bound solution by path.
    for key in tuple(env):
        upper = key.upper()
        if upper.endswith(("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD")):
            env.pop(key, None)
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONPATH"] = ""
    env["JARVIS_ADAPTER_PROTOCOL"] = ADAPTER_PROTOCOL
    return env


def _within(path: Path, root: Path) -> Path:
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as error:
        raise AdapterInvocationError("adapter input escapes the allowed root") from error
    return resolved


def _normalize_payload(
    payload: Mapping[str, Any],
    *,
    manifest: Mapping[str, Any],
    allowed_root: Path,
) -> dict[str, Any]:
    normalized = json.loads(json.dumps(dict(payload)))
    path_fields = manifest.get("path_fields") or []
    if not isinstance(path_fields, list) or any(not isinstance(item, str) for item in path_fields):
        raise AdapterInvocationError("adapter manifest path_fields is malformed")
    for field in path_fields:
        value = normalized.get(field)
        if value is None:
            continue
        if not isinstance(value, str) or not value.strip():
            raise AdapterInvocationError(f"adapter path field {field!r} must be a string")
        candidate = Path(value)
        normalized[field] = str(_within(candidate if candidate.is_absolute() else allowed_root / candidate, allowed_root))
    normalized["_allowed_root"] = str(allowed_root.resolve())
    return normalized


def _invoke(
    manifest_path: Path,
    *,
    command: str,
    payload: Mapping[str, Any] | None,
    allowed_root: Path,
    timeout_s: float,
) -> dict[str, Any]:
    manifest, resolved_manifest = _manifest(manifest_path)
    root = allowed_root.expanduser().resolve()
    if not root.is_dir():
        raise AdapterInvocationError(f"allowed adapter root is missing: {root}")
    normalized = _normalize_payload(payload or {}, manifest=manifest, allowed_root=root)
    wrapper = (resolved_manifest.parent / str(manifest["wrapper_path"])).resolve()
    command_line = [
        sys.executable,
        "-I",
        str(wrapper),
        command,
        "--manifest",
        str(resolved_manifest),
    ]
    encoded = json.dumps(normalized, sort_keys=True, separators=(",", ":"))
    try:
        completed = subprocess.run(
            command_line,
            cwd=str(root),
            input=(encoded if command == "execute" else ""),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
            env=_safe_environment(),
            **headless_run_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return {
            "claimed": "UNKNOWN",
            "error_code": "adapter_timeout",
            "performed": False,
        }
    except OSError as error:
        return {
            "claimed": "FAILED",
            "error_code": "adapter_process_error",
            "detail": redact_text(str(error))[:300],
            "performed": False,
        }
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    if len(stdout.encode("utf-8")) > MAX_OUTPUT_BYTES:
        return {
            "claimed": "FAILED",
            "error_code": "adapter_output_too_large",
            "performed": False,
        }
    if completed.returncode != 0:
        return {
            "claimed": "FAILED",
            "error_code": "adapter_exit_nonzero",
            "exit_code": completed.returncode,
            "detail": redact_text(stderr[-500:]),
            "performed": False,
        }
    try:
        result = json.loads(stdout)
    except ValueError:
        return {
            "claimed": "FAILED",
            "error_code": "adapter_output_not_json",
            "detail": redact_text(stdout[-300:]),
            "performed": False,
        }
    if not isinstance(result, dict):
        return {
            "claimed": "FAILED",
            "error_code": "adapter_output_not_object",
            "performed": False,
        }
    encoded_result = json.dumps(result, sort_keys=True, separators=(",", ":"))
    if contains_secret_like(encoded_result):
        raise AdapterInvocationError("adapter result contains secret-like material")
    result.setdefault("claimed", "COMPLETED" if command == "execute" else "COMPLETED")
    result.setdefault("performed", command == "execute")
    result["adapter_protocol"] = ADAPTER_PROTOCOL
    result["adapter_manifest_sha256"] = _sha256(resolved_manifest)
    return result


def probe_installed_adapter(
    manifest_path: str | Path,
    *,
    allowed_root: str | Path,
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    """Run the adapter's readiness probe in a clean subprocess."""

    return _invoke(
        Path(manifest_path), command="probe", payload=None,
        allowed_root=Path(allowed_root), timeout_s=timeout_s,
    )


def run_installed_adapter(
    manifest_path: str | Path,
    payload: Mapping[str, Any],
    *,
    allowed_root: str | Path,
    timeout_s: float = 120.0,
) -> dict[str, Any]:
    """Execute one bounded adapter call with path containment and hashes."""

    return _invoke(
        Path(manifest_path), command="execute", payload=payload,
        allowed_root=Path(allowed_root), timeout_s=timeout_s,
    )


__all__ = [
    "ADAPTER_PROTOCOL",
    "AdapterInvocationError",
    "probe_installed_adapter",
    "run_installed_adapter",
]
