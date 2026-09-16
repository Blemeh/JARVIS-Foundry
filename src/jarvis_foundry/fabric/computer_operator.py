"""V13 computer-operator capabilities beneath the existing Fabric spine.

This module binds exact local process ownership and Windows accessibility
actions to Fabric. It owns no planning, scheduling, policy, or effect journal.
"""
from __future__ import annotations

import hashlib
import time
from pathlib import Path
from typing import Any, Mapping

from ..connectors.personal_operator import (
    PersonalOperatorConnector,
    PersonalOperatorError,
)
from ..connectors.windows_operator import (
    ControlCondition,
    ControlSelector,
    ExpectedDialog,
    OperatorAction,
    ProcessIdentity,
    TargetMatchError,
    WindowIdentity,
    WindowSelector,
    WindowsOperatorError,
    WindowsOperatorProvider,
    normalize_document_text,
)
from ..control.broker import emergency_stop_reason
from ..effects import effect_state, finalize_verified_effect
from ..events import canonical_json
from .model import (
    CapabilitySpec,
    Kind,
    Semantic,
    UnattendedMode,
    VerifyVerdict,
)


COMPUTER_OPERATOR_ACTIONS = frozenset({
    "local.process.launch",
    "local.process.inspect",
    "local.process.reconcile",
    "local.process.close",
    "desktop.open",
    "desktop.focus",
    "desktop.close",
    "desktop.window.observe",
    "desktop.control.inspect",
    "desktop.control.click",
    "desktop.control.replace_text",
    "desktop.control.type",
    "desktop.control.select",
    "desktop.control.wait",
    "desktop.control.assert",
    "desktop.control.paste",
    "desktop.open_artifact_and_assert",
    "desktop.file_dialog",
    "desktop.action.reconcile",
})
PERSONAL_OPERATOR_SHARED_CAPABILITY_IDS = frozenset({
    "cap.local.process.launch",
    "cap.local.process.inspect",
    "cap.local.process.close",
    "cap.desktop.open",
    "cap.desktop.focus",
    "cap.desktop.close",
})

_UI_ACTION_KINDS = {
    "desktop.control.click": "click",
    "desktop.control.replace_text": "replace_text",
    "desktop.control.type": "type",
    "desktop.control.select": "select",
    "desktop.control.wait": "wait",
    "desktop.control.assert": "assert",
    "desktop.control.paste": "paste",
}
_MUTATING_UI_ACTIONS = frozenset({
    "desktop.control.click",
    "desktop.control.replace_text",
    "desktop.control.type",
    "desktop.control.select",
    "desktop.control.paste",
})
_POLICY_OVERRIDE_KEYS = frozenset({
    "environment_allowlist",
    "executable_allowlist",
    "operator_environment_allowlist",
    "operator_executable_allowlist",
})


