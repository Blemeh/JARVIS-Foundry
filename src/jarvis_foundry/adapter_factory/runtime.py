"""Runtime for transparent, generated V12 thin adapters.

Generated adapters contain only a frozen descriptor and a tiny entry point.
This module is the reviewable semantic composer: it validates schemas, maps a
typed JARVIS request to a package/CLI/API/MCP/component call, enforces bounded
process execution, and normalizes the result into the existing V10 receipt.
It owns no catalog, effect journal, scheduler, worker pool, or trust state.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from ..control.redaction import contains_secret_like, redact_text
from ..effects import EffectClass
from ..events import canonical_json
from ..fabric.contracts import (
    AdapterRequest,
    PostconditionStatus,
    evaluate_postconditions,
    normalize_receipt,
)
from ..fabric.model import CapabilitySpec, ClaimOutcome, VerifyVerdict
from ..platform.windows_subprocess import headless_popen_kwargs, sanitized_env
from .model import (
    ADAPTER_PROTOCOL,
    AdapterFactoryError,
    CapabilityAdapterContract,
    InvocationMapping,
    ProbeKind,
    SelectedSolutionDescriptor,
    SolutionClass,
)


MAX_RESULT_BYTES = 128 * 1024
MAX_HTTP_BYTES = 2 * 1024 * 1024
DISABLED_FILE = "ADAPTER_DISABLED.json"
MANIFEST_FILE = "adapter-manifest.json"


class AdapterRuntimeError(AdapterFactoryError):
    """A built adapter cannot be safely probed or invoked."""


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_object(text: str, name: str) -> dict[str, Any]:
    try:
        value = json.loads(text)
    except ValueError as error:
        raise AdapterRuntimeError(f"{name} is not valid JSON") from error
    if not isinstance(value, dict):
        raise AdapterRuntimeError(f"{name} must be a JSON object")
    return value


def _contained(path: Path, root: Path, name: str) -> Path:
    resolved = path.expanduser().resolve()
    try:
        resolved.relative_to(root.expanduser().resolve())
    except ValueError as error:
        raise AdapterRuntimeError(f"{name} escapes the adapter artifact") from error
    return resolved


def load_verified_artifact(root_or_manifest: str | Path) -> tuple[Path, dict[str, Any], SelectedSolutionDescriptor]:
    """Load a hash-bound artifact without importing generated code."""

    supplied = Path(root_or_manifest).expanduser().resolve()
    manifest_path = supplied if supplied.is_file() else supplied / MANIFEST_FILE
    if not manifest_path.is_file():
        raise AdapterRuntimeError(f"adapter manifest is missing: {manifest_path}")
    root = manifest_path.parent.resolve()
    manifest = _json_object(manifest_path.read_text(encoding="utf-8"), "adapter manifest")
    if manifest.get("protocol") != ADAPTER_PROTOCOL:
        raise AdapterRuntimeError("adapter manifest protocol is unsupported")
    descriptor_rel = manifest.get("descriptor_path")
    if not isinstance(descriptor_rel, str):
        raise AdapterRuntimeError("adapter manifest descriptor_path is missing")
    descriptor_path = _contained(root / descriptor_rel, root, "descriptor path")
    if not descriptor_path.is_file() or manifest.get("descriptor_sha256") != _sha256(descriptor_path):
        raise AdapterRuntimeError("adapter descriptor is missing or hash-mismatched")
    files = manifest.get("files")
    if not isinstance(files, Mapping) or not files:
        raise AdapterRuntimeError("adapter manifest file inventory is missing")
    for relative, digest in files.items():
        if not isinstance(relative, str) or not isinstance(digest, str):
            raise AdapterRuntimeError("adapter manifest file inventory is malformed")
        candidate = _contained(root / relative, root, "artifact file")
        if not candidate.is_file() or _sha256(candidate) != digest:
            raise AdapterRuntimeError(f"adapter artifact hash mismatch: {relative}")
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() and path.name not in {MANIFEST_FILE, DISABLED_FILE}
    }
    if actual != set(files):
        unexpected = sorted(actual - set(files))
        missing = sorted(set(files) - actual)
        raise AdapterRuntimeError(
            f"adapter artifact inventory mismatch; unexpected={unexpected[:5]} missing={missing[:5]}"
        )
    descriptor = SelectedSolutionDescriptor.load(descriptor_path)
    if descriptor.descriptor_hash != manifest.get("descriptor_hash"):
        raise AdapterRuntimeError("descriptor identity does not match the artifact manifest")
    if descriptor.adapter_id != manifest.get("adapter_id"):
        raise AdapterRuntimeError("adapter identity does not match the artifact manifest")
    return root, manifest, descriptor


def _lookup(root: Any, path: str) -> Any:
    current = root
    if not path:
        return current
    for part in path.split("."):
        if isinstance(current, Mapping) and part in current:
            current = current[part]
        elif isinstance(current, Sequence) and not isinstance(current, (str, bytes)) and part.isdigit():
            index = int(part)
            if index >= len(current):
                raise AdapterRuntimeError(f"mapping reference index is missing: {path}")
            current = current[index]
        else:
            raise AdapterRuntimeError(f"mapping reference is missing: {path}")
    return current


_INLINE_REF = re.compile(r"\$\{(input|config|raw|request|artifact)\.([^}]+)\}")


def _resolve(value: Any, context: Mapping[str, Any]) -> Any:
    if isinstance(value, str):
        if value == "$artifact_root":
            return context["artifact_root"]
        exact = re.fullmatch(r"\$(input|config|raw|request|artifact)\.(.+)", value)
        if exact:
            return _lookup(context[exact.group(1)], exact.group(2))

        def replace(match: re.Match[str]) -> str:
            resolved = _lookup(context[match.group(1)], match.group(2))
            if isinstance(resolved, (dict, list)):
                return canonical_json(resolved)
            return str(resolved)

        return _INLINE_REF.sub(replace, value)
    if isinstance(value, list):
        return [_resolve(item, context) for item in value]
    if isinstance(value, tuple):
        return tuple(_resolve(item, context) for item in value)
    if isinstance(value, Mapping):
        return {str(key): _resolve(item, context) for key, item in value.items()}
    return value


def validate_schema(value: Any, schema: Mapping[str, Any], *, path: str = "$", errors: list[str] | None = None) -> tuple[str, ...]:
    """Validate the deterministic JSON-Schema subset emitted by the factory."""

    found = errors if errors is not None else []
    expected = schema.get("type")
    matches = {
        "object": isinstance(value, Mapping),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }
    if isinstance(expected, str) and expected in matches and not matches[expected]:
        found.append(f"{path}: expected {expected}")
        return tuple(found)
    if "enum" in schema and value not in schema.get("enum", ()):
        found.append(f"{path}: value is not in enum")
    if isinstance(value, Mapping):
        required = schema.get("required", [])
        if not isinstance(required, list):
            found.append(f"{path}: schema required must be a list")
        else:
            for name in required:
                if name not in value:
                    found.append(f"{path}.{name}: required field is missing")
        properties = schema.get("properties") or {}
        if isinstance(properties, Mapping):
            for name, item in value.items():
                child = properties.get(name)
                if isinstance(child, Mapping):
                    validate_schema(item, child, path=f"{path}.{name}", errors=found)
                elif schema.get("additionalProperties") is False:
                    found.append(f"{path}.{name}: additional property is forbidden")
    elif isinstance(value, list):
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if isinstance(minimum, int) and len(value) < minimum:
            found.append(f"{path}: has fewer than {minimum} items")
        if isinstance(maximum, int) and len(value) > maximum:
            found.append(f"{path}: has more than {maximum} items")
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(value):
                validate_schema(item, item_schema, path=f"{path}[{index}]", errors=found)
    elif isinstance(value, str):
        minimum = schema.get("minLength")
        maximum = schema.get("maxLength")
        pattern = schema.get("pattern")
        if isinstance(minimum, int) and len(value) < minimum:
            found.append(f"{path}: string is shorter than {minimum}")
        if isinstance(maximum, int) and len(value) > maximum:
            found.append(f"{path}: string is longer than {maximum}")
        if isinstance(pattern, str) and re.search(pattern, value) is None:
            found.append(f"{path}: string does not match pattern")
    return tuple(found)


def _capability(descriptor: SelectedSolutionDescriptor, capability_id: str | None) -> CapabilityAdapterContract:
    if capability_id is None:
        if len(descriptor.capabilities) != 1:
            raise AdapterRuntimeError("capability_id is required for a multi-capability adapter")
        return descriptor.capabilities[0]
    for capability in descriptor.capabilities:
        if capability.capability_id == capability_id:
            return capability
    raise AdapterRuntimeError(f"adapter does not declare capability {capability_id!r}")


def _required_config(descriptor: SelectedSolutionDescriptor, supplied: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for requirement in descriptor.configuration:
        value = supplied.get(requirement.name)
        if value is None and requirement.source == "environment":
            value = os.environ.get(requirement.name)
        if requirement.required and (value is None or value == ""):
            raise AdapterRuntimeError(f"required runtime configuration is missing: {requirement.name}")
        if value is not None:
            result[requirement.name] = value
    return result


def _bounded_path_payload(
    payload: Mapping[str, Any], schema: Mapping[str, Any], allowed_root: str | Path | None,
) -> dict[str, Any]:
    result = dict(payload)
    path_fields = {
        name
        for name, field_schema in (schema.get("properties") or {}).items()
        if isinstance(field_schema, Mapping) and field_schema.get("format") == "path"
    }
    if not path_fields:
        return result
    if allowed_root is None:
        raise AdapterRuntimeError("path inputs require an explicit JARVIS owned root")
    root = Path(allowed_root).expanduser().resolve(strict=False)
    for name in path_fields:
        raw = result.get(name)
        if raw is None:
            continue
        if not isinstance(raw, str) or not raw.strip():
            raise AdapterRuntimeError(f"path input {name!r} must be non-empty text")
        candidate = Path(raw).expanduser()
        candidate = (candidate if candidate.is_absolute() else root / candidate).resolve(strict=False)
        try:
            candidate.relative_to(root)
        except ValueError as error:
            raise AdapterRuntimeError(f"path input {name!r} escapes the JARVIS owned root") from error
        result[name] = str(candidate)
    return result


def _expected_matches(observed: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
    for key, wanted in expected.items():
        if key == "stdout_contains":
            if str(wanted) not in str(observed.get("stdout") or ""):
                return False
        elif key == "tool":
            tools = observed.get("tools") or []
            if not any(isinstance(item, Mapping) and item.get("name") == wanted for item in tools):
                return False
        elif _lookup(observed, key) != wanted:
            return False
    return True


def _safe_http_url(url: str, allowed_origins: Sequence[str]) -> str:
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise AdapterRuntimeError("HTTP adapter target must be an http(s) URL")
    origin = f"{parsed.scheme}://{parsed.hostname.lower()}"
    if parsed.port is not None:
        origin += f":{parsed.port}"
    normalized = {item.rstrip("/").lower() for item in allowed_origins}
    if origin.lower() not in normalized:
        raise AdapterRuntimeError(f"HTTP adapter target origin is not allowed: {origin}")
    if parsed.username or parsed.password:
        raise AdapterRuntimeError("HTTP adapter target must not embed credentials")
    return url


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        return None


def _http_call(mapping: InvocationMapping, context: Mapping[str, Any], *, timeout_s: float) -> dict[str, Any]:
    url = str(_resolve(mapping.target, context))
    _safe_http_url(url, mapping.allowed_origins)
    method = mapping.method or "GET"
    kwargs = _resolve(mapping.keyword_arguments, context)
    if kwargs:
        query = urllib.parse.urlencode(kwargs, doseq=True)
        url = url + ("&" if "?" in url else "?") + query
    headers = {str(key): str(value) for key, value in _resolve(mapping.headers, context).items()}
    body = None
    arguments = _resolve(list(mapping.arguments), context)
    if method != "GET" and arguments:
        body = canonical_json(arguments[0]).encode("utf-8")
        headers.setdefault("Content-Type", "application/json")
    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.build_opener(_NoRedirect).open(request, timeout=timeout_s) as response:
            raw = response.read(MAX_HTTP_BYTES + 1)
            if len(raw) > MAX_HTTP_BYTES:
                raise AdapterRuntimeError("HTTP adapter response exceeds the bounded limit")
            text = raw.decode("utf-8", errors="replace")
            content_type = response.headers.get("Content-Type", "")
            value = json.loads(text) if "json" in content_type.lower() or text.lstrip().startswith(("{", "[")) else None
            return {
                "status": int(response.status),
                "url": response.geturl(),
                "body": text,
                "json": value,
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
    except urllib.error.HTTPError as error:
        return {"status": int(error.code), "url": url, "body": "", "json": None, "sha256": None}


def _python_call(target: str, mapping: InvocationMapping, context: Mapping[str, Any], *, jarvis_only: bool = False) -> dict[str, Any]:
    module_name, separator, attribute = target.partition(":")
    if not separator or not module_name or not attribute:
        raise AdapterRuntimeError("Python target must use module:attribute")
    if jarvis_only and not module_name.startswith("jarvis_foundry"):
        raise AdapterRuntimeError("JARVIS component adapter target must remain under jarvis_foundry")
    module = importlib.import_module(module_name)
    current: Any = module
    for part in attribute.split("."):
        current = getattr(current, part)
    if not callable(current):
        raise AdapterRuntimeError("Python adapter target is not callable")
    args = _resolve(list(mapping.arguments), context)
    kwargs = _resolve(mapping.keyword_arguments, context)
    value = current(*args, **kwargs)
    return {"value": value}


def _command_call(target: str, mapping: InvocationMapping, context: Mapping[str, Any], *, timeout_s: float) -> dict[str, Any]:
    executable = shutil.which(target) or (str(Path(target).expanduser().resolve()) if Path(target).is_file() else None)
    if not executable:
        raise AdapterRuntimeError(f"CLI executable is not available: {target}")
    arguments = [str(item) for item in _resolve(list(mapping.arguments), context)]
    try:
        completed = subprocess.run(
            [executable, *arguments], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=timeout_s, check=False,
            env=sanitized_env(),
        )
    except subprocess.TimeoutExpired as error:
        raise AdapterRuntimeError("CLI adapter timed out") from error
    stdout = completed.stdout or ""
    stderr = completed.stderr or ""
    if len(stdout.encode("utf-8")) + len(stderr.encode("utf-8")) > MAX_RESULT_BYTES:
        raise AdapterRuntimeError("CLI adapter output exceeds the bounded limit")
    parsed = None
    if stdout.lstrip().startswith(("{", "[")):
        try:
            parsed = json.loads(stdout)
        except ValueError:
            parsed = None
    return {
        "returncode": completed.returncode,
        "stdout": stdout.rstrip("\r\n"),
        "stderr": stderr.rstrip("\r\n"),
        "json": parsed,
    }


def _mcp_exchange(command: list[str], messages: list[dict[str, Any]], *, timeout_s: float) -> list[dict[str, Any]]:
    executable = shutil.which(command[0]) or (str(Path(command[0]).resolve()) if Path(command[0]).is_file() else None)
    if not executable:
        raise AdapterRuntimeError(f"MCP executable is not available: {command[0]}")
    payload = "".join(canonical_json(item) + "\n" for item in messages)
    try:
        completed = subprocess.run(
            [executable, *command[1:]], input=payload, capture_output=True,
            text=True, encoding="utf-8", errors="replace", timeout=timeout_s,
            check=False, env=sanitized_env(),
        )
    except subprocess.TimeoutExpired as error:
        raise AdapterRuntimeError("MCP adapter timed out") from error
    if completed.returncode not in {0, None}:
        raise AdapterRuntimeError(f"MCP backend exited with code {completed.returncode}")
    responses: list[dict[str, Any]] = []
    for line in (completed.stdout or "").splitlines():
        try:
            value = json.loads(line)
        except ValueError:
            continue
        if isinstance(value, dict):
            responses.append(value)
    return responses


def _mcp_messages(method: str, params: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    return [
        {
            "jsonrpc": "2.0", "id": 1, "method": "initialize",
            "params": {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "jarvis-adapter-factory", "version": "1.0.0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
        {"jsonrpc": "2.0", "id": 2, "method": method, "params": dict(params or {})},
    ]


def _mcp_call(descriptor: SelectedSolutionDescriptor, mapping: InvocationMapping, context: Mapping[str, Any], *, timeout_s: float) -> dict[str, Any]:
    command = [descriptor.readiness.target, *descriptor.readiness.arguments]
    params = {"name": mapping.target, "arguments": _resolve(mapping.keyword_arguments, context)}
    responses = _mcp_exchange(command, _mcp_messages("tools/call", params), timeout_s=timeout_s)
    response = next((item for item in responses if item.get("id") == 2), None)
    if response is None or response.get("error") is not None:
        raise AdapterRuntimeError("MCP tools/call did not return a successful response")
    return {"result": response.get("result"), "responses": responses}


def _invoke_mapping(
    descriptor: SelectedSolutionDescriptor,
    capability: CapabilityAdapterContract,
    mapping: InvocationMapping,
    payload: Mapping[str, Any],
    config: Mapping[str, Any],
    request: Mapping[str, Any],
    artifact_root: Path,
) -> dict[str, Any]:
    context: dict[str, Any] = {
        "input": dict(payload), "config": dict(config), "request": dict(request),
        "raw": {}, "artifact": {"root": str(artifact_root)}, "artifact_root": str(artifact_root),
    }
    timeout_s = capability.timeout_ms / 1000.0
    if descriptor.solution_class is SolutionClass.CLI:
        raw = _command_call(mapping.target, mapping, context, timeout_s=timeout_s)
        claimed = "COMPLETED" if raw.get("returncode") == 0 else "FAILED"
    elif descriptor.solution_class is SolutionClass.PYTHON_SDK:
        raw = _python_call(mapping.target, mapping, context)
        claimed = "COMPLETED"
    elif descriptor.solution_class is SolutionClass.JARVIS_COMPONENT:
        raw = _python_call(mapping.target, mapping, context, jarvis_only=True)
        claimed = "COMPLETED"
    elif descriptor.solution_class is SolutionClass.HTTP_API:
        raw = _http_call(mapping, context, timeout_s=timeout_s)
        claimed = "COMPLETED" if 200 <= int(raw.get("status") or 0) < 300 else "FAILED"
    elif descriptor.solution_class is SolutionClass.MCP_STDIO:
        raw = _mcp_call(descriptor, mapping, context, timeout_s=timeout_s)
        claimed = "COMPLETED"
    else:  # pragma: no cover - enum validation prevents this
        raise AdapterRuntimeError("unsupported solution class")
    context["raw"] = raw
    mapped = _resolve(mapping.output, context) if mapping.output else raw
    if not isinstance(mapped, Mapping):
        mapped = {"value": mapped}
    result = {
        "claimed": claimed,
        **dict(mapped),
        "adapter_id": descriptor.adapter_id,
        "capability_id": capability.capability_id,
        "action_kind": capability.action_kind,
        "solution_id": descriptor.solution_id,
        "solution_class": descriptor.solution_class.value,
        "adapter_protocol": ADAPTER_PROTOCOL,
        "authority": "DATA_ONLY" if descriptor.solution_class in {SolutionClass.HTTP_API, SolutionClass.MCP_STDIO} else "JARVIS_SYSTEM_METADATA",
    }
    if contains_secret_like(canonical_json(result)):
        raise AdapterRuntimeError("adapter result contains secret-like material")
    output_errors = validate_schema(result, capability.output_schema)
    if output_errors:
        return {
            "claimed": "FAILED", "error_code": "output_schema_mismatch",
            "schema_errors": list(output_errors), "adapter_id": descriptor.adapter_id,
            "capability_id": capability.capability_id, "action_kind": capability.action_kind,
            "adapter_protocol": ADAPTER_PROTOCOL,
        }
    return result


def probe_descriptor(descriptor: SelectedSolutionDescriptor, *, config: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Perform the descriptor's bounded, non-effecting readiness probe."""

    try:
        resolved_config = _required_config(descriptor, config or {})
        probe = descriptor.readiness
        timeout_s = probe.timeout_ms / 1000.0
        if probe.kind in {ProbeKind.PYTHON_IMPORT, ProbeKind.JARVIS_IMPORT}:
            if probe.kind is ProbeKind.JARVIS_IMPORT and not probe.target.startswith("jarvis_foundry"):
                raise AdapterRuntimeError("JARVIS readiness imports must remain under jarvis_foundry")
            module = importlib.import_module(probe.target)
            observed = {
                "module": probe.target,
                "version": getattr(module, "__version__", None),
                "file": str(getattr(module, "__file__", "")),
            }
        elif probe.kind is ProbeKind.COMMAND:
            mapping = InvocationMapping(target=probe.target, arguments=probe.arguments)
            observed = _command_call(probe.target, mapping, {
                "input": {}, "config": resolved_config, "raw": {}, "request": {},
                "artifact": {}, "artifact_root": "",
            }, timeout_s=timeout_s)
        elif probe.kind is ProbeKind.HTTP:
            mapping = InvocationMapping(
                target=probe.target, method="GET",
                allowed_origins=tuple(probe.expected.get("allowed_origins") or ()),
            )
            observed = _http_call(mapping, {
                "input": {}, "config": resolved_config, "raw": {}, "request": {},
                "artifact": {}, "artifact_root": "",
            }, timeout_s=timeout_s)
        elif probe.kind is ProbeKind.MCP_TOOLS_LIST:
            responses = _mcp_exchange(
                [probe.target, *probe.arguments], _mcp_messages("tools/list"), timeout_s=timeout_s,
            )
            response = next((item for item in responses if item.get("id") == 2), {})
            observed = {
                "tools": ((response.get("result") or {}).get("tools") or []),
                "initialize": next((item.get("result") for item in responses if item.get("id") == 1), None),
            }
        else:  # pragma: no cover
            raise AdapterRuntimeError("unsupported readiness probe")
        expected = {key: value for key, value in probe.expected.items() if key != "allowed_origins"}
        ready = _expected_matches(observed, expected)
        return {
            "ready": ready,
            "state": "READY" if ready else "NOT_READY",
            "protocol": ADAPTER_PROTOCOL,
            "adapter_id": descriptor.adapter_id,
            "solution_id": descriptor.solution_id,
            "solution_class": descriptor.solution_class.value,
            "capability_ids": [item.capability_id for item in descriptor.capabilities],
            "observed": observed,
            "error_code": None if ready else "readiness_expectation_mismatch",
        }
    except Exception as error:  # fail closed; no traceback or secrets in readiness
        return {
            "ready": False, "state": "NOT_READY", "protocol": ADAPTER_PROTOCOL,
            "adapter_id": descriptor.adapter_id, "solution_id": descriptor.solution_id,
            "solution_class": descriptor.solution_class.value,
            "capability_ids": [item.capability_id for item in descriptor.capabilities],
            "observed": {}, "error_code": "readiness_probe_failed",
            "detail": redact_text(str(error))[:400],
        }


