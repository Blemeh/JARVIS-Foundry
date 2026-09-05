"""General Work capability adapters for the existing Fabric spine."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ..connectors.general_work_files import GeneralWorkFileConnector
from ..connectors.local_artifacts import (
    ArtifactPostWriteError,
    ArtifactValidationError,
    InjectedArtifactFailure,
    LocalArtifactConnector,
    LocalArtifactError,
)
from ..general_work.ledger import load_recorded_plan
from ..general_work.model import GENERAL_WORK_ACTION_KINDS
from ..general_work.verification import verify_recorded_package
from .model import (
    CapabilitySpec,
    Kind,
    Semantic,
    UnattendedMode,
    VerifyVerdict,
)


FILE_ACTIONS = frozenset(
    {
        "file.inspect",
        "file.search",
        "file.copy",
        "file.move",
        "file.rename",
    }
)
FILE_TRANSFER_ACTIONS = frozenset(
    {"file.copy", "file.move", "file.rename"}
)
ARTIFACT_ACTIONS = frozenset(
    {
        "artifact.table.merge",
        "artifact.table.transform",
        "artifact.xlsx.create",
        "artifact.xlsx.update",
        "artifact.docx.compose",
        "artifact.docx.edit",
        "artifact.pdf.create",
        "artifact.pdf.combine",
        "artifact.pdf.split",
        "artifact.pptx.create",
        "artifact.pptx.update",
    }
)
VERIFY_ACTIONS = frozenset({"general_work.verify"})
GENERAL_WORK_ACTIONS = FILE_ACTIONS | ARTIFACT_ACTIONS | VERIFY_ACTIONS
RECONCILABLE_ACTIONS = ARTIFACT_ACTIONS | FILE_TRANSFER_ACTIONS
_SOURCE_ROOT_KEYS = frozenset({"source_root", "source_roots"})


def general_work_specs() -> tuple[CapabilitySpec, ...]:
    common_file_limits = (
        "owned resource root only",
        "symbolic links refused",
        "bounded files and readback required",
    )
    common_artifact_limits = (
        "new owned-root output target only",
        "bounded parser and deterministic readback",
        "external content is DATA_ONLY",
    )
    return (
        CapabilitySpec(
            capability_id="cap.file.inspect",
            version="1.0.0",
            kind=Kind.CONNECTOR,
            action_kind="file.inspect",
            semantic=Semantic.READ,
            executor="general_work_files",
            unattended=UnattendedMode.ALLOWED,
            limitations=common_file_limits,
        ),
        CapabilitySpec(
            capability_id="cap.file.search",
            version="1.0.0",
            kind=Kind.CONNECTOR,
            action_kind="file.search",
            semantic=Semantic.READ,
            executor="general_work_files",
            unattended=UnattendedMode.ALLOWED,
            limitations=common_file_limits,
        ),
        CapabilitySpec(
            capability_id="cap.file.copy",
            version="1.0.0",
            kind=Kind.CONNECTOR,
            action_kind="file.copy",
            semantic=Semantic.WRITE,
            executor="general_work_files",
            unattended=UnattendedMode.ALLOWED,
            limitations=common_file_limits + ("no overwrite",),
        ),
        CapabilitySpec(
            capability_id="cap.file.move",
            version="1.0.0",
            kind=Kind.CONNECTOR,
            action_kind="file.move",
            semantic=Semantic.WRITE,
            executor="general_work_files",
            unattended=UnattendedMode.ALLOWED,
            limitations=common_file_limits + ("no overwrite",),
        ),
        CapabilitySpec(
            capability_id="cap.file.rename",
            version="1.0.0",
            kind=Kind.CONNECTOR,
            action_kind="file.rename",
            semantic=Semantic.WRITE,
            executor="general_work_files",
            unattended=UnattendedMode.ALLOWED,
            limitations=common_file_limits + ("no overwrite",),
        ),
        CapabilitySpec(
            capability_id="cap.artifact.table.merge",
            version="1.0.0",
            kind=Kind.ADAPTER,
            action_kind="artifact.table.merge",
            semantic=Semantic.WRITE,
            executor="general_work",
            requirements=("artifact.parse",),
            unattended=UnattendedMode.ALLOWED,
            limitations=(
                "bounded deterministic multi-source table composition only",
                "new owned-root output target only",
            ),
        ),
        CapabilitySpec(
            capability_id="cap.artifact.table.transform",
            version="1.0.0",
            kind=Kind.ADAPTER,
            action_kind="artifact.table.transform",
            semantic=Semantic.EXECUTE,
            executor="general_work",
            requirements=("artifact.parse",),
            unattended=UnattendedMode.ALLOWED,
            limitations=("bounded deterministic projection and calculation only",),
        ),
        CapabilitySpec(
            capability_id="cap.artifact.xlsx.create",
            version="1.0.0",
            kind=Kind.ADAPTER,
            action_kind="artifact.xlsx.create",
            semantic=Semantic.WRITE,
            executor="general_work",
            requirements=("artifact.parse",),
            unattended=UnattendedMode.ALLOWED,
            limitations=common_artifact_limits,
        ),
        CapabilitySpec(
            capability_id="cap.artifact.xlsx.update",
            version="1.0.0",
            kind=Kind.ADAPTER,
            action_kind="artifact.xlsx.update",
            semantic=Semantic.WRITE,
            executor="general_work",
            requirements=("artifact.parse",),
            unattended=UnattendedMode.ALLOWED,
            limitations=common_artifact_limits,
        ),
        CapabilitySpec(
            capability_id="cap.artifact.docx.compose",
            version="1.0.0",
            kind=Kind.ADAPTER,
            action_kind="artifact.docx.compose",
            semantic=Semantic.WRITE,
            executor="general_work",
            requirements=("artifact.parse",),
            unattended=UnattendedMode.ALLOWED,
            limitations=common_artifact_limits
            + ("bounded structured document sections and tables only",),
        ),
        CapabilitySpec(
            capability_id="cap.artifact.docx.edit",
            version="1.0.0",
            kind=Kind.ADAPTER,
            action_kind="artifact.docx.edit",
            semantic=Semantic.WRITE,
            executor="general_work",
            requirements=("artifact.parse",),
            unattended=UnattendedMode.ALLOWED,
            limitations=common_artifact_limits,
        ),
        CapabilitySpec(
            capability_id="cap.artifact.pdf.create",
            version="1.0.0",
            kind=Kind.ADAPTER,
            action_kind="artifact.pdf.create",
            semantic=Semantic.WRITE,
            executor="general_work",
            unattended=UnattendedMode.ALLOWED,
            limitations=common_artifact_limits,
        ),
        CapabilitySpec(
            capability_id="cap.artifact.pdf.combine",
            version="1.0.0",
            kind=Kind.ADAPTER,
            action_kind="artifact.pdf.combine",
            semantic=Semantic.WRITE,
            executor="general_work",
            requirements=("artifact.parse",),
            unattended=UnattendedMode.ALLOWED,
            limitations=common_artifact_limits,
        ),
        CapabilitySpec(
            capability_id="cap.artifact.pdf.split",
            version="1.0.0",
            kind=Kind.ADAPTER,
            action_kind="artifact.pdf.split",
            semantic=Semantic.WRITE,
            executor="general_work",
            requirements=("artifact.parse",),
            unattended=UnattendedMode.ALLOWED,
            limitations=common_artifact_limits,
        ),
        CapabilitySpec(
            capability_id="cap.artifact.pptx.create",
            version="1.0.0",
            kind=Kind.ADAPTER,
            action_kind="artifact.pptx.create",
            semantic=Semantic.WRITE,
            executor="general_work",
            unattended=UnattendedMode.ALLOWED,
            limitations=common_artifact_limits,
        ),
        CapabilitySpec(
            capability_id="cap.artifact.pptx.update",
            version="1.0.0",
            kind=Kind.ADAPTER,
            action_kind="artifact.pptx.update",
            semantic=Semantic.WRITE,
            executor="general_work",
            requirements=("artifact.parse",),
            unattended=UnattendedMode.ALLOWED,
            limitations=common_artifact_limits,
        ),
        CapabilitySpec(
            capability_id="cap.general_work.verify",
            version="1.0.0",
            kind=Kind.WORKFLOW,
            action_kind="general_work.verify",
            semantic=Semantic.READ,
            executor="general_work",
            unattended=UnattendedMode.ALLOWED,
            limitations=(
                "recorded immutable plan only",
                "read-only source and output reinspection",
                "no execution or repair authority",
            ),
        ),
    )


def _reject_payload_roots(payload: Mapping[str, Any]) -> None:
    supplied = sorted(_SOURCE_ROOT_KEYS.intersection(payload))
    if supplied:
        raise ArtifactValidationError(
            "source roots are trusted ExecutorContext policy, not action payload"
        )


def artifact_connector(ctx: Any, payload: Mapping[str, Any]) -> LocalArtifactConnector:
    _reject_payload_roots(payload)
    roots = tuple(getattr(ctx, "artifact_source_roots", ()) or ())
    repair_existing = payload.get("repair_existing", False)
    if not isinstance(repair_existing, bool):
        raise ArtifactValidationError("repair_existing must be boolean")
    return LocalArtifactConnector(
        ctx.owned_root,
        source_roots=roots,
        repair_existing=repair_existing,
    )


def _source(payload: Mapping[str, Any]) -> str:
    value = payload.get("source")
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ArtifactValidationError("source is required")
    return str(value)


def _target(payload: Mapping[str, Any]) -> str:
    value = payload.get("target")
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ArtifactValidationError("target is required")
    return str(value)


def _sequence(
    payload: Mapping[str, Any],
    key: str,
    *,
    allow_empty: bool = False,
) -> tuple[Any, ...]:
    value = payload.get(key)
    if value is None and allow_empty:
        return ()
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ArtifactValidationError(f"{key} must be a sequence")
    result = tuple(value)
    if not result and not allow_empty:
        raise ArtifactValidationError(f"{key} must be non-empty")
    return result


def _expected_source_hash(payload: Mapping[str, Any]) -> str | None:
    value = payload.get("expected_source_sha256")
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ArtifactValidationError(
            "expected_source_sha256 must be non-empty text"
        )
    return value.removeprefix("sha256:").lower()


def _expected_source_hashes(
    payload: Mapping[str, Any],
) -> dict[str, str]:
    value = payload.get("expected_source_sha256s")
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise ArtifactValidationError(
            "expected_source_sha256s must be an object"
        )
    result: dict[str, str] = {}
    for source_ref, digest in value.items():
        if (
            not isinstance(source_ref, str)
            or not source_ref.strip()
            or not isinstance(digest, str)
            or not digest.strip()
        ):
            raise ArtifactValidationError(
                "expected_source_sha256s requires path and hash strings"
            )
        normalized = digest.removeprefix("sha256:").lower()
        if len(normalized) != 64 or any(
            character not in "0123456789abcdef"
            for character in normalized
        ):
            raise ArtifactValidationError(
                "expected_source_sha256s contains an invalid digest"
            )
        result[source_ref] = normalized
    return result


def _validate_expected_sources(
    connector: LocalArtifactConnector,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    captured: dict[str, Any] = {}
    for source_ref, expected in _expected_source_hashes(payload).items():
        receipt = connector.capture(source_ref)
        if receipt.sha256 != expected:
            raise ArtifactValidationError(
                f"source changed since the General Work plan captured it: "
                f"{source_ref}"
            )
        captured[source_ref] = receipt
    return captured


def _parse_options(payload: Mapping[str, Any]) -> dict[str, Any]:
    options: dict[str, Any] = {
        "expected_columns": payload.get("expected_columns"),
        "sheet": payload.get("sheet"),
    }
    for name in ("allow_formulas", "allow_external_links"):
        value = payload.get(name, False)
        if not isinstance(value, bool):
            raise ArtifactValidationError(f"{name} must be boolean")
        options[name] = value
    return options


def _parsed_table(
    connector: LocalArtifactConnector,
    payload: Mapping[str, Any],
) -> Any:
    parsed = connector.parse(_source(payload), **_parse_options(payload))
    expected_hash = _expected_source_hash(payload)
    if expected_hash is not None and parsed.source.sha256 != expected_hash:
        raise ArtifactValidationError(
            "source changed since the General Work plan captured it"
        )
    if any(
        key in payload for key in ("select_columns", "filters", "computed_columns")
    ):
        parsed = connector.transform_table(
            parsed,
            select_columns=payload.get("select_columns"),
            filters=payload.get("filters") or (),
            computed_columns=payload.get("computed_columns"),
        )
    return parsed


def _table_digest(parsed: Any) -> str:
    if parsed.table is None:
        raise ArtifactValidationError("operation requires a tabular source")
    encoded = json.dumps(
        {
            "headers": list(parsed.table.headers),
            "rows": [list(row) for row in parsed.table.rows],
        },
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _file_claim(receipt: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(receipt)
    result["claimed"] = str(receipt.get("status") or "UNKNOWN")
    error = receipt.get("error")
    if isinstance(error, Mapping) and error.get("classification"):
        result["error_code"] = str(error["classification"])
    result["file_receipt"] = dict(receipt)
    return result


def _artifact_failure(error: BaseException) -> dict[str, Any]:
    if isinstance(error, (ArtifactPostWriteError, InjectedArtifactFailure)):
        evidence = getattr(error, "evidence", None)
        observed = dict(evidence) if isinstance(evidence, Mapping) else {}
        return {
            "claimed": "UNKNOWN",
            "error_code": "partial_failure",
            "failure_class": "artifact_post_write_unknown",
            "error": str(error),
            **observed,
            "post_write_evidence": observed,
        }
    return {
        "claimed": "FAILED",
        "error_code": getattr(error, "error_code", "artifact_error"),
        "error": str(error),
    }


def _artifact_success(
    receipt: Any,
    *,
    source_ref: str | None = None,
    source_sha256: str | None = None,
    extra: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    result = {
        "claimed": "COMPLETED",
        **receipt.as_dict(),
    }
    if source_ref is not None:
        result["source_ref"] = source_ref
    if source_sha256 is not None:
        result["source_sha256"] = source_sha256
    if extra:
        result.update(dict(extra))
    return result


def _dispatch_file(
    ctx: Any,
    action_kind: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    connector = GeneralWorkFileConnector(ctx.owned_root)
    try:
        if action_kind == "file.inspect":
            return _file_claim(
                connector.inspect_file(
                    payload.get("target") or payload.get("source") or ".",
                    preview_bytes=payload.get("preview_bytes", 0),
                )
            )
        if action_kind == "file.search":
            return _file_claim(
                connector.search(
                    name_pattern=payload.get("name_pattern"),
                    content_query=payload.get("content_query"),
                    start=payload.get("start") or ".",
                    max_results=payload.get("max_results"),
                    max_files=payload.get("max_files"),
                )
            )
        if action_kind in FILE_TRANSFER_ACTIONS:
            source_ref = _source(payload)
            target_ref = _target(payload)
            expected_source_hash = payload.get("expected_source_sha256")
            if expected_source_hash is None:
                inspected = connector.inspect_file(source_ref)
                identity = inspected.get("target_identity")
                if (
                    inspected.get("status") != "COMPLETED"
                    or not isinstance(identity, Mapping)
                    or not identity.get("sha256")
                ):
                    return _file_claim(inspected)
                expected_source_hash = identity["sha256"]
            common = {
                "expected_source_hash": expected_source_hash,
                "idempotency_key": (
                    payload.get("idempotency_key")
                    or f"{action_kind}:{source_ref}:{target_ref}"
                ),
            }
            if action_kind == "file.copy":
                receipt = connector.copy_file(
                    source_ref, target_ref, **common
                )
            elif action_kind == "file.move":
                receipt = connector.move_file(
                    source_ref, target_ref, **common
                )
            else:
                receipt = connector.rename_file(
                    source_ref, target_ref, **common
                )
        else:
            expected_source_hash = (
                payload.get("expected_source_sha256")
                or payload.get("expected_source_hash")
                or payload.get("source_sha256")
            )
            receipt = connector.safe_text_edit(
                _target(payload),
                payload.get("new_text"),
                expected_source_hash=expected_source_hash,
                idempotency_key=(
                    payload.get("idempotency_key")
                    or f"{action_kind}:{_target(payload)}"
                ),
                preserve_original=payload.get("preserve_original", True),
                encoding=payload.get("encoding", "utf-8"),
            )
        return _file_claim(receipt)
    except (LocalArtifactError, KeyError, TypeError, ValueError) as error:
        missing_required_field = str(error) in {
            "source is required",
            "target is required",
        }
        return {
            "claimed": "BLOCKED" if missing_required_field else "FAILED",
            "error_code": "invalid_input",
            "error": str(error),
        }


def _dispatch_artifact(
    ctx: Any,
    action_kind: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    try:
        connector = artifact_connector(ctx, payload)
        captured_by_ref = _validate_expected_sources(connector, payload)
        if action_kind == "artifact.table.merge":
            sources = tuple(
                str(value) for value in _sequence(payload, "sources")
            )
            parsed_sources = tuple(
                connector.parse(source) for source in sources
            )
            receipt = connector.merge_tables(
                parsed_sources,
                target_ref=_target(payload),
                idempotency_key=str(payload["idempotency_key"]),
                column_aliases=payload.get("column_aliases"),
                select_columns=payload.get("select_columns"),
                deduplicate_by=payload.get("deduplicate_by") or (),
                sort_by=payload.get("sort_by") or (),
                source_column=payload.get("source_column"),
                trim_strings=payload.get("trim_strings", True),
                fault_after_write=bool(
                    payload.get("fault_after_write", False)
                ),
            )
            return _artifact_success(
                receipt,
                extra={
                    "source_refs": sources,
                    "source_sha256s": [
                        parsed.source.sha256 for parsed in parsed_sources
                    ],
                    "row_count": receipt.readback.get("row_count"),
                    "column_count": receipt.readback.get("column_count"),
                    "transformation": receipt.readback.get("transformation"),
                },
            )
        if action_kind == "artifact.table.transform":
            parsed = _parsed_table(connector, payload)
            summary = connector.summarize_table(
                parsed,
                group_by=payload.get("group_by"),
                sum_column=payload.get("sum_column"),
            )
            return {
                "claimed": "COMPLETED",
                "source_ref": parsed.source.source_ref,
                "source_sha256": parsed.source.sha256,
                "schema": list(parsed.table.headers),
                "row_count": parsed.table.row_count,
                "column_count": parsed.table.column_count,
                "table_sha256": _table_digest(parsed),
                "transformation": dict(parsed.details.get("transformation") or {}),
                "preview_rows": parsed.table.as_dict()["preview_rows"],
                "summary": summary,
            }
        if action_kind == "artifact.xlsx.create":
            parsed = _parsed_table(connector, payload)
            receipt = connector.create_xlsx(
                parsed,
                target_ref=_target(payload),
                idempotency_key=str(payload["idempotency_key"]),
                sheet_name=str(payload.get("sheet_name") or "Data"),
                formulas=payload.get("formulas"),
                fault_after_write=bool(payload.get("fault_after_write", False)),
            )
            return _artifact_success(
                receipt,
                source_ref=parsed.source.source_ref,
                source_sha256=parsed.source.sha256,
                extra={"table_sha256": _table_digest(parsed)},
            )
        if action_kind == "artifact.xlsx.update":
            source = _source(payload)
            captured = connector.capture(source, expected_format="xlsx")
            expected_hash = _expected_source_hash(payload)
            if expected_hash is not None and captured.sha256 != expected_hash:
                raise ArtifactValidationError(
                    "source changed since the General Work plan captured it"
                )
            receipt = connector.update_xlsx(
                captured,
                target_ref=_target(payload),
                idempotency_key=str(payload["idempotency_key"]),
                sheet_name=str(payload.get("sheet_name") or "Data"),
                cells=payload.get("cells"),
                ranges=payload.get("ranges"),
                formulas=payload.get("formulas"),
            )
            return _artifact_success(
                receipt,
                source_ref=captured.source_ref,
                source_sha256=captured.sha256,
            )
        if action_kind == "artifact.docx.compose":
            sources = tuple(
                str(value)
                for value in _sequence(
                    payload,
                    "sources",
                    allow_empty=True,
                )
            )
            receipts = tuple(
                captured_by_ref.get(source) or connector.capture(source)
                for source in sources
            )
            receipt = connector.create_docx_document(
                title=str(payload.get("title") or "General work document"),
                opening=_sequence(payload, "opening", allow_empty=True),
                sections=_sequence(payload, "sections"),
                target_ref=_target(payload),
                idempotency_key=str(payload["idempotency_key"]),
                sources=receipts,
                required_values=_sequence(
                    payload,
                    "required_values",
                    allow_empty=True,
                ),
                fault_after_write=bool(
                    payload.get("fault_after_write", False)
                ),
            )
            return _artifact_success(
                receipt,
                extra={
                    "source_refs": sources,
                    "source_sha256s": [
                        receipt.sha256 for receipt in receipts
                    ],
                },
            )
        if action_kind == "artifact.docx.edit":
            source = _source(payload)
            captured = connector.capture(source, expected_format="docx")
            expected_hash = _expected_source_hash(payload)
            if expected_hash is not None and captured.sha256 != expected_hash:
                raise ArtifactValidationError(
                    "source changed since the General Work plan captured it"
                )
            receipt = connector.edit_docx(
                captured,
                target_ref=_target(payload),
                idempotency_key=str(payload["idempotency_key"]),
                replacements=payload.get("replacements"),
                append_paragraphs=payload.get("append_paragraphs") or (),
            )
            return _artifact_success(
                receipt,
                source_ref=captured.source_ref,
                source_sha256=captured.sha256,
            )
        if action_kind == "artifact.pdf.create":
            source = payload.get("source")
            lineage_sources = tuple(
                str(value)
                for value in _sequence(
                    payload,
                    "sources",
                    allow_empty=True,
                )
            )
            captured = None
            if source is not None:
                captured = connector.capture(str(source))
                expected_hash = _expected_source_hash(payload)
                if (
                    expected_hash is not None
                    and captured.sha256 != expected_hash
                ):
                    raise ArtifactValidationError(
                        "source changed since the General Work plan captured it"
                    )
            lineage_receipts = tuple(
                captured_by_ref.get(source_ref)
                or connector.capture(source_ref)
                for source_ref in lineage_sources
            )
            if captured is None and lineage_receipts:
                captured = lineage_receipts[0]
            receipt = connector.create_pdf(
                pages=_sequence(payload, "pages"),
                target_ref=_target(payload),
                idempotency_key=str(payload["idempotency_key"]),
                title=str(payload.get("title") or "JARVIS local artifact"),
                source=captured,
                fault_after_write=bool(payload.get("fault_after_write", False)),
            )
            return _artifact_success(
                receipt,
                source_ref=(captured.source_ref if captured is not None else None),
                source_sha256=(captured.sha256 if captured is not None else None),
                extra={
                    "source_refs": list(
                        dict.fromkeys(
                            [
                                *(
                                    [captured.source_ref]
                                    if captured is not None
                                    else []
                                ),
                                *lineage_sources,
                            ]
                        )
                    ),
                    "source_sha256s": [
                        (captured_by_ref.get(source_ref)
                        or connector.capture(source_ref)).sha256
                        for source_ref in dict.fromkeys(
                            [
                                *(
                                    [captured.source_ref]
                                    if captured is not None
                                    else []
                                ),
                                *lineage_sources,
                            ]
                        )
                    ],
                },
            )
        if action_kind == "artifact.pdf.combine":
            sources = tuple(str(value) for value in _sequence(payload, "sources"))
            receipts = [connector.capture(value, expected_format="pdf") for value in sources]
            receipt = connector.combine_pdfs(
                receipts,
                target_ref=_target(payload),
                idempotency_key=str(payload["idempotency_key"]),
            )
            return _artifact_success(
                receipt,
                extra={
                    "source_refs": [item.source_ref for item in receipts],
                    "source_sha256s": [item.sha256 for item in receipts],
                },
            )
        if action_kind == "artifact.pdf.split":
            source = _source(payload)
            captured = connector.capture(source, expected_format="pdf")
            expected_hash = _expected_source_hash(payload)
            if expected_hash is not None and captured.sha256 != expected_hash:
                raise ArtifactValidationError(
                    "source changed since the General Work plan captured it"
                )
            receipt = connector.split_pdf(
                captured,
                target_ref=_target(payload),
                idempotency_key=str(payload["idempotency_key"]),
                start_page=payload.get("start_page"),
                end_page=payload.get("end_page"),
            )
            return _artifact_success(
                receipt,
                source_ref=captured.source_ref,
                source_sha256=captured.sha256,
            )
        if action_kind == "artifact.pptx.create":
            source = payload.get("source")
            lineage_sources = tuple(
                str(value)
                for value in _sequence(
                    payload,
                    "sources",
                    allow_empty=True,
                )
            )
            captured = connector.capture(str(source)) if source is not None else None
            expected_hash = _expected_source_hash(payload)
            if (
                captured is not None
                and expected_hash is not None
                and captured.sha256 != expected_hash
            ):
                raise ArtifactValidationError(
                    "source changed since the General Work plan captured it"
                )
            lineage_receipts = tuple(
                captured_by_ref.get(source_ref)
                or connector.capture(source_ref)
                for source_ref in lineage_sources
            )
            if captured is None and lineage_receipts:
                captured = lineage_receipts[0]
            receipt = connector.create_pptx(
                slides=_sequence(payload, "slides"),
                target_ref=_target(payload),
                idempotency_key=str(payload["idempotency_key"]),
                source=captured,
                fault_after_write=bool(payload.get("fault_after_write", False)),
            )
            return _artifact_success(
                receipt,
                source_ref=(captured.source_ref if captured is not None else None),
                source_sha256=(captured.sha256 if captured is not None else None),
                extra={
                    "source_refs": list(
                        dict.fromkeys(
                            [
                                *(
                                    [captured.source_ref]
                                    if captured is not None
                                    else []
                                ),
                                *lineage_sources,
                            ]
                        )
                    ),
                    "source_sha256s": [
                        (captured_by_ref.get(source_ref)
                        or connector.capture(source_ref)).sha256
                        for source_ref in dict.fromkeys(
                            [
                                *(
                                    [captured.source_ref]
                                    if captured is not None
                                    else []
                                ),
                                *lineage_sources,
                            ]
                        )
                    ],
                },
            )
        source = _source(payload)
        captured = connector.capture(source, expected_format="pptx")
        expected_hash = _expected_source_hash(payload)
        if expected_hash is not None and captured.sha256 != expected_hash:
            raise ArtifactValidationError(
                "source changed since the General Work plan captured it"
            )
        receipt = connector.update_pptx(
            captured,
            target_ref=_target(payload),
            idempotency_key=str(payload["idempotency_key"]),
            replacements=payload.get("replacements"),
            append_slides=payload.get("append_slides") or (),
        )
        return _artifact_success(
            receipt,
            source_ref=captured.source_ref,
            source_sha256=captured.sha256,
        )
    except (LocalArtifactError, InjectedArtifactFailure) as error:
        return _artifact_failure(error)


def dispatch_general_work(
    ctx: Any,
    action_kind: str,
    payload: Mapping[str, Any],
) -> dict[str, Any] | None:
    if action_kind in FILE_ACTIONS:
        return _dispatch_file(ctx, action_kind, payload)
    if action_kind in ARTIFACT_ACTIONS:
        return _dispatch_artifact(ctx, action_kind, payload)
    if action_kind in VERIFY_ACTIONS:
        try:
            plan_id = str(payload.get("plan_id") or "").strip()
            if not plan_id:
                raise ArtifactValidationError("plan_id is required")
            plan = load_recorded_plan(
                ctx.database,
                plan_id,
                allowed_action_kinds=tuple(GENERAL_WORK_ACTION_KINDS),
            )
            resource = ctx.database.row(
                "resources", "resource_id", plan.resource_id
            )
            if resource is None:
                raise ArtifactValidationError(
                    f"plan resource is not registered: {plan.resource_id}"
                )
            resource_root = Path(str(resource["resolved_root"])).resolve()
            if resource_root != Path(ctx.owned_root).resolve():
                raise ArtifactValidationError(
                    "plan resource root does not match the executor context"
                )
            verification = verify_recorded_package(
                ctx.database,
                plan_id,
                resource_root=resource_root,
                allowed_action_kinds=tuple(GENERAL_WORK_ACTION_KINDS),
            )
            return {
                "claimed": (
                    "COMPLETED"
                    if verification.status == "PASS"
                    else "FAILED"
                ),
                "plan_id": plan_id,
                "plan_hash": verification.plan_hash,
                "package_status": verification.status,
                "verification_hash": verification.verification_hash,
                "readback_verified": verification.status == "PASS",
                "package_verification": {
                    **verification.stable_payload(),
                    "verification_hash": verification.verification_hash,
                },
                **(
                    {}
                    if verification.status == "PASS"
                    else {"error_code": "package_verification_failed"}
                ),
            }
        except Exception as error:  # noqa: BLE001 - verifier fails closed
            return {
                "claimed": "FAILED",
                "error_code": "package_verification_error",
                "error": f"{type(error).__name__}: {error}",
            }
    return None


def _current_file_identity(ctx: Any, reference: str) -> Mapping[str, Any] | None:
    receipt = GeneralWorkFileConnector(ctx.owned_root).inspect_file(reference)
    if receipt.get("status") != "COMPLETED":
        return None
    identity = receipt.get("target_identity")
    return identity if isinstance(identity, Mapping) else None


def _verify_file(
    ctx: Any,
    action_kind: str,
    claimed: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> VerifyVerdict:
    if claimed.get("claimed") != "COMPLETED":
        return (
            VerifyVerdict.UNKNOWN
            if claimed.get("claimed") in {"UNKNOWN", "BLOCKED"}
            else VerifyVerdict.MATCH
        )
    if action_kind == "file.inspect":
        reference = str(payload.get("target") or payload.get("source") or ".")
        current = _current_file_identity(ctx, reference)
        return (
            VerifyVerdict.MATCH
            if current == claimed.get("target_identity")
            else VerifyVerdict.MISMATCH
        )
    if action_kind == "file.search":
        rerun = _dispatch_file(ctx, action_kind, payload)
        return (
            VerifyVerdict.MATCH
            if rerun.get("readback") == claimed.get("readback")
            and rerun.get("result") == claimed.get("result")
            else VerifyVerdict.MISMATCH
        )
    target = str(payload.get("target") or "")
    current_target = _current_file_identity(ctx, target)
    target_claim = claimed.get("target_identity")
    if (
        current_target is None
        or not isinstance(target_claim, Mapping)
        or current_target.get("sha256") != target_claim.get("sha256")
    ):
        return VerifyVerdict.MISMATCH
    if action_kind in {"file.copy", "file.move", "file.rename"}:
        current_source = _current_file_identity(ctx, str(payload.get("source") or ""))
        if current_source is None:
            return VerifyVerdict.MISMATCH
        source_ok = (
            current_source.get("sha256") == current_target.get("sha256")
            if action_kind == "file.copy"
            else current_source.get("exists") is False
        )
        return VerifyVerdict.MATCH if source_ok else VerifyVerdict.MISMATCH
    result = claimed.get("result")
    if not isinstance(result, Mapping):
        return VerifyVerdict.MISMATCH
    backup = _current_file_identity(ctx, str(result.get("backup_ref") or ""))
    expected = str(
        payload.get("expected_source_sha256")
        or payload.get("expected_source_hash")
        or payload.get("source_sha256")
        or ""
    )
    expected = (
        expected
        if expected.startswith("sha256:")
        else f"sha256:{expected}"
    )
    return (
        VerifyVerdict.MATCH
        if backup is not None and backup.get("sha256") == expected
        else VerifyVerdict.MISMATCH
    )


def _verify_source(
    connector: LocalArtifactConnector,
    claimed: Mapping[str, Any],
) -> bool:
    source_ref = claimed.get("source_ref")
    source_hash = claimed.get("source_sha256")
    if not source_ref:
        return source_hash in (None, "")
    try:
        return connector.capture(str(source_ref)).sha256 == source_hash
    except LocalArtifactError:
        return False


def _verify_source_set(
    connector: LocalArtifactConnector,
    claimed: Mapping[str, Any],
) -> bool:
    source_refs = claimed.get("source_refs")
    source_hashes = claimed.get("source_sha256s")
    if source_refs is None and source_hashes is None:
        return _verify_source(connector, claimed)
    if (
        isinstance(source_refs, (str, bytes))
        or not isinstance(source_refs, Sequence)
        or isinstance(source_hashes, (str, bytes))
        or not isinstance(source_hashes, Sequence)
        or len(source_refs) != len(source_hashes)
    ):
        return False
    try:
        return all(
            connector.capture(str(source_ref)).sha256 == str(source_hash)
            for source_ref, source_hash in zip(source_refs, source_hashes)
        )
    except LocalArtifactError:
        return False


def _required_docx_text(payload: Mapping[str, Any]) -> tuple[str, ...]:
    values: list[str] = []
    title = payload.get("title")
    if isinstance(title, str) and title:
        values.append(title)
    for item in payload.get("opening") or ():
        if str(item):
            values.append(str(item))
    for section in payload.get("sections") or ():
        if not isinstance(section, Mapping):
            continue
        heading = section.get("heading")
        if isinstance(heading, str) and heading:
            values.append(heading)
        for key in ("paragraphs", "bullets"):
            for item in section.get(key) or ():
                if str(item):
                    values.append(str(item))
        table = section.get("table")
        if isinstance(table, Mapping):
            for header in table.get("headers") or ():
                if str(header):
                    values.append(str(header))
            for row in table.get("rows") or ():
                if isinstance(row, Sequence) and not isinstance(
                    row, (str, bytes)
                ):
                    values.extend(str(item) for item in row if str(item))
    values.extend(
        str(item)
        for item in payload.get("required_values") or ()
        if str(item)
    )
    return tuple(dict.fromkeys(values))


def _required_pptx_text(slides: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    values: list[str] = []
    for slide in slides:
        title = slide.get("title")
        if isinstance(title, str) and title:
            values.append(title)
        body = slide.get("body")
        if isinstance(body, str):
            values.extend(line for line in body.splitlines() if line)
        elif isinstance(body, Sequence) and not isinstance(body, (str, bytes)):
            values.extend(str(line) for line in body if str(line))
    return tuple(values)


def _verify_artifact(
    ctx: Any,
    action_kind: str,
    claimed: Mapping[str, Any],
    payload: Mapping[str, Any],
) -> VerifyVerdict:
    status = claimed.get("claimed")
    if status != "COMPLETED":
        return (
            VerifyVerdict.UNKNOWN
            if status in {"UNKNOWN", "BLOCKED"}
            else VerifyVerdict.MATCH
        )
    try:
        connector = artifact_connector(ctx, payload)
        _validate_expected_sources(connector, payload)
        if action_kind == "artifact.table.transform":
            parsed = _parsed_table(connector, payload)
            return (
                VerifyVerdict.MATCH
                if parsed.source.sha256 == claimed.get("source_sha256")
                and _table_digest(parsed) == claimed.get("table_sha256")
                else VerifyVerdict.MISMATCH
            )
        target = str(claimed.get("target_ref") or payload.get("target") or "")
        expected_hash = str(claimed.get("output_sha256") or "")
        if not target or not expected_hash:
            return VerifyVerdict.MISMATCH
        output = connector.parse(target)
        if output.source.sha256 != expected_hash:
            return VerifyVerdict.MISMATCH
        if action_kind == "artifact.table.merge":
            sources = tuple(
                str(value) for value in _sequence(payload, "sources")
            )
            parsed_sources = tuple(
                connector.parse(source) for source in sources
            )
            expected_table, _details = connector.compose_tables(
                parsed_sources,
                column_aliases=payload.get("column_aliases"),
                select_columns=payload.get("select_columns"),
                deduplicate_by=payload.get("deduplicate_by") or (),
                sort_by=payload.get("sort_by") or (),
                source_column=payload.get("source_column"),
                trim_strings=payload.get("trim_strings", True),
            )
            actual_table = output.table
            if actual_table is None:
                return VerifyVerdict.MISMATCH
            expected_rows = expected_table.rows
            if output.source.format in {"csv", "tsv"}:
                expected_rows = tuple(
                    tuple("" if value is None else str(value) for value in row)
                    for row in expected_table.rows
                )
            return (
                VerifyVerdict.MATCH
                if _verify_source_set(connector, claimed)
                and actual_table.headers == expected_table.headers
                and actual_table.rows == expected_rows
                else VerifyVerdict.MISMATCH
            )
        if action_kind == "artifact.xlsx.create":
            source = _parsed_table(connector, payload)
            expected_table = connector.xlsx_rendered_table(source.table)
            return (
                VerifyVerdict.MATCH
                if _verify_source(connector, claimed)
                and output.table is not None
                and output.table.headers == expected_table.headers
                and output.table.rows == expected_table.rows
                else VerifyVerdict.MISMATCH
            )
        if action_kind == "artifact.docx.compose":
            readback = connector.verify_docx(
                target,
                expected_sha256=expected_hash,
                required_values=_required_docx_text(payload),
            )
            return (
                VerifyVerdict.MATCH
                if readback.get("verified")
                and _verify_source_set(connector, claimed)
                else VerifyVerdict.MISMATCH
            )
        if action_kind == "artifact.xlsx.update":
            from openpyxl import load_workbook

            workbook = load_workbook(
                Path(output.source.path),
                data_only=False,
                read_only=True,
                keep_links=False,
            )
            try:
                sheet_name = str(payload.get("sheet_name") or "Data")
                if sheet_name not in workbook.sheetnames:
                    return VerifyVerdict.MISMATCH
                sheet = workbook[sheet_name]
                expected_cells = dict(payload.get("cells") or {})
                expected_cells.update(dict(payload.get("formulas") or {}))
                for cell_ref, value in expected_cells.items():
                    if sheet[str(cell_ref)].value != value:
                        return VerifyVerdict.MISMATCH
            finally:
                workbook.close()
            return (
                VerifyVerdict.MATCH
                if _verify_source(connector, claimed)
                else VerifyVerdict.MISMATCH
            )
        if action_kind == "artifact.docx.edit":
            required = [
                *list((payload.get("replacements") or {}).values()),
                *list(payload.get("append_paragraphs") or ()),
            ]
            readback = connector.verify_docx(
                target,
                expected_sha256=expected_hash,
                required_values=required,
            )
            return (
                VerifyVerdict.MATCH
                if readback.get("verified")
                and _verify_source_set(connector, claimed)
                else VerifyVerdict.MISMATCH
            )
        if action_kind == "artifact.pdf.create":
            pages = _sequence(payload, "pages")
            required = [
                str(payload.get("title") or "JARVIS local artifact"),
                *[str(page)[:80] for page in pages if str(page)],
            ]
            readback = connector.verify_pdf(
                target,
                expected_sha256=expected_hash,
                expected_page_count=len(pages),
                required_text=required,
            )
            return (
                VerifyVerdict.MATCH
                if readback.get("verified")
                and _verify_source_set(connector, claimed)
                else VerifyVerdict.MISMATCH
            )
        if action_kind == "artifact.pdf.combine":
            page_count = 0
            for source in _sequence(payload, "sources"):
                parsed = connector.parse(str(source))
                page_count += int(parsed.details.get("page_count") or 0)
            readback = connector.verify_pdf(
                target,
                expected_sha256=expected_hash,
                expected_page_count=page_count,
            )
            return (
                VerifyVerdict.MATCH
                if readback.get("verified")
                and _verify_source_set(connector, claimed)
                else VerifyVerdict.MISMATCH
            )
        if action_kind == "artifact.pdf.split":
            expected_pages = (
                int(payload.get("end_page")) - int(payload.get("start_page")) + 1
            )
            readback = connector.verify_pdf(
                target,
                expected_sha256=expected_hash,
                expected_page_count=expected_pages,
            )
            return (
                VerifyVerdict.MATCH
                if readback.get("verified")
                and _verify_source_set(connector, claimed)
                else VerifyVerdict.MISMATCH
            )
        if action_kind == "artifact.pptx.create":
            slides = _sequence(payload, "slides")
            readback = connector.verify_pptx(
                target,
                expected_sha256=expected_hash,
                expected_slide_count=len(slides),
                required_text=_required_pptx_text(slides),
                expected_source_sha256=claimed.get("source_sha256"),
            )
            return (
                VerifyVerdict.MATCH
                if readback.get("verified")
                and _verify_source_set(connector, claimed)
                else VerifyVerdict.MISMATCH
            )
        appended = _sequence(payload, "append_slides", allow_empty=True)
        source = connector.parse(str(payload.get("source")))
        required = [
            *list((payload.get("replacements") or {}).values()),
            *_required_pptx_text(appended),
        ]
        readback = connector.verify_pptx(
            target,
            expected_sha256=expected_hash,
            expected_slide_count=(
                int(source.details.get("slide_count") or 0) + len(appended)
            ),
            required_text=required,
        )
        return (
            VerifyVerdict.MATCH
            if readback.get("verified") and _verify_source(connector, claimed)
            else VerifyVerdict.MISMATCH
        )
    except Exception:
        return VerifyVerdict.MISMATCH


def inspect_general_work_reconciliation(
    ctx: Any,
    action_kind: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Read current local output truth before any General Work repair write."""

    if action_kind in FILE_TRANSFER_ACTIONS:
        connector = GeneralWorkFileConnector(ctx.owned_root)
        receipt = connector.inspect_transfer_reconciliation(
            action_kind,
            str(payload.get("source") or ""),
            str(payload.get("target") or ""),
            expected_source_hash=str(
                payload.get("expected_source_sha256") or ""
            ),
            idempotency_key=str(payload.get("idempotency_key") or ""),
        )
        target_identity = receipt.get("target_identity")
        target_hash = (
            target_identity.get("sha256")
            if isinstance(target_identity, Mapping)
            else None
        )
        matches = (
            receipt.get("status") == "COMPLETED"
            and receipt.get("reconciled") is True
        )
        error = receipt.get("error")
        return {
            "status": "MATCH" if matches else "MISMATCH",
            "matches": matches,
            "target_ref": str(payload.get("target") or ""),
            "artifact_sha256": target_hash,
            "claim": _file_claim(receipt) if matches else None,
            "reason": (
                str(error.get("message") or error.get("classification"))
                if isinstance(error, Mapping)
                else None
            ),
            "readback": receipt.get("readback"),
        }
    if action_kind not in ARTIFACT_ACTIONS:
        return {
            "status": "UNSUPPORTED",
            "matches": False,
            "reason": "action is not supported by General Work reconciliation",
        }
    target = payload.get("target")
    if not isinstance(target, (str, Path)) or not str(target).strip():
        return {
            "status": "MISSING_TARGET",
            "matches": False,
            "reason": "artifact action has no target",
        }
    connector = artifact_connector(ctx, payload)
    try:
        captured_target = connector.capture(str(target))
    except LocalArtifactError as error:
        return {
            "status": "MISSING_OR_UNREADABLE",
            "matches": False,
            "target_ref": str(target),
            "reason": f"{type(error).__name__}: {error}",
        }
    claim: dict[str, Any] = {
        "claimed": "COMPLETED",
        "target_ref": captured_target.source_ref,
        "output_sha256": captured_target.sha256,
        "sha256": captured_target.sha256,
        "size_bytes": captured_target.size_bytes,
    }
    try:
        if action_kind == "artifact.table.merge":
            source_refs = tuple(
                str(value) for value in _sequence(payload, "sources")
            )
            source_receipts = tuple(
                connector.capture(source_ref) for source_ref in source_refs
            )
            claim.update(
                {
                    "source_refs": list(source_refs),
                    "source_sha256s": [
                        receipt.sha256 for receipt in source_receipts
                    ],
                }
            )
        elif action_kind == "artifact.docx.compose":
            source_refs = tuple(
                str(value)
                for value in _sequence(payload, "sources", allow_empty=True)
            )
            source_receipts = tuple(
                connector.capture(source_ref) for source_ref in source_refs
            )
            claim.update(
                {
                    "source_refs": list(source_refs),
                    "source_sha256s": [
                        receipt.sha256 for receipt in source_receipts
                    ],
                }
            )
        elif action_kind in {
            "artifact.pdf.create",
            "artifact.pptx.create",
        }:
            source_refs = list(
                dict.fromkeys(
                    [
                        *(
                            [str(payload["source"])]
                            if payload.get("source") is not None
                            else []
                        ),
                        *(
                            str(value)
                            for value in _sequence(
                                payload,
                                "sources",
                                allow_empty=True,
                            )
                        ),
                    ]
                )
            )
            source_receipts = tuple(
                connector.capture(source_ref) for source_ref in source_refs
            )
            claim.update(
                {
                    "source_refs": source_refs,
                    "source_sha256s": [
                        receipt.sha256 for receipt in source_receipts
                    ],
                }
            )
            if source_receipts:
                claim["source_ref"] = source_receipts[0].source_ref
                claim["source_sha256"] = source_receipts[0].sha256
        elif payload.get("source") is not None:
            source_receipt = connector.capture(str(payload["source"]))
            claim["source_ref"] = source_receipt.source_ref
            claim["source_sha256"] = source_receipt.sha256
    except LocalArtifactError as error:
        return {
            "status": "SOURCE_MISMATCH",
            "matches": False,
            "target_ref": captured_target.source_ref,
            "artifact_sha256": captured_target.sha256,
            "reason": f"{type(error).__name__}: {error}",
        }
    verdict = _verify_artifact(ctx, action_kind, claim, payload)
    return {
        "status": "MATCH" if verdict is VerifyVerdict.MATCH else "MISMATCH",
        "matches": verdict is VerifyVerdict.MATCH,
        "target_ref": captured_target.source_ref,
        "artifact_sha256": captured_target.sha256,
        "claim": claim,
    }