def computer_operator_specs() -> tuple[CapabilitySpec, ...]:
    """Return additive built-ins without claiming observed readiness."""

    allowed = UnattendedMode.ALLOWED
    return (
        CapabilitySpec(
            capability_id="cap.local.process.launch",
            version="1.1.0",
            kind=Kind.TOOL,
            action_kind="local.process.launch",
            semantic=Semantic.EXECUTE,
            executor="computer_operator",
            unattended=allowed,
            limitations=(
                "trusted executable allow-list; exact PID/create-time/executable identity",
            ),
        ),
        CapabilitySpec(
            capability_id="cap.local.process.inspect",
            version="1.1.0",
            kind=Kind.TOOL,
            action_kind="local.process.inspect",
            semantic=Semantic.READ,
            executor="computer_operator",
            unattended=allowed,
            limitations=("registry-owned exact process identity only",),
        ),
        CapabilitySpec(
            capability_id="cap.local.process.reconcile",
            version="1.0.0",
            kind=Kind.TOOL,
            action_kind="local.process.reconcile",
            semantic=Semantic.READ,
            executor="computer_operator",
            unattended=allowed,
            limitations=("read-only reconciliation; never adopts a changed PID",),
        ),
        CapabilitySpec(
            capability_id="cap.local.process.close",
            version="1.1.0",
            kind=Kind.TOOL,
            action_kind="local.process.close",
            semantic=Semantic.EXECUTE,
            executor="computer_operator",
            unattended=allowed,
            limitations=("exact registry-owned process only; never terminate by name",),
        ),
        CapabilitySpec(
            capability_id="cap.desktop.open",
            version="1.1.0",
            kind=Kind.TOOL,
            action_kind="desktop.open",
            semantic=Semantic.EXECUTE,
            executor="computer_operator",
            unattended=allowed,
            limitations=("allow-listed application and owned-root target only",),
        ),
        CapabilitySpec(
            capability_id="cap.desktop.focus",
            version="1.1.0",
            kind=Kind.TOOL,
            action_kind="desktop.focus",
            semantic=Semantic.EXECUTE,
            executor="computer_operator",
            unattended=allowed,
            limitations=("one visible HWND owned by the exact registered process",),
        ),
        CapabilitySpec(
            capability_id="cap.desktop.close",
            version="1.1.0",
            kind=Kind.TOOL,
            action_kind="desktop.close",
            semantic=Semantic.EXECUTE,
            executor="computer_operator",
            unattended=allowed,
            limitations=("exact registry-owned process only; never close by app name",),
        ),
        CapabilitySpec(
            capability_id="cap.desktop.window.observe",
            version="1.0.0",
            kind=Kind.CONNECTOR,
            action_kind="desktop.window.observe",
            semantic=Semantic.READ,
            executor="computer_operator",
            unattended=allowed,
            limitations=(
                "PID-bound exact HWND/title/class/accessibility identity; ambiguity blocks",
            ),
        ),
        CapabilitySpec(
            capability_id="cap.desktop.control.inspect",
            version="1.0.0",
            kind=Kind.CONNECTOR,
            action_kind="desktop.control.inspect",
            semantic=Semantic.READ,
            executor="computer_operator",
            unattended=allowed,
            limitations=("bounded accessibility tree; no coordinate fallback",),
        ),
        CapabilitySpec(
            capability_id="cap.desktop.control.click",
            version="1.0.0",
            kind=Kind.CONNECTOR,
            action_kind="desktop.control.click",
            semantic=Semantic.EXECUTE,
            executor="computer_operator",
            unattended=allowed,
            limitations=(
                "exact accessible control; deterministic readback or expected dialog required",
            ),
        ),
        CapabilitySpec(
            capability_id="cap.desktop.control.replace_text",
            version="1.0.0",
            kind=Kind.CONNECTOR,
            action_kind="desktop.control.replace_text",
            semantic=Semantic.EXECUTE,
            executor="computer_operator",
            unattended=allowed,
            limitations=("Value pattern only; exact value hash readback",),
        ),
        CapabilitySpec(
            capability_id="cap.desktop.control.type",
            version="1.0.0",
            kind=Kind.CONNECTOR,
            action_kind="desktop.control.type",
            semantic=Semantic.EXECUTE,
            executor="computer_operator",
            unattended=allowed,
            limitations=(
                "verified exact-control focus; bounded text; explicit final-value readback",
            ),
        ),
        CapabilitySpec(
            capability_id="cap.desktop.control.select",
            version="1.0.0",
            kind=Kind.CONNECTOR,
            action_kind="desktop.control.select",
            semantic=Semantic.EXECUTE,
            executor="computer_operator",
            unattended=allowed,
            limitations=("SelectionItem pattern and exact item identity only",),
        ),
        CapabilitySpec(
            capability_id="cap.desktop.control.wait",
            version="1.0.0",
            kind=Kind.CONNECTOR,
            action_kind="desktop.control.wait",
            semantic=Semantic.READ,
            executor="computer_operator",
            unattended=allowed,
            limitations=("bounded polling with STOP checks",),
        ),
        CapabilitySpec(
            capability_id="cap.desktop.control.assert",
            version="1.0.0",
            kind=Kind.CONNECTOR,
            action_kind="desktop.control.assert",
            semantic=Semantic.READ,
            executor="computer_operator",
            unattended=allowed,
            limitations=("single exact accessibility assertion",),
        ),
        CapabilitySpec(
            capability_id="cap.desktop.control.paste",
            version="1.0.0",
            kind=Kind.CONNECTOR,
            action_kind="desktop.control.paste",
            semantic=Semantic.EXECUTE,
            executor="computer_operator",
            unattended=allowed,
            limitations=(
                "clipboard text is set then pasted only into a bound control; exact value readback",
            ),
        ),
        CapabilitySpec(
            capability_id="cap.desktop.open_artifact_and_assert",
            version="1.0.0",
            kind=Kind.CONNECTOR,
            action_kind="desktop.open_artifact_and_assert",
            semantic=Semantic.EXECUTE,
            executor="computer_operator",
            unattended=allowed,
            limitations=(
                "allow-listed app; owned-root file; window-owning process identity; document value hash",
            ),
        ),
        CapabilitySpec(
            capability_id="cap.desktop.file_dialog",
            version="1.0.0",
            kind=Kind.CONNECTOR,
            action_kind="desktop.file_dialog",
            semantic=Semantic.EXECUTE,
            executor="computer_operator",
            unattended=allowed,
            limitations=(
                "named UIA File Open/Save As; keyboard-paste full path; #32770; Confirm Yes only for the submitted basename",
            ),
        ),
        CapabilitySpec(
            capability_id="cap.desktop.action.reconcile",
            version="1.0.0",
            kind=Kind.CONNECTOR,
            action_kind="desktop.action.reconcile",
            semantic=Semantic.READ,
            executor="computer_operator",
            unattended=allowed,
            limitations=(
                "read-only original-effect reconciliation; never redispatches under STOP",
            ),
        ),
    )


