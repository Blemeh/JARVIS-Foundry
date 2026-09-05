"""V13 personal-operator capability declarations and thin integration.

The implementation adapters remain local hands.  This module only describes
their Fabric-facing verbs and, below, binds those verbs to the existing
connector and independent-verification seams.  It owns no scheduling,
mission, effect-journal, owner-control, or world-model authority.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Mapping

from .model import (
    CapabilitySpec,
    Kind,
    Semantic,
    UnattendedMode,
    VerifyVerdict,
)


PERSONAL_OPERATOR_ACTIONS = frozenset({
    "file.inspect",
    "file.search",
    "file.copy",
    "file.move",
    "file.rename",
    "file.safe_edit",
    "artifact.table.transform",
    "artifact.xlsx.create",
    "artifact.xlsx.update",
    "artifact.docx.edit",
    "artifact.pdf.create",
    "artifact.pdf.combine",
    "artifact.pdf.split",
    "artifact.pptx.create",
    "artifact.pptx.update",
    "local.system.inspect",
    "local.command.run",
    "local.process.launch",
    "local.process.inspect",
    "local.process.close",
    "desktop.open",
    "desktop.focus",
    "desktop.close",
})

POLICY_OVERRIDE_KEYS = frozenset({
    "artifact_source_roots",
    "environment_allowlist",
    "executable_allowlist",
    "source_root",
    "source_roots",
})


def personal_operator_specs() -> tuple[CapabilitySpec, ...]:
    """Return additive built-ins without claiming observed readiness."""

    allowed = UnattendedMode.ALLOWED
    return (
        CapabilitySpec(
            capability_id="cap.file.inspect", version="1.0.0",
            kind=Kind.CONNECTOR, action_kind="file.inspect",
            semantic=Semantic.READ, executor="personal_operator",
            unattended=allowed,
            limitations=("owned-root files only; bounded metadata and hash readback",),
        ),
        CapabilitySpec(
            capability_id="cap.file.search", version="1.0.0",
            kind=Kind.CONNECTOR, action_kind="file.search",
            semantic=Semantic.READ, executor="personal_operator",
            unattended=allowed,
            limitations=("owned-root bounded name/content search; no indexing authority",),
        ),
        CapabilitySpec(
            capability_id="cap.file.copy", version="1.0.0",
            kind=Kind.CONNECTOR, action_kind="file.copy",
            semantic=Semantic.WRITE, executor="personal_operator",
            unattended=allowed,
            limitations=("owned-root files only; new target; source hash preserved",),
        ),
        CapabilitySpec(
            capability_id="cap.file.move", version="1.0.0",
            kind=Kind.CONNECTOR, action_kind="file.move",
            semantic=Semantic.WRITE, executor="personal_operator",
            unattended=allowed,
            limitations=("owned-root files only; new target; exact source identity",),
        ),
        CapabilitySpec(
            capability_id="cap.file.rename", version="1.0.0",
            kind=Kind.CONNECTOR, action_kind="file.rename",
            semantic=Semantic.WRITE, executor="personal_operator",
            unattended=allowed,
            limitations=("owned-root files only; exact source identity; no overwrite",),
        ),
        CapabilitySpec(
            capability_id="cap.file.safe_edit", version="1.0.0",
            kind=Kind.CONNECTOR, action_kind="file.safe_edit",
            semantic=Semantic.WRITE, executor="personal_operator",
            unattended=allowed,
            limitations=(
                "expected source hash required; original backup preserved by default; "
                "atomic readback",
            ),
        ),
        CapabilitySpec(
            capability_id="cap.artifact.table.transform", version="1.0.0",
            kind=Kind.ADAPTER, action_kind="artifact.table.transform",
            semantic=Semantic.EXECUTE, executor="local_artifacts",
            requirements=("artifact.parse",), unattended=allowed,
            limitations=("bounded deterministic select/filter/compute operations only",),
        ),
        CapabilitySpec(
            capability_id="cap.artifact.xlsx.create", version="1.0.0",
            kind=Kind.ADAPTER, action_kind="artifact.xlsx.create",
            semantic=Semantic.WRITE, executor="local_artifacts",
            requirements=("artifact.parse",), unattended=allowed,
            limitations=(
                "new owned-root workbook; fixed bounded formatting/formulas; "
                "independent reopen",
            ),
        ),
        CapabilitySpec(
            capability_id="cap.artifact.xlsx.update", version="1.0.0",
            kind=Kind.ADAPTER, action_kind="artifact.xlsx.update",
            semantic=Semantic.WRITE, executor="local_artifacts",
            requirements=("artifact.parse",), unattended=allowed,
            limitations=(
                "source-preserving exact cell/range/formula updates; new target only",
            ),
        ),
        CapabilitySpec(
            capability_id="cap.artifact.docx.edit", version="1.0.0",
            kind=Kind.ADAPTER, action_kind="artifact.docx.edit",
            semantic=Semantic.WRITE, executor="local_artifacts",
            requirements=("artifact.parse",), unattended=allowed,
            limitations=(
                "source-preserving bounded text edits; new target; independent reopen",
            ),
        ),
        CapabilitySpec(
            capability_id="cap.artifact.pdf.create", version="1.0.0",
            kind=Kind.ADAPTER, action_kind="artifact.pdf.create",
            semantic=Semantic.WRITE, executor="local_artifacts",
            requirements=("artifact.parse",), unattended=allowed,
            limitations=("new bounded text PDF; embedded source lineage; pypdf readback",),
        ),
        CapabilitySpec(
            capability_id="cap.artifact.pdf.combine", version="1.0.0",
            kind=Kind.ADAPTER, action_kind="artifact.pdf.combine",
            semantic=Semantic.WRITE, executor="local_artifacts",
            unattended=allowed,
            limitations=("registered source roots; bounded pages; new target only",),
        ),
        CapabilitySpec(
            capability_id="cap.artifact.pdf.split", version="1.0.0",
            kind=Kind.ADAPTER, action_kind="artifact.pdf.split",
            semantic=Semantic.WRITE, executor="local_artifacts",
            requirements=("artifact.parse",), unattended=allowed,
            limitations=("bounded inclusive page selection; new target only",),
        ),
        CapabilitySpec(
            capability_id="cap.artifact.pptx.create", version="1.0.0",
            kind=Kind.ADAPTER, action_kind="artifact.pptx.create",
            semantic=Semantic.WRITE, executor="local_artifacts",
            requirements=("artifact.parse",), unattended=allowed,
            limitations=(
                "new bounded presentation with basic layouts/text/owned images; "
                "independent reopen",
            ),
        ),
        CapabilitySpec(
            capability_id="cap.artifact.pptx.update", version="1.0.0",
            kind=Kind.ADAPTER, action_kind="artifact.pptx.update",
            semantic=Semantic.WRITE, executor="local_artifacts",
            requirements=("artifact.parse",), unattended=allowed,
            limitations=(
                "source-preserving bounded text replacement/slide append; new target only",
            ),
        ),
        CapabilitySpec(
            capability_id="cap.local.system.inspect", version="1.0.0",
            kind=Kind.TOOL, action_kind="local.system.inspect",
            semantic=Semantic.READ, executor="personal_operator",
            unattended=allowed,
            limitations=("read-only bounded host/environment/process facts",),
        ),
        CapabilitySpec(
            capability_id="cap.local.command.run", version="1.0.0",
            kind=Kind.TOOL, action_kind="local.command.run",
            semantic=Semantic.EXECUTE, executor="personal_operator",
            unattended=allowed,
            limitations=(
                "no shell; exact executable allow-list; owned cwd; timeout/output bounds",
            ),
        ),
        CapabilitySpec(
            capability_id="cap.local.process.launch", version="1.0.0",
            kind=Kind.TOOL, action_kind="local.process.launch",
            semantic=Semantic.EXECUTE, executor="personal_operator",
            unattended=allowed,
            limitations=(
                "exact executable allow-list; durable PID/create-time/executable identity",
            ),
        ),
        CapabilitySpec(
            capability_id="cap.local.process.inspect", version="1.0.0",
            kind=Kind.TOOL, action_kind="local.process.inspect",
            semantic=Semantic.READ, executor="personal_operator",
            unattended=allowed,
            limitations=("registry-owned exact process identity only",),
        ),
        CapabilitySpec(
            capability_id="cap.local.process.close", version="1.0.0",
            kind=Kind.TOOL, action_kind="local.process.close",
            semantic=Semantic.EXECUTE, executor="personal_operator",
            unattended=allowed,
            limitations=("PID/create-time/executable match required; never kill by name",),
        ),
        CapabilitySpec(
            capability_id="cap.desktop.open", version="1.0.0",
            kind=Kind.TOOL, action_kind="desktop.open",
            semantic=Semantic.EXECUTE, executor="personal_operator",
            unattended=allowed,
            limitations=(
                "explicit allow-listed application and owned target only; no generic RPA",
            ),
        ),
        CapabilitySpec(
            capability_id="cap.desktop.focus", version="1.0.0",
            kind=Kind.TOOL, action_kind="desktop.focus",
            semantic=Semantic.EXECUTE, executor="personal_operator",
            unattended=allowed,
            limitations=("top-level window belonging to exact registry-owned process only",),
        ),
        CapabilitySpec(
            capability_id="cap.desktop.close", version="1.0.0",
            kind=Kind.TOOL, action_kind="desktop.close",
            semantic=Semantic.EXECUTE, executor="personal_operator",
            unattended=allowed,
            limitations=("owned window/process identity only; no application-name close",),
        ),
    )


def dispatch_personal_operator(
    owned_root: Path,
    action_kind: str,
    payload: Mapping[str, Any],
    *,
    executable_allowlist: tuple[str | Path, ...] = (),
    environment_allowlist: tuple[str, ...] = (),
    artifact_source_roots: tuple[str | Path, ...] = (),
) -> dict[str, Any] | None:
    """Dispatch one V13 verb, or return ``None`` for another Fabric owner."""

    if action_kind not in PERSONAL_OPERATOR_ACTIONS:
        return None
    policy_overrides = sorted(POLICY_OVERRIDE_KEYS.intersection(payload))
    if policy_overrides:
        return {
            "claimed": "BLOCKED",
            "action_kind": action_kind,
            "error_code": "policy_override_refused",
            "error": (
                "trusted policy cannot be supplied by an action payload: "
                + ", ".join(policy_overrides)
            ),
        }
    try:
        if action_kind.startswith(("file.", "local.", "desktop.")):
            return _dispatch_local_operator(
                owned_root,
                action_kind,
                payload,
                executable_allowlist=executable_allowlist,
                environment_allowlist=environment_allowlist,
            )
        return _dispatch_artifact(
            owned_root,
            action_kind,
            payload,
            source_roots=artifact_source_roots,
        )
    except _artifact_post_write_error_type() as error:
        return {
            "claimed": "UNKNOWN",
            "action_kind": action_kind,
            "error_code": getattr(error, "error_code", "post_write_failure"),
            "error": str(error),
            **getattr(error, "evidence", {}),
        }
    except _artifact_error_type() as error:
        return {
            "claimed": "BLOCKED",
            "action_kind": action_kind,
            "error_code": getattr(error, "error_code", "artifact_error"),
            "error": str(error),
        }
    except _injected_artifact_failure_type() as error:
        return {
            "claimed": "UNKNOWN",
            "action_kind": action_kind,
            "error_code": "partial_failure",
            "error": str(error),
            **getattr(error, "evidence", {}),
        }
    except (TypeError, ValueError) as error:
        return {
            "claimed": "BLOCKED",
            "action_kind": action_kind,
            "error_code": "invalid_input",
            "error": str(error),
        }


def _operator_connector(
    owned_root: Path,
    *,
    executable_allowlist: tuple[str | Path, ...] = (),
    environment_allowlist: tuple[str, ...] = (),
):
    from ..connectors.personal_operator import PersonalOperatorConnector

    return PersonalOperatorConnector(
        owned_root,
        executable_allowlist=executable_allowlist,
        environment_allowlist=environment_allowlist,
    )


def _artifact_error_type() -> type[ValueError]:
    from ..connectors.local_artifacts import LocalArtifactError

    return LocalArtifactError


def _artifact_post_write_error_type() -> type[ValueError]:
    from ..connectors.local_artifacts import ArtifactPostWriteError

    return ArtifactPostWriteError


def _injected_artifact_failure_type() -> type[RuntimeError]:
    from ..connectors.local_artifacts import InjectedArtifactFailure

    return InjectedArtifactFailure


def _artifact_connector(
    owned_root: Path,
    *,
    source_roots: tuple[str | Path, ...] = (),
):
    from ..connectors.local_artifacts import LocalArtifactConnector

    return LocalArtifactConnector(owned_root, source_roots=source_roots)


def _boolean(
    payload: Mapping[str, Any],
    name: str,
    *,
    default: bool = False,
) -> bool:
    value = payload.get(name, default)
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be boolean")
    return value


def _artifact_parse_options(payload: Mapping[str, Any]) -> dict[str, Any]:
    for name in ("allow_formulas", "allow_external_links"):
        value = payload.get(name, False)
        if not isinstance(value, bool):
            raise ValueError(f"{name} must be boolean")
    return {
        "expected_columns": payload.get("expected_columns"),
        "allow_formulas": payload.get("allow_formulas", False),
        "allow_external_links": payload.get("allow_external_links", False),
        "sheet": payload.get("sheet"),
    }


def _table_digest(parsed: Any) -> str:
    table = parsed.table
    if table is None:
        raise ValueError("table digest requires tabular data")
    encoded = json.dumps(
        {
            "headers": list(table.headers),
            "rows": [list(row) for row in table.rows],
        },
        ensure_ascii=True,
        separators=(",", ":"),
        default=str,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _parsed_table(connector: Any, payload: Mapping[str, Any]) -> Any:
    parsed = connector.parse(
        _required_text(payload, "source"),
        **_artifact_parse_options(payload),
    )
    if any(
        name in payload
        for name in ("select_columns", "filters", "computed_columns")
    ):
        parsed = connector.transform_table(
            parsed,
            select_columns=payload.get("select_columns"),
            filters=payload.get("filters") or (),
            computed_columns=payload.get("computed_columns"),
        )
    return parsed


def _artifact_result(
    action_kind: str,
    receipt: Any,
    *,
    source_ref: str | None = None,
    source_sha256: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    result = {
        "claimed": "COMPLETED",
        "action_kind": action_kind,
        **receipt.as_dict(),
    }
    if source_ref is not None:
        result["source_ref"] = source_ref
    if source_sha256 is not None:
        result["source_sha256"] = source_sha256
    if extra:
        result.update(dict(extra))
    return result


def _dispatch_artifact(
    owned_root: Path,
    action_kind: str,
    payload: Mapping[str, Any],
    *,
    source_roots: tuple[str | Path, ...] = (),
) -> dict[str, Any]:
    connector = _artifact_connector(owned_root, source_roots=source_roots)
    if action_kind == "artifact.table.transform":
        parsed = _parsed_table(connector, payload)
        summary = connector.summarize_table(
            parsed,
            group_by=payload.get("group_by"),
            sum_column=payload.get("sum_column"),
        )
        if parsed.table is None:  # pragma: no cover - connector enforces this
            raise ValueError("table transform did not produce a table")
        return {
            "claimed": "COMPLETED",
            "action_kind": action_kind,
            "source": parsed.source.as_dict(),
            "source_ref": parsed.source.source_ref,
            "source_sha256": parsed.source.sha256,
            "format": parsed.source.format,
            "schema": list(parsed.table.headers),
            "row_count": parsed.table.row_count,
            "column_count": parsed.table.column_count,
            "preview_rows": parsed.table.as_dict()["preview_rows"],
            "summary": summary,
            "table_sha256": _table_digest(parsed),
        }

    target = _required_text(payload, "target")
    idempotency_key = str(
        payload.get("idempotency_key") or f"{action_kind}:{target}"
    )
    if action_kind == "artifact.xlsx.create":
        parsed = _parsed_table(connector, payload)
        receipt = connector.create_xlsx(
            parsed,
            target_ref=target,
            idempotency_key=idempotency_key,
            sheet_name=str(payload.get("sheet_name") or "Data"),
            formulas=payload.get("formulas"),
            fault_after_write=_boolean(payload, "fault_after_write"),
        )
        return _artifact_result(
            action_kind,
            receipt,
            source_ref=parsed.source.source_ref,
            source_sha256=parsed.source.sha256,
            extra={"table_sha256": _table_digest(parsed)},
        )
    if action_kind == "artifact.xlsx.update":
        source = connector.capture(_required_text(payload, "source"))
        receipt = connector.update_xlsx(
            source,
            target_ref=target,
            idempotency_key=idempotency_key,
            sheet_name=_required_text(payload, "sheet_name", "sheet"),
            cells=payload.get("cells"),
            ranges=payload.get("ranges"),
            formulas=payload.get("formulas"),
        )
        return _artifact_result(
            action_kind,
            receipt,
            source_ref=source.source_ref,
            source_sha256=source.sha256,
        )
    if action_kind == "artifact.docx.edit":
        source = connector.capture(_required_text(payload, "source"))
        receipt = connector.edit_docx(
            source,
            target_ref=target,
            idempotency_key=idempotency_key,
            replacements=payload.get("replacements"),
            append_paragraphs=payload.get("append_paragraphs") or (),
        )
        return _artifact_result(
            action_kind,
            receipt,
            source_ref=source.source_ref,
            source_sha256=source.sha256,
        )
    if action_kind == "artifact.pdf.create":
        source_ref = payload.get("source")
        source = connector.capture(str(source_ref)) if source_ref else None
        receipt = connector.create_pdf(
            pages=payload.get("pages") or (),
            target_ref=target,
            idempotency_key=idempotency_key,
            title=str(payload.get("title") or "JARVIS local artifact"),
            source=source,
            fault_after_write=_boolean(payload, "fault_after_write"),
        )
        return _artifact_result(
            action_kind,
            receipt,
            source_ref=source.source_ref if source is not None else None,
            source_sha256=source.sha256 if source is not None else None,
        )
    if action_kind == "artifact.pdf.combine":
        raw_sources = payload.get("sources")
        if (
            isinstance(raw_sources, (str, bytes))
            or not isinstance(raw_sources, (list, tuple))
        ):
            raise ValueError("sources must be a sequence")
        sources = [connector.capture(str(value)) for value in raw_sources]
        receipt = connector.combine_pdfs(
            sources,
            target_ref=target,
            idempotency_key=idempotency_key,
        )
        return _artifact_result(
            action_kind,
            receipt,
            extra={
                "source_refs": [item.source_ref for item in sources],
                "source_sha256s": [item.sha256 for item in sources],
            },
        )
    if action_kind == "artifact.pdf.split":
        source = connector.capture(_required_text(payload, "source"))
        receipt = connector.split_pdf(
            source,
            target_ref=target,
            idempotency_key=idempotency_key,
            start_page=payload.get("start_page"),
            end_page=payload.get("end_page"),
        )
        return _artifact_result(
            action_kind,
            receipt,
            source_ref=source.source_ref,
            source_sha256=source.sha256,
        )
    if action_kind == "artifact.pptx.create":
        source_ref = payload.get("source")
        source = connector.capture(str(source_ref)) if source_ref else None
        receipt = connector.create_pptx(
            slides=payload.get("slides") or (),
            target_ref=target,
            idempotency_key=idempotency_key,
            source=source,
            fault_after_write=_boolean(payload, "fault_after_write"),
        )
        return _artifact_result(
            action_kind,
            receipt,
            source_ref=source.source_ref if source is not None else None,
            source_sha256=source.sha256 if source is not None else None,
        )
    if action_kind == "artifact.pptx.update":
        source = connector.capture(_required_text(payload, "source"))
        receipt = connector.update_pptx(
            source,
            target_ref=target,
            idempotency_key=idempotency_key,
            replacements=payload.get("replacements"),
            append_slides=payload.get("append_slides") or (),
        )
        return _artifact_result(
            action_kind,
            receipt,
            source_ref=source.source_ref,
            source_sha256=source.sha256,
        )
    raise ValueError(f"unsupported artifact action: {action_kind}")


def _claimed_receipt(
    action_kind: str,
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    result = dict(receipt)
    result["claimed"] = str(receipt.get("status") or "UNKNOWN")
    result["action_kind"] = action_kind
    error = receipt.get("error")
    if isinstance(error, Mapping) and error.get("classification"):
        result["error_code"] = str(error["classification"])
    return result


def _required_text(payload: Mapping[str, Any], *names: str) -> str:
    for name in names:
        value = payload.get(name)
        if isinstance(value, str) and value:
            return value
    raise ValueError(f"{names[0]} is required")


def _dispatch_local_operator(
    owned_root: Path,
    action_kind: str,
    payload: Mapping[str, Any],
    *,
    executable_allowlist: tuple[str | Path, ...] = (),
    environment_allowlist: tuple[str, ...] = (),
) -> dict[str, Any]:
    connector = _operator_connector(
        owned_root,
        executable_allowlist=executable_allowlist,
        environment_allowlist=environment_allowlist,
    )
    if action_kind == "file.inspect":
        receipt = connector.inspect_file(
            _required_text(payload, "target", "source", "path"),
            preview_bytes=int(payload.get("preview_bytes") or 0),
        )
    elif action_kind == "file.search":
        receipt = connector.search(
            name_pattern=payload.get("name_pattern"),
            content_query=payload.get("content_query"),
            start=str(payload.get("start") or "."),
            max_results=payload.get("max_results"),
            max_files=payload.get("max_files"),
        )
    elif action_kind in {"file.copy", "file.move", "file.rename"}:
        method = {
            "file.copy": connector.copy_file,
            "file.move": connector.move_file,
            "file.rename": connector.rename_file,
        }[action_kind]
        receipt = method(
            _required_text(payload, "source"),
            _required_text(payload, "target"),
            idempotency_key=_required_text(payload, "idempotency_key"),
        )
    elif action_kind == "file.safe_edit":
        receipt = connector.safe_text_edit(
            _required_text(payload, "target"),
            _required_text(payload, "new_text", "content"),
            expected_source_hash=_required_text(
                payload, "expected_source_hash", "source_sha256",
            ),
            idempotency_key=_required_text(payload, "idempotency_key"),
            preserve_original=_boolean(
                payload, "preserve_original", default=True
            ),
            encoding=str(payload.get("encoding") or "utf-8"),
        )
    elif action_kind == "local.system.inspect":
        system = connector.system_info()
        names = payload.get("environment_names") or ()
        environment = connector.environment_info(names) if names else None
        process_ids = payload.get("process_ids") or ()
        processes = (
            connector.inspect_processes(process_ids) if process_ids else None
        )
        applications = payload.get("applications") or ()
        application_info = (
            connector.inspect_applications(applications)
            if applications
            else None
        )
        claimed = str(system.get("status") or "UNKNOWN")
        observations = (environment, processes, application_info)
        for observation in observations:
            if (
                observation is not None
                and observation.get("status") != "COMPLETED"
            ):
                claimed = str(observation.get("status") or "FAILED")
                break
        result = {
            "claimed": claimed,
            "action_kind": action_kind,
            "system": system,
            "environment": environment,
            "processes": processes,
            "applications": application_info,
            "owned_root": str(owned_root.resolve()),
            "readback_verified": (
                system.get("status") == "COMPLETED"
                and all(
                    observation is None
                    or observation.get("status") == "COMPLETED"
                    for observation in observations
                )
            ),
        }
        if claimed != "COMPLETED":
            failed = next(
                (
                    observation
                    for observation in observations
                    if observation is not None
                    and observation.get("status") != "COMPLETED"
                ),
                system,
            )
            error = failed.get("error") or {}
            result["error_code"] = str(
                error.get("classification") or "system_inspect_failed"
            )
        return result
    elif action_kind == "local.command.run":
        receipt = connector.run_command(
            _required_text(payload, "executable"),
            payload.get("args") or (),
            cwd=str(payload.get("cwd") or "."),
            timeout_seconds=payload.get("timeout_seconds"),
            output_limit_bytes=payload.get("output_limit_bytes"),
        )
    elif action_kind == "local.process.launch":
        receipt = connector.launch_process(
            _required_text(payload, "process_id", "target"),
            _required_text(payload, "executable"),
            payload.get("args") or (),
            cwd=str(payload.get("cwd") or "."),
            idempotency_key=_required_text(payload, "idempotency_key"),
            show_window=_boolean(payload, "show_window"),
        )
    elif action_kind == "local.process.inspect":
        receipt = connector.inspect_process(
            _required_text(payload, "process_id", "target"),
        )
    elif action_kind == "local.process.close":
        receipt = connector.close_process(
            _required_text(payload, "process_id", "target"),
            idempotency_key=_required_text(payload, "idempotency_key"),
            grace_seconds=float(payload.get("grace_seconds") or 2.0),
        )
    elif action_kind == "desktop.open":
        receipt = connector.desktop_open(
            _required_text(payload, "process_id"),
            _required_text(payload, "application", "executable"),
            _required_text(payload, "target", "path"),
            args=payload.get("args") or (),
            cwd=str(payload.get("cwd") or "."),
            idempotency_key=_required_text(payload, "idempotency_key"),
            show_window=_boolean(payload, "show_window", default=True),
        )
    elif action_kind == "desktop.focus":
        receipt = connector.focus_process(
            _required_text(payload, "process_id", "target"),
        )
    elif action_kind == "desktop.close":
        receipt = connector.close_process(
            _required_text(payload, "process_id", "target"),
            idempotency_key=_required_text(payload, "idempotency_key"),
            grace_seconds=float(payload.get("grace_seconds") or 2.0),
        )
    else:  # pragma: no cover - guarded by the action set
        raise ValueError(f"unsupported personal-operator action: {action_kind}")
    return _claimed_receipt(action_kind, receipt)


def verify_personal_operator(
    action_kind: str,
    claimed: Mapping[str, Any],
    owned_root: Path,
    payload: Mapping[str, Any] | None = None,
    *,
    executable_allowlist: tuple[str | Path, ...] = (),
    environment_allowlist: tuple[str, ...] = (),
    artifact_source_roots: tuple[str | Path, ...] = (),
) -> VerifyVerdict | None:
    """Independently verify one V13 claim, or defer to the existing verifier."""

    if action_kind not in PERSONAL_OPERATOR_ACTIONS:
        return None
    if action_kind.startswith(("file.", "local.", "desktop.")):
        return _verify_local_operator(
            action_kind,
            claimed,
            owned_root,
            payload,
            executable_allowlist=executable_allowlist,
            environment_allowlist=environment_allowlist,
        )
    return _verify_artifact(
        action_kind,
        claimed,
        owned_root,
        payload,
        source_roots=artifact_source_roots,
    )


def _owned_path(root: Path, reference: object) -> Path | None:
    if not isinstance(reference, str) or not reference:
        return None
    path = (root / reference).resolve() if not Path(reference).is_absolute() else Path(reference).resolve()
    try:
        path.relative_to(root.resolve())
    except ValueError:
        return None
    return path


def _prefixed_sha256(path: Path) -> str:
    return f"sha256:{hashlib.sha256(path.read_bytes()).hexdigest()}"


def _source_is_current(
    connector: Any,
    source_ref: object,
    expected_sha256: object,
) -> bool:
    if (
        not isinstance(source_ref, str)
        or not source_ref
        or not isinstance(expected_sha256, str)
        or not expected_sha256
    ):
        return False
    try:
        return connector.capture(source_ref).sha256 == expected_sha256
    except Exception:  # noqa: BLE001 - verifier fails closed
        return False


def _output_is_current(
    connector: Any,
    claimed: Mapping[str, Any],
) -> bool:
    target_ref = claimed.get("target_ref")
    output_sha256 = claimed.get("output_sha256")
    if (
        not isinstance(target_ref, str)
        or not target_ref
        or not isinstance(output_sha256, str)
        or not output_sha256
    ):
        return False
    try:
        return connector.capture(target_ref).sha256 == output_sha256
    except Exception:  # noqa: BLE001 - verifier fails closed
        return False


def _slide_required_text(slides: object) -> list[str]:
    from ..connectors.local_artifacts import LocalArtifactConnector

    if (
        not isinstance(slides, (list, tuple))
        or isinstance(slides, (str, bytes))
    ):
        return []
    values: list[str] = []
    for raw_slide in slides:
        if not isinstance(raw_slide, Mapping):
            continue
        title = raw_slide.get("title")
        if isinstance(title, str) and title:
            values.append(title)
        body = raw_slide.get("body")
        values.extend(
            line
            for line in LocalArtifactConnector._pptx_body_lines(body)
            if line
        )
    return values


def _xlsx_payload_matches(
    path: Path,
    *,
    sheet_name: object,
    cells: object = None,
    ranges: object = None,
    formulas: object = None,
) -> bool:
    try:
        from openpyxl import load_workbook
        from openpyxl.utils.cell import range_boundaries

        workbook = load_workbook(
            path, data_only=False, read_only=False, keep_links=False
        )
        try:
            name = str(sheet_name or "Data")
            if name not in workbook.sheetnames:
                return False
            worksheet = workbook[name]
            expected = {}
            for source in (cells or {}, formulas or {}):
                if not isinstance(source, Mapping):
                    return False
                expected.update({str(key): value for key, value in source.items()})
            if ranges:
                if not isinstance(ranges, Mapping):
                    return False
                for range_ref, raw_rows in ranges.items():
                    min_col, min_row, max_col, max_row = range_boundaries(
                        str(range_ref)
                    )
                    rows = list(raw_rows)
                    if len(rows) != max_row - min_row + 1:
                        return False
                    for row_offset, row in enumerate(rows):
                        values = list(row)
                        if len(values) != max_col - min_col + 1:
                            return False
                        for column_offset, value in enumerate(values):
                            coordinate = worksheet.cell(
                                min_row + row_offset,
                                min_col + column_offset,
                            ).coordinate
                            expected[coordinate] = value
            return all(
                worksheet[str(coordinate)].value == expected_value
                for coordinate, expected_value in expected.items()
            )
        finally:
            workbook.close()
    except Exception:  # noqa: BLE001 - verifier fails closed
        return False


def _xlsx_created_from_table(
    path: Path,
    parsed: Any,
    *,
    sheet_name: object,
    formulas: object,
) -> bool:
    if parsed.table is None:
        return False
    try:
        from openpyxl import load_workbook
        from openpyxl.utils import get_column_letter
        from openpyxl.utils.cell import coordinate_to_tuple

        formula_map = dict(formulas or {})
        workbook = load_workbook(
            path,
            data_only=False,
            read_only=False,
            keep_links=False,
        )
        try:
            name = str(sheet_name or "Data")
            if workbook.sheetnames != [name]:
                return False
            worksheet = workbook[name]
            expected: dict[str, Any] = {}
            literal_formula_like: set[str] = set()
            for row_index, row in enumerate(
                (parsed.table.headers, *parsed.table.rows),
                start=1,
            ):
                for column_index, value in enumerate(row, start=1):
                    coordinate = f"{get_column_letter(column_index)}{row_index}"
                    expected[coordinate] = value
                    if isinstance(value, str) and value.startswith("="):
                        literal_formula_like.add(coordinate)
            explicit_formulas: set[str] = set()
            for coordinate, formula in formula_map.items():
                if (
                    not isinstance(coordinate, str)
                    or not isinstance(formula, str)
                    or not formula.startswith("=")
                ):
                    return False
                coordinate_to_tuple(coordinate)
                normalized = coordinate.upper()
                expected[normalized] = formula
                explicit_formulas.add(normalized)
            for coordinate, expected_value in expected.items():
                cell = worksheet[coordinate]
                if cell.value != expected_value:
                    return False
                if coordinate in literal_formula_like and cell.data_type == "f":
                    return False
                if coordinate in explicit_formulas and cell.data_type != "f":
                    return False
            actual_nonempty = {
                cell.coordinate
                for row in worksheet.iter_rows()
                for cell in row
                if cell.value is not None
            }
            expected_nonempty = {
                coordinate
                for coordinate, value in expected.items()
                if value is not None
            }
            if actual_nonempty != expected_nonempty:
                return False
            max_row = max(
                (coordinate_to_tuple(value)[0] for value in expected),
                default=1,
            )
            max_column = max(
                (coordinate_to_tuple(value)[1] for value in expected),
                default=1,
            )
            expected_filter = (
                f"A1:{get_column_letter(max_column)}{max_row}"
            )
            header_styled = all(
                worksheet.cell(1, column).font.bold
                and worksheet.cell(1, column).fill.fill_type == "solid"
                for column in range(1, max_column + 1)
            )
            return (
                worksheet.freeze_panes == "A2"
                and worksheet.auto_filter.ref == expected_filter
                and header_styled
            )
        finally:
            workbook.close()
    except Exception:  # noqa: BLE001 - verifier fails closed
        return False


def _xlsx_updated_from_source(
    source_path: Path,
    target_path: Path,
    *,
    sheet_name: object,
    cells: object,
    ranges: object,
    formulas: object,
) -> bool:
    try:
        from openpyxl import load_workbook
        from openpyxl.utils import get_column_letter
        from openpyxl.utils.cell import coordinate_to_tuple, range_boundaries

        source_book = load_workbook(
            source_path,
            data_only=False,
            read_only=False,
            keep_links=False,
        )
        target_book = load_workbook(
            target_path,
            data_only=False,
            read_only=False,
            keep_links=False,
        )
        try:
            if source_book.sheetnames != target_book.sheetnames:
                return False
            name = str(sheet_name or "")
            if name not in source_book.sheetnames:
                return False
            expected_updates: dict[str, tuple[Any, bool]] = {}
            if not isinstance(cells or {}, Mapping):
                return False
            for coordinate, value in (cells or {}).items():
                if not isinstance(coordinate, str) or ":" in coordinate:
                    return False
                row, column = coordinate_to_tuple(coordinate)
                normalized = f"{get_column_letter(column)}{row}"
                expected_updates[normalized] = (value, False)
            if not isinstance(ranges or {}, Mapping):
                return False
            for range_ref, raw_rows in (ranges or {}).items():
                min_col, min_row, max_col, max_row = range_boundaries(
                    str(range_ref)
                )
                rows = [list(row) for row in raw_rows]
                if (
                    len(rows) != max_row - min_row + 1
                    or any(
                        len(row) != max_col - min_col + 1
                        for row in rows
                    )
                ):
                    return False
                for row_offset, row in enumerate(rows):
                    for column_offset, value in enumerate(row):
                        coordinate = (
                            f"{get_column_letter(min_col + column_offset)}"
                            f"{min_row + row_offset}"
                        )
                        if coordinate in expected_updates:
                            return False
                        expected_updates[coordinate] = (value, False)
            if not isinstance(formulas or {}, Mapping):
                return False
            for coordinate, formula in (formulas or {}).items():
                if (
                    not isinstance(coordinate, str)
                    or ":" in coordinate
                    or not isinstance(formula, str)
                    or not formula.startswith("=")
                ):
                    return False
                row, column = coordinate_to_tuple(coordinate)
                normalized = f"{get_column_letter(column)}{row}"
                if normalized in expected_updates:
                    return False
                expected_updates[normalized] = (formula, True)

            for source_sheet, target_sheet in zip(
                source_book.worksheets,
                target_book.worksheets,
            ):
                max_row = max(source_sheet.max_row, target_sheet.max_row)
                max_column = max(
                    source_sheet.max_column,
                    target_sheet.max_column,
                )
                if source_sheet.title == name:
                    for coordinate in expected_updates:
                        row, column = coordinate_to_tuple(coordinate)
                        max_row = max(max_row, row)
                        max_column = max(max_column, column)
                for row in range(1, max_row + 1):
                    for column in range(1, max_column + 1):
                        coordinate = f"{get_column_letter(column)}{row}"
                        expected_value = source_sheet[coordinate].value
                        explicit_formula = False
                        if (
                            source_sheet.title == name
                            and coordinate in expected_updates
                        ):
                            expected_value, explicit_formula = expected_updates[
                                coordinate
                            ]
                        actual = target_sheet[coordinate]
                        if actual.value != expected_value:
                            return False
                        if (
                            source_sheet.title == name
                            and coordinate in expected_updates
                        ):
                            if explicit_formula and actual.data_type != "f":
                                return False
                            if (
                                not explicit_formula
                                and isinstance(expected_value, str)
                                and expected_value.startswith("=")
                                and actual.data_type == "f"
                            ):
                                return False
            return True
        finally:
            source_book.close()
            target_book.close()
    except Exception:  # noqa: BLE001 - verifier fails closed
        return False


def _docx_updated_from_source(
    source_path: Path,
    target_path: Path,
    *,
    replacements: object,
    append_paragraphs: object,
) -> bool:
    try:
        from docx import Document

        replace_map = dict(replacements or {})
        appended = list(append_paragraphs or ())
        source = Document(source_path)
        target = Document(target_path)

        def transformed(value: str) -> str:
            for old in sorted(replace_map):
                value = value.replace(str(old), str(replace_map[old]))
            return value

        expected_paragraphs = [
            transformed(paragraph.text) for paragraph in source.paragraphs
        ] + [str(value) for value in appended]
        actual_paragraphs = [
            paragraph.text for paragraph in target.paragraphs
        ]
        if actual_paragraphs != expected_paragraphs:
            return False
        if len(source.tables) != len(target.tables):
            return False
        for source_table, target_table in zip(source.tables, target.tables):
            if len(source_table.rows) != len(target_table.rows):
                return False
            for source_row, target_row in zip(
                source_table.rows,
                target_table.rows,
            ):
                expected_cells = [
                    transformed(cell.text) for cell in source_row.cells
                ]
                actual_cells = [cell.text for cell in target_row.cells]
                if actual_cells != expected_cells:
                    return False
        return True
    except Exception:  # noqa: BLE001 - verifier fails closed
        return False


def _pdf_pages(path: Path) -> list[str] | None:
    try:
        from pypdf import PdfReader

        return [
            page.extract_text() or ""
            for page in PdfReader(path, strict=False).pages
        ]
    except Exception:  # noqa: BLE001 - verifier fails closed
        return None


def _pptx_text_by_slide(path: Path) -> tuple[list[list[str]], int] | None:
    try:
        from pptx import Presentation
        from pptx.enum.shapes import MSO_SHAPE_TYPE

        presentation = Presentation(path)
        slides: list[list[str]] = []
        pictures = 0
        for slide in presentation.slides:
            slide_text: list[str] = []
            for shape in slide.shapes:
                if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                    pictures += 1
                if hasattr(shape, "text"):
                    slide_text.append(str(shape.text))
            slides.append(slide_text)
        return slides, pictures
    except Exception:  # noqa: BLE001 - verifier fails closed
        return None


def _verify_artifact(
    action_kind: str,
    claimed: Mapping[str, Any],
    owned_root: Path,
    payload: Mapping[str, Any] | None,
    *,
    source_roots: tuple[str | Path, ...] = (),
) -> VerifyVerdict:
    if str(claimed.get("claimed") or "UNKNOWN") != "COMPLETED":
        return VerifyVerdict.UNKNOWN
    payload = payload or {}
    if POLICY_OVERRIDE_KEYS.intersection(payload):
        return VerifyVerdict.MISMATCH
    connector = _artifact_connector(owned_root, source_roots=source_roots)

    if action_kind == "artifact.table.transform":
        try:
            parsed = _parsed_table(connector, payload)
            summary = connector.summarize_table(
                parsed,
                group_by=payload.get("group_by"),
                sum_column=payload.get("sum_column"),
            )
        except Exception:  # noqa: BLE001 - verifier fails closed
            return VerifyVerdict.MISMATCH
        matches = (
            parsed.table is not None
            and parsed.source.source_ref == claimed.get("source_ref")
            and parsed.source.sha256 == claimed.get("source_sha256")
            and list(parsed.table.headers) == claimed.get("schema")
            and parsed.table.row_count == claimed.get("row_count")
            and parsed.table.column_count == claimed.get("column_count")
            and _table_digest(parsed) == claimed.get("table_sha256")
            and summary == claimed.get("summary")
        )
        return VerifyVerdict.MATCH if matches else VerifyVerdict.MISMATCH

    if not _output_is_current(connector, claimed):
        return VerifyVerdict.MISMATCH
    if claimed.get("readback_verified") is not True:
        return VerifyVerdict.MISMATCH

    source_ref = claimed.get("source_ref")
    source_sha256 = claimed.get("source_sha256")
    requested_source = payload.get("source")
    if action_kind != "artifact.pdf.combine":
        if requested_source is None:
            if source_ref is not None or source_sha256 is not None:
                return VerifyVerdict.MISMATCH
        else:
            try:
                requested_receipt = connector.capture(str(requested_source))
            except Exception:  # noqa: BLE001 - verifier fails closed
                return VerifyVerdict.MISMATCH
            if (
                source_ref != requested_receipt.source_ref
                or source_sha256 != requested_receipt.sha256
            ):
                return VerifyVerdict.MISMATCH
            if not _source_is_current(
                connector,
                requested_receipt.source_ref,
                requested_receipt.sha256,
            ):
                return VerifyVerdict.MISMATCH

    target_ref = str(claimed.get("target_ref"))
    output_sha256 = str(claimed.get("output_sha256"))
    readback = claimed.get("readback")
    readback = readback if isinstance(readback, Mapping) else {}

    if action_kind == "artifact.xlsx.create":
        target_path = _owned_path(owned_root, target_ref)
        if target_path is None:
            return VerifyVerdict.MISMATCH
        try:
            parsed = _parsed_table(connector, payload)
        except Exception:  # noqa: BLE001 - verifier fails closed
            return VerifyVerdict.MISMATCH
        matches = (
            parsed.source.source_ref == source_ref
            and parsed.source.sha256 == source_sha256
            and
            _table_digest(parsed) == claimed.get("table_sha256")
            and _xlsx_created_from_table(
                target_path,
                parsed,
                sheet_name=payload.get("sheet_name") or "Data",
                formulas=payload.get("formulas"),
            )
            and readback.get("verified") is True
        )
        return VerifyVerdict.MATCH if matches else VerifyVerdict.MISMATCH

    if action_kind == "artifact.xlsx.update":
        target_path = _owned_path(owned_root, target_ref)
        if target_path is None or requested_source is None:
            return VerifyVerdict.MISMATCH
        source_path = Path(requested_receipt.path)
        return (
            VerifyVerdict.MATCH
            if _xlsx_updated_from_source(
                source_path,
                target_path,
                sheet_name=payload.get("sheet_name") or payload.get("sheet"),
                cells=payload.get("cells"),
                ranges=payload.get("ranges"),
                formulas=payload.get("formulas"),
            )
            else VerifyVerdict.MISMATCH
        )

    if action_kind == "artifact.docx.edit":
        target_path = _owned_path(owned_root, target_ref)
        if (
            target_path is None
            or requested_source is None
            or not _docx_updated_from_source(
                Path(requested_receipt.path),
                target_path,
                replacements=payload.get("replacements"),
                append_paragraphs=payload.get("append_paragraphs"),
            )
        ):
            return VerifyVerdict.MISMATCH
        return VerifyVerdict.MATCH

    if action_kind.startswith("artifact.pdf."):
        target_path = _owned_path(owned_root, target_ref)
        if target_path is None:
            return VerifyVerdict.MISMATCH
        expected_page_text: list[str] | None = None
        if action_kind == "artifact.pdf.combine":
            raw_sources = payload.get("sources")
            if (
                isinstance(raw_sources, (str, bytes))
                or not isinstance(raw_sources, (list, tuple))
                or not raw_sources
            ):
                return VerifyVerdict.MISMATCH
            try:
                source_receipts = [
                    connector.capture(str(value))
                    for value in raw_sources
                ]
            except Exception:  # noqa: BLE001 - verifier fails closed
                return VerifyVerdict.MISMATCH
            refs = claimed.get("source_refs")
            hashes = claimed.get("source_sha256s")
            if (
                refs
                != [receipt.source_ref for receipt in source_receipts]
                or hashes
                != [receipt.sha256 for receipt in source_receipts]
            ):
                return VerifyVerdict.MISMATCH
            expected_page_text = []
            for receipt in source_receipts:
                pages = _pdf_pages(Path(receipt.path))
                if pages is None:
                    return VerifyVerdict.MISMATCH
                expected_page_text.extend(pages)
        required: list[str] = []
        expected_pages: int
        if action_kind == "artifact.pdf.create":
            required.append(
                str(payload.get("title") or "JARVIS local artifact")
            )
            required.extend(
                str(page)[:80] for page in payload.get("pages") or () if page
            )
            if source_sha256:
                required.append(str(source_sha256))
            expected_pages = len(payload.get("pages") or ())
        elif action_kind == "artifact.pdf.split":
            source_pages = _pdf_pages(Path(requested_receipt.path))
            start_page = payload.get("start_page")
            end_page = payload.get("end_page")
            if (
                source_pages is None
                or not isinstance(start_page, int)
                or isinstance(start_page, bool)
                or not isinstance(end_page, int)
                or isinstance(end_page, bool)
                or start_page < 1
                or end_page < start_page
                or end_page > len(source_pages)
            ):
                return VerifyVerdict.MISMATCH
            expected_page_text = source_pages[start_page - 1:end_page]
            expected_pages = len(expected_page_text)
        else:
            if expected_page_text is None:
                return VerifyVerdict.MISMATCH
            expected_pages = len(expected_page_text)
        try:
            observed = connector.verify_pdf(
                target_ref,
                expected_sha256=output_sha256,
                expected_page_count=expected_pages,
                required_text=required,
            )
        except Exception:  # noqa: BLE001 - verifier fails closed
            return VerifyVerdict.MISMATCH
        matches = (
            observed.get("verified") is True
            and (
                expected_page_text is None
                or _pdf_pages(target_path) == expected_page_text
            )
            and (
                not readback.get("text_sha256")
                or observed.get("text_sha256") == readback.get("text_sha256")
            )
        )
        return VerifyVerdict.MATCH if matches else VerifyVerdict.MISMATCH

    if action_kind.startswith("artifact.pptx."):
        target_path = _owned_path(owned_root, target_ref)
        if target_path is None:
            return VerifyVerdict.MISMATCH
        required = []
        expected_slides: int
        expected_images: int
        expected_source = None
        if action_kind == "artifact.pptx.create":
            slide_specs = payload.get("slides") or ()
            required = _slide_required_text(slide_specs)
            expected_slides = len(slide_specs)
            expected_images = sum(
                1
                for spec in slide_specs
                if isinstance(spec, Mapping) and spec.get("image_ref")
            )
            expected_source = source_sha256
        else:
            source_state = _pptx_text_by_slide(Path(requested_receipt.path))
            target_state = _pptx_text_by_slide(target_path)
            if source_state is None or target_state is None:
                return VerifyVerdict.MISMATCH
            source_slides, source_images = source_state
            target_slides, target_images = target_state
            append_slides = payload.get("append_slides") or ()
            replace_map = dict(payload.get("replacements") or {})
            expected_existing: list[list[str]] = []
            for slide in source_slides:
                expected_slide: list[str] = []
                for text in slide:
                    for old in sorted(replace_map):
                        text = text.replace(
                            str(old),
                            str(replace_map[old]),
                        )
                    expected_slide.append(text)
                expected_existing.append(expected_slide)
            if target_slides[:len(source_slides)] != expected_existing:
                return VerifyVerdict.MISMATCH
            expected_slides = len(source_slides) + len(append_slides)
            expected_images = source_images + sum(
                1
                for spec in append_slides
                if isinstance(spec, Mapping) and spec.get("image_ref")
            )
            if (
                len(target_slides) != expected_slides
                or target_images != expected_images
            ):
                return VerifyVerdict.MISMATCH
            required = [
                str(value)
                for value in replace_map.values()
            ]
            required.extend(
                _slide_required_text(append_slides)
            )
        try:
            observed = connector.verify_pptx(
                target_ref,
                expected_sha256=output_sha256,
                expected_slide_count=expected_slides,
                required_text=required,
                expected_image_count=expected_images,
                expected_source_sha256=(
                    str(expected_source) if expected_source else None
                ),
            )
        except Exception:  # noqa: BLE001 - verifier fails closed
            return VerifyVerdict.MISMATCH
        return (
            VerifyVerdict.MATCH
            if observed.get("verified")
            else VerifyVerdict.MISMATCH
        )

    return VerifyVerdict.UNKNOWN


def _verify_local_operator(
    action_kind: str,
    claimed: Mapping[str, Any],
    owned_root: Path,
    payload: Mapping[str, Any] | None,
    *,
    executable_allowlist: tuple[str | Path, ...] = (),
    environment_allowlist: tuple[str, ...] = (),
) -> VerifyVerdict:
    if str(claimed.get("claimed") or "UNKNOWN") != "COMPLETED":
        return VerifyVerdict.UNKNOWN
    payload = payload or {}
    target_identity = claimed.get("target_identity")
    source_identity = claimed.get("source_identity")
    readback = claimed.get("readback")

    if action_kind == "file.inspect":
        if not isinstance(target_identity, Mapping):
            return VerifyVerdict.MISMATCH
        path = _owned_path(owned_root, target_identity.get("ref"))
        if path is None:
            return VerifyVerdict.MISMATCH
        if target_identity.get("kind") == "file":
            return (
                VerifyVerdict.MATCH
                if path.is_file()
                and _prefixed_sha256(path) == target_identity.get("sha256")
                else VerifyVerdict.MISMATCH
            )
        if target_identity.get("kind") == "directory":
            return VerifyVerdict.MATCH if path.is_dir() else VerifyVerdict.MISMATCH
        return VerifyVerdict.MATCH if not path.exists() else VerifyVerdict.MISMATCH

    if action_kind == "file.search":
        if not isinstance(readback, Mapping):
            return VerifyVerdict.MISMATCH
        matches = (claimed.get("result") or {}).get("matches") or []
        for match in matches:
            identity = match.get("identity") if isinstance(match, Mapping) else None
            path = _owned_path(
                owned_root, identity.get("ref") if isinstance(identity, Mapping) else None
            )
            if path is None or not path.is_file():
                return VerifyVerdict.MISMATCH
        return (
            VerifyVerdict.MATCH
            if int(readback.get("match_count") or 0) == len(matches)
            else VerifyVerdict.MISMATCH
        )

    if action_kind in {"file.copy", "file.move", "file.rename", "file.safe_edit"}:
        if not isinstance(target_identity, Mapping):
            return VerifyVerdict.MISMATCH
        target = _owned_path(owned_root, target_identity.get("ref"))
        if (
            target is None
            or not target.is_file()
            or _prefixed_sha256(target) != target_identity.get("sha256")
        ):
            return VerifyVerdict.MISMATCH
        if action_kind == "file.copy":
            if not isinstance(source_identity, Mapping):
                return VerifyVerdict.MISMATCH
            source = _owned_path(owned_root, source_identity.get("ref"))
            return (
                VerifyVerdict.MATCH
                if source is not None
                and source.is_file()
                and _prefixed_sha256(source) == target_identity.get("sha256")
                else VerifyVerdict.MISMATCH
            )
        if action_kind in {"file.move", "file.rename"}:
            if not isinstance(source_identity, Mapping):
                return VerifyVerdict.MISMATCH
            source = _owned_path(owned_root, source_identity.get("ref"))
            return (
                VerifyVerdict.MATCH
                if source is not None and not source.exists()
                else VerifyVerdict.MISMATCH
            )
        result = claimed.get("result") or {}
        backup = _owned_path(owned_root, result.get("backup_ref"))
        expected_source = payload.get("expected_source_hash") or payload.get(
            "source_sha256"
        )
        return (
            VerifyVerdict.MATCH
            if backup is not None
            and backup.is_file()
            and _prefixed_sha256(backup) == expected_source
            else VerifyVerdict.MISMATCH
        )

    if action_kind == "local.system.inspect":
        system = claimed.get("system") or {}
        result = system.get("result") if isinstance(system, Mapping) else None
        if (
            claimed.get("readback_verified") is not True
            or not isinstance(result, Mapping)
            or result.get("owned_root") != str(owned_root.resolve())
            or not owned_root.is_dir()
        ):
            return VerifyVerdict.MISMATCH
        try:
            connector = _operator_connector(
                owned_root,
                executable_allowlist=executable_allowlist,
                environment_allowlist=environment_allowlist,
            )
            if payload.get("environment_names"):
                observed = connector.environment_info(
                    payload.get("environment_names") or ()
                )
                expected = claimed.get("environment") or {}
                if observed.get("result") != expected.get("result"):
                    return VerifyVerdict.MISMATCH
            if payload.get("process_ids"):
                observed = connector.inspect_processes(
                    payload.get("process_ids") or ()
                )
                expected = claimed.get("processes") or {}
                if observed.get("result") != expected.get("result"):
                    return VerifyVerdict.MISMATCH
            if payload.get("applications"):
                observed = connector.inspect_applications(
                    payload.get("applications") or ()
                )
                expected = claimed.get("applications") or {}
                if observed.get("result") != expected.get("result"):
                    return VerifyVerdict.MISMATCH
        except Exception:  # noqa: BLE001 - verifier fails closed
            return VerifyVerdict.MISMATCH
        return VerifyVerdict.MATCH

    if action_kind == "local.command.run":
        result = claimed.get("result") or {}
        return (
            VerifyVerdict.MATCH
            if result.get("exit_code") == 0
            and isinstance(readback, Mapping)
            and readback.get("alive") is False
            else VerifyVerdict.MISMATCH
        )

    if action_kind in {
        "local.process.launch", "local.process.inspect",
        "desktop.open", "desktop.focus",
    }:
        process_id = (
            (claimed.get("result") or {}).get("process_id")
            or payload.get("process_id")
            or payload.get("target")
        )
        try:
            connector = _operator_connector(
                owned_root,
                executable_allowlist=executable_allowlist,
                environment_allowlist=environment_allowlist,
            )
            observed = connector.inspect_process(str(process_id))
        except Exception:  # noqa: BLE001 - verifier fails closed
            return VerifyVerdict.MISMATCH
        running = (
            observed.get("status") == "COMPLETED"
            and (observed.get("result") or {}).get("state") == "running"
            and (observed.get("result") or {}).get("matches") is True
        )
        if not running:
            return VerifyVerdict.MISMATCH
        if action_kind == "desktop.focus":
            return (
                VerifyVerdict.MATCH
                if (claimed.get("result") or {}).get("focused") is True
                and isinstance(readback, Mapping)
                and readback.get("window_handle")
                and readback.get("foreground_window") == readback.get("window_handle")
                else VerifyVerdict.MISMATCH
            )
        return VerifyVerdict.MATCH

    if action_kind in {"local.process.close", "desktop.close"}:
        process_id = (
            (claimed.get("result") or {}).get("process_id")
            or payload.get("process_id")
            or payload.get("target")
        )
        try:
            connector = _operator_connector(
                owned_root,
                executable_allowlist=executable_allowlist,
                environment_allowlist=environment_allowlist,
            )
            observed = connector.inspect_process(str(process_id))
        except Exception:  # noqa: BLE001 - verifier fails closed
            return VerifyVerdict.MISMATCH
        return (
            VerifyVerdict.MATCH
            if observed.get("status") == "COMPLETED"
            and (observed.get("result") or {}).get("state") == "exited"
            and (observed.get("readback") or {}).get("alive") is False
            else VerifyVerdict.MISMATCH
        )

    return VerifyVerdict.UNKNOWN


__all__ = [
    "PERSONAL_OPERATOR_ACTIONS",
    "dispatch_personal_operator",
    "personal_operator_specs",
    "verify_personal_operator",
]