def verify_general_work_action(
    ctx: Any,
    action_kind: str,
    claimed: Mapping[str, Any],
    payload: Mapping[str, Any] | None,
) -> VerifyVerdict | None:
    if action_kind not in GENERAL_WORK_ACTIONS:
        return None
    bound = payload or {}
    if action_kind in VERIFY_ACTIONS:
        status = claimed.get("claimed")
        if status != "COMPLETED":
            return (
                VerifyVerdict.UNKNOWN
                if status in {"UNKNOWN", "BLOCKED"}
                else VerifyVerdict.MATCH
            )
        try:
            plan = load_recorded_plan(
                ctx.database,
                str(bound.get("plan_id") or ""),
                allowed_action_kinds=tuple(GENERAL_WORK_ACTION_KINDS),
            )
            resource = ctx.database.row(
                "resources", "resource_id", plan.resource_id
            )
            if resource is None:
                return VerifyVerdict.MISMATCH
            resource_root = Path(str(resource["resolved_root"])).resolve()
            if resource_root != Path(ctx.owned_root).resolve():
                return VerifyVerdict.MISMATCH
            rerun = verify_recorded_package(
                ctx.database,
                plan.plan_id,
                resource_root=resource_root,
                allowed_action_kinds=tuple(GENERAL_WORK_ACTION_KINDS),
            )
            return (
                VerifyVerdict.MATCH
                if rerun.status == "PASS"
                and rerun.verification_hash == claimed.get("verification_hash")
                and rerun.plan_hash == claimed.get("plan_hash")
                else VerifyVerdict.MISMATCH
            )
        except Exception:
            return VerifyVerdict.MISMATCH
    if action_kind in FILE_ACTIONS:
        return _verify_file(ctx, action_kind, claimed, bound)
    return _verify_artifact(ctx, action_kind, claimed, bound)


__all__ = [
    "ARTIFACT_ACTIONS",
    "FILE_ACTIONS",
    "FILE_TRANSFER_ACTIONS",
    "GENERAL_WORK_ACTIONS",
    "RECONCILABLE_ACTIONS",
    "VERIFY_ACTIONS",
    "artifact_connector",
    "dispatch_general_work",
    "general_work_specs",
    "inspect_general_work_reconciliation",
    "verify_general_work_action",
]