def _required_text(payload: Mapping[str, Any], *names: str) -> str:
    for name in names:
        value = payload.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    raise ValueError(f"{names[0]} is required")


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _runtime_id(value: object) -> tuple[int, ...]:
    if value in (None, (), []):
        return ()
    if not isinstance(value, (list, tuple)):
        raise ValueError("runtime_id must be a list of integers")
    return tuple(int(item) for item in value)


def _optional_text(value: object) -> str | None:
    if value is None:
        return None
    result = str(value).strip()
    return result or None


def _window_selector(value: object) -> WindowSelector:
    item = _mapping(value, "window_selector")
    return WindowSelector(
        hwnd=int(item["hwnd"]) if item.get("hwnd") is not None else None,
        title=_optional_text(item.get("title")),
        class_name=_optional_text(item.get("class_name")),
        automation_id=_optional_text(item.get("automation_id")),
        runtime_id=_runtime_id(item.get("runtime_id")),
    )


def _process_identity(value: object) -> ProcessIdentity:
    item = _mapping(value, "process")
    return ProcessIdentity(
        logical_id=str(item["logical_id"]),
        pid=int(item["pid"]),
        create_time=int(item["create_time"]),
        executable=str(item["executable"]),
    )


def _window_identity(value: object) -> WindowIdentity:
    item = _mapping(value, "window")
    if isinstance(item.get("identity"), Mapping):
        item = _mapping(item["identity"], "window.identity")
    return WindowIdentity(
        process=_process_identity(item["process"]),
        hwnd=int(item["hwnd"]),
        title=str(item.get("title") or ""),
        class_name=str(item.get("class_name") or ""),
        automation_id=str(item.get("automation_id") or ""),
        runtime_id=_runtime_id(item.get("runtime_id")),
        observation_generation=int(item["observation_generation"]),
    )


def _control_selector(value: object, name: str) -> ControlSelector:
    item = _mapping(value, name)
    return ControlSelector(
        control_type=_optional_text(item.get("control_type")),
        name=_optional_text(item.get("name")),
        automation_id=_optional_text(item.get("automation_id")),
        class_name=_optional_text(item.get("class_name")),
        runtime_id=_runtime_id(item.get("runtime_id")),
    )


def _condition(value: object) -> ControlCondition | None:
    if value is None:
        return None
    item = _mapping(value, "condition")
    return ControlCondition(
        field=str(item["field"]),
        expected=item.get("expected"),
    )


def _expected_dialog(value: object) -> ExpectedDialog | None:
    if value is None:
        return None
    item = _mapping(value, "expected_dialog")
    return ExpectedDialog(
        selector=_window_selector(item.get("selector") or item),
        timeout_s=float(item.get("timeout_s") or 5.0),
    )


def _timeout_seconds(payload: Mapping[str, Any]) -> float:
    if payload.get("timeout_s") is not None:
        return float(payload["timeout_s"])
    if payload.get("timeout_ms") is not None:
        return float(payload["timeout_ms"]) / 1000.0
    return 5.0


def _context_connector(ctx: Any) -> PersonalOperatorConnector:
    current = getattr(ctx, "operator_connector", None)
    if current is not None:
        return current
    connector = PersonalOperatorConnector(
        ctx.owned_root,
        executable_allowlist=getattr(
            ctx, "operator_executable_allowlist", (),
        ),
        environment_allowlist=getattr(
            ctx, "operator_environment_allowlist", (),
        ),
    )
    ctx.operator_connector = connector
    return connector


def _stop_active(ctx: Any, payload: Mapping[str, Any]) -> bool:
    identities = {"GLOBAL"}
    request = payload.get("_adapter_request")
    if isinstance(request, Mapping):
        identities.update(
            str(item)
            for item in request.get("stop_scope") or ()
            if str(item).strip()
        )
        if request.get("mission_id"):
            identities.add(str(request["mission_id"]))
    return bool(
        emergency_stop_reason(
            ctx.database,
            payload.get("stop_path"),
            identities=identities,
        )
    )


def _wait_for_owned_window(
    ctx: Any,
    payload: Mapping[str, Any],
    process_id: str,
) -> Mapping[str, Any]:
    timeout_s = _timeout_seconds(payload)
    if timeout_s <= 0 or timeout_s > 60:
        raise ValueError("timeout must be within (0, 60] seconds")
    deadline = time.monotonic() + timeout_s
    connector = _context_connector(ctx)
    last: Mapping[str, Any] = {}
    while True:
        if _stop_active(ctx, payload):
            return {
                "status": "UNKNOWN",
                "error": {
                    "classification": "stop_after_possible_effect",
                    "message": "STOP became active after desktop launch",
                },
            }
        last = connector.observe_process_window(process_id)
        if last.get("status") == "COMPLETED":
            return last
        error = last.get("error")
        classification = (
            str(error.get("classification") or "")
            if isinstance(error, Mapping)
            else ""
        )
        if classification != "window_not_found" or time.monotonic() >= deadline:
            return last
        time.sleep(min(0.05, max(0.0, deadline - time.monotonic())))