def invoke_descriptor(
    descriptor: SelectedSolutionDescriptor,
    capability_id: str | None,
    payload: Mapping[str, Any],
    *,
    config: Mapping[str, Any] | None = None,
    adapter_request: Mapping[str, Any] | None = None,
    canonical_effect_authorized: bool = False,
    artifact_root: str | Path = ".",
    allowed_root: str | Path | None = None,
    reconcile: bool = False,
    prior_result: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    capability = _capability(descriptor, capability_id)
    errors = validate_schema(dict(payload), capability.input_schema)
    if errors:
        return {
            "claimed": "FAILED", "error_code": "input_schema_mismatch",
            "schema_errors": list(errors), "adapter_id": descriptor.adapter_id,
            "capability_id": capability.capability_id, "action_kind": capability.action_kind,
            "adapter_protocol": ADAPTER_PROTOCOL,
        }
    try:
        payload = _bounded_path_payload(payload, capability.input_schema, allowed_root)
    except AdapterRuntimeError as error:
        return {
            "claimed": "BLOCKED", "error_code": "path_outside_owned_root",
            "detail": str(error), "adapter_id": descriptor.adapter_id,
            "capability_id": capability.capability_id, "action_kind": capability.action_kind,
            "adapter_protocol": ADAPTER_PROTOCOL,
        }
    request = dict(adapter_request or {})
    if capability.effect_class is not EffectClass.READ_ONLY:
        bound = (
            canonical_effect_authorized
            and request.get("effect_id")
            and request.get("idempotency_key")
            and request.get("capability_id") == capability.capability_id
        )
        if not bound:
            return {
                "claimed": "BLOCKED", "error_code": "canonical_effect_authority_required",
                "adapter_id": descriptor.adapter_id, "capability_id": capability.capability_id,
                "action_kind": capability.action_kind, "adapter_protocol": ADAPTER_PROTOCOL,
            }
    mapping = capability.invocation
    if reconcile:
        if capability.reconciliation.invocation is None:
            return {
                "claimed": "UNKNOWN", "error_code": "reconciliation_unavailable",
                "adapter_id": descriptor.adapter_id, "capability_id": capability.capability_id,
                "action_kind": capability.action_kind, "adapter_protocol": ADAPTER_PROTOCOL,
            }
        mapping = capability.reconciliation.invocation
        payload = {**dict(payload), "_prior_result": dict(prior_result or {})}
    return _invoke_mapping(
        descriptor, capability, mapping, payload,
        _required_config(descriptor, config or {}), request,
        Path(artifact_root).expanduser().resolve(),
    )


def child_main(descriptor_path: str | Path, argv: list[str] | None = None) -> int:
    """Entry point used by each generated, inspectable ``adapter.py``."""

    parser = argparse.ArgumentParser(description="Generated JARVIS capability adapter")
    parser.add_argument("command", choices=("probe", "execute", "reconcile"))
    parser.add_argument("--manifest")
    parser.add_argument("--capability")
    args = parser.parse_args(argv)
    descriptor = SelectedSolutionDescriptor.load(descriptor_path)
    root = Path(descriptor_path).resolve().parent
    if (root / DISABLED_FILE).exists():
        result = {
            "ready": False, "state": "DISABLED", "claimed": "BLOCKED",
            "error_code": "adapter_disabled", "protocol": ADAPTER_PROTOCOL,
            "adapter_protocol": ADAPTER_PROTOCOL, "adapter_id": descriptor.adapter_id,
        }
    elif args.command == "probe":
        result = probe_descriptor(descriptor)
        if len(descriptor.capabilities) == 1:
            result["capability_id"] = descriptor.capabilities[0].capability_id
            result["action_kind"] = descriptor.capabilities[0].action_kind
    else:
        raw = sys.stdin.read(MAX_RESULT_BYTES + 1)
        if len(raw.encode("utf-8")) > MAX_RESULT_BYTES:
            result = {"claimed": "FAILED", "error_code": "adapter_input_too_large"}
        else:
            envelope = _json_object(raw or "{}", "adapter input")
            payload = envelope.get("payload") if isinstance(envelope.get("payload"), Mapping) else envelope
            result = invoke_descriptor(
                descriptor, args.capability or envelope.get("capability_id"), payload,
                config=envelope.get("config") or envelope.get("_config") or {},
                adapter_request=envelope.get("adapter_request") or envelope.get("_adapter_request") or {},
                canonical_effect_authorized=bool(envelope.get("canonical_effect_authorized") or envelope.get("_canonical_effect_authorized")),
                artifact_root=root,
                allowed_root=envelope.get("allowed_root"),
                reconcile=args.command == "reconcile",
                prior_result=envelope.get("prior_result") or {},
            )
    encoded = canonical_json(result)
    if len(encoded.encode("utf-8")) > MAX_RESULT_BYTES:
        encoded = canonical_json({"claimed": "FAILED", "error_code": "adapter_output_too_large"})
    print(encoded)
    return 0


def _subprocess_environment(python_executable: str | Path) -> dict[str, str]:
    env = sanitized_env()
    for name in tuple(env):
        upper = name.upper()
        if upper.endswith(("_API_KEY", "_TOKEN", "_SECRET", "_PASSWORD")):
            env.pop(name, None)
    env["PYTHONNOUSERSITE"] = "1"
    requested = Path(python_executable).expanduser().resolve()
    if requested == Path(sys.executable).resolve():
        # Development/tests must bind children to this exact worktree rather
        # than an unrelated editable JARVIS installation in the user site.
        env["PYTHONPATH"] = str(Path(__file__).resolve().parents[2])
    else:
        # An explicitly supplied interpreter is an isolated installation;
        # never let the caller's worktree PYTHONPATH leak into it.
        env.pop("PYTHONPATH", None)
    return env


def _terminate(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def _run_adapter_process(
    command: list[str], input_text: str, *, timeout_s: float,
    cancel_check: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    kwargs: dict[str, Any] = headless_popen_kwargs(
        cwd=str(Path(command[1]).resolve().parent), env=_subprocess_environment(command[0]),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True,
    )
    kwargs.update({"encoding": "utf-8", "errors": "replace"})
    process = subprocess.Popen(command, **kwargs)
    holder: dict[str, Any] = {}

    def communicate() -> None:
        try:
            holder["output"] = process.communicate(input=input_text)
        except BaseException as error:  # surfaced below
            holder["error"] = error

    thread = threading.Thread(target=communicate, daemon=True)
    thread.start()
    deadline = time.monotonic() + timeout_s
    cancelled = False
    timed_out = False
    while thread.is_alive():
        if cancel_check is not None and cancel_check():
            cancelled = True
            _terminate(process)
            break
        if time.monotonic() >= deadline:
            timed_out = True
            _terminate(process)
            break
        thread.join(timeout=0.05)
    thread.join(timeout=4)
    if thread.is_alive():
        _terminate(process)
        return {"claimed": "UNKNOWN", "error_code": "adapter_process_unreaped"}
    if cancelled:
        return {"claimed": "BLOCKED", "error_code": "adapter_cancelled"}
    if timed_out:
        return {"claimed": "UNKNOWN", "error_code": "adapter_timeout"}
    if "error" in holder:
        return {"claimed": "FAILED", "error_code": "adapter_process_error", "detail": redact_text(str(holder["error"]))[:300]}
    stdout, stderr = holder.get("output", ("", ""))
    if len((stdout or "").encode("utf-8")) > MAX_RESULT_BYTES:
        return {"claimed": "FAILED", "error_code": "adapter_output_too_large"}
    if process.returncode != 0:
        return {
            "claimed": "FAILED", "error_code": "adapter_exit_nonzero",
            "exit_code": process.returncode, "detail": redact_text((stderr or "")[-500:]),
        }
    try:
        result = _json_object(stdout or "{}", "adapter output")
    except AdapterRuntimeError as error:
        return {"claimed": "FAILED", "error_code": "adapter_output_not_json", "detail": str(error)}
    if contains_secret_like(canonical_json(result)):
        raise AdapterRuntimeError("adapter output contains secret-like material")
    return result


def probe_artifact(
    root_or_manifest: str | Path, *, python_executable: str | Path | None = None,
    timeout_s: float = 30.0,
) -> dict[str, Any]:
    root, manifest, descriptor = load_verified_artifact(root_or_manifest)
    if (root / DISABLED_FILE).exists():
        return {
            "ready": False, "state": "DISABLED", "protocol": ADAPTER_PROTOCOL,
            "adapter_id": descriptor.adapter_id, "error_code": "adapter_disabled",
        }
    adapter = _contained(root / str(manifest.get("wrapper_path") or "adapter.py"), root, "wrapper")
    result = _run_adapter_process(
        [str(python_executable or sys.executable), str(adapter), "probe", "--manifest", str(root / MANIFEST_FILE)],
        "", timeout_s=timeout_s,
    )
    result["adapter_manifest_sha256"] = _sha256(root / MANIFEST_FILE)
    return result


def invoke_artifact(
    root_or_manifest: str | Path,
    capability_id: str,
    payload: Mapping[str, Any],
    *,
    config: Mapping[str, Any] | None = None,
    adapter_request: Mapping[str, Any] | None = None,
    canonical_effect_authorized: bool = False,
    python_executable: str | Path | None = None,
    timeout_s: float | None = None,
    cancel_check: Callable[[], bool] | None = None,
    stop_path: str | Path | None = None,
    reconcile: bool = False,
    prior_result: Mapping[str, Any] | None = None,
    allowed_root: str | Path | None = None,
) -> dict[str, Any]:
    root, manifest, descriptor = load_verified_artifact(root_or_manifest)
    capability = _capability(descriptor, capability_id)
    if (root / DISABLED_FILE).exists():
        return {"claimed": "BLOCKED", "error_code": "adapter_disabled", "adapter_id": descriptor.adapter_id}
    stop = Path(stop_path).expanduser().resolve() if stop_path is not None else None

    def cancelled() -> bool:
        return bool((stop is not None and stop.exists()) or (cancel_check is not None and cancel_check()))

    envelope = {
        "capability_id": capability_id,
        "payload": dict(payload),
        "config": dict(config or {}),
        "adapter_request": dict(adapter_request or {}),
        "canonical_effect_authorized": canonical_effect_authorized,
        "prior_result": dict(prior_result or {}),
        "allowed_root": str(Path(allowed_root).expanduser().resolve()) if allowed_root is not None else None,
    }
    adapter = _contained(root / str(manifest.get("wrapper_path") or "adapter.py"), root, "wrapper")
    command = "reconcile" if reconcile else "execute"
    result = _run_adapter_process(
        [str(python_executable or sys.executable), str(adapter), command, "--manifest", str(root / MANIFEST_FILE), "--capability", capability_id],
        canonical_json(envelope), timeout_s=timeout_s or capability.timeout_ms / 1000.0,
        cancel_check=cancelled if (stop is not None or cancel_check is not None) else None,
    )
    result["adapter_manifest_sha256"] = _sha256(root / MANIFEST_FILE)
    return result


def _field_values(result: Mapping[str, Any], fields: Sequence[str]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for field in fields:
        try:
            values[field] = _lookup(result, field)
        except AdapterRuntimeError:
            values[field] = None
    return values


def invoke_typed_adapter(
    root_or_manifest: str | Path,
    capability_id: str,
    payload: Mapping[str, Any],
    *,
    mission_id: str,
    workflow_id: str,
    step_id: str,
    attempt: int = 1,
    config: Mapping[str, Any] | None = None,
    python_executable: str | Path | None = None,
    canonical_effect_authorized: bool = False,
    stop_path: str | Path | None = None,
    allowed_root: str | Path | None = None,
) -> dict[str, Any]:
    """Run a built adapter and emit the canonical ``AdapterReceipt`` shape."""

    root, manifest, descriptor = load_verified_artifact(root_or_manifest)
    capability = _capability(descriptor, capability_id)
    executor = str(root / MANIFEST_FILE)
    spec: CapabilitySpec = capability.fabric_spec(executor=executor)
    request = AdapterRequest.build(
        mission_id=mission_id, workflow_id=workflow_id, step_id=step_id,
        step_version=1, attempt=attempt, action_kind=capability.action_kind,
        payload=payload, spec=spec,
    )
    result = invoke_artifact(
        root, capability_id, payload, config=config,
        adapter_request=request.to_evidence(),
        canonical_effect_authorized=canonical_effect_authorized,
        python_executable=python_executable, stop_path=stop_path, allowed_root=allowed_root,
    )
    claimed_text = str(result.get("claimed") or "UNKNOWN")
    try:
        claimed = ClaimOutcome(claimed_text)
    except ValueError:
        claimed = ClaimOutcome.UNKNOWN
    schema_ok = not validate_schema(result, capability.output_schema)
    reconciliation = capability.reconciliation
    reconciled: bool | None = None
    readback: dict[str, Any] | None = None
    if claimed is ClaimOutcome.COMPLETED and schema_ok:
        if reconciliation.strategy == "repeat_read" and capability.effect_class is EffectClass.READ_ONLY:
            readback = invoke_artifact(
                root, capability_id, payload, config=config,
                adapter_request=request.to_evidence(), python_executable=python_executable,
                allowed_root=allowed_root,
            )
            fields = reconciliation.compare_fields
            reconciled = _field_values(result, fields) == _field_values(readback, fields)
        elif reconciliation.strategy in {"system_readback", "provider_query"}:
            readback = invoke_artifact(
                root, capability_id, payload, config=config,
                adapter_request=request.to_evidence(),
                canonical_effect_authorized=canonical_effect_authorized,
                python_executable=python_executable, reconcile=True, prior_result=result,
                allowed_root=allowed_root,
            )
            fields = reconciliation.compare_fields
            reconciled = _field_values(result, fields) == _field_values(readback, fields)
        elif capability.effect_class is EffectClass.READ_ONLY:
            reconciled = True
    postconditions = evaluate_postconditions(result, capability.postconditions)
    post_statuses = {item.status for item in postconditions}
    if claimed is ClaimOutcome.COMPLETED and schema_ok and reconciled is True and PostconditionStatus.FAIL not in post_statuses and PostconditionStatus.UNKNOWN not in post_statuses:
        verdict = VerifyVerdict.MATCH
    elif claimed is ClaimOutcome.COMPLETED and (not schema_ok or reconciled is False or PostconditionStatus.FAIL in post_statuses):
        verdict = VerifyVerdict.MISMATCH
    else:
        verdict = VerifyVerdict.UNKNOWN
    observed = dict(result)
    observed.update({
        "readback_verified": reconciled,
        "readback": readback,
        "adapter_manifest_sha256": _sha256(root / MANIFEST_FILE),
    })
    receipt = normalize_receipt(
        request,
        claimed=observed,
        claimed_outcome=claimed.value,
        verified=verdict.value,
        note=str(result.get("error_code") or ""),
        postcondition_results=postconditions,
    ).as_dict()
    return {
        "request": request.to_evidence(),
        "result": result,
        "readback": readback,
        "reconciled": reconciled,
        "verified": verdict.value,
        "receipt": receipt,
        "success": claimed is ClaimOutcome.COMPLETED and verdict is VerifyVerdict.MATCH,
    }


__all__ = [
    "AdapterRuntimeError", "DISABLED_FILE", "MANIFEST_FILE", "child_main",
    "invoke_artifact", "invoke_descriptor", "invoke_typed_adapter",
    "load_verified_artifact", "probe_artifact", "probe_descriptor", "validate_schema",
]