def _opened_target_matches(
    ctx: Any,
    payload: Mapping[str, Any],
    record: Mapping[str, Any],
) -> bool:
    opened = record.get("opened_identity")
    if not isinstance(opened, Mapping):
        return False
    target_ref = payload.get("target") or payload.get("path")
    if not isinstance(target_ref, str) or not target_ref.strip():
        return False
    root = Path(ctx.owned_root).resolve()
    target = Path(target_ref)
    target = (
        target.expanduser().resolve(strict=False)
        if target.is_absolute()
        else (root / target).resolve(strict=False)
    )
    try:
        target.relative_to(root)
    except ValueError:
        return False
    if target.is_symlink() or not target.exists():
        return False
    if opened.get("path") != str(target):
        return False
    args = record.get("args")
    if not isinstance(args, list) or not args or args[-1] != str(target):
        return False
    if target.is_file():
        content = target.read_bytes()
        return (
            opened.get("kind") == "file"
            and opened.get("size_bytes") == len(content)
            and opened.get("sha256")
            == f"sha256:{hashlib.sha256(content).hexdigest()}"
        )
    return target.is_dir() and opened.get("kind") == "directory"


def _record_identity(record: Mapping[str, Any]) -> ProcessIdentity:
    creation = _mapping(record.get("creation_identity"), "creation_identity")
    return ProcessIdentity(
        logical_id=str(record["process_id"]),
        pid=int(record["pid"]),
        create_time=int(creation["create_time"]),
        executable=str(creation.get("exe") or record["executable"]),
    )


def _document_digest(text: str) -> tuple[str, str]:
    """Editor-neutral document text and SHA-256 for native readback."""

    normalized = normalize_document_text(text).rstrip("\n")
    digest = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return normalized, digest


def open_artifact_and_assert(
    connector: PersonalOperatorConnector,
    *,
    process_id: str,
    executable: str,
    path: str,
    expected: str,
    idempotency_key: str,
    args: tuple[str, ...] = (),
    timeout_s: float = 15.0,
    cwd: str = ".",
    control: ControlSelector | None = None,
    backend: Any = None,
    stop_check: Any = None,
) -> dict[str, Any]:
    """Launch/open one owned file, bind identities, and assert document text."""

    opened = connector.desktop_open(
        process_id,
        executable,
        path,
        args=args,
        cwd=cwd,
        idempotency_key=idempotency_key,
        show_window=True,
    )
    result = _personal_result("desktop.open_artifact_and_assert", opened)
    if result.get("claimed") != "COMPLETED":
        return result
    if (opened.get("result") or {}).get("state") != "running":
        result["claimed"] = "FAILED"
        result["error_code"] = "process_not_running"
        result["detail"] = "desktop open did not leave a live process"
        return result
    record = connector.get_validated_process_record(process_id)
    provider = WindowsOperatorProvider(
        _record_identity(record),
        backend=backend,
        stop_check=stop_check or (lambda: False),
    )
    marker = Path(path).name
    deadline = time.monotonic() + max(float(timeout_s), 0.0)
    window = None
    document = None
    while True:
        try:
            window = provider.observe_main_window(title_contains=marker)
            document = provider.find_document_control(window.identity, control)
            break
        except TargetMatchError as error:
            if error.code not in {"window_not_found", "control_not_found"}:
                raise
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.1)
    assert window is not None and document is not None
    observed = document.value if isinstance(document.value, str) else ""
    expected_norm, expected_sha = _document_digest(expected)
    observed_norm, observed_sha = _document_digest(observed)
    matched = expected_norm == observed_norm
    payload = {
        **result,
        "process": provider.process.to_dict(),
        "window": window.to_dict(),
        "control": document.selector.to_dict(),
        "expected_sha256": expected_sha,
        "expected_length": len(expected_norm),
        "observed_sha256": observed_sha,
        "observed_length": len(observed_norm),
        "readback_verified": matched,
        "reconciled": matched,
        "provider_id": "windows-uiautomation",
        "provider_ref": (
            f"computer:{provider.process.pid}:{provider.process.create_time}:"
            f"{window.identity.hwnd}"
        ),
    }
    if not matched:
        payload["claimed"] = "FAILED"
        payload["error_code"] = "artifact_text_mismatch"
        payload["detail"] = "document value hash did not match expected text"
        return payload
    payload["claimed"] = "COMPLETED"
    return payload


def _provider(
    ctx: Any,
    payload: Mapping[str, Any],
    *,
    honor_stop: bool = True,
) -> WindowsOperatorProvider:
    process_id = _required_text(payload, "process_id", "target")
    record = _context_connector(ctx).get_validated_process_record(process_id)
    identities = {"GLOBAL"}
    request = payload.get("_adapter_request")
    if isinstance(request, Mapping):
        identities.update(
            str(item)
            for item in request.get("stop_scope") or ()
            if str(item).strip()
        )
        if request.get("mission_id"):
            identities.add(str(request["mission_id"]))

    def stop_check() -> bool:
        if not honor_stop:
            return False
        return bool(
            emergency_stop_reason(
                ctx.database,
                payload.get("stop_path"),
                identities=identities,
            )
        )

    return WindowsOperatorProvider(
        _record_identity(record),
        backend=getattr(ctx, "windows_operator_backend", None),
        stop_check=stop_check,
    )


def _operator_action(
    payload: Mapping[str, Any],
    action_kind: str,
) -> OperatorAction:
    request = payload.get("_adapter_request")
    request = request if isinstance(request, Mapping) else {}
    control = payload.get("control")
    readback = payload.get("readback_control")
    return OperatorAction(
        action_id=str(
            payload.get("original_action_id")
            or request.get("request_id")
            or payload.get("idempotency_key")
            or f"operator:{action_kind}"
        ),
        kind=_UI_ACTION_KINDS[action_kind],
        window=_window_identity(payload.get("window")),
        control=(
            _control_selector(control, "control")
            if control is not None
            else None
        ),
        readback_control=(
            _control_selector(readback, "readback_control")
            if readback is not None
            else None
        ),
        value=(
            str(payload["value"])
            if payload.get("value") is not None
            else None
        ),
        condition=_condition(payload.get("condition")),
        timeout_s=_timeout_seconds(payload),
        expected_dialog=_expected_dialog(payload.get("expected_dialog")),
    )


def _personal_result(action_kind: str, receipt: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(receipt)
    result["action_kind"] = action_kind
    result["claimed"] = str(receipt.get("status") or "UNKNOWN")
    error = receipt.get("error")
    if isinstance(error, Mapping) and error.get("classification"):
        result["error_code"] = str(error["classification"])
    return result


def _operator_result(action_kind: str, receipt: Any) -> dict[str, Any]:
    claimed = "BLOCKED" if receipt.outcome == "CANCELLED" else receipt.outcome
    evidence = receipt.to_dict()
    provider_ref = (
        f"computer:{receipt.process.pid}:{receipt.process.create_time}:"
        f"{receipt.window.hwnd}"
    )
    return {
        "claimed": claimed,
        "action_kind": action_kind,
        "operator_receipt": evidence,
        "process_id": receipt.process.logical_id,
        "provider_id": "windows-uiautomation",
        "provider_ref": provider_ref,
        "readback_verified": bool(
            receipt.outcome == "COMPLETED" and receipt.reconciled
        ),
        "reconciled": bool(receipt.reconciled),
        "effect_possible": bool(receipt.effect_possible),
        "reconciliation_required": bool(receipt.reconciliation_required),
        "performed": bool(receipt.effect_possible),
        "error_code": receipt.error_code,
    }


def _effect_row(database: Any, action_key: str) -> Mapping[str, Any] | None:
    with database.connect(readonly=True) as connection:
        row = connection.execute(
            "SELECT action_key, system_id, target_ref, payload_hash, kind, status "
            "FROM system_actions WHERE action_key = ?",
            (action_key,),
        ).fetchone()
    if row is None:
        return None
    return {
        "action_key": str(row[0]),
        "system_id": str(row[1]),
        "target_ref": str(row[2]),
        "payload_hash": str(row[3]),
        "kind": str(row[4]),
        "status": str(row[5]),
    }


def _reconcile_action(ctx: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
    action_key = _required_text(payload, "original_effect_id", "effect_id")
    original_kind = _required_text(payload, "original_action_kind")
    if original_kind not in _MUTATING_UI_ACTIONS:
        return {
            "claimed": "BLOCKED",
            "error_code": "unsupported_reconciliation_target",
        }
    row = _effect_row(ctx.database, action_key)
    if row is None:
        return {"claimed": "BLOCKED", "error_code": "effect_not_found"}
    if row["system_id"] != "computer" or row["kind"] != original_kind:
        return {
            "claimed": "BLOCKED",
            "error_code": "effect_identity_mismatch",
        }
    state = effect_state(ctx.database, action_key)
    if state.get("status") not in {"COMPLETED", "FAILED", "FROZEN"}:
        return {
            "claimed": "BLOCKED",
            "error_code": "effect_not_terminal",
        }
    action = _operator_action(payload, original_kind)
    receipt = _provider(ctx, payload, honor_stop=False).reconcile(action)
    result = _operator_result("desktop.action.reconcile", receipt)
    if receipt.outcome != "COMPLETED" or not receipt.reconciled:
        return result
    provider_receipt = {
        "provider_id": result["provider_id"],
        "provider_ref": result["provider_ref"],
        "outcome": receipt.outcome,
        "receipt_sha256": hashlib.sha256(
            canonical_json(result["operator_receipt"]).encode("utf-8")
        ).hexdigest(),
    }
    readback = {
        "status": "MATCH",
        "matches": True,
        "provider_ref": result["provider_ref"],
    }
    accepted = finalize_verified_effect(
        ctx.database,
        action_key,
        provider_receipt=provider_receipt,
        readback=readback,
        result={
            "provider_id": result["provider_id"],
            "provider_ref": result["provider_ref"],
            "operator_receipt": result["operator_receipt"],
            "readback_verified": True,
        },
        acceptance_evidence={
            "authority": "windows_accessibility_readback",
            "receipt_sha256": provider_receipt["receipt_sha256"],
        },
        reconciled=True,
    )
    return {
        **result,
        **accepted,
        "claimed": "COMPLETED",
        "readback": readback,
    }


def dispatch_computer_operator(
    ctx: Any,
    action_kind: str,
    payload: Mapping[str, Any],
) -> dict[str, Any] | None:
    """Dispatch one V13 verb, or return ``None`` for another executor."""

    if action_kind not in COMPUTER_OPERATOR_ACTIONS:
        return None
    overrides = sorted(_POLICY_OVERRIDE_KEYS.intersection(payload))
    if overrides:
        return {
            "claimed": "BLOCKED",
            "action_kind": action_kind,
            "error_code": "policy_override_refused",
            "detail": "trusted operator policy cannot be supplied by an action",
        }
    connector = _context_connector(ctx)
    try:
        if action_kind == "local.process.launch":
            receipt = connector.launch_process(
                _required_text(payload, "process_id", "target"),
                _required_text(payload, "executable"),
                payload.get("args") or (),
                cwd=str(payload.get("cwd") or "."),
                idempotency_key=_required_text(payload, "idempotency_key"),
                show_window=bool(payload.get("show_window", False)),
            )
            return _personal_result(action_kind, receipt)
        if action_kind == "local.process.inspect":
            return _personal_result(
                action_kind,
                connector.inspect_process(
                    _required_text(payload, "process_id", "target")
                ),
            )
        if action_kind == "local.process.reconcile":
            process_id = payload.get("process_id") or payload.get("target")
            receipt = (
                connector.inspect_process(str(process_id))
                if process_id
                else connector.reconcile_processes()
            )
            return _personal_result(action_kind, receipt)
        if action_kind in {"local.process.close", "desktop.close"}:
            receipt = connector.close_process(
                _required_text(payload, "process_id", "target"),
                idempotency_key=_required_text(payload, "idempotency_key"),
                grace_seconds=float(payload.get("grace_seconds") or 2.0),
            )
            return _personal_result(action_kind, receipt)
        if action_kind == "desktop.open":
            receipt = connector.desktop_open(
                _required_text(payload, "process_id"),
                _required_text(payload, "application", "executable"),
                _required_text(payload, "target", "path"),
                args=payload.get("args") or (),
                cwd=str(payload.get("cwd") or "."),
                idempotency_key=_required_text(payload, "idempotency_key"),
                show_window=bool(payload.get("show_window", True)),
            )
            result = _personal_result(action_kind, receipt)
            if result.get("claimed") != "COMPLETED":
                return result
            process_id = _required_text(payload, "process_id")
            window = _wait_for_owned_window(ctx, payload, process_id)
            if window.get("status") != "COMPLETED":
                error = window.get("error")
                classification = (
                    str(error.get("classification") or "")
                    if isinstance(error, Mapping)
                    else ""
                )
                return {
                    **result,
                    "claimed": "UNKNOWN",
                    "performed": True,
                    "error_code": (
                        "window_postcondition_timeout"
                        if classification == "window_not_found"
                        else classification or "window_postcondition_failed"
                    ),
                    "window_observation": dict(window),
                    "readback_verified": False,
                }
            return {
                **result,
                "window_observation": dict(window),
                "readback_verified": True,
                "reconciled": True,
            }
        if action_kind == "desktop.focus":
            return _personal_result(
                action_kind,
                connector.focus_process(
                    _required_text(payload, "process_id", "target")
                ),
            )
        if action_kind == "desktop.window.observe":
            observation = _provider(ctx, payload).observe(
                _window_selector(payload.get("window_selector"))
            )
            return {
                "claimed": "COMPLETED",
                "action_kind": action_kind,
                "process_id": observation.identity.process.logical_id,
                "window": observation.to_dict(),
                "readback_verified": True,
                "reconciled": True,
            }
        if action_kind == "desktop.control.inspect":
            provider = _provider(ctx, payload)
            selector_value = payload.get("control")
            controls = provider.inspect(
                _window_identity(payload.get("window")),
                (
                    _control_selector(selector_value, "control")
                    if selector_value is not None
                    else None
                ),
            )
            return {
                "claimed": "COMPLETED",
                "action_kind": action_kind,
                "process_id": provider.process.logical_id,
                "controls": [control.to_dict() for control in controls],
                "readback_verified": True,
                "reconciled": True,
            }
        if action_kind == "desktop.open_artifact_and_assert":
            expected = payload.get("expected")
            if not isinstance(expected, str):
                raise ValueError("expected text is required")
            control_value = payload.get("control")
            return open_artifact_and_assert(
                connector,
                process_id=_required_text(payload, "process_id"),
                executable=_required_text(payload, "application", "executable"),
                path=_required_text(payload, "target", "path"),
                expected=expected,
                idempotency_key=_required_text(payload, "idempotency_key"),
                args=tuple(str(value) for value in payload.get("args") or ()),
                timeout_s=_timeout_seconds(payload),
                cwd=str(payload.get("cwd") or "."),
                control=(
                    _control_selector(control_value, "control")
                    if control_value is not None
                    else None
                ),
                backend=getattr(ctx, "windows_operator_backend", None),
            )
        if action_kind == "desktop.file_dialog":
            action = str(payload.get("action") or "open").strip()
            if action not in {"open", "save_as"}:
                raise ValueError("action must be open or save_as")
            path_text = _required_text(payload, "path", "target")
            path = Path(path_text)
            if not path.is_absolute():
                path = Path(ctx.owned_root) / path_text
            receipt = _provider(ctx, payload).invoke_file_dialog(
                _window_identity(payload.get("window")),
                path=str(path),
                action=action,
                timeout_s=max(_timeout_seconds(payload), 15.0),
                action_id=str(payload.get("action_id") or "file-dialog"),
            )
            return _operator_result(action_kind, receipt)
        if action_kind == "desktop.action.reconcile":
            return _reconcile_action(ctx, payload)
        action = _operator_action(payload, action_kind)
        receipt = _provider(ctx, payload).perform(action)
        return _operator_result(action_kind, receipt)
    except PersonalOperatorError as error:
        return {
            "claimed": (
                "UNKNOWN" if error.status == "UNKNOWN" else "FAILED"
            ),
            "action_kind": action_kind,
            "error_code": error.classification,
            "detail": str(error)[:300],
        }
    except WindowsOperatorError as error:
        return {
            "claimed": "FAILED",
            "action_kind": action_kind,
            "error_code": error.code,
            "detail": str(error)[:300],
        }
    except (KeyError, TypeError, ValueError) as error:
        return {
            "claimed": "FAILED",
            "action_kind": action_kind,
            "error_code": "operator_contract_error",
            "detail": str(error)[:300],
        }


def _stable_evidence(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {
            str(key): _stable_evidence(item)
            for key, item in value.items()
            if str(key) != "observation_generation"
        }
    if isinstance(value, list):
        return [_stable_evidence(item) for item in value]
    return value


def verify_computer_operator(
    action_kind: str,
    claimed: Mapping[str, Any],
    payload: Mapping[str, Any] | None,
    ctx: Any | None,
) -> VerifyVerdict | None:
    """Independently re-observe a V13 claim through the same bounded provider."""

    if action_kind not in COMPUTER_OPERATOR_ACTIONS:
        return None
    if str(claimed.get("claimed") or "UNKNOWN") != "COMPLETED":
        return VerifyVerdict.UNKNOWN
    if ctx is None:
        return VerifyVerdict.UNKNOWN
    payload = payload or {}
    try:
        connector = _context_connector(ctx)
        if action_kind in {"local.process.launch", "local.process.inspect"}:
            observed = connector.inspect_process(
                _required_text(payload, "process_id", "target")
            )
            running = (
                observed.get("status") == "COMPLETED"
                and (observed.get("result") or {}).get("state") == "running"
                and (observed.get("result") or {}).get("matches") is True
            )
            if not running:
                return VerifyVerdict.MISMATCH
            return VerifyVerdict.MATCH
        if action_kind == "desktop.open":
            process_id = _required_text(payload, "process_id", "target")
            observed = connector.inspect_process(process_id)
            running = (
                observed.get("status") == "COMPLETED"
                and (observed.get("result") or {}).get("state") == "running"
                and (observed.get("result") or {}).get("matches") is True
            )
            if not running:
                return VerifyVerdict.MISMATCH
            record = connector.get_validated_process_record(process_id)
            if (
                record.get("operation") != "desktop.open"
                or not _opened_target_matches(ctx, payload, record)
            ):
                return VerifyVerdict.MISMATCH
            claimed_window = claimed.get("window_observation")
            fresh_window = connector.observe_process_window(process_id)
            if (
                not isinstance(claimed_window, Mapping)
                or fresh_window.get("status") != "COMPLETED"
                or (fresh_window.get("result") or {}).get("window_handle")
                != (claimed_window.get("result") or {}).get("window_handle")
            ):
                return VerifyVerdict.MISMATCH
            return VerifyVerdict.MATCH
        if action_kind == "desktop.focus":
            process_id = _required_text(payload, "process_id", "target")
            observed = connector.inspect_process(process_id)
            fresh_window = connector.observe_process_window(process_id)
            readback = claimed.get("readback") or {}
            return (
                VerifyVerdict.MATCH
                if observed.get("status") == "COMPLETED"
                and (observed.get("result") or {}).get("state") == "running"
                and (observed.get("result") or {}).get("matches") is True
                and fresh_window.get("status") == "COMPLETED"
                and (fresh_window.get("result") or {}).get("focused") is True
                and (fresh_window.get("result") or {}).get("window_handle")
                == readback.get("window_handle")
                else VerifyVerdict.MISMATCH
            )
        if action_kind in {"local.process.close", "desktop.close"}:
            observed = connector.inspect_process(
                _required_text(payload, "process_id", "target")
            )
            return (
                VerifyVerdict.MATCH
                if observed.get("status") == "COMPLETED"
                and (observed.get("result") or {}).get("state") == "exited"
                and (observed.get("readback") or {}).get("alive") is False
                else VerifyVerdict.MISMATCH
            )
        if action_kind == "local.process.reconcile":
            return (
                VerifyVerdict.MATCH
                if claimed.get("status") == "COMPLETED"
                else VerifyVerdict.MISMATCH
            )
        if action_kind == "desktop.window.observe":
            observed = _provider(ctx, payload, honor_stop=False).observe(
                _window_selector(payload.get("window_selector"))
            )
            return (
                VerifyVerdict.MATCH
                if _stable_evidence(observed.to_dict())
                == _stable_evidence(claimed.get("window"))
                else VerifyVerdict.MISMATCH
            )
        if action_kind == "desktop.control.inspect":
            provider = _provider(ctx, payload, honor_stop=False)
            selector_value = payload.get("control")
            controls = provider.inspect(
                _window_identity(payload.get("window")),
                (
                    _control_selector(selector_value, "control")
                    if selector_value is not None
                    else None
                ),
            )
            actual = [control.to_dict() for control in controls]
            return (
                VerifyVerdict.MATCH
                if _stable_evidence(actual)
                == _stable_evidence(claimed.get("controls"))
                else VerifyVerdict.MISMATCH
            )
        if action_kind == "desktop.open_artifact_and_assert":
            process_id = _required_text(payload, "process_id")
            observed = connector.inspect_process(process_id)
            running = (
                observed.get("status") == "COMPLETED"
                and (observed.get("result") or {}).get("state") == "running"
                and (observed.get("result") or {}).get("matches") is True
            )
            if not running:
                return VerifyVerdict.MISMATCH
            provider = _provider(ctx, payload, honor_stop=False)
            claimed_window = claimed.get("window")
            if not isinstance(claimed_window, Mapping):
                return VerifyVerdict.MISMATCH
            identity = _window_identity(
                claimed_window.get("identity") or claimed_window
            )
            control_value = payload.get("control") or claimed.get("control")
            selector = (
                _control_selector(control_value, "control")
                if isinstance(control_value, Mapping)
                else None
            )
            document = provider.find_document_control(identity, selector)
            observed_text = (
                document.value if isinstance(document.value, str) else ""
            )
            observed_sha = _document_digest(observed_text)[1]
            return (
                VerifyVerdict.MATCH
                if observed_sha == claimed.get("observed_sha256")
                and observed_sha == claimed.get("expected_sha256")
                else VerifyVerdict.MISMATCH
            )
        if action_kind == "desktop.file_dialog":
            process_id = _required_text(payload, "process_id")
            observed = connector.inspect_process(process_id)
            running = (
                observed.get("status") == "COMPLETED"
                and (observed.get("result") or {}).get("state") == "running"
                and (observed.get("result") or {}).get("matches") is True
            )
            if not running:
                return VerifyVerdict.MISMATCH
            path_text = _required_text(payload, "path", "target")
            stem = Path(path_text).stem
            provider = _provider(ctx, payload, honor_stop=False)
            window = provider.observe_main_window(title_contains=stem)
            action = str(payload.get("action") or "open").strip()
            if action == "save_as":
                saved = Path(path_text)
                if not saved.is_absolute():
                    saved = Path(ctx.owned_root) / path_text
                if not saved.is_file():
                    return VerifyVerdict.MISMATCH
            return (
                VerifyVerdict.MATCH
                if stem.casefold() in window.identity.title.casefold()
                else VerifyVerdict.MISMATCH
            )
        if action_kind == "desktop.action.reconcile":
            return (
                VerifyVerdict.MATCH
                if claimed.get("accepted") is True
                and claimed.get("terminal_truth") is True
                and claimed.get("readback_verified") is True
                else VerifyVerdict.MISMATCH
            )
        action = _operator_action(payload, action_kind)
        receipt = _provider(
            ctx, payload, honor_stop=False
        ).reconcile(action)
        return (
            VerifyVerdict.MATCH
            if receipt.outcome == "COMPLETED" and receipt.reconciled
            else VerifyVerdict.MISMATCH
        )
    except Exception:  # noqa: BLE001 - independent verification fails closed
        return VerifyVerdict.MISMATCH


__all__ = [
    "COMPUTER_OPERATOR_ACTIONS",
    "PERSONAL_OPERATOR_SHARED_CAPABILITY_IDS",
    "computer_operator_specs",
    "dispatch_computer_operator",
    "open_artifact_and_assert",
    "verify_computer_operator",
]
