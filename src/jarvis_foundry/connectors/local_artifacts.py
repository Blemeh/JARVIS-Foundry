"""Bounded local document, data, and image adapters for V10 Lane B.

The adapters in this module are deliberately local and format-aware.  They
capture a source hash before parsing, enforce conservative limits, produce
JSON-safe receipts, and use the existing :class:`FilesystemConnector` for
owned-root binary writes and idempotent reconciliation.  They do not decide
missions, acquire tools, or treat parsed content as instructions.
"""

from __future__ import annotations

import csv
import hashlib
import io
import importlib.metadata
import json
import math
import re
import textwrap
import warnings
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, fields
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any, Mapping, Sequence

from ..clock import isoformat
from .filesystem import FilesystemConnector, FilesystemConnectorError


class LocalArtifactError(ValueError):
    """Base class for a bounded local artifact refusal or parse failure."""

    error_code = "artifact_error"


class ArtifactValidationError(LocalArtifactError):
    error_code = "validation_error"


class SourceChangedError(ArtifactValidationError):
    error_code = "source_changed"


class ArtifactParseError(LocalArtifactError):
    error_code = "parse_error"


class ArtifactSchemaError(ArtifactParseError):
    error_code = "schema_mismatch"


class ArtifactWriteError(LocalArtifactError):
    error_code = "write_error"


class ArtifactPostWriteError(ArtifactWriteError):
    """An output landed, but its source/readback verification is unresolved."""

    error_code = "post_write_failure"

    def __init__(self, message: str, *, evidence: Mapping[str, Any]) -> None:
        super().__init__(message)
        self.evidence = dict(evidence)


class InjectedArtifactFailure(RuntimeError):
    """Test-only fault boundary after an output has been written."""

    def __init__(
        self,
        message: str,
        *,
        evidence: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.evidence = dict(evidence or {})


CORE_DISTRIBUTIONS = (
    "pypdf",
    "python-docx",
    "openpyxl",
    "python-pptx",
    "Pillow",
    "reportlab",
)


def installed_library_versions() -> dict[str, str]:
    """Return the versions of the format libraries used by this lane.

    Imports stay lazy so a missing optional parser produces a bounded parser
    error at the capability boundary rather than making the whole Foundry
    package unimportable.
    """
    versions: dict[str, str] = {}
    for distribution in CORE_DISTRIBUTIONS:
        try:
            versions[distribution] = importlib.metadata.version(distribution)
        except importlib.metadata.PackageNotFoundError:
            versions[distribution] = "MISSING"
    return versions


@dataclass(frozen=True, slots=True)
class ArtifactLimits:
    """Conservative bounds applied before a parser receives local bytes."""

    max_source_bytes: int = 64 * 1024 * 1024
    max_text_chars: int = 250_000
    max_docx_paragraphs: int = 50_000
    max_docx_tables: int = 1_000
    max_docx_table_rows: int = 50_000
    max_docx_table_cells: int = 1_000_000
    max_pdf_pages: int = 250
    max_sheets: int = 32
    max_rows: int = 50_000
    max_columns: int = 256
    max_cells: int = 1_000_000
    max_header_chars: int = 512
    max_groups: int = 1_000
    max_group_key_chars: int = 1_024
    max_slides: int = 100
    max_shapes: int = 5_000
    max_pixels: int = 25_000_000
    max_zip_members: int = 5_000
    max_zip_expanded_bytes: int = 128 * 1024 * 1024
    max_csv_field_bytes: int = 4 * 1024 * 1024

    def __post_init__(self) -> None:
        for item in fields(self):
            name = item.name
            value = getattr(self, name)
            if not isinstance(value, int) or value <= 0:
                raise ArtifactValidationError(f"{name} must be a positive integer")


@dataclass(frozen=True, slots=True)
class SourceReceipt:
    source_ref: str
    path: str
    format: str
    sha256: str
    size_bytes: int
    captured_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "source_ref": self.source_ref,
            "path": self.path,
            "format": self.format,
            "sha256": self.sha256,
            "source_sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "captured_at": self.captured_at,
        }


@dataclass(frozen=True, slots=True)
class TableData:
    headers: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]

    @property
    def row_count(self) -> int:
        return len(self.rows)

    @property
    def column_count(self) -> int:
        return len(self.headers)

    def as_dict(
        self, *, include_preview: bool = True, max_preview_chars: int = 2_048
    ) -> dict[str, Any]:
        value: dict[str, Any] = {
            "headers": list(self.headers),
            "row_count": self.row_count,
            "column_count": self.column_count,
        }
        if include_preview:
            value["preview_rows"] = [
                {
                    header: _bounded_receipt_value(row[index], max_preview_chars)
                    for index, header in enumerate(self.headers)
                }
                for row in self.rows[:5]
            ]
        return value

    def row_dicts(self) -> tuple[dict[str, Any], ...]:
        return tuple(
            {header: row[index] for index, header in enumerate(self.headers)}
            for row in self.rows
        )


@dataclass(frozen=True, slots=True)
class ParsedArtifact:
    source: SourceReceipt
    kind: str
    details: Mapping[str, Any] = field(default_factory=dict)
    table: TableData | None = None

    def as_dict(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "source": self.source.as_dict(),
            "kind": self.kind,
            "details": _json_safe(dict(self.details)),
        }
        if self.table is not None:
            result["table"] = self.table.as_dict()
        return result


@dataclass(frozen=True, slots=True)
class OutputReceipt:
    target_ref: str
    path: str
    format: str
    sha256: str
    size_bytes: int
    source_sha256: str | None
    idempotency_key: str
    readback: Mapping[str, Any]
    reconciled: bool = False
    replayed: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "target_ref": self.target_ref,
            "path": self.path,
            "format": self.format,
            "sha256": self.sha256,
            "output_sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "source_sha256": self.source_sha256,
            "idempotency_key": self.idempotency_key,
            "readback": _json_safe(dict(self.readback)),
            "readback_verified": bool(self.readback.get("verified")),
            "reconciled": self.reconciled,
            "replayed": self.replayed,
        }


def _json_safe(value: Any) -> Any:
    """Convert parser values to bounded JSON-safe receipt values."""
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def _bounded_receipt_value(value: Any, max_chars: int) -> Any:
    value = _json_safe(value)
    if isinstance(value, str) and len(value) > max_chars:
        return f"{value[:max_chars]}...[truncated]"
    return value


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _normalise_header(value: Any, index: int) -> str:
    if value is None or not str(value).strip():
        raise ArtifactSchemaError(f"header at column {index + 1} is empty")
    return str(value).strip()


def _set_xlsx_value(cell: Any, value: Any, *, formula: bool = False) -> None:
    """Write source data literally; formulas require the explicit formula API."""
    cell.value = value
    if isinstance(value, str) and value.startswith("=") and not formula:
        cell.data_type = "s"


def _xlsx_display_value(value: Any, *, numeric_column: bool) -> Any:
    """Normalize mixed numeric columns without coercing identifier-like text."""
    if not numeric_column or not isinstance(value, str) or value != value.strip():
        return value
    if not re.fullmatch(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?", value):
        return value
    unsigned = value.removeprefix("-")
    integer_part = unsigned.split(".", 1)[0]
    if len(integer_part) > 1 and integer_part.startswith("0"):
        return value
    numeric = Decimal(value)
    if numeric == numeric.to_integral_value():
        return int(numeric)
    converted = float(numeric)
    return converted if math.isfinite(converted) else value


def _xlsx_numeric_columns(table: TableData) -> frozenset[int]:
    return frozenset(
        column_index
        for column_index in range(table.column_count)
        if any(
            isinstance(row[column_index], (int, float, Decimal))
            and not isinstance(row[column_index], bool)
            for row in table.rows
        )
    )


def _make_table(
    rows: Sequence[Sequence[Any]],
    limits: ArtifactLimits,
    *,
    expected_columns: Sequence[str] | None = None,
) -> TableData:
    if not rows:
        raise ArtifactSchemaError("table has no header row")
    if len(rows[0]) > limits.max_columns:
        raise ArtifactSchemaError("table exceeds max_columns")
    headers = tuple(
        _normalise_header(value, index)
        for index, value in enumerate(rows[0])
    )
    if len(set(header.casefold() for header in headers)) != len(headers):
        raise ArtifactSchemaError("table headers must be unique")
    if any(len(header) > limits.max_header_chars for header in headers):
        raise ArtifactSchemaError("table header exceeds max_header_chars")
    if expected_columns is not None:
        if isinstance(expected_columns, (str, bytes)):
            raise ArtifactSchemaError("expected_columns must be a sequence of column names")
        expected = tuple(str(item).strip() for item in expected_columns)
        if not expected or any(not item for item in expected):
            raise ArtifactSchemaError("expected_columns must contain non-empty names")
        if len(set(item.casefold() for item in expected)) != len(expected):
            raise ArtifactSchemaError("expected_columns must be unique")
        if any(len(item) > limits.max_header_chars for item in expected):
            raise ArtifactSchemaError("expected column exceeds max_header_chars")
        if headers != expected:
            raise ArtifactSchemaError(
                f"schema mismatch: expected columns {list(expected)!r}, got {list(headers)!r}"
            )
    data_rows: list[tuple[Any, ...]] = []
    cell_count = 0
    for row_number, raw_row in enumerate(rows[1:], start=2):
        if len(raw_row) > limits.max_columns:
            raise ArtifactSchemaError(
                f"row {row_number} exceeds max_columns ({limits.max_columns})"
            )
        padded = tuple(_json_safe(value) for value in raw_row)
        if not any(value not in (None, "") for value in padded):
            continue
        if len(data_rows) >= limits.max_rows:
            raise ArtifactSchemaError("table exceeds max_rows")
        if len(padded) != len(headers):
            raise ArtifactSchemaError(
                f"row {row_number} has {len(padded)} columns; expected {len(headers)}"
            )
        cell_count += len(headers)
        if cell_count > limits.max_cells:
            raise ArtifactSchemaError("table exceeds max_cells")
        data_rows.append(padded)
    return TableData(headers=headers, rows=tuple(data_rows))


def _validate_zip_container(data: bytes, limits: ArtifactLimits, label: str) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            if len(infos) > limits.max_zip_members:
                raise ArtifactValidationError(
                    f"{label} contains too many package members"
                )
            expanded = 0
            for info in infos:
                normalized_name = info.filename.replace("\\", "/")
                parts = PurePosixPath(normalized_name).parts
                if (
                    "\x00" in info.filename
                    or normalized_name.startswith("/")
                    or ".." in parts
                    or (parts and ":" in parts[0])
                ):
                    raise ArtifactValidationError(
                        f"{label} contains an unsafe package member"
                    )
                expanded += max(0, int(info.file_size))
                if expanded > limits.max_zip_expanded_bytes:
                    raise ArtifactValidationError(
                        f"{label} exceeds expanded-byte package bound"
                    )
    except ArtifactValidationError:
        raise
    except (OSError, zipfile.BadZipFile) as error:
        raise ArtifactParseError(f"invalid {label} package") from error


def _post_write_evidence(
    written: OutputReceipt,
    readback: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    observed = dict(readback or {})
    observed.setdefault("exists", True)
    observed.setdefault("sha256", written.sha256)
    return {
        "target_ref": written.target_ref,
        "path": written.path,
        "format": written.format,
        "sha256": written.sha256,
        "output_sha256": written.sha256,
        "size_bytes": written.size_bytes,
        "source_sha256": written.source_sha256,
        "idempotency_key": written.idempotency_key,
        "readback": _json_safe(observed),
        "readback_verified": bool(observed.get("verified")),
        "reconciled": written.reconciled,
        "replayed": written.replayed,
    }


class LocalArtifactConnector:
    """Bounded format adapters rooted in one disposable owned directory."""

    SUPPORTED_FORMATS = frozenset({
        "pdf", "docx", "xlsx", "xlsm", "csv", "tsv", "json", "pptx",
        "txt", "md",
        "png", "jpg", "jpeg", "bmp", "gif", "tif", "tiff", "webp",
    })
    IMAGE_FORMATS = frozenset({
        "png", "jpg", "jpeg", "bmp", "gif", "tif", "tiff", "webp",
    })

    def __init__(
        self,
        root: str | Path,
        limits: ArtifactLimits | None = None,
        *,
        source_roots: Sequence[str | Path] | None = None,
        repair_existing: bool = False,
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.limits = limits or ArtifactLimits()
        if not isinstance(repair_existing, bool):
            raise ArtifactValidationError("repair_existing must be boolean")
        self.repair_existing = repair_existing
        if isinstance(source_roots, (str, bytes, Path)):
            raise ArtifactValidationError("source_roots must be a sequence of directories")
        roots: list[Path] = [self.root]
        for raw_root in source_roots or ():
            source_root = Path(raw_root).expanduser().resolve()
            if not source_root.is_dir():
                raise ArtifactValidationError(
                    f"source root is not a directory: {source_root}"
                )
            if source_root not in roots:
                roots.append(source_root)
        self.source_roots = tuple(roots)
        try:
            self.filesystem = FilesystemConnector(self.root)
        except FilesystemConnectorError as error:
            raise ArtifactValidationError(str(error)) from error

    @staticmethod
    def _relative_to(path: Path, root: Path) -> Path | None:
        try:
            return path.relative_to(root)
        except ValueError:
            return None

    @staticmethod
    def _validate_path_reference(candidate: Path) -> None:
        if candidate.drive and not candidate.is_absolute():
            raise ArtifactValidationError(
                "artifact path cannot be drive-relative"
            )
        parts = candidate.parts
        start = (
            1
            if candidate.anchor and parts and parts[0] == candidate.anchor
            else 0
        )
        if any(":" in part for part in parts[start:]):
            raise ArtifactValidationError(
                "artifact path components cannot contain a drive or stream"
            )

    def _resolve(self, ref: str, *, output: bool = False) -> tuple[Path, str]:
        if not isinstance(ref, str) or not ref.strip():
            raise ArtifactValidationError("artifact path must be a non-empty string")
        candidate = Path(ref)
        self._validate_path_reference(candidate)
        resolved = (
            candidate.expanduser().resolve()
            if candidate.is_absolute()
            else (self.root / candidate).resolve()
        )
        relative = self._relative_to(resolved, self.root)
        if output:
            if relative is None:
                raise ArtifactValidationError("output path escapes the owned root")
            identity = relative.as_posix()
        else:
            allowed = any(self._relative_to(resolved, root) is not None for root in self.source_roots)
            if not allowed:
                raise ArtifactValidationError(
                    "source path is outside the configured source roots"
                )
            # Keep owned-root references compact, while retaining a stable
            # absolute identity for an explicitly registered external file.
            identity = relative.as_posix() if relative is not None else str(resolved)
        if resolved == self.root:
            raise ArtifactValidationError("artifact path must name a file")
        if not output and not resolved.is_file():
            raise ArtifactValidationError(f"source is not a regular file: {ref}")
        return resolved, identity

    def _format_for(self, path: Path) -> str:
        suffix = path.suffix.lower().lstrip(".")
        if suffix not in self.SUPPORTED_FORMATS:
            raise ArtifactValidationError(
                f"unsupported artifact format {path.suffix or '<none>'!r}"
            )
        return suffix

    def capture(self, source_ref: str, *, expected_format: str | None = None) -> SourceReceipt:
        path, relative = self._resolve(source_ref)
        fmt = self._format_for(path)
        if expected_format and fmt != expected_format.lower().lstrip("."):
            raise ArtifactValidationError(
                f"source format mismatch: expected {expected_format!r}, got {fmt!r}"
            )
        size = path.stat().st_size
        if size > self.limits.max_source_bytes:
            raise ArtifactValidationError(
                f"source exceeds max_source_bytes ({self.limits.max_source_bytes})"
            )
        data = path.read_bytes()
        if len(data) != size:
            raise SourceChangedError("source changed while it was being captured")
        return SourceReceipt(
            source_ref=relative,
            path=str(path),
            format=fmt,
            sha256=_sha256(data),
            size_bytes=size,
            captured_at=isoformat(),
        )

    def _captured_bytes(self, source: SourceReceipt) -> bytes:
        path, relative = self._resolve(source.source_ref)
        if relative != source.source_ref or str(path) != source.path:
            raise SourceChangedError("captured source identity no longer matches")
        data = path.read_bytes()
        if len(data) != source.size_bytes or _sha256(data) != source.sha256:
            raise SourceChangedError(
                "source changed after capture; reject and restart the read contract"
            )
        return data

    def _assert_source_current(self, source: SourceReceipt) -> None:
        """Re-read the registered source before and after a derived write."""
        self._captured_bytes(source)

    def parse(
        self,
        source: SourceReceipt | str | Path,
        *,
        expected_columns: Sequence[str] | None = None,
        allow_formulas: bool = False,
        allow_external_links: bool = False,
        sheet: str | None = None,
    ) -> ParsedArtifact:
        if not isinstance(allow_formulas, bool):
            raise ArtifactValidationError("allow_formulas must be boolean")
        if not isinstance(allow_external_links, bool):
            raise ArtifactValidationError("allow_external_links must be boolean")
        if sheet is not None and (not isinstance(sheet, str) or not sheet.strip()):
            raise ArtifactValidationError("sheet must be a non-empty string")
        receipt = self.capture(str(source)) if isinstance(source, (str, Path)) else source
        data = self._captured_bytes(receipt)
        if receipt.format == "pdf":
            return self._parse_pdf(receipt, data)
        if receipt.format == "docx":
            return self._parse_docx(receipt, data)
        if receipt.format in {"xlsx", "xlsm"}:
            return self._parse_workbook(
                receipt, data, expected_columns=expected_columns,
                allow_formulas=allow_formulas,
                allow_external_links=allow_external_links,
                sheet=sheet,
            )
        if receipt.format in {"csv", "tsv"}:
            return self._parse_delimited(receipt, data, expected_columns=expected_columns)
        if receipt.format == "json":
            return self._parse_json(
                receipt, data, expected_columns=expected_columns
            )
        if receipt.format == "pptx":
            return self._parse_pptx(receipt, data)
        if receipt.format in {"txt", "md"}:
            return self._parse_text(receipt, data)
        if receipt.format in self.IMAGE_FORMATS:
            return self._parse_image(receipt, data)
        raise ArtifactValidationError(f"no parser for {receipt.format}")

    def _parse_pdf(self, source: SourceReceipt, data: bytes) -> ParsedArtifact:
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(data), strict=False)
            page_count = len(reader.pages)
            if page_count > self.limits.max_pdf_pages:
                raise ArtifactValidationError("PDF exceeds max_pdf_pages")
            text_parts: list[str] = []
            for page in reader.pages:
                text_parts.append(page.extract_text() or "")
            text = "\n".join(text_parts)
            if len(text) > self.limits.max_text_chars:
                raise ArtifactValidationError("PDF extracted text exceeds max_text_chars")
            metadata = {
                str(key): _json_safe(value)
                for key, value in (reader.metadata or {}).items()
            }
            details = {
                "page_count": page_count,
                "text_chars": len(text),
                "text_sha256": _sha256(text.encode("utf-8")),
                "text": text,
                "pages": text_parts,
                "metadata": metadata,
            }
            return ParsedArtifact(source=source, kind="pdf", details=details)
        except ArtifactValidationError:
            raise
        except Exception as error:  # noqa: BLE001 - parser boundary
            raise ArtifactParseError(f"pypdf could not parse {source.source_ref}: {error}") from error

    def _parse_docx(self, source: SourceReceipt, data: bytes) -> ParsedArtifact:
        _validate_zip_container(data, self.limits, "DOCX")
        try:
            from docx import Document
            document = Document(io.BytesIO(data))
            paragraphs = [paragraph.text for paragraph in document.paragraphs]
            table_rows = sum(len(table.rows) for table in document.tables)
            table_cells = sum(
                len(row.cells)
                for table in document.tables
                for row in table.rows
            )
            table_text = [
                " | ".join(cell.text for cell in row.cells)
                for table in document.tables
                for row in table.rows
            ]
            text = "\n".join([*paragraphs, *table_text])
            if len(paragraphs) > self.limits.max_docx_paragraphs:
                raise ArtifactValidationError("DOCX exceeds max_docx_paragraphs")
            if len(document.tables) > self.limits.max_docx_tables:
                raise ArtifactValidationError("DOCX exceeds max_docx_tables")
            if table_rows > self.limits.max_docx_table_rows:
                raise ArtifactValidationError("DOCX exceeds max_docx_table_rows")
            if table_cells > self.limits.max_docx_table_cells:
                raise ArtifactValidationError("DOCX exceeds max_docx_table_cells")
            if len(text) > self.limits.max_text_chars:
                raise ArtifactValidationError("DOCX text exceeds max_text_chars")
            details = {
                "paragraph_count": len(paragraphs),
                "table_count": len(document.tables),
                "table_rows": table_rows,
                "table_cells": table_cells,
                "text_chars": len(text),
                "text_sha256": _sha256(text.encode("utf-8")),
                "text": text,
                "paragraphs": paragraphs,
                "table_text_rows": table_text,
            }
            return ParsedArtifact(source=source, kind="docx", details=details)
        except ArtifactValidationError:
            raise
        except Exception as error:  # noqa: BLE001 - parser boundary
            raise ArtifactParseError(
                f"python-docx could not parse {source.source_ref}: {error}"
            ) from error

    def _parse_delimited(
        self,
        source: SourceReceipt,
        data: bytes,
        *,
        expected_columns: Sequence[str] | None,
    ) -> ParsedArtifact:
        if len(data) > self.limits.max_source_bytes:
            raise ArtifactValidationError("delimited source exceeds size bound")
        delimiter = "\t" if source.format == "tsv" else ","
        old_field_limit = csv.field_size_limit()
        csv.field_size_limit(self.limits.max_csv_field_bytes)
        try:
            try:
                stream = io.TextIOWrapper(
                    io.BytesIO(data), encoding="utf-8-sig", newline=""
                )
                reader = csv.reader(stream, delimiter=delimiter)
                rows: list[list[str]] = []
                for row in reader:
                    rows.append(row)
                    if len(rows) > self.limits.max_rows + 1:
                        raise ArtifactSchemaError("table exceeds max_rows")
            except (UnicodeDecodeError, csv.Error) as error:
                raise ArtifactParseError(
                    f"delimited parser rejected {source.source_ref}: {error}"
                ) from error
            table = _make_table(rows, self.limits, expected_columns=expected_columns)
            return ParsedArtifact(
                source=source,
                kind="table",
                details={
                    "delimiter": "tab" if delimiter == "\t" else "comma",
                    "schema": list(table.headers),
                },
                table=table,
            )
        finally:
            csv.field_size_limit(old_field_limit)

    def _parse_json(
        self,
        source: SourceReceipt,
        data: bytes,
        *,
        expected_columns: Sequence[str] | None,
    ) -> ParsedArtifact:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise ArtifactParseError(
                f"JSON parser rejected {source.source_ref}: {error}"
            ) from error
        if len(text) > self.limits.max_text_chars:
            raise ArtifactValidationError("JSON text exceeds max_text_chars")
        try:
            value = json.loads(text)
        except json.JSONDecodeError as error:
            raise ArtifactParseError(
                f"JSON parser rejected {source.source_ref}: {error}"
            ) from error

        records: list[Any] | None = None
        records_path: str | None = None
        if isinstance(value, list) and all(isinstance(item, Mapping) for item in value):
            records = value
            records_path = "$"
        elif (
            isinstance(value, Mapping)
            and isinstance(value.get("records"), list)
            and all(isinstance(item, Mapping) for item in value["records"])
        ):
            records = value["records"]
            records_path = "$.records"

        if records is not None:
            if not records:
                raise ArtifactSchemaError("JSON records table is empty")
            if len(records) > self.limits.max_rows:
                raise ArtifactSchemaError("JSON records exceed max_rows")
            headers: list[str] = []
            seen: set[str] = set()
            for record in records:
                for raw_key in record:
                    header = str(raw_key).strip()
                    if not header:
                        raise ArtifactSchemaError("JSON record key is empty")
                    folded = header.casefold()
                    if folded not in seen:
                        seen.add(folded)
                        headers.append(header)
            rows: list[list[Any]] = [headers]
            rows.extend(
                [record.get(header) for header in headers]
                for record in records
            )
            table = _make_table(
                rows, self.limits, expected_columns=expected_columns
            )
            return ParsedArtifact(
                source=source,
                kind="table",
                details={
                    "json_type": "records",
                    "records_path": records_path,
                    "schema": list(table.headers),
                    "container_keys": (
                        sorted(str(key) for key in value)
                        if isinstance(value, Mapping)
                        else []
                    ),
                },
                table=table,
            )

        if expected_columns is not None:
            raise ArtifactSchemaError(
                "expected_columns requires a JSON list-of-records or records table"
            )
        if isinstance(value, Mapping):
            if len(value) > self.limits.max_columns:
                raise ArtifactSchemaError("JSON object exceeds max_columns")
            keys = [str(key) for key in value]
            if any(not key.strip() for key in keys):
                raise ArtifactSchemaError("JSON object key is empty")
            if any(len(key) > self.limits.max_header_chars for key in keys):
                raise ArtifactSchemaError("JSON object key exceeds max_header_chars")
            json_type = "object"
        elif isinstance(value, list):
            if len(value) > self.limits.max_rows:
                raise ArtifactSchemaError("JSON list exceeds max_rows")
            keys = []
            json_type = "list"
        else:
            keys = []
            json_type = type(value).__name__
        return ParsedArtifact(
            source=source,
            kind="json",
            details={
                "json_type": json_type,
                "keys": sorted(keys),
                "value": _json_safe(value),
            },
        )

    def _parse_text(
        self,
        source: SourceReceipt,
        data: bytes,
    ) -> ParsedArtifact:
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError as error:
            raise ArtifactParseError(
                f"text parser rejected {source.source_ref}: {error}"
            ) from error
        if len(text) > self.limits.max_text_chars:
            raise ArtifactValidationError("text source exceeds max_text_chars")
        lines = text.splitlines()
        return ParsedArtifact(
            source=source,
            kind="text",
            details={
                "text": text,
                "text_chars": len(text),
                "text_sha256": _sha256(text.encode("utf-8")),
                "line_count": len(lines),
                "nonempty_line_count": sum(bool(line.strip()) for line in lines),
            },
        )

    def _collect_workbook_rows(
        self,
        workbook: Any,
        *,
        selected_sheet: str | None,
    ) -> tuple[dict[str, list[tuple[Any, ...]]], list[str]]:
        sheet_names = list(workbook.sheetnames)
        if len(sheet_names) > self.limits.max_sheets:
            raise ArtifactValidationError("workbook exceeds max_sheets")
        if selected_sheet is not None and selected_sheet not in sheet_names:
            raise ArtifactSchemaError(f"workbook sheet not found: {selected_sheet}")
        collected: dict[str, list[tuple[Any, ...]]] = {}
        formula_cells: list[str] = []
        for sheet_name in sheet_names:
            if selected_sheet is not None and sheet_name != selected_sheet:
                continue
            worksheet = workbook[sheet_name]
            if worksheet.max_column and worksheet.max_column > self.limits.max_columns:
                raise ArtifactSchemaError(
                    f"worksheet {sheet_name!r} exceeds max_columns"
                )
            if worksheet.max_row and worksheet.max_row > self.limits.max_rows + 1:
                raise ArtifactSchemaError(
                    f"worksheet {sheet_name!r} exceeds max_rows"
                )
            rows: list[tuple[Any, ...]] = []
            for row_number, row in enumerate(
                worksheet.iter_rows(values_only=False), start=1
            ):
                values = tuple(cell.value for cell in row)
                if len(values) > self.limits.max_columns:
                    raise ArtifactSchemaError(
                        f"worksheet {sheet_name!r} exceeds max_columns"
                    )
                for column_number, cell in enumerate(row, start=1):
                    if cell.data_type == "f":
                        formula_cells.append(
                            f"{sheet_name}!{column_number}:{row_number}"
                        )
                if any(value not in (None, "") for value in values):
                    rows.append(values)
                if len(rows) > self.limits.max_rows + 1:
                    raise ArtifactSchemaError("workbook exceeds max_rows")
            if rows:
                collected[sheet_name] = rows
        return collected, formula_cells

    def _parse_workbook(
        self,
        source: SourceReceipt,
        data: bytes,
        *,
        expected_columns: Sequence[str] | None,
        allow_formulas: bool,
        allow_external_links: bool,
        sheet: str | None,
    ) -> ParsedArtifact:
        _validate_zip_container(data, self.limits, "OOXML workbook")
        try:
            from openpyxl import load_workbook
            with zipfile.ZipFile(io.BytesIO(data), "r") as archive:
                external_link_members = [
                    name for name in archive.namelist()
                    if name.casefold().startswith("xl/externallinks/")
                ]
            if external_link_members and not allow_external_links:
                raise ArtifactSchemaError(
                    "workbook external-link policy violation"
                )
            workbook = load_workbook(
                io.BytesIO(data),
                read_only=True,
                data_only=False,
                keep_links=False,
                keep_vba=source.format == "xlsm",
            )
            try:
                collected, formula_cells = self._collect_workbook_rows(
                    workbook, selected_sheet=sheet
                )
            finally:
                workbook.close()
            if formula_cells and not allow_formulas:
                raise ArtifactSchemaError(
                    f"workbook formula policy violation ({len(formula_cells)} cells)"
                )
            if not collected:
                raise ArtifactSchemaError("workbook has no non-empty worksheet")
            selected_name = sheet or next(iter(collected))
            rows = collected[selected_name]
            table: TableData
            if formula_cells and allow_formulas:
                cached = load_workbook(
                    io.BytesIO(data), read_only=True, data_only=True,
                    keep_links=False, keep_vba=source.format == "xlsm",
                )
                try:
                    cached_rows, _ = self._collect_workbook_rows(
                        cached, selected_sheet=selected_name
                    )
                finally:
                    cached.close()
                rows = cached_rows.get(selected_name, rows)
            table = _make_table(rows, self.limits, expected_columns=expected_columns)
            return ParsedArtifact(
                source=source,
                kind="table",
                details={
                    "workbook_format": source.format,
                    "sheet_name": selected_name,
                    "sheet_count": len(collected),
                    "formula_cells": len(formula_cells),
                    "external_links": len(external_link_members),
                    "schema": list(table.headers),
                },
                table=table,
            )
        except (ArtifactValidationError, ArtifactParseError):
            raise
        except Exception as error:  # noqa: BLE001 - parser boundary
            raise ArtifactParseError(
                f"openpyxl could not parse {source.source_ref}: {error}"
            ) from error

    def _parse_pptx(self, source: SourceReceipt, data: bytes) -> ParsedArtifact:
        _validate_zip_container(data, self.limits, "PPTX")
        try:
            from pptx import Presentation
            presentation = Presentation(io.BytesIO(data))
            if len(presentation.slides) > self.limits.max_slides:
                raise ArtifactValidationError("PPTX exceeds max_slides")
            texts: list[str] = []
            shape_count = 0
            for slide in presentation.slides:
                shape_count += len(slide.shapes)
                if shape_count > self.limits.max_shapes:
                    raise ArtifactValidationError("PPTX exceeds max_shapes")
                for shape in slide.shapes:
                    if hasattr(shape, "text"):
                        texts.append(str(shape.text))
            text = "\n".join(texts)
            if len(text) > self.limits.max_text_chars:
                raise ArtifactValidationError("PPTX text exceeds max_text_chars")
            return ParsedArtifact(
                source=source,
                kind="pptx",
                details={
                    "slide_count": len(presentation.slides),
                    "shape_count": shape_count,
                    "text_chars": len(text),
                    "text_sha256": _sha256(text.encode("utf-8")),
                    "text": text,
                    "text_blocks": texts,
                },
            )
        except ArtifactValidationError:
            raise
        except Exception as error:  # noqa: BLE001 - parser boundary
            raise ArtifactParseError(
                f"python-pptx could not parse {source.source_ref}: {error}"
            ) from error

    def _parse_image(self, source: SourceReceipt, data: bytes) -> ParsedArtifact:
        try:
            from PIL import Image, UnidentifiedImageError
            from PIL.Image import DecompressionBombError, DecompressionBombWarning
            old_limit = Image.MAX_IMAGE_PIXELS
            Image.MAX_IMAGE_PIXELS = self.limits.max_pixels
            try:
                with warnings.catch_warnings():
                    warnings.simplefilter("error", DecompressionBombWarning)
                    with Image.open(io.BytesIO(data)) as image:
                        width, height = image.size
                        if width * height > self.limits.max_pixels:
                            raise ArtifactValidationError("image exceeds max_pixels")
                        image.load()
                        fmt = str(image.format or source.format).lower()
                        mode = image.mode
            finally:
                Image.MAX_IMAGE_PIXELS = old_limit
            return ParsedArtifact(
                source=source,
                kind="image",
                details={
                    "format": fmt,
                    "width": width,
                    "height": height,
                    "pixels": width * height,
                    "mode": mode,
                },
            )
        except ArtifactValidationError:
            raise
        except Exception as error:  # noqa: BLE001 - parser boundary
            raise ArtifactParseError(
                f"Pillow could not parse {source.source_ref}: {error}"
            ) from error

    def summarize_table(
        self,
        parsed: ParsedArtifact,
        *,
        group_by: str | None = None,
        sum_column: str | None = None,
    ) -> dict[str, Any]:
        if parsed.table is None:
            raise ArtifactSchemaError("table summary requires a tabular source")
        table = parsed.table
        headers = table.headers
        for requested, label in ((group_by, "group_by"), (sum_column, "sum_column")):
            if requested is not None and requested not in headers:
                raise ArtifactSchemaError(
                    f"{label} column {requested!r} is not in the captured schema"
                )
        missing = {
            header: sum(row[index] in (None, "") for row in table.rows)
            for index, header in enumerate(headers)
        }
        result: dict[str, Any] = {
            "source_ref": parsed.source.source_ref,
            "source_sha256": parsed.source.sha256,
            "format": parsed.source.format,
            "headers": list(headers),
            "row_count": table.row_count,
            "column_count": table.column_count,
            "missing_by_column": missing,
        }
        if parsed.details.get("sheet_name") is not None:
            result["sheet_name"] = parsed.details["sheet_name"]
        if group_by is not None:
            index = headers.index(group_by)
            groups: dict[str, int] = {}
            for row in table.rows:
                key = "<missing>" if row[index] in (None, "") else str(row[index])
                if len(key) > self.limits.max_group_key_chars:
                    raise ArtifactSchemaError(
                        f"{group_by} contains a value exceeding max_group_key_chars"
                    )
                if key not in groups and len(groups) >= self.limits.max_groups:
                    raise ArtifactSchemaError("table exceeds max_groups")
                groups[key] = groups.get(key, 0) + 1
            result["group_by"] = group_by
            result["group_counts"] = dict(sorted(groups.items()))
        if sum_column is not None:
            index = headers.index(sum_column)
            total = Decimal("0")
            values_count = 0
            for row_number, row in enumerate(table.rows, start=2):
                value = row[index]
                if value in (None, ""):
                    continue
                if isinstance(value, bool):
                    raise ArtifactSchemaError(
                        f"{sum_column} row {row_number} is boolean, not numeric"
                    )
                try:
                    number = Decimal(str(value).replace(",", "").strip())
                except (InvalidOperation, ValueError) as error:
                    raise ArtifactSchemaError(
                        f"{sum_column} row {row_number} is not numeric"
                    ) from error
                total += number
                values_count += 1
            result["sum_column"] = sum_column
            result["sum_value"] = format(total, "f")
            result["sum_value_count"] = values_count
        return result

    @staticmethod
    def _computed_operand(
        operand: Any,
        row: Mapping[str, Any],
    ) -> Any:
        if isinstance(operand, Mapping):
            if set(operand) == {"column"}:
                column = operand["column"]
                if not isinstance(column, str) or column not in row:
                    raise ArtifactSchemaError(
                        f"computed column operand references unknown column {column!r}"
                    )
                return row[column]
            if set(operand) == {"value"}:
                return operand["value"]
            raise ArtifactSchemaError(
                "computed operand must use exactly one of column or value"
            )
        return operand

    @staticmethod
    def _decimal_operand(value: Any, label: str) -> Decimal:
        if isinstance(value, bool) or value in (None, ""):
            raise ArtifactSchemaError(f"{label} must be numeric")
        try:
            return Decimal(str(value).replace(",", "").strip())
        except (InvalidOperation, ValueError) as error:
            raise ArtifactSchemaError(f"{label} must be numeric") from error

    @staticmethod
    def _decimal_result(value: Decimal) -> int | float:
        if value == value.to_integral_value():
            return int(value)
        return float(value)

    def _evaluate_computed(
        self,
        name: str,
        spec: Mapping[str, Any],
        row: Mapping[str, Any],
    ) -> Any:
        op = spec.get("op")
        if op == "concat":
            values = spec.get("values")
            if (
                not isinstance(values, Sequence)
                or isinstance(values, (str, bytes))
                or not values
            ):
                raise ArtifactSchemaError(
                    f"computed column {name!r} concat requires values"
                )
            return "".join(
                str(self._computed_operand(value, row) or "")
                for value in values
            )
        if op not in {"add", "subtract", "multiply", "divide"}:
            raise ArtifactSchemaError(
                f"computed column {name!r} has unsupported op {op!r}"
            )
        if "left" not in spec or "right" not in spec:
            raise ArtifactSchemaError(
                f"computed column {name!r} requires left and right operands"
            )
        left = self._decimal_operand(
            self._computed_operand(spec["left"], row), f"{name}.left"
        )
        right = self._decimal_operand(
            self._computed_operand(spec["right"], row), f"{name}.right"
        )
        if op == "add":
            result = left + right
        elif op == "subtract":
            result = left - right
        elif op == "multiply":
            result = left * right
        else:
            if right == 0:
                raise ArtifactSchemaError(
                    f"computed column {name!r} divides by zero"
                )
            result = left / right
        return self._decimal_result(result)

    def _filter_matches(
        self,
        row: Mapping[str, Any],
        filter_spec: Mapping[str, Any],
    ) -> bool:
        column = filter_spec.get("column")
        op = filter_spec.get("op", "eq")
        if not isinstance(column, str) or column not in row:
            raise ArtifactSchemaError(
                f"filter references unknown column {column!r}"
            )
        if "value" not in filter_spec:
            raise ArtifactSchemaError("filter requires a value")
        actual = row[column]
        expected = filter_spec["value"]
        if op == "eq":
            return _json_safe(actual) == _json_safe(expected)
        if op == "ne":
            return _json_safe(actual) != _json_safe(expected)
        if op == "contains":
            return str(expected) in str(actual or "")
        if op == "in":
            if (
                not isinstance(expected, Sequence)
                or isinstance(expected, (str, bytes))
            ):
                raise ArtifactSchemaError("filter in value must be a sequence")
            return actual in expected
        if op not in {"gt", "gte", "lt", "lte"}:
            raise ArtifactSchemaError(f"unsupported filter op {op!r}")
        try:
            left: Any = self._decimal_operand(actual, f"filter {column}")
            right: Any = self._decimal_operand(expected, f"filter {column}")
        except ArtifactSchemaError:
            left = str(actual)
            right = str(expected)
        if op == "gt":
            return left > right
        if op == "gte":
            return left >= right
        if op == "lt":
            return left < right
        return left <= right

    def transform_table(
        self,
        parsed: ParsedArtifact,
        *,
        select_columns: Sequence[str] | None = None,
        filters: Sequence[Mapping[str, Any]] = (),
        computed_columns: Mapping[str, Mapping[str, Any]] | None = None,
    ) -> ParsedArtifact:
        """Apply a deterministic bounded table projection and transformation."""
        if parsed.table is None:
            raise ArtifactSchemaError("table transform requires a tabular source")
        if isinstance(select_columns, (str, bytes)):
            raise ArtifactValidationError("select_columns must be a sequence")
        if isinstance(filters, (str, bytes)) or not isinstance(filters, Sequence):
            raise ArtifactValidationError("filters must be a sequence")
        if not all(isinstance(item, Mapping) for item in filters):
            raise ArtifactValidationError("each filter must be an object")
        computed = dict(computed_columns or {})
        if len(filters) + len(computed) > self.limits.max_columns:
            raise ArtifactValidationError("table transform exceeds operation bound")
        existing = list(parsed.table.headers)
        for name, spec in computed.items():
            if not isinstance(name, str) or not name.strip():
                raise ArtifactSchemaError(
                    "computed column names must be non-empty strings"
                )
            if len(name) > self.limits.max_header_chars:
                raise ArtifactSchemaError(
                    "computed column name exceeds max_header_chars"
                )
            if name in existing:
                raise ArtifactSchemaError(
                    f"computed column {name!r} already exists"
                )
            if not isinstance(spec, Mapping):
                raise ArtifactSchemaError(
                    f"computed column {name!r} must be an object"
                )
            existing.append(name)
        selected = tuple(select_columns or existing)
        if not selected:
            raise ArtifactSchemaError("table transform must select at least one column")
        if len(set(selected)) != len(selected):
            raise ArtifactSchemaError("select_columns must be unique")
        unknown = [name for name in selected if name not in existing]
        if unknown:
            raise ArtifactSchemaError(
                f"select_columns contain unknown columns: {unknown!r}"
            )
        if len(selected) > self.limits.max_columns:
            raise ArtifactSchemaError("table transform exceeds max_columns")

        output_rows: list[tuple[Any, ...]] = []
        for source_row in parsed.table.row_dicts():
            if not all(self._filter_matches(source_row, item) for item in filters):
                continue
            derived = dict(source_row)
            for name, spec in computed.items():
                derived[name] = self._evaluate_computed(name, spec, derived)
            output_rows.append(tuple(_json_safe(derived[name]) for name in selected))
            if len(output_rows) > self.limits.max_rows:
                raise ArtifactSchemaError("table transform exceeds max_rows")
            if len(output_rows) * len(selected) > self.limits.max_cells:
                raise ArtifactSchemaError("table transform exceeds max_cells")
        table = TableData(headers=selected, rows=tuple(output_rows))
        return ParsedArtifact(
            source=parsed.source,
            kind="table",
            details={
                "schema": list(table.headers),
                "source_kind": parsed.kind,
                "source_sha256": parsed.source.sha256,
                "transformation": {
                    "select_columns": list(selected),
                    "filters": _json_safe(list(filters)),
                    "computed_columns": _json_safe(computed),
                },
            },
            table=table,
        )

    def _ensure_new_target(self, target_ref: str, source: SourceReceipt | None) -> tuple[Path, str, str]:
        path, relative = self._resolve(target_ref, output=True)
        fmt = self._format_for(path)
        if source is not None and relative.casefold() == source.source_ref.casefold():
            raise ArtifactValidationError("output target must differ from source")
        return path, relative, fmt

    def _source_receipt(
        self,
        source: ParsedArtifact | SourceReceipt | str | Path,
        *,
        formats: set[str],
    ) -> SourceReceipt:
        if isinstance(source, ParsedArtifact):
            receipt = source.source
        elif isinstance(source, SourceReceipt):
            receipt = source
        else:
            receipt = self.capture(str(source))
        if receipt.format not in formats:
            expected = ", ".join(sorted(formats))
            raise ArtifactSchemaError(
                f"source must use one of {expected}; got {receipt.format}"
            )
        self._captured_bytes(receipt)
        return receipt

    def _any_source_receipt(
        self,
        source: ParsedArtifact | SourceReceipt | str | Path,
    ) -> SourceReceipt:
        if isinstance(source, ParsedArtifact):
            receipt = source.source
        elif isinstance(source, SourceReceipt):
            receipt = source
        else:
            receipt = self.capture(str(source))
        self._captured_bytes(receipt)
        return receipt

    def _source_receipts(
        self,
        sources: Sequence[ParsedArtifact | SourceReceipt | str | Path],
    ) -> tuple[SourceReceipt, ...]:
        if isinstance(sources, (str, bytes)) or not isinstance(sources, Sequence):
            raise ArtifactValidationError("sources must be a sequence")
        receipts = tuple(self._any_source_receipt(source) for source in sources)
        if not receipts:
            raise ArtifactValidationError("sources must be non-empty")
        if len(receipts) > self.limits.max_columns:
            raise ArtifactValidationError("source count exceeds operation bound")
        refs = [receipt.source_ref.casefold() for receipt in receipts]
        if len(set(refs)) != len(refs):
            raise ArtifactValidationError("sources cannot contain duplicates")
        return receipts

    @staticmethod
    def _lineage_hash(receipts: Sequence[SourceReceipt]) -> str:
        payload = [
            {
                "source_ref": receipt.source_ref,
                "sha256": receipt.sha256,
                "size_bytes": receipt.size_bytes,
            }
            for receipt in receipts
        ]
        return _sha256(
            json.dumps(
                payload,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        )

    @staticmethod
    def _canonical_zip(data: bytes) -> bytes:
        """Normalize OOXML package metadata so retries have stable bytes."""
        namespaces = {
            "cp": "http://schemas.openxmlformats.org/package/2006/metadata/core-properties",
            "dc": "http://purl.org/dc/elements/1.1/",
            "dcterms": "http://purl.org/dc/terms/",
            "dcmitype": "http://purl.org/dc/dcmitype/",
            "xsi": "http://www.w3.org/2001/XMLSchema-instance",
        }
        for prefix, uri in namespaces.items():
            ET.register_namespace(prefix, uri)
        source = io.BytesIO(data)
        target = io.BytesIO()
        with zipfile.ZipFile(source, "r") as archive, zipfile.ZipFile(
            target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9
        ) as normalized:
            for name in sorted(archive.namelist()):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.create_system = 0
                info.external_attr = 0
                info.extra = b""
                info.comment = b""
                payload = archive.read(name)
                if name.casefold() == "docprops/core.xml":
                    core = ET.fromstring(payload)
                    for tag in ("created", "modified"):
                        element = core.find(f"{{{namespaces['dcterms']}}}{tag}")
                        if element is not None:
                            element.text = "2000-01-01T00:00:00Z"
                    payload = ET.tostring(
                        core,
                        encoding="utf-8",
                        xml_declaration=False,
                        short_empty_elements=True,
                    )
                normalized.writestr(info, payload)
        return target.getvalue()

    def _write_output(
        self,
        data: bytes,
        *,
        target_ref: str,
        source_sha256: str | None,
        idempotency_key: str,
        format: str,
        readback: Mapping[str, Any],
    ) -> OutputReceipt:
        if len(data) > self.limits.max_source_bytes:
            raise ArtifactValidationError(
                f"output exceeds max_source_bytes ({self.limits.max_source_bytes})"
            )
        path, relative = self._resolve(target_ref, output=True)
        desired_hash = _sha256(data)
        preserved: dict[str, Any] | None = None
        overwrite = False
        if self.repair_existing and path.is_file():
            prior = path.read_bytes()
            prior_hash = _sha256(prior)
            if prior_hash != desired_hash:
                repair_token = hashlib.sha256(
                    f"{relative}:{prior_hash}".encode("utf-8")
                ).hexdigest()[:20]
                backup_ref = (
                    PurePosixPath(".jarvis-general-work")
                    / "repairs"
                    / repair_token
                    / f"{path.name}.{prior_hash[:16]}.bak"
                ).as_posix()
                try:
                    backup_attempt = self.filesystem.request_bytes(
                        backup_ref,
                        prior,
                        idempotency_key=(
                            f"{idempotency_key}:preserve:{prior_hash}"
                        ),
                        overwrite=False,
                    )
                except FilesystemConnectorError as error:
                    raise ArtifactWriteError(
                        f"prior output preservation failed: {error}"
                    ) from error
                if backup_attempt.outcome != "COMPLETED":
                    raise ArtifactWriteError(
                        "prior output preservation "
                        f"{backup_attempt.outcome}: "
                        f"{backup_attempt.error_code or 'unknown'}"
                    )
                preserved = {
                    "target_ref": relative,
                    "sha256": prior_hash,
                    "backup_ref": backup_ref,
                }
                overwrite = True
        try:
            attempt = self.filesystem.request_bytes(
                relative,
                data,
                idempotency_key=idempotency_key,
                overwrite=overwrite,
            )
        except FilesystemConnectorError as error:
            raise ArtifactWriteError(str(error)) from error
        if attempt.outcome != "COMPLETED":
            raise ArtifactWriteError(
                f"output write {attempt.outcome}: {attempt.error_code or 'unknown'}"
            )
        actual = path.read_bytes()
        output_hash = _sha256(actual)
        if output_hash != desired_hash:
            raise ArtifactWriteError("output hash changed during write")
        bounded_readback = dict(readback)
        if preserved is not None:
            bounded_readback["preserved_prior_output"] = preserved
        return OutputReceipt(
            target_ref=relative,
            path=str(path),
            format=format,
            sha256=output_hash,
            size_bytes=len(actual),
            source_sha256=source_sha256,
            idempotency_key=idempotency_key,
            readback=bounded_readback,
            reconciled=attempt.reconciled or preserved is not None,
            replayed=attempt.replayed,
        )

    @staticmethod
    def _validate_sheet_name(sheet_name: str) -> str:
        if not isinstance(sheet_name, str) or not sheet_name.strip():
            raise ArtifactValidationError("sheet_name must be a non-empty string")
        value = sheet_name.strip()
        if len(value) > 31 or any(character in value for character in r"[]:*?/\\"):
            raise ArtifactValidationError("sheet_name is not valid for Excel")
        return value

    @staticmethod
    def xlsx_rendered_table(table: TableData) -> TableData:
        """Return the exact table values written to a newly created workbook."""
        if not isinstance(table, TableData):
            raise ArtifactSchemaError("XLSX rendering requires TableData")
        numeric_columns = _xlsx_numeric_columns(table)
        return TableData(
            headers=table.headers,
            rows=tuple(
                tuple(
                    _xlsx_display_value(
                        value,
                        numeric_column=column_index in numeric_columns,
                    )
                    for column_index, value in enumerate(row)
                )
                for row in table.rows
            ),
        )

    def _style_worksheet(self, worksheet: Any, *, header_row: int = 1) -> None:
        from openpyxl.styles import Alignment, Font, PatternFill
        from openpyxl.utils import get_column_letter

        if worksheet.max_column > self.limits.max_columns:
            raise ArtifactSchemaError("worksheet exceeds max_columns")
        if worksheet.max_row > self.limits.max_rows + 1:
            raise ArtifactSchemaError("worksheet exceeds max_rows")
        header_fill = PatternFill("solid", fgColor="1F4E78")
        stripe_fill = PatternFill("solid", fgColor="EAF2F8")
        for cell in worksheet[header_row]:
            cell.fill = header_fill
            cell.font = Font(name="Aptos", size=11, bold=True, color="FFFFFF")
            cell.alignment = Alignment(horizontal="center", vertical="center")
        worksheet.row_dimensions[header_row].height = 24
        worksheet.freeze_panes = f"A{header_row + 1}"
        if worksheet.max_column:
            worksheet.auto_filter.ref = (
                f"A{header_row}:"
                f"{get_column_letter(worksheet.max_column)}{worksheet.max_row}"
            )
        worksheet.sheet_view.showGridLines = False
        worksheet.print_title_rows = f"{header_row}:{header_row}"
        for row in worksheet.iter_rows(min_row=header_row + 1):
            for cell in row:
                cell.font = Font(name="Aptos", size=10)
                cell.alignment = Alignment(vertical="center")
                if cell.row % 2 == 0:
                    cell.fill = stripe_fill
                if isinstance(cell.value, (int, float)) and not isinstance(
                    cell.value, bool
                ):
                    cell.number_format = (
                        "#,##0"
                        if isinstance(cell.value, int) or cell.value.is_integer()
                        else "#,##0.##"
                    )
        for column_index in range(1, worksheet.max_column + 1):
            values = [
                "" if cell.value is None else str(cell.value)
                for cell in worksheet.iter_cols(
                    min_col=column_index, max_col=column_index
                ).__next__()
            ]
            width = min(40, max(10, max((len(value) for value in values), default=0) + 2))
            worksheet.column_dimensions[get_column_letter(column_index)].width = width

    def compose_tables(
        self,
        parsed_sources: Sequence[ParsedArtifact],
        *,
        column_aliases: Mapping[str, str] | None = None,
        select_columns: Sequence[str] | None = None,
        deduplicate_by: Sequence[str] = (),
        sort_by: Sequence[str] = (),
        source_column: str | None = None,
        trim_strings: bool = True,
    ) -> tuple[TableData, dict[str, Any]]:
        """Compose compatible captured tables with explicit normalization."""

        if (
            isinstance(parsed_sources, (str, bytes))
            or not isinstance(parsed_sources, Sequence)
            or not parsed_sources
        ):
            raise ArtifactValidationError(
                "parsed_sources must be a non-empty sequence"
            )
        if not isinstance(trim_strings, bool):
            raise ArtifactValidationError("trim_strings must be boolean")
        aliases = dict(column_aliases or {})
        if any(
            not isinstance(key, str)
            or not key.strip()
            or not isinstance(value, str)
            or not value.strip()
            for key, value in aliases.items()
        ):
            raise ArtifactValidationError(
                "column_aliases require non-empty string names"
            )
        alias_by_fold = {
            key.strip().casefold(): value.strip()
            for key, value in aliases.items()
        }
        if isinstance(select_columns, (str, bytes)):
            raise ArtifactValidationError("select_columns must be a sequence")
        if isinstance(deduplicate_by, (str, bytes)):
            raise ArtifactValidationError("deduplicate_by must be a sequence")
        if isinstance(sort_by, (str, bytes)):
            raise ArtifactValidationError("sort_by must be a sequence")
        selected = tuple(str(value).strip() for value in (select_columns or ()))
        dedupe = tuple(str(value).strip() for value in deduplicate_by)
        sorting = tuple(str(value).strip() for value in sort_by)
        if any(not value for value in (*selected, *dedupe, *sorting)):
            raise ArtifactValidationError(
                "column selections must use non-empty names"
            )
        if source_column is not None:
            if not isinstance(source_column, str) or not source_column.strip():
                raise ArtifactValidationError(
                    "source_column must be non-empty text"
                )
            source_column = source_column.strip()

        normalized: list[tuple[ParsedArtifact, tuple[str, ...]]] = []
        union: list[str] = []
        seen_union: set[str] = set()
        for parsed in parsed_sources:
            if not isinstance(parsed, ParsedArtifact) or parsed.table is None:
                raise ArtifactSchemaError(
                    "table composition requires only tabular sources"
                )
            headers = tuple(
                alias_by_fold.get(header.strip().casefold(), header.strip())
                for header in parsed.table.headers
            )
            if len({header.casefold() for header in headers}) != len(headers):
                raise ArtifactSchemaError(
                    f"column aliases collide for {parsed.source.source_ref}"
                )
            normalized.append((parsed, headers))
            for header in headers:
                folded = header.casefold()
                if folded not in seen_union:
                    seen_union.add(folded)
                    union.append(header)
        columns = selected or tuple(union)
        if source_column is not None and source_column.casefold() in {
            column.casefold() for column in columns
        }:
            raise ArtifactSchemaError(
                "source_column collides with an output column"
            )
        output_headers = (
            (*columns, source_column) if source_column is not None else columns
        )
        if not output_headers:
            raise ArtifactSchemaError(
                "table composition requires at least one output column"
            )
        if len(output_headers) > self.limits.max_columns:
            raise ArtifactSchemaError("composed table exceeds max_columns")
        available = {header.casefold(): header for header in union}
        unknown = [
            column
            for column in (*columns, *dedupe, *sorting)
            if column.casefold() not in available
        ]
        if unknown:
            raise ArtifactSchemaError(
                f"table composition references unknown columns: {unknown!r}"
            )
        canonical = {
            column.casefold(): column
            for column in columns
        }
        output_rows: list[tuple[Any, ...]] = []
        for parsed, headers in normalized:
            for source_row in parsed.table.rows:
                row = {
                    canonical_header.casefold(): value
                    for canonical_header, value in zip(headers, source_row)
                }
                values: list[Any] = []
                for column in columns:
                    value = row.get(column.casefold())
                    if trim_strings and isinstance(value, str):
                        value = value.strip()
                    values.append(value)
                if not any(value not in (None, "") for value in values):
                    continue
                if source_column is not None:
                    values.append(parsed.source.source_ref)
                output_rows.append(tuple(_json_safe(value) for value in values))
                if len(output_rows) > self.limits.max_rows:
                    raise ArtifactSchemaError("composed table exceeds max_rows")
                if len(output_rows) * len(output_headers) > self.limits.max_cells:
                    raise ArtifactSchemaError("composed table exceeds max_cells")

        if dedupe:
            dedupe_indexes = [
                next(
                    index
                    for index, header in enumerate(output_headers)
                    if header.casefold() == column.casefold()
                )
                for column in dedupe
            ]
            unique_rows: list[tuple[Any, ...]] = []
            seen_keys: set[tuple[str, ...]] = set()
            for row in output_rows:
                key = tuple(
                    json.dumps(
                        _json_safe(row[index]),
                        ensure_ascii=True,
                        sort_keys=True,
                        separators=(",", ":"),
                    )
                    for index in dedupe_indexes
                )
                if key in seen_keys:
                    continue
                seen_keys.add(key)
                unique_rows.append(row)
            output_rows = unique_rows
        if sorting:
            sort_indexes = [
                next(
                    index
                    for index, header in enumerate(output_headers)
                    if header.casefold() == column.casefold()
                )
                for column in sorting
            ]
            output_rows.sort(
                key=lambda row: tuple(
                    (row[index] is None, str(row[index] or "").casefold())
                    for index in sort_indexes
                )
            )
        table = TableData(
            headers=tuple(output_headers),
            rows=tuple(output_rows),
        )
        details = {
            "source_refs": [
                parsed.source.source_ref for parsed, _headers in normalized
            ],
            "column_aliases": dict(sorted(aliases.items())),
            "selected_columns": list(columns),
            "deduplicate_by": list(dedupe),
            "sort_by": list(sorting),
            "source_column": source_column,
            "trim_strings": trim_strings,
            "row_count": table.row_count,
            "column_count": table.column_count,
        }
        return table, details

    def merge_tables(
        self,
        parsed_sources: Sequence[ParsedArtifact],
        *,
        target_ref: str,
        idempotency_key: str,
        column_aliases: Mapping[str, str] | None = None,
        select_columns: Sequence[str] | None = None,
        deduplicate_by: Sequence[str] = (),
        sort_by: Sequence[str] = (),
        source_column: str | None = None,
        trim_strings: bool = True,
        fault_after_write: bool = False,
    ) -> OutputReceipt:
        """Write one deterministic normalized table from captured sources."""

        receipts = self._source_receipts(
            tuple(parsed.source for parsed in parsed_sources)
        )
        table, transformation = self.compose_tables(
            parsed_sources,
            column_aliases=column_aliases,
            select_columns=select_columns,
            deduplicate_by=deduplicate_by,
            sort_by=sort_by,
            source_column=source_column,
            trim_strings=trim_strings,
        )
        _, relative, fmt = self._ensure_new_target(target_ref, receipts[0])
        if fmt not in {"csv", "tsv", "json", "xlsx"}:
            raise ArtifactValidationError(
                "merged table target must use .csv, .tsv, .json, or .xlsx"
            )
        for receipt in receipts:
            self._assert_source_current(receipt)
        if fmt in {"csv", "tsv"}:
            delimiter = "\t" if fmt == "tsv" else ","
            output = io.StringIO(newline="")
            writer = csv.writer(output, delimiter=delimiter, lineterminator="\n")
            writer.writerow(table.headers)
            writer.writerows(table.rows)
            data = output.getvalue().encode("utf-8")
        elif fmt == "json":
            records = [
                {
                    header: _json_safe(row[index])
                    for index, header in enumerate(table.headers)
                }
                for row in table.rows
            ]
            data = (
                json.dumps(
                    {"records": records},
                    ensure_ascii=True,
                    indent=2,
                    sort_keys=False,
                )
                + "\n"
            ).encode("utf-8")
        else:
            try:
                from openpyxl import Workbook

                workbook = Workbook()
                workbook.properties.created = datetime(2000, 1, 1)
                workbook.properties.modified = datetime(2000, 1, 1)
                worksheet = workbook.active
                worksheet.title = "Data"
                for row_index, row in enumerate(
                    (table.headers, *table.rows),
                    start=1,
                ):
                    for column_index, value in enumerate(row, start=1):
                        _set_xlsx_value(
                            worksheet.cell(row_index, column_index),
                            value,
                        )
                self._style_worksheet(worksheet)
                output = io.BytesIO()
                workbook.save(output)
                workbook.close()
                data = self._canonical_zip(output.getvalue())
            except Exception as error:  # noqa: BLE001 - workbook boundary
                raise ArtifactWriteError(
                    f"openpyxl merged table creation failed: {error}"
                ) from error
        lineage_hash = self._lineage_hash(receipts)
        written = self._write_output(
            data,
            target_ref=relative,
            source_sha256=lineage_hash,
            idempotency_key=idempotency_key,
            format=fmt,
            readback={},
        )
        if fault_after_write:
            raise InjectedArtifactFailure(
                "injected failure after merged table creation before verification",
                evidence=_post_write_evidence(written),
            )
        for receipt in receipts:
            self._assert_source_current(receipt)
        parsed_output = self.parse(
            relative,
            expected_columns=table.headers,
            allow_formulas=fmt == "xlsx",
        )
        expected_rows = table.rows
        if fmt in {"csv", "tsv"}:
            expected_rows = tuple(
                tuple("" if value is None else str(value) for value in row)
                for row in table.rows
            )
        verified = (
            parsed_output.table is not None
            and parsed_output.table.headers == table.headers
            and parsed_output.table.rows == expected_rows
        )
        readback = {
            "verified": verified,
            "target_ref": relative,
            "sha256": written.sha256,
            "source_refs": [receipt.source_ref for receipt in receipts],
            "source_sha256s": [receipt.sha256 for receipt in receipts],
            "lineage_hash": lineage_hash,
            "row_count": table.row_count,
            "column_count": table.column_count,
            "transformation": transformation,
        }
        if not verified:
            raise ArtifactPostWriteError(
                "merged table readback differs from requested composition",
                evidence=_post_write_evidence(written, readback),
            )
        return OutputReceipt(
            target_ref=written.target_ref,
            path=written.path,
            format=written.format,
            sha256=written.sha256,
            size_bytes=written.size_bytes,
            source_sha256=written.source_sha256,
            idempotency_key=written.idempotency_key,
            readback=readback,
            reconciled=written.reconciled,
            replayed=written.replayed,
        )

    def _verify_xlsx(
        self,
        target_ref: str,
        *,
        expected_sha256: str,
        sheet_name: str,
        expected_cells: Mapping[str, Any],
    ) -> dict[str, Any]:
        path, relative = self._resolve(target_ref)
        data = path.read_bytes()
        digest = _sha256(data)
        if digest != expected_sha256:
            return {
                "verified": False,
                "target_ref": relative,
                "sha256": digest,
                "error_code": "output_hash_mismatch",
            }
        _validate_zip_container(data, self.limits, "OOXML workbook")
        try:
            from openpyxl import load_workbook

            workbook = load_workbook(
                io.BytesIO(data), data_only=False, read_only=False, keep_links=False
            )
            try:
                if sheet_name not in workbook.sheetnames:
                    return {
                        "verified": False,
                        "target_ref": relative,
                        "sha256": digest,
                        "error_code": "sheet_missing",
                    }
                worksheet = workbook[sheet_name]
                mismatches = {
                    cell_ref: {
                        "expected": _json_safe(expected),
                        "actual": _json_safe(worksheet[cell_ref].value),
                    }
                    for cell_ref, expected in expected_cells.items()
                    if _json_safe(worksheet[cell_ref].value) != _json_safe(expected)
                }
                formula_count = sum(
                    isinstance(cell.value, str) and cell.value.startswith("=")
                    for row in worksheet.iter_rows()
                    for cell in row
                )
                styled_header = bool(
                    worksheet.max_column
                    and worksheet.cell(1, 1).font.bold
                    and worksheet.cell(1, 1).fill.fill_type == "solid"
                )
                readback = {
                    "verified": not mismatches,
                    "target_ref": relative,
                    "sha256": digest,
                    "sheet_name": sheet_name,
                    "sheet_count": len(workbook.sheetnames),
                    "max_row": worksheet.max_row,
                    "max_column": worksheet.max_column,
                    "formula_count": formula_count,
                    "freeze_panes": str(worksheet.freeze_panes or ""),
                    "auto_filter": worksheet.auto_filter.ref,
                    "styled_header": styled_header,
                    "mismatches": mismatches,
                }
            finally:
                workbook.close()
            return readback
        except Exception as error:  # noqa: BLE001 - workbook boundary
            raise ArtifactParseError(
                f"openpyxl readback failed for {relative}: {error}"
            ) from error

    def create_xlsx(
        self,
        parsed: ParsedArtifact,
        *,
        target_ref: str,
        idempotency_key: str,
        sheet_name: str = "Data",
        formulas: Mapping[str, str] | None = None,
        fault_after_write: bool = False,
    ) -> OutputReceipt:
        """Create a formatted workbook from a captured table."""
        if parsed.table is None:
            raise ArtifactSchemaError("XLSX creation requires a tabular source")
        _, relative, fmt = self._ensure_new_target(target_ref, parsed.source)
        if fmt != "xlsx":
            raise ArtifactValidationError("XLSX target must use .xlsx")
        sheet_name = self._validate_sheet_name(sheet_name)
        formula_map = dict(formulas or {})
        if len(formula_map) > self.limits.max_cells:
            raise ArtifactValidationError("XLSX formulas exceed max_cells")
        self._assert_source_current(parsed.source)
        expected_cells: dict[str, Any] = {}
        try:
            from openpyxl import Workbook
            from openpyxl.utils.cell import coordinate_to_tuple

            workbook = Workbook()
            workbook.properties.creator = "JARVIS Foundry"
            workbook.properties.created = datetime(2000, 1, 1)
            workbook.properties.modified = datetime(2000, 1, 1)
            worksheet = workbook.active
            worksheet.title = sheet_name
            rendered_table = self.xlsx_rendered_table(parsed.table)
            for row_index, row in enumerate(
                (rendered_table.headers, *rendered_table.rows), start=1
            ):
                for column_index, value in enumerate(row, start=1):
                    cell = worksheet.cell(row_index, column_index)
                    _set_xlsx_value(cell, value)
                    expected_cells[cell.coordinate] = value
            for cell_ref in sorted(formula_map):
                if not isinstance(cell_ref, str):
                    raise ArtifactValidationError(
                        "XLSX formula cell references must be strings"
                    )
                formula = formula_map[cell_ref]
                if (
                    not isinstance(formula, str)
                    or not formula.startswith("=")
                    or ":" in cell_ref
                ):
                    raise ArtifactValidationError(
                        "XLSX formulas require exact cells and '=' expressions"
                    )
                try:
                    row_index, column_index = coordinate_to_tuple(cell_ref)
                except ValueError as error:
                    raise ArtifactValidationError(
                        f"invalid XLSX cell reference {cell_ref!r}"
                    ) from error
                if (
                    row_index > self.limits.max_rows + 1
                    or column_index > self.limits.max_columns
                ):
                    raise ArtifactValidationError(
                        f"XLSX cell {cell_ref!r} exceeds workbook bounds"
                    )
                _set_xlsx_value(worksheet[cell_ref], formula, formula=True)
                expected_cells[cell_ref.upper()] = formula
            self._style_worksheet(worksheet)
            output = io.BytesIO()
            workbook.save(output)
            workbook.close()
            data = self._canonical_zip(output.getvalue())
        except (ArtifactValidationError, ArtifactSchemaError):
            raise
        except Exception as error:  # noqa: BLE001 - workbook boundary
            raise ArtifactWriteError(f"openpyxl XLSX creation failed: {error}") from error
        written = self._write_output(
            data,
            target_ref=relative,
            source_sha256=parsed.source.sha256,
            idempotency_key=idempotency_key,
            format="xlsx",
            readback={},
        )
        if fault_after_write:
            raise InjectedArtifactFailure(
                "injected failure after XLSX creation before verification",
                evidence=_post_write_evidence(written),
            )
        try:
            self._assert_source_current(parsed.source)
            readback = self._verify_xlsx(
                relative,
                expected_sha256=written.sha256,
                sheet_name=sheet_name,
                expected_cells=expected_cells,
            )
        except LocalArtifactError as error:
            raise ArtifactPostWriteError(
                f"XLSX post-write verification failed: {error}",
                evidence=_post_write_evidence(written),
            ) from error
        if not readback.get("verified"):
            raise ArtifactPostWriteError(
                "XLSX readback differs from requested workbook",
                evidence=_post_write_evidence(written, readback),
            )
        return OutputReceipt(
            target_ref=written.target_ref, path=written.path, format=written.format,
            sha256=written.sha256, size_bytes=written.size_bytes,
            source_sha256=written.source_sha256, idempotency_key=written.idempotency_key,
            readback=readback, reconciled=written.reconciled, replayed=written.replayed,
        )

    def update_xlsx(
        self,
        source: ParsedArtifact | SourceReceipt | str | Path,
        *,
        target_ref: str,
        idempotency_key: str,
        sheet_name: str,
        cells: Mapping[str, Any] | None = None,
        ranges: Mapping[str, Sequence[Sequence[Any]]] | None = None,
        formulas: Mapping[str, str] | None = None,
    ) -> OutputReceipt:
        """Apply exact cell/range edits to a new source-preserving workbook."""
        receipt = self._source_receipt(source, formats={"xlsx"})
        _, relative, fmt = self._ensure_new_target(target_ref, receipt)
        if fmt != receipt.format:
            raise ArtifactValidationError(
                "updated workbook target format must match the source"
            )
        sheet_name = self._validate_sheet_name(sheet_name)
        cell_updates = dict(cells or {})
        range_updates = dict(ranges or {})
        formula_updates = dict(formulas or {})
        if not cell_updates and not range_updates and not formula_updates:
            raise ArtifactValidationError(
                "XLSX update requires cells, ranges, or formulas"
            )
        data = self._captured_bytes(receipt)
        _validate_zip_container(data, self.limits, "XLSX")
        expected_cells: dict[str, Any] = {}
        touched: set[tuple[int, int]] = set()
        try:
            from openpyxl import load_workbook
            from openpyxl.utils import get_column_letter
            from openpyxl.utils.cell import coordinate_to_tuple, range_boundaries

            workbook = load_workbook(
                io.BytesIO(data),
                data_only=False,
                keep_links=False,
                keep_vba=False,
            )
            if sheet_name not in workbook.sheetnames:
                workbook.close()
                raise ArtifactSchemaError(
                    f"workbook sheet not found: {sheet_name}"
                )
            worksheet = workbook[sheet_name]
            for range_ref in sorted(range_updates):
                if not isinstance(range_ref, str):
                    raise ArtifactValidationError(
                        "XLSX range references must be strings"
                    )
                values = range_updates[range_ref]
                if (
                    not isinstance(values, Sequence)
                    or isinstance(values, (str, bytes))
                ):
                    raise ArtifactValidationError(
                        f"XLSX range {range_ref!r} values must be rows"
                    )
                try:
                    min_col, min_row, max_col, max_row = range_boundaries(range_ref)
                except ValueError as error:
                    raise ArtifactValidationError(
                        f"invalid XLSX range reference {range_ref!r}"
                    ) from error
                if (
                    max_row > self.limits.max_rows + 1
                    or max_col > self.limits.max_columns
                ):
                    raise ArtifactValidationError(
                        f"XLSX range {range_ref!r} exceeds workbook bounds"
                    )
                rows = [list(row) for row in values]
                expected_height = max_row - min_row + 1
                expected_width = max_col - min_col + 1
                if len(rows) != expected_height or any(
                    len(row) != expected_width for row in rows
                ):
                    raise ArtifactValidationError(
                        f"XLSX range {range_ref!r} dimensions do not match values"
                    )
                for row_offset, row in enumerate(rows):
                    for column_offset, value in enumerate(row):
                        coordinate = (
                            min_row + row_offset,
                            min_col + column_offset,
                        )
                        if coordinate in touched:
                            raise ArtifactValidationError(
                                "XLSX update contains overlapping cells or ranges"
                            )
                        touched.add(coordinate)
                        cell = worksheet.cell(*coordinate)
                        _set_xlsx_value(cell, value)
                        expected_cells[cell.coordinate] = value
            for cell_ref in sorted(cell_updates):
                if not isinstance(cell_ref, str):
                    raise ArtifactValidationError(
                        "XLSX cell references must be strings"
                    )
                if ":" in cell_ref:
                    raise ArtifactValidationError(
                        "XLSX cell updates require exact cell references"
                    )
                try:
                    row_index, column_index = coordinate_to_tuple(cell_ref)
                except ValueError as error:
                    raise ArtifactValidationError(
                        f"invalid XLSX cell reference {cell_ref!r}"
                    ) from error
                if (
                    row_index > self.limits.max_rows + 1
                    or column_index > self.limits.max_columns
                ):
                    raise ArtifactValidationError(
                        f"XLSX cell {cell_ref!r} exceeds workbook bounds"
                    )
                if (row_index, column_index) in touched:
                    raise ArtifactValidationError(
                        "XLSX update contains overlapping cells or ranges"
                    )
                touched.add((row_index, column_index))
                normalized = f"{get_column_letter(column_index)}{row_index}"
                _set_xlsx_value(
                    worksheet[normalized],
                    cell_updates[cell_ref],
                )
                expected_cells[normalized] = cell_updates[cell_ref]
            for cell_ref in sorted(formula_updates):
                if not isinstance(cell_ref, str) or ":" in cell_ref:
                    raise ArtifactValidationError(
                        "XLSX formulas require exact string cell references"
                    )
                formula = formula_updates[cell_ref]
                if not isinstance(formula, str) or not formula.startswith("="):
                    raise ArtifactValidationError(
                        "XLSX formulas require '=' expressions"
                    )
                try:
                    row_index, column_index = coordinate_to_tuple(cell_ref)
                except ValueError as error:
                    raise ArtifactValidationError(
                        f"invalid XLSX cell reference {cell_ref!r}"
                    ) from error
                if (
                    row_index > self.limits.max_rows + 1
                    or column_index > self.limits.max_columns
                ):
                    raise ArtifactValidationError(
                        f"XLSX cell {cell_ref!r} exceeds workbook bounds"
                    )
                if (row_index, column_index) in touched:
                    raise ArtifactValidationError(
                        "XLSX update contains overlapping cells, ranges, or formulas"
                    )
                touched.add((row_index, column_index))
                normalized = f"{get_column_letter(column_index)}{row_index}"
                _set_xlsx_value(
                    worksheet[normalized],
                    formula,
                    formula=True,
                )
                expected_cells[normalized] = formula
            if len(touched) > self.limits.max_cells:
                raise ArtifactValidationError("XLSX update exceeds max_cells")
            self._style_worksheet(worksheet)
            workbook.properties.modified = datetime(2000, 1, 1)
            output = io.BytesIO()
            workbook.save(output)
            workbook.close()
            output_data = self._canonical_zip(output.getvalue())
        except (ArtifactValidationError, ArtifactSchemaError):
            raise
        except Exception as error:  # noqa: BLE001 - workbook boundary
            raise ArtifactWriteError(f"openpyxl XLSX update failed: {error}") from error
        written = self._write_output(
            output_data,
            target_ref=relative,
            source_sha256=receipt.sha256,
            idempotency_key=idempotency_key,
            format=fmt,
            readback={},
        )
        try:
            self._assert_source_current(receipt)
            readback = self._verify_xlsx(
                relative,
                expected_sha256=written.sha256,
                sheet_name=sheet_name,
                expected_cells=expected_cells,
            )
        except LocalArtifactError as error:
            raise ArtifactPostWriteError(
                f"XLSX post-write verification failed: {error}",
                evidence=_post_write_evidence(written),
            ) from error
        if not readback.get("verified"):
            raise ArtifactPostWriteError(
                "XLSX readback differs from requested updates",
                evidence=_post_write_evidence(written, readback),
            )
        return OutputReceipt(
            target_ref=written.target_ref, path=written.path, format=written.format,
            sha256=written.sha256, size_bytes=written.size_bytes,
            source_sha256=written.source_sha256, idempotency_key=written.idempotency_key,
            readback=readback, reconciled=written.reconciled, replayed=written.replayed,
        )

    def verify_docx(
        self,
        target_ref: str,
        *,
        expected_sha256: str | None = None,
        required_values: Sequence[str] = (),
    ) -> dict[str, Any]:
        path, relative = self._resolve(target_ref)
        if path.suffix.lower() != ".docx":
            raise ArtifactValidationError("DOCX verification requires a .docx target")
        data = path.read_bytes()
        digest = _sha256(data)
        if expected_sha256 is not None and digest != expected_sha256:
            return {
                "verified": False, "target_ref": relative,
                "sha256": digest, "error_code": "output_hash_mismatch",
            }
        _validate_zip_container(data, self.limits, "DOCX")
        try:
            from docx import Document
            document = Document(io.BytesIO(data))
            values = [paragraph.text for paragraph in document.paragraphs]
            for table in document.tables:
                for row in table.rows:
                    values.append(" | ".join(cell.text for cell in row.cells))
            text = "\n".join(values)
        except Exception as error:  # noqa: BLE001 - parser boundary
            raise ArtifactParseError(f"python-docx readback failed: {error}") from error
        missing = [value for value in required_values if str(value) not in text]
        return {
            "verified": not missing,
            "target_ref": relative,
            "sha256": digest,
            "paragraph_count": len(document.paragraphs),
            "table_count": len(document.tables),
            "required_values": [str(value) for value in required_values],
            "missing_values": missing,
            "text_sha256": _sha256(text.encode("utf-8")),
        }
    @staticmethod
    def _docx_paragraphs(document: Any) -> list[Any]:
        paragraphs = list(document.paragraphs)
        for table in document.tables:
            for row in table.rows:
                for cell in row.cells:
                    paragraphs.extend(cell.paragraphs)
        return paragraphs

    @staticmethod
    def _replace_docx_text(paragraph: Any, old: str, new: str) -> int:
        count = paragraph.text.count(old)
        if not count:
            return 0
        replacement = paragraph.text.replace(old, new)
        if paragraph.runs:
            paragraph.runs[0].text = replacement
            for run in paragraph.runs[1:]:
                run.text = ""
        else:
            paragraph.add_run(replacement)
        return count

    def edit_docx(
        self,
        source: ParsedArtifact | SourceReceipt | str | Path,
        *,
        target_ref: str,
        idempotency_key: str,
        replacements: Mapping[str, str] | None = None,
        append_paragraphs: Sequence[str] = (),
    ) -> OutputReceipt:
        """Apply bounded text edits to a new DOCX while preserving the source."""
        receipt = self._source_receipt(source, formats={"docx"})
        _, relative, fmt = self._ensure_new_target(target_ref, receipt)
        if fmt != "docx":
            raise ArtifactValidationError("DOCX edit target must use .docx")
        replace_map = dict(replacements or {})
        if isinstance(append_paragraphs, (str, bytes)):
            raise ArtifactValidationError("append_paragraphs must be a sequence")
        if not replace_map and not append_paragraphs:
            raise ArtifactValidationError(
                "DOCX edit requires replacements or appended paragraphs"
            )
        if len(replace_map) + len(append_paragraphs) > self.limits.max_docx_paragraphs:
            raise ArtifactValidationError("DOCX edit exceeds paragraph operation bound")
        for old, new in replace_map.items():
            if not isinstance(old, str) or not old:
                raise ArtifactValidationError(
                    "DOCX replacement keys must be non-empty strings"
                )
            if not isinstance(new, str):
                raise ArtifactValidationError(
                    "DOCX replacement values must be strings"
                )
        if not all(isinstance(value, str) for value in append_paragraphs):
            raise ArtifactValidationError("appended DOCX paragraphs must be strings")
        if sum(
            len(value)
            for pair in replace_map.items()
            for value in pair
        ) + sum(len(value) for value in append_paragraphs) > self.limits.max_text_chars:
            raise ArtifactValidationError("DOCX edit text exceeds max_text_chars")
        data = self._captured_bytes(receipt)
        _validate_zip_container(data, self.limits, "DOCX")
        try:
            from docx import Document

            document = Document(io.BytesIO(data))
            replacements_applied: dict[str, int] = {}
            paragraphs = self._docx_paragraphs(document)
            for old in sorted(replace_map):
                count = sum(
                    self._replace_docx_text(paragraph, old, replace_map[old])
                    for paragraph in paragraphs
                )
                if count == 0:
                    raise ArtifactSchemaError(
                        f"DOCX replacement text not found: {old!r}"
                    )
                replacements_applied[old] = count
            for paragraph_text in append_paragraphs:
                document.add_paragraph(paragraph_text)
            document.core_properties.modified = datetime(2000, 1, 1)
            output = io.BytesIO()
            document.save(output)
            output_data = self._canonical_zip(output.getvalue())
        except (ArtifactValidationError, ArtifactSchemaError):
            raise
        except Exception as error:  # noqa: BLE001 - document boundary
            raise ArtifactWriteError(f"python-docx edit failed: {error}") from error
        written = self._write_output(
            output_data,
            target_ref=relative,
            source_sha256=receipt.sha256,
            idempotency_key=idempotency_key,
            format="docx",
            readback={},
        )
        required_values = [*replace_map.values(), *append_paragraphs]
        try:
            self._assert_source_current(receipt)
            readback = self.verify_docx(
                relative,
                expected_sha256=written.sha256,
                required_values=required_values,
            )
        except LocalArtifactError as error:
            raise ArtifactPostWriteError(
                f"DOCX post-write verification failed: {error}",
                evidence=_post_write_evidence(written),
            ) from error
        readback["replacements_applied"] = replacements_applied
        if not readback.get("verified"):
            raise ArtifactPostWriteError(
                "DOCX readback differs from requested edits",
                evidence=_post_write_evidence(written, readback),
            )
        return OutputReceipt(
            target_ref=written.target_ref, path=written.path, format=written.format,
            sha256=written.sha256, size_bytes=written.size_bytes,
            source_sha256=written.source_sha256, idempotency_key=written.idempotency_key,
            readback=readback, reconciled=written.reconciled, replayed=written.replayed,
        )

    def create_docx_report(
        self,
        parsed: ParsedArtifact,
        summary: Mapping[str, Any],
        *,
        target_ref: str,
        idempotency_key: str,
        title: str = "JARVIS local data report",
        fault_after_write: bool = False,
    ) -> OutputReceipt:
        if parsed.table is None:
            raise ArtifactSchemaError("DOCX report requires a tabular parsed source")
        _, relative, fmt = self._ensure_new_target(target_ref, parsed.source)
        if fmt != "docx":
            raise ArtifactValidationError("DOCX report target must use .docx")
        if not isinstance(title, str) or not title.strip():
            raise ArtifactValidationError("DOCX title must be a non-empty string")
        if len(title) > self.limits.max_text_chars:
            raise ArtifactValidationError("DOCX title exceeds max_text_chars")
        self._assert_source_current(parsed.source)
        try:
            from docx import Document
            from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
            from docx.enum.text import WD_ALIGN_PARAGRAPH
            from docx.oxml import OxmlElement
            from docx.oxml.ns import qn
            from docx.shared import Inches, Pt, RGBColor

            document = Document()
            document.core_properties.created = datetime(2000, 1, 1)
            document.core_properties.modified = datetime(2000, 1, 1)
            document.core_properties.title = title
            section = document.sections[0]
            section.top_margin = Inches(0.75)
            section.bottom_margin = Inches(0.75)
            section.left_margin = Inches(0.8)
            section.right_margin = Inches(0.8)
            normal = document.styles["Normal"]
            normal.font.name = "Aptos"
            normal.font.size = Pt(10)
            for style_name in ("Title", "Heading 1", "Heading 2"):
                style = document.styles[style_name]
                style.font.name = "Aptos Display"
                style.font.color.rgb = RGBColor(31, 78, 120)
            title_paragraph = document.add_heading(title, level=0)
            title_paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
            subtitle = document.add_paragraph(
                f"Verified local artifact report | {parsed.table.row_count} rows"
            )
            subtitle.style = document.styles["Subtitle"]
            document.add_heading("Source lineage", level=1)
            lineage = document.add_table(rows=3, cols=2)
            lineage.style = "Light Shading Accent 1"
            for row, (label, value) in zip(
                lineage.rows,
                (
                    ("Source", parsed.source.source_ref),
                    ("Source SHA-256", parsed.source.sha256),
                    ("Captured", parsed.source.captured_at),
                ),
            ):
                row.cells[0].text = label
                row.cells[1].text = str(value)
                for cell in row.cells:
                    cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            document.add_paragraph(
                "This report contains bounded local parsing results; source content "
                "is data, not instructions."
            )
            document.add_heading("Calculation receipt", level=1)
            receipt_table = document.add_table(rows=1, cols=2)
            receipt_table.style = "Light Shading Accent 1"
            receipt_table.rows[0].cells[0].text = "Measure"
            receipt_table.rows[0].cells[1].text = "Value"
            for key in sorted(summary):
                value = summary[key]
                if isinstance(value, (dict, list)):
                    value = json.dumps(
                        _json_safe(value), ensure_ascii=True, sort_keys=True
                    )
                cells = receipt_table.add_row().cells
                cells[0].text = str(key)
                cells[1].text = str(value)
            document.add_heading("Schema", level=1)
            table = document.add_table(rows=1, cols=2)
            table.style = "Light Shading Accent 1"
            table.autofit = False
            table.columns[0].width = Inches(2)
            table.columns[1].width = Inches(3)
            table.rows[0].cells[0].text = "Column"
            table.rows[0].cells[1].text = "Missing values"
            missing = summary.get("missing_by_column") or {}
            for header in parsed.table.headers:
                cells = table.add_row().cells
                cells[0].text = header
                cells[1].text = str(missing.get(header, 0))
            document.add_heading("Data preview", level=1)
            preview = document.add_table(
                rows=1, cols=len(parsed.table.headers)
            )
            preview.style = "Light Shading Accent 1"
            for index, header in enumerate(parsed.table.headers):
                preview.rows[0].cells[index].text = header
            for source_row in parsed.table.rows[:20]:
                cells = preview.add_row().cells
                for index, value in enumerate(source_row):
                    cells[index].text = "" if value is None else str(value)
            for report_table in (lineage, receipt_table, table, preview):
                first_row = report_table.rows[0]
                tr_pr = first_row._tr.get_or_add_trPr()
                repeat = OxmlElement("w:tblHeader")
                repeat.set(qn("w:val"), "true")
                tr_pr.append(repeat)
                for row in report_table.rows:
                    for cell in row.cells:
                        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
            output = io.BytesIO()
            document.save(output)
            data = self._canonical_zip(output.getvalue())
        except Exception as error:  # noqa: BLE001 - document boundary
            raise ArtifactWriteError(f"python-docx report creation failed: {error}") from error
        required = [
            parsed.source.sha256,
            str(summary.get("row_count", parsed.table.row_count)),
        ]
        written = self._write_output(
            data, target_ref=relative, source_sha256=parsed.source.sha256,
            idempotency_key=idempotency_key, format="docx", readback={},
        )
        if fault_after_write:
            raise InjectedArtifactFailure(
                "injected failure after DOCX creation before verification",
                evidence=_post_write_evidence(written),
            )
        try:
            self._assert_source_current(parsed.source)
            readback = self.verify_docx(
                relative,
                expected_sha256=written.sha256,
                required_values=required,
            )
        except LocalArtifactError as error:
            raise ArtifactPostWriteError(
                f"DOCX post-write verification failed: {error}",
                evidence=_post_write_evidence(written),
            ) from error
        if not readback.get("verified"):
            raise ArtifactPostWriteError(
                "DOCX readback did not verify required values",
                evidence=_post_write_evidence(written, readback),
            )
        return OutputReceipt(
            target_ref=written.target_ref, path=written.path, format=written.format,
            sha256=written.sha256, size_bytes=written.size_bytes,
            source_sha256=written.source_sha256, idempotency_key=written.idempotency_key,
            readback=readback, reconciled=written.reconciled, replayed=written.replayed,
        )

    def create_docx_document(
        self,
        *,
        title: str,
        opening: Sequence[str],
        sections: Sequence[Mapping[str, Any]],
        target_ref: str,
        idempotency_key: str,
        sources: Sequence[ParsedArtifact | SourceReceipt | str | Path] = (),
        required_values: Sequence[str] = (),
        fault_after_write: bool = False,
    ) -> OutputReceipt:
        """Create one structured source-preserving document package."""

        if not isinstance(title, str) or not title.strip():
            raise ArtifactValidationError("DOCX title must be non-empty text")
        if isinstance(opening, (str, bytes)) or not isinstance(opening, Sequence):
            raise ArtifactValidationError("DOCX opening must be a sequence")
        if isinstance(sections, (str, bytes)) or not isinstance(sections, Sequence):
            raise ArtifactValidationError("DOCX sections must be a sequence")
        if isinstance(required_values, (str, bytes)):
            raise ArtifactValidationError("required_values must be a sequence")
        if not all(isinstance(value, str) for value in opening):
            raise ArtifactValidationError("DOCX opening paragraphs must be text")
        if not all(isinstance(value, str) for value in required_values):
            raise ArtifactValidationError("DOCX required values must be text")
        if not all(isinstance(section, Mapping) for section in sections):
            raise ArtifactValidationError("DOCX sections must be objects")
        receipts = self._source_receipts(sources) if sources else ()
        source_for_target = receipts[0] if receipts else None
        _, relative, fmt = self._ensure_new_target(
            target_ref,
            source_for_target,
        )
        if fmt != "docx":
            raise ArtifactValidationError("DOCX target must use .docx")
        if any(
            receipt.source_ref.casefold() == relative.casefold()
            for receipt in receipts
        ):
            raise ArtifactValidationError("DOCX target must differ from every source")

        normalized_sections: list[dict[str, Any]] = []
        text_count = len(title)
        table_rows = 0
        table_cells = 0
        generated_values: list[str] = [title.strip()]
        for paragraph in opening:
            text_count += len(paragraph)
            if paragraph:
                generated_values.append(paragraph)
        for index, section in enumerate(sections, start=1):
            heading = section.get("heading")
            if not isinstance(heading, str) or not heading.strip():
                raise ArtifactValidationError(
                    f"DOCX section {index} heading must be non-empty text"
                )
            paragraphs = section.get("paragraphs") or ()
            bullets = section.get("bullets") or ()
            table = section.get("table")
            if isinstance(paragraphs, (str, bytes)) or not isinstance(
                paragraphs, Sequence
            ):
                raise ArtifactValidationError(
                    f"DOCX section {index} paragraphs must be a sequence"
                )
            if isinstance(bullets, (str, bytes)) or not isinstance(
                bullets, Sequence
            ):
                raise ArtifactValidationError(
                    f"DOCX section {index} bullets must be a sequence"
                )
            if not all(isinstance(value, str) for value in (*paragraphs, *bullets)):
                raise ArtifactValidationError(
                    f"DOCX section {index} content must be text"
                )
            normalized_table = None
            if table is not None:
                if not isinstance(table, Mapping):
                    raise ArtifactValidationError(
                        f"DOCX section {index} table must be an object"
                    )
                headers = table.get("headers")
                rows = table.get("rows")
                if (
                    isinstance(headers, (str, bytes))
                    or not isinstance(headers, Sequence)
                    or not headers
                    or not all(isinstance(value, str) and value for value in headers)
                ):
                    raise ArtifactValidationError(
                        f"DOCX section {index} table headers are invalid"
                    )
                if (
                    isinstance(rows, (str, bytes))
                    or not isinstance(rows, Sequence)
                    or not all(
                        isinstance(row, Sequence)
                        and not isinstance(row, (str, bytes))
                        for row in rows
                    )
                ):
                    raise ArtifactValidationError(
                        f"DOCX section {index} table rows are invalid"
                    )
                normalized_rows = [list(row) for row in rows]
                if any(len(row) != len(headers) for row in normalized_rows):
                    raise ArtifactValidationError(
                        f"DOCX section {index} table row width is invalid"
                    )
                table_rows += len(normalized_rows) + 1
                table_cells += (len(normalized_rows) + 1) * len(headers)
                normalized_table = {
                    "headers": list(headers),
                    "rows": normalized_rows,
                }
                for value in [*headers, *[item for row in normalized_rows for item in row]]:
                    text_count += len(str(value))
            text_count += len(heading) + sum(
                len(value) for value in (*paragraphs, *bullets)
            )
            generated_values.extend(
                [
                    heading.strip(),
                    *[value for value in paragraphs if value],
                    *[value for value in bullets if value],
                ]
            )
            normalized_sections.append(
                {
                    "heading": heading.strip(),
                    "paragraphs": list(paragraphs),
                    "bullets": list(bullets),
                    "table": normalized_table,
                }
            )
        if text_count > self.limits.max_text_chars:
            raise ArtifactValidationError("DOCX content exceeds max_text_chars")
        if table_rows > self.limits.max_docx_table_rows:
            raise ArtifactValidationError("DOCX tables exceed row bound")
        if table_cells > self.limits.max_docx_table_cells:
            raise ArtifactValidationError("DOCX tables exceed cell bound")
        if len(normalized_sections) > self.limits.max_docx_paragraphs:
            raise ArtifactValidationError("DOCX sections exceed operation bound")
        for receipt in receipts:
            self._assert_source_current(receipt)

        try:
            from docx import Document
            from docx.enum.table import WD_CELL_VERTICAL_ALIGNMENT
            from docx.enum.text import WD_ALIGN_PARAGRAPH
            from docx.oxml import OxmlElement
            from docx.oxml.ns import qn
            from docx.shared import Inches, Pt, RGBColor

            def set_cell_shading(cell: Any, color: str) -> None:
                properties = cell._tc.get_or_add_tcPr()
                shading = properties.find(qn("w:shd"))
                if shading is None:
                    shading = OxmlElement("w:shd")
                    properties.append(shading)
                shading.set(qn("w:fill"), color)

            def set_cell_frame(cell: Any) -> None:
                properties = cell._tc.get_or_add_tcPr()
                borders = properties.find(qn("w:tcBorders"))
                if borders is None:
                    borders = OxmlElement("w:tcBorders")
                    properties.append(borders)
                for edge in ("top", "left", "bottom", "right", "insideH", "insideV"):
                    element = borders.find(qn(f"w:{edge}"))
                    if element is None:
                        element = OxmlElement(f"w:{edge}")
                        borders.append(element)
                    element.set(qn("w:val"), "single")
                    element.set(qn("w:sz"), "4")
                    element.set(qn("w:color"), "D9D9D9")
                margins = properties.find(qn("w:tcMar"))
                if margins is None:
                    margins = OxmlElement("w:tcMar")
                    properties.append(margins)
                for edge in ("top", "left", "bottom", "right"):
                    element = margins.find(qn(f"w:{edge}"))
                    if element is None:
                        element = OxmlElement(f"w:{edge}")
                        margins.append(element)
                    element.set(qn("w:w"), "100")
                    element.set(qn("w:type"), "dxa")

            def style_table(table: Any) -> None:
                table.autofit = True
                header_row = table.rows[0]
                header_properties = header_row._tr.get_or_add_trPr()
                repeat = OxmlElement("w:tblHeader")
                repeat.set(qn("w:val"), "true")
                header_properties.append(repeat)
                for row_index, row in enumerate(table.rows):
                    for cell in row.cells:
                        set_cell_frame(cell)
                        cell.vertical_alignment = WD_CELL_VERTICAL_ALIGNMENT.CENTER
                        if row_index == 0:
                            set_cell_shading(cell, "1F4E78")
                        elif row_index % 2 == 0:
                            set_cell_shading(cell, "EAF2F8")
                        for paragraph in cell.paragraphs:
                            paragraph.paragraph_format.space_after = Pt(0)
                            for run in paragraph.runs:
                                run.font.name = "Aptos"
                                run.font.size = Pt(9.5)
                                if row_index == 0:
                                    run.font.bold = True
                                    run.font.color.rgb = RGBColor(255, 255, 255)

            document = Document()
            document.core_properties.created = datetime(2000, 1, 1)
            document.core_properties.modified = datetime(2000, 1, 1)
            document.core_properties.title = title.strip()
            document.core_properties.author = "JARVIS Foundry"
            section = document.sections[0]
            section.page_width = Inches(8.5)
            section.page_height = Inches(11)
            section.top_margin = Inches(0.75)
            section.bottom_margin = Inches(0.75)
            section.left_margin = Inches(0.8)
            section.right_margin = Inches(0.8)
            normal = document.styles["Normal"]
            normal.font.name = "Aptos"
            normal.font.size = Pt(11)
            for style_name in ("Title", "Heading 1", "Heading 2"):
                style = document.styles[style_name]
                style.font.name = "Aptos Display"
                style.font.color.rgb = RGBColor(0, 0, 0)
            document.styles["Title"].font.size = Pt(24)
            document.styles["Heading 1"].font.size = Pt(16)
            document.styles["Heading 2"].font.size = Pt(13)

            title_paragraph = document.add_paragraph(style="Title")
            title_paragraph.alignment = WD_ALIGN_PARAGRAPH.LEFT
            title_paragraph.add_run(title.strip())
            for paragraph_text in opening:
                paragraph = document.add_paragraph(paragraph_text)
                paragraph.paragraph_format.space_after = Pt(8)

            if receipts:
                document.add_heading("Source lineage", level=1)
                lineage = document.add_table(rows=1, cols=3)
                lineage.rows[0].cells[0].text = "Source"
                lineage.rows[0].cells[1].text = "Format"
                lineage.rows[0].cells[2].text = "SHA 256"
                for receipt in receipts:
                    cells = lineage.add_row().cells
                    cells[0].text = receipt.source_ref
                    cells[1].text = receipt.format.upper()
                    cells[2].text = receipt.sha256
                style_table(lineage)

            for item in normalized_sections:
                document.add_heading(item["heading"], level=1)
                for paragraph_text in item["paragraphs"]:
                    paragraph = document.add_paragraph(paragraph_text)
                    paragraph.paragraph_format.space_after = Pt(8)
                for bullet in item["bullets"]:
                    paragraph = document.add_paragraph(
                        bullet,
                        style="List Bullet",
                    )
                    paragraph.paragraph_format.space_after = Pt(4)
                table_spec = item["table"]
                if table_spec is not None:
                    table = document.add_table(
                        rows=1,
                        cols=len(table_spec["headers"]),
                    )
                    for column, header in enumerate(table_spec["headers"]):
                        table.rows[0].cells[column].text = str(header)
                    for source_row in table_spec["rows"]:
                        cells = table.add_row().cells
                        for column, value in enumerate(source_row):
                            cells[column].text = "" if value is None else str(value)
                    style_table(table)
                    document.add_paragraph()

            output = io.BytesIO()
            document.save(output)
            data = self._canonical_zip(output.getvalue())
        except ArtifactValidationError:
            raise
        except Exception as error:  # noqa: BLE001 - document boundary
            raise ArtifactWriteError(
                f"python-docx document creation failed: {error}"
            ) from error

        lineage_hash = self._lineage_hash(receipts) if receipts else None
        written = self._write_output(
            data,
            target_ref=relative,
            source_sha256=lineage_hash,
            idempotency_key=idempotency_key,
            format="docx",
            readback={},
        )
        if fault_after_write:
            raise InjectedArtifactFailure(
                "injected failure after DOCX creation before verification",
                evidence=_post_write_evidence(written),
            )
        for receipt in receipts:
            self._assert_source_current(receipt)
        required = tuple(
            dict.fromkeys(
                [
                    *required_values,
                    *generated_values,
                    *[receipt.sha256 for receipt in receipts],
                ]
            )
        )
        readback = self.verify_docx(
            relative,
            expected_sha256=written.sha256,
            required_values=required,
        )
        readback["source_refs"] = [
            receipt.source_ref for receipt in receipts
        ]
        readback["source_sha256s"] = [
            receipt.sha256 for receipt in receipts
        ]
        readback["lineage_hash"] = lineage_hash
        if not readback.get("verified"):
            raise ArtifactPostWriteError(
                "DOCX readback differs from requested document",
                evidence=_post_write_evidence(written, readback),
            )
        return OutputReceipt(
            target_ref=written.target_ref,
            path=written.path,
            format=written.format,
            sha256=written.sha256,
            size_bytes=written.size_bytes,
            source_sha256=written.source_sha256,
            idempotency_key=written.idempotency_key,
            readback=readback,
            reconciled=written.reconciled,
            replayed=written.replayed,
        )

    def reconcile_docx(
        self,
        target_ref: str,
        *,
        expected_sha256: str,
        required_values: Sequence[str] = (),
    ) -> dict[str, Any]:
        readback = self.verify_docx(
            target_ref, expected_sha256=expected_sha256,
            required_values=required_values,
        )
        return {
            "reconciled": bool(readback.get("verified")),
            "target_ref": readback.get("target_ref"),
            "sha256": readback.get("sha256"),
            "readback": readback,
        }

    def verify_pdf(
        self,
        target_ref: str,
        *,
        expected_sha256: str | None = None,
        expected_page_count: int | None = None,
        required_text: Sequence[str] = (),
    ) -> dict[str, Any]:
        path, relative = self._resolve(target_ref)
        if path.suffix.lower() != ".pdf":
            raise ArtifactValidationError("PDF verification requires a .pdf target")
        data = path.read_bytes()
        digest = _sha256(data)
        if expected_sha256 is not None and digest != expected_sha256:
            return {
                "verified": False,
                "target_ref": relative,
                "sha256": digest,
                "error_code": "output_hash_mismatch",
            }
        try:
            from pypdf import PdfReader

            reader = PdfReader(io.BytesIO(data), strict=False)
            page_count = len(reader.pages)
            if page_count > self.limits.max_pdf_pages:
                raise ArtifactValidationError("PDF exceeds max_pdf_pages")
            page_text = [page.extract_text() or "" for page in reader.pages]
            text = "\n".join(page_text)
        except ArtifactValidationError:
            raise
        except Exception as error:  # noqa: BLE001 - PDF boundary
            raise ArtifactParseError(f"pypdf readback failed: {error}") from error
        normalized_text = " ".join(text.split())
        missing = [
            value
            for value in required_text
            if " ".join(str(value).split()) not in normalized_text
        ]
        count_matches = (
            expected_page_count is None or page_count == expected_page_count
        )
        return {
            "verified": count_matches and not missing,
            "target_ref": relative,
            "sha256": digest,
            "page_count": page_count,
            "expected_page_count": expected_page_count,
            "missing_text": missing,
            "text_sha256": _sha256(text.encode("utf-8")),
            "text_chars": len(text),
        }

    def create_pdf(
        self,
        *,
        pages: Sequence[str],
        target_ref: str,
        idempotency_key: str,
        title: str = "JARVIS local artifact",
        source: ParsedArtifact | SourceReceipt | str | Path | None = None,
        fault_after_write: bool = False,
    ) -> OutputReceipt:
        """Create a deterministic text PDF using a lazy ReportLab import."""
        if isinstance(pages, (str, bytes)) or not isinstance(pages, Sequence):
            raise ArtifactValidationError("PDF pages must be a sequence of strings")
        if not pages or len(pages) > self.limits.max_pdf_pages:
            raise ArtifactValidationError(
                "PDF pages must be non-empty and within max_pdf_pages"
            )
        if not all(isinstance(page, str) for page in pages):
            raise ArtifactValidationError("PDF page content must be strings")
        if not isinstance(title, str) or not title.strip():
            raise ArtifactValidationError("PDF title must be a non-empty string")
        if sum(len(page) for page in pages) + len(title) > self.limits.max_text_chars:
            raise ArtifactValidationError("PDF text exceeds max_text_chars")
        source_receipt = (
            self._any_source_receipt(source) if source is not None else None
        )
        _, relative, fmt = self._ensure_new_target(target_ref, source_receipt)
        if fmt != "pdf":
            raise ArtifactValidationError("PDF target must use .pdf")
        if source_receipt is not None:
            self._assert_source_current(source_receipt)
        try:
            from reportlab.lib.pagesizes import letter
            from reportlab.pdfbase.pdfmetrics import stringWidth
            from reportlab.pdfgen import canvas

            output = io.BytesIO()
            pdf = canvas.Canvas(
                output,
                pagesize=letter,
                pageCompression=1,
                invariant=1,
            )
            pdf.setTitle(title)
            pdf.setAuthor("JARVIS Foundry")
            if source_receipt is not None:
                pdf.setSubject(f"Source SHA-256: {source_receipt.sha256}")
            page_width, page_height = letter
            for page_number, page in enumerate(pages, start=1):
                pdf.setFillColorRGB(31 / 255, 78 / 255, 120 / 255)
                pdf.setFont("Helvetica-Bold", 16)
                pdf.drawString(54, page_height - 54, title)
                pdf.setFillColorRGB(0.2, 0.2, 0.2)
                pdf.setFont("Helvetica", 9)
                page_label = f"Page {page_number} of {len(pages)}"
                pdf.drawRightString(page_width - 54, 30, page_label)
                if source_receipt is not None:
                    pdf.setFont("Helvetica", 7)
                    pdf.drawString(
                        54,
                        18,
                        f"Source SHA-256: {source_receipt.sha256}",
                    )
                text_object = pdf.beginText(54, page_height - 84)
                text_object.setFont("Helvetica", 10)
                text_object.setLeading(14)
                lines: list[str] = []
                for raw_line in page.splitlines() or [""]:
                    if not raw_line:
                        lines.append("")
                        continue
                    wrapped = textwrap.wrap(
                        raw_line,
                        width=92,
                        break_long_words=True,
                        break_on_hyphens=False,
                    )
                    lines.extend(wrapped or [""])
                fitted_lines: list[str] = []
                max_line_width = page_width - 108
                for line in lines:
                    remaining = line
                    if not remaining:
                        fitted_lines.append("")
                        continue
                    while stringWidth(remaining, "Helvetica", 10) > max_line_width:
                        low = 1
                        high = len(remaining)
                        while low < high:
                            midpoint = (low + high + 1) // 2
                            if (
                                stringWidth(
                                    remaining[:midpoint], "Helvetica", 10
                                )
                                <= max_line_width
                            ):
                                low = midpoint
                            else:
                                high = midpoint - 1
                        fitted_lines.append(remaining[:low])
                        remaining = remaining[low:]
                    fitted_lines.append(remaining)
                if len(fitted_lines) > 46:
                    raise ArtifactValidationError(
                        "PDF page text exceeds single-page layout bound"
                    )
                for line in fitted_lines:
                    text_object.textLine(line)
                pdf.drawText(text_object)
                pdf.showPage()
            pdf.save()
            data = output.getvalue()
        except ArtifactValidationError:
            raise
        except ImportError as error:
            raise ArtifactWriteError(
                "ReportLab is required for PDF creation"
            ) from error
        except Exception as error:  # noqa: BLE001 - PDF boundary
            raise ArtifactWriteError(f"ReportLab PDF creation failed: {error}") from error
        written = self._write_output(
            data,
            target_ref=relative,
            source_sha256=(
                source_receipt.sha256 if source_receipt is not None else None
            ),
            idempotency_key=idempotency_key,
            format="pdf",
            readback={},
        )
        if fault_after_write:
            raise InjectedArtifactFailure(
                "injected failure after PDF creation before verification",
                evidence=_post_write_evidence(written),
            )
        required = [title, *[page[:80] for page in pages if page]]
        try:
            if source_receipt is not None:
                self._assert_source_current(source_receipt)
                required.append(source_receipt.sha256)
            readback = self.verify_pdf(
                relative,
                expected_sha256=written.sha256,
                expected_page_count=len(pages),
                required_text=required,
            )
        except LocalArtifactError as error:
            raise ArtifactPostWriteError(
                f"PDF post-write verification failed: {error}",
                evidence=_post_write_evidence(written),
            ) from error
        if source_receipt is not None:
            readback["source_ref"] = source_receipt.source_ref
            readback["source_sha256"] = source_receipt.sha256
        if not readback.get("verified"):
            raise ArtifactPostWriteError(
                "PDF readback differs from requested pages",
                evidence=_post_write_evidence(written, readback),
            )
        return OutputReceipt(
            target_ref=written.target_ref, path=written.path, format=written.format,
            sha256=written.sha256, size_bytes=written.size_bytes,
            source_sha256=written.source_sha256, idempotency_key=written.idempotency_key,
            readback=readback, reconciled=written.reconciled, replayed=written.replayed,
        )

    def combine_pdfs(
        self,
        sources: Sequence[ParsedArtifact | SourceReceipt | str | Path],
        *,
        target_ref: str,
        idempotency_key: str,
    ) -> OutputReceipt:
        if isinstance(sources, (str, bytes)) or not isinstance(sources, Sequence):
            raise ArtifactValidationError("PDF sources must be a sequence")
        if not sources or len(sources) > self.limits.max_pdf_pages:
            raise ArtifactValidationError("PDF source count is out of bounds")
        receipts = [
            self._source_receipt(source, formats={"pdf"})
            for source in sources
        ]
        _, relative, fmt = self._ensure_new_target(target_ref, receipts[0])
        if fmt != "pdf":
            raise ArtifactValidationError("combined PDF target must use .pdf")
        if any(
            receipt.source_ref.casefold() == relative.casefold()
            for receipt in receipts
        ):
            raise ArtifactValidationError("output target must differ from source")
        total_pages = 0
        source_text: list[str] = []
        try:
            from pypdf import PdfReader, PdfWriter

            writer = PdfWriter()
            for receipt in receipts:
                reader = PdfReader(
                    io.BytesIO(self._captured_bytes(receipt)), strict=False
                )
                total_pages += len(reader.pages)
                if total_pages > self.limits.max_pdf_pages:
                    raise ArtifactValidationError(
                        "combined PDF exceeds max_pdf_pages"
                    )
                for page in reader.pages:
                    source_text.append(page.extract_text() or "")
                    writer.add_page(page)
            writer.add_metadata({
                "/Title": "JARVIS combined local artifact",
                "/Producer": "JARVIS Foundry",
            })
            output = io.BytesIO()
            writer.write(output)
            data = output.getvalue()
        except ArtifactValidationError:
            raise
        except Exception as error:  # noqa: BLE001 - PDF boundary
            raise ArtifactWriteError(f"pypdf combine failed: {error}") from error
        lineage_hash = _sha256(
            "\n".join(receipt.sha256 for receipt in receipts).encode("ascii")
        )
        written = self._write_output(
            data,
            target_ref=relative,
            source_sha256=lineage_hash,
            idempotency_key=idempotency_key,
            format="pdf",
            readback={},
        )
        try:
            for receipt in receipts:
                self._assert_source_current(receipt)
            readback = self.verify_pdf(
                relative,
                expected_sha256=written.sha256,
                expected_page_count=total_pages,
            )
        except LocalArtifactError as error:
            raise ArtifactPostWriteError(
                f"combined PDF post-write verification failed: {error}",
                evidence=_post_write_evidence(written),
            ) from error
        readback["source_sha256s"] = [receipt.sha256 for receipt in receipts]
        readback["source_text_sha256"] = _sha256(
            "\n".join(source_text).encode("utf-8")
        )
        if (
            not readback.get("verified")
            or readback["text_sha256"] != readback["source_text_sha256"]
        ):
            raise ArtifactPostWriteError(
                "combined PDF readback differs from sources",
                evidence=_post_write_evidence(written, readback),
            )
        return OutputReceipt(
            target_ref=written.target_ref, path=written.path, format=written.format,
            sha256=written.sha256, size_bytes=written.size_bytes,
            source_sha256=written.source_sha256, idempotency_key=written.idempotency_key,
            readback=readback, reconciled=written.reconciled, replayed=written.replayed,
        )

    def split_pdf(
        self,
        source: ParsedArtifact | SourceReceipt | str | Path,
        *,
        target_ref: str,
        idempotency_key: str,
        start_page: int,
        end_page: int,
    ) -> OutputReceipt:
        """Write one inclusive 1-based PDF page range to a new target."""
        receipt = self._source_receipt(source, formats={"pdf"})
        _, relative, fmt = self._ensure_new_target(target_ref, receipt)
        if fmt != "pdf":
            raise ArtifactValidationError("split PDF target must use .pdf")
        if (
            not isinstance(start_page, int)
            or isinstance(start_page, bool)
            or not isinstance(end_page, int)
            or isinstance(end_page, bool)
            or start_page < 1
            or end_page < start_page
        ):
            raise ArtifactValidationError(
                "PDF page range must be positive, inclusive, and ordered"
            )
        try:
            from pypdf import PdfReader, PdfWriter

            reader = PdfReader(
                io.BytesIO(self._captured_bytes(receipt)), strict=False
            )
            if end_page > len(reader.pages):
                raise ArtifactValidationError("PDF page range exceeds source pages")
            writer = PdfWriter()
            selected_text: list[str] = []
            for page_index in range(start_page - 1, end_page):
                page = reader.pages[page_index]
                selected_text.append(page.extract_text() or "")
                writer.add_page(page)
            writer.add_metadata({
                "/Title": f"JARVIS PDF pages {start_page}-{end_page}",
                "/Producer": "JARVIS Foundry",
            })
            output = io.BytesIO()
            writer.write(output)
            data = output.getvalue()
        except ArtifactValidationError:
            raise
        except Exception as error:  # noqa: BLE001 - PDF boundary
            raise ArtifactWriteError(f"pypdf split failed: {error}") from error
        written = self._write_output(
            data,
            target_ref=relative,
            source_sha256=receipt.sha256,
            idempotency_key=idempotency_key,
            format="pdf",
            readback={},
        )
        try:
            self._assert_source_current(receipt)
            readback = self.verify_pdf(
                relative,
                expected_sha256=written.sha256,
                expected_page_count=end_page - start_page + 1,
            )
        except LocalArtifactError as error:
            raise ArtifactPostWriteError(
                f"split PDF post-write verification failed: {error}",
                evidence=_post_write_evidence(written),
            ) from error
        expected_text_hash = _sha256("\n".join(selected_text).encode("utf-8"))
        readback["source_page_range"] = [start_page, end_page]
        readback["source_text_sha256"] = expected_text_hash
        if (
            not readback.get("verified")
            or readback["text_sha256"] != expected_text_hash
        ):
            raise ArtifactPostWriteError(
                "split PDF readback differs from source pages",
                evidence=_post_write_evidence(written, readback),
            )
        return OutputReceipt(
            target_ref=written.target_ref, path=written.path, format=written.format,
            sha256=written.sha256, size_bytes=written.size_bytes,
            source_sha256=written.source_sha256, idempotency_key=written.idempotency_key,
            readback=readback, reconciled=written.reconciled, replayed=written.replayed,
        )

    def export_table(
        self,
        parsed: ParsedArtifact,
        *,
        target_ref: str,
        idempotency_key: str,
    ) -> OutputReceipt:
        if parsed.table is None:
            raise ArtifactSchemaError("table export requires a tabular source")
        _, relative, fmt = self._ensure_new_target(target_ref, parsed.source)
        self._assert_source_current(parsed.source)
        table = parsed.table
        if fmt in {"csv", "tsv"}:
            delimiter = "\t" if fmt == "tsv" else ","
            output = io.StringIO(newline="")
            writer = csv.writer(output, delimiter=delimiter, lineterminator="\n")
            writer.writerow(table.headers)
            writer.writerows(table.rows)
            data = output.getvalue().encode("utf-8")
        elif fmt == "json":
            records = [
                {
                    header: _json_safe(row[index])
                    for index, header in enumerate(table.headers)
                }
                for row in table.rows
            ]
            data = (
                json.dumps(
                    {"records": records},
                    ensure_ascii=True,
                    indent=2,
                    sort_keys=False,
                )
                + "\n"
            ).encode("utf-8")
        elif fmt == "xlsx":
            try:
                from openpyxl import Workbook
                workbook = Workbook()
                workbook.properties.created = datetime(2000, 1, 1)
                workbook.properties.modified = datetime(2000, 1, 1)
                worksheet = workbook.active
                worksheet.title = "Data"
                for row_index, row in enumerate(
                    (table.headers, *table.rows),
                    start=1,
                ):
                    for column_index, value in enumerate(row, start=1):
                        _set_xlsx_value(
                            worksheet.cell(row_index, column_index),
                            value,
                        )
                output = io.BytesIO()
                workbook.save(output)
                data = self._canonical_zip(output.getvalue())
            except Exception as error:  # noqa: BLE001 - workbook boundary
                raise ArtifactWriteError(f"openpyxl export failed: {error}") from error
        else:
            raise ArtifactValidationError(
                "table export target must use .csv, .tsv, .json, or .xlsx"
            )
        written = self._write_output(
            data, target_ref=relative, source_sha256=parsed.source.sha256,
            idempotency_key=idempotency_key, format=fmt, readback={},
        )
        try:
            self._assert_source_current(parsed.source)
            parsed_output = self.parse(
                relative,
                expected_columns=table.headers,
            )
        except LocalArtifactError as error:
            raise ArtifactPostWriteError(
                f"table export post-write verification failed: {error}",
                evidence=_post_write_evidence(written),
            ) from error
        if parsed_output.table is None or parsed_output.table.rows != table.rows:
            raise ArtifactPostWriteError(
                "table export readback differs from source table",
                evidence=_post_write_evidence(written),
            )
        readback = {
            "verified": True,
            "sha256": written.sha256,
            "row_count": parsed_output.table.row_count,
            "column_count": parsed_output.table.column_count,
            "headers": list(parsed_output.table.headers),
        }
        return OutputReceipt(
            target_ref=written.target_ref, path=written.path, format=written.format,
            sha256=written.sha256, size_bytes=written.size_bytes,
            source_sha256=written.source_sha256, idempotency_key=written.idempotency_key,
            readback=readback, reconciled=written.reconciled, replayed=written.replayed,
        )

    def copy_pdf(
        self,
        parsed: ParsedArtifact,
        *,
        target_ref: str,
        idempotency_key: str,
    ) -> OutputReceipt:
        if parsed.kind != "pdf":
            raise ArtifactSchemaError("PDF copy requires a parsed PDF")
        _, relative, fmt = self._ensure_new_target(target_ref, parsed.source)
        if fmt != "pdf":
            raise ArtifactValidationError("PDF target must use .pdf")
        data = self._captured_bytes(parsed.source)
        written = self._write_output(
            data, target_ref=relative, source_sha256=parsed.source.sha256,
            idempotency_key=idempotency_key, format="pdf", readback={},
        )
        self._assert_source_current(parsed.source)
        readback = self.parse(relative)
        verified = readback.kind == "pdf" and readback.details.get("page_count") == parsed.details.get("page_count")
        if not verified:
            raise ArtifactWriteError("pypdf output readback differs from source")
        return OutputReceipt(
            target_ref=written.target_ref, path=written.path, format=written.format,
            sha256=written.sha256, size_bytes=written.size_bytes,
            source_sha256=written.source_sha256, idempotency_key=written.idempotency_key,
            readback={"verified": True, **readback.details},
            reconciled=written.reconciled, replayed=written.replayed,
        )

    @staticmethod
    def _pptx_body_lines(value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, str):
            raw_lines = value.splitlines() or [""]
        elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            if not all(isinstance(item, str) for item in value):
                raise ArtifactValidationError(
                    "PPTX body items must be strings"
                )
            raw_lines = [
                line
                for item in value
                for line in (item.splitlines() or [""])
            ]
        else:
            raise ArtifactValidationError(
                "PPTX body must be a string or sequence of strings"
            )
        wrapped: list[str] = []
        for line in raw_lines:
            wrapped.extend(
                textwrap.wrap(
                    line,
                    width=54,
                    break_long_words=False,
                    break_on_hyphens=False,
                )
                or [""]
            )
        return wrapped

    def _add_pptx_slide(
        self,
        presentation: Any,
        spec: Mapping[str, Any],
    ) -> tuple[list[str], list[SourceReceipt]]:
        from pptx.dml.color import RGBColor
        from pptx.enum.text import PP_ALIGN
        from pptx.util import Inches, Pt

        if not isinstance(spec, Mapping):
            raise ArtifactValidationError("each PPTX slide must be an object")
        layout_name = spec.get("layout", "title_and_content")
        layout_indexes = {"title": 0, "title_and_content": 1, "blank": 6}
        if layout_name not in layout_indexes:
            raise ArtifactValidationError(
                f"unsupported PPTX layout {layout_name!r}"
            )
        title = spec.get("title", "")
        if not isinstance(title, str):
            raise ArtifactValidationError("PPTX slide title must be a string")
        body_lines = self._pptx_body_lines(spec.get("body"))
        text_chars = len(title) + sum(len(line) for line in body_lines)
        if text_chars > self.limits.max_text_chars:
            raise ArtifactValidationError("PPTX slide text exceeds max_text_chars")

        slide = presentation.slides.add_slide(
            presentation.slide_layouts[layout_indexes[layout_name]]
        )
        required_text: list[str] = []
        if layout_name == "title":
            if slide.shapes.title is not None:
                title_shape = slide.shapes.title
                title_shape.left = Inches(0.8)
                title_shape.top = Inches(1.8)
                title_shape.width = Inches(8.4)
                title_shape.height = Inches(0.9)
                title_shape.text = title
                title_paragraph = title_shape.text_frame.paragraphs[0]
                title_paragraph.alignment = PP_ALIGN.CENTER
                title_paragraph.font.name = "Aptos Display"
                title_paragraph.font.size = Pt(32)
                title_paragraph.font.bold = True
                title_paragraph.font.color.rgb = RGBColor(31, 78, 120)
                if title:
                    required_text.append(title)
            if len(slide.placeholders) > 1 and body_lines:
                body = slide.placeholders[1]
                body.left = Inches(0.8)
                body.top = Inches(3.0)
                body.width = Inches(8.4)
                body.height = Inches(2.4)
                body.text_frame.clear()
                body.text_frame.word_wrap = True
                for index, line in enumerate(body_lines):
                    paragraph = (
                        body.text_frame.paragraphs[0]
                        if index == 0
                        else body.text_frame.add_paragraph()
                    )
                    paragraph.text = line
                    paragraph.alignment = PP_ALIGN.CENTER
                    paragraph.font.name = "Aptos"
                    paragraph.font.size = Pt(18)
                    paragraph.font.color.rgb = RGBColor(112, 112, 112)
                    if line:
                        required_text.append(line)
        elif layout_name == "title_and_content":
            if slide.shapes.title is not None:
                title_shape = slide.shapes.title
                title_shape.left = Inches(0.7)
                title_shape.top = Inches(0.45)
                title_shape.width = Inches(8.6)
                title_shape.height = Inches(0.75)
                title_shape.text_frame.clear()
                title_shape.text_frame.word_wrap = True
                title_paragraph = title_shape.text_frame.paragraphs[0]
                title_paragraph.text = title
                title_paragraph.alignment = PP_ALIGN.CENTER
                title_paragraph.font.name = "Aptos Display"
                title_paragraph.font.size = Pt(28)
                title_paragraph.font.bold = True
                title_paragraph.font.color.rgb = RGBColor(31, 78, 120)
                if title:
                    required_text.append(title)
            body = slide.placeholders[1]
            body.left = Inches(0.7)
            body.top = Inches(1.55)
            body.width = Inches(8.6)
            body.height = Inches(4.9)
            body.text_frame.clear()
            body.text_frame.word_wrap = True
            for index, line in enumerate(body_lines):
                paragraph = (
                    body.text_frame.paragraphs[0]
                    if index == 0
                    else body.text_frame.add_paragraph()
                )
                paragraph.text = line
                paragraph.level = 0
                paragraph.font.name = "Aptos"
                paragraph.font.size = Pt(20)
                if line:
                    required_text.append(line)
        else:
            title_box = slide.shapes.add_textbox(
                Inches(0.6), Inches(0.45), Inches(8.8), Inches(0.8)
            )
            title_box.text_frame.text = title
            title_paragraph = title_box.text_frame.paragraphs[0]
            title_paragraph.font.name = "Aptos Display"
            title_paragraph.font.size = Pt(28)
            title_paragraph.font.bold = True
            title_paragraph.font.color.rgb = RGBColor(31, 78, 120)
            if title:
                required_text.append(title)
            if body_lines:
                body_box = slide.shapes.add_textbox(
                    Inches(0.8), Inches(1.55), Inches(8.3), Inches(4.9)
                )
                body_box.text_frame.text = ""
                for index, line in enumerate(body_lines):
                    paragraph = (
                        body_box.text_frame.paragraphs[0]
                        if index == 0
                        else body_box.text_frame.add_paragraph()
                    )
                    paragraph.text = line
                    paragraph.font.name = "Aptos"
                    paragraph.font.size = Pt(20)
                    paragraph.alignment = PP_ALIGN.LEFT
                    if line:
                        required_text.append(line)

        image_sources: list[SourceReceipt] = []
        image_ref = spec.get("image_ref")
        if image_ref is not None:
            if not isinstance(image_ref, str) or not image_ref.strip():
                raise ArtifactValidationError(
                    "PPTX image_ref must be a non-empty string"
                )
            image_path, _ = self._resolve(image_ref)
            if self._relative_to(image_path, self.root) is None:
                raise ArtifactValidationError(
                    "PPTX images must be inside the owned root"
                )
            image_format = self._format_for(image_path)
            if image_format not in self.IMAGE_FORMATS:
                raise ArtifactValidationError(
                    "PPTX image_ref must name a supported image"
                )
            image_receipt = self.capture(image_ref)
            image_data = self._captured_bytes(image_receipt)
            image_sources.append(image_receipt)
            self._parse_image(image_receipt, image_data)
            box = spec.get("image_box") or {}
            if not isinstance(box, Mapping):
                raise ArtifactValidationError("PPTX image_box must be an object")
            defaults = {"left": 6.3, "top": 1.7, "width": 3.0}
            values: dict[str, float] = {}
            for key in ("left", "top", "width", "height"):
                raw = box.get(key, defaults.get(key))
                if raw is None:
                    continue
                if (
                    not isinstance(raw, (int, float))
                    or isinstance(raw, bool)
                    or raw <= 0
                ):
                    raise ArtifactValidationError(
                        f"PPTX image_box {key} must be positive"
                    )
                values[key] = float(raw)
            slide.shapes.add_picture(
                str(image_path),
                Inches(values["left"]),
                Inches(values["top"]),
                width=Inches(values["width"]),
                height=(
                    Inches(values["height"])
                    if "height" in values
                    else None
                ),
            )
        if len(slide.shapes) > self.limits.max_shapes:
            raise ArtifactValidationError("PPTX slide exceeds max_shapes")
        return required_text, image_sources

    def verify_pptx(
        self,
        target_ref: str,
        *,
        expected_sha256: str | None = None,
        expected_slide_count: int | None = None,
        required_text: Sequence[str] = (),
        expected_image_count: int | None = None,
        expected_source_sha256: str | None = None,
    ) -> dict[str, Any]:
        path, relative = self._resolve(target_ref)
        if path.suffix.lower() != ".pptx":
            raise ArtifactValidationError("PPTX verification requires a .pptx target")
        data = path.read_bytes()
        digest = _sha256(data)
        if expected_sha256 is not None and digest != expected_sha256:
            return {
                "verified": False,
                "target_ref": relative,
                "sha256": digest,
                "error_code": "output_hash_mismatch",
            }
        _validate_zip_container(data, self.limits, "PPTX")
        try:
            from pptx import Presentation
            from pptx.enum.shapes import MSO_SHAPE_TYPE

            presentation = Presentation(io.BytesIO(data))
            texts: list[str] = []
            image_count = 0
            shape_count = 0
            for slide in presentation.slides:
                shape_count += len(slide.shapes)
                for shape in slide.shapes:
                    if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:
                        image_count += 1
                    if hasattr(shape, "text"):
                        texts.append(str(shape.text))
            text = "\n".join(texts)
            subject = str(presentation.core_properties.subject or "")
        except Exception as error:  # noqa: BLE001 - presentation boundary
            raise ArtifactParseError(f"python-pptx readback failed: {error}") from error
        normalized_text = " ".join(text.split())
        missing = [
            value
            for value in required_text
            if " ".join(str(value).split()) not in normalized_text
        ]
        slide_count = len(presentation.slides)
        slide_matches = (
            expected_slide_count is None or slide_count == expected_slide_count
        )
        image_matches = (
            expected_image_count is None or image_count == expected_image_count
        )
        source_matches = (
            expected_source_sha256 is None
            or expected_source_sha256 in subject
        )
        return {
            "verified": (
                slide_matches
                and image_matches
                and source_matches
                and not missing
            ),
            "target_ref": relative,
            "sha256": digest,
            "slide_count": slide_count,
            "shape_count": shape_count,
            "image_count": image_count,
            "missing_text": missing,
            "text_sha256": _sha256(text.encode("utf-8")),
            "source_subject": subject,
            "source_matches": source_matches,
        }

    def create_pptx(
        self,
        *,
        slides: Sequence[Mapping[str, Any]],
        target_ref: str,
        idempotency_key: str,
        source: ParsedArtifact | SourceReceipt | str | Path | None = None,
        fault_after_write: bool = False,
    ) -> OutputReceipt:
        """Create a bounded presentation with basic layouts and owned images."""
        if isinstance(slides, (str, bytes)) or not isinstance(slides, Sequence):
            raise ArtifactValidationError("PPTX slides must be a sequence")
        if not slides or len(slides) > self.limits.max_slides:
            raise ArtifactValidationError(
                "PPTX slides must be non-empty and within max_slides"
            )
        source_receipt = (
            self._any_source_receipt(source) if source is not None else None
        )
        _, relative, fmt = self._ensure_new_target(target_ref, source_receipt)
        if fmt != "pptx":
            raise ArtifactValidationError("PPTX target must use .pptx")
        if source_receipt is not None:
            self._assert_source_current(source_receipt)
        try:
            from pptx import Presentation

            presentation = Presentation()
            presentation.core_properties.author = "JARVIS Foundry"
            presentation.core_properties.created = datetime(2000, 1, 1)
            presentation.core_properties.modified = datetime(2000, 1, 1)
            if source_receipt is not None:
                presentation.core_properties.subject = (
                    f"Source SHA-256: {source_receipt.sha256}"
                )
            required_text: list[str] = []
            image_sources: list[SourceReceipt] = []
            for spec in slides:
                slide_text, slide_images = self._add_pptx_slide(
                    presentation, spec
                )
                required_text.extend(slide_text)
                image_sources.extend(slide_images)
            if sum(len(slide.shapes) for slide in presentation.slides) > self.limits.max_shapes:
                raise ArtifactValidationError("PPTX exceeds max_shapes")
            output = io.BytesIO()
            presentation.save(output)
            data = self._canonical_zip(output.getvalue())
        except (ArtifactValidationError, ArtifactSchemaError):
            raise
        except Exception as error:  # noqa: BLE001 - presentation boundary
            raise ArtifactWriteError(
                f"python-pptx presentation creation failed: {error}"
            ) from error
        image_hashes = [source.sha256 for source in image_sources]
        image_lineage = (
            _sha256("\n".join(image_hashes).encode("ascii"))
            if image_sources
            else None
        )
        written = self._write_output(
            data,
            target_ref=relative,
            source_sha256=(
                source_receipt.sha256
                if source_receipt is not None
                else image_lineage
            ),
            idempotency_key=idempotency_key,
            format="pptx",
            readback={},
        )
        if fault_after_write:
            raise InjectedArtifactFailure(
                "injected failure after PPTX creation before verification",
                evidence=_post_write_evidence(written),
            )
        try:
            if source_receipt is not None:
                self._assert_source_current(source_receipt)
            for image_source in image_sources:
                self._assert_source_current(image_source)
            readback = self.verify_pptx(
                relative,
                expected_sha256=written.sha256,
                expected_slide_count=len(slides),
                required_text=required_text,
                expected_image_count=len(image_hashes),
                expected_source_sha256=(
                    source_receipt.sha256
                    if source_receipt is not None
                    else None
                ),
            )
        except LocalArtifactError as error:
            raise ArtifactPostWriteError(
                f"PPTX post-write verification failed: {error}",
                evidence=_post_write_evidence(written),
            ) from error
        readback["image_sha256s"] = image_hashes
        if source_receipt is not None:
            readback["source_ref"] = source_receipt.source_ref
            readback["source_sha256"] = source_receipt.sha256
        if not readback.get("verified"):
            raise ArtifactPostWriteError(
                "PPTX readback differs from requested slides",
                evidence=_post_write_evidence(written, readback),
            )
        return OutputReceipt(
            target_ref=written.target_ref, path=written.path, format=written.format,
            sha256=written.sha256, size_bytes=written.size_bytes,
            source_sha256=written.source_sha256, idempotency_key=written.idempotency_key,
            readback=readback, reconciled=written.reconciled, replayed=written.replayed,
        )

    def update_pptx(
        self,
        source: ParsedArtifact | SourceReceipt | str | Path,
        *,
        target_ref: str,
        idempotency_key: str,
        replacements: Mapping[str, str] | None = None,
        append_slides: Sequence[Mapping[str, Any]] = (),
    ) -> OutputReceipt:
        """Apply text replacements and append slides to a new presentation."""
        receipt = self._source_receipt(source, formats={"pptx"})
        _, relative, fmt = self._ensure_new_target(target_ref, receipt)
        if fmt != "pptx":
            raise ArtifactValidationError("PPTX update target must use .pptx")
        replace_map = dict(replacements or {})
        if isinstance(append_slides, (str, bytes)):
            raise ArtifactValidationError("append_slides must be a sequence")
        if not replace_map and not append_slides:
            raise ArtifactValidationError(
                "PPTX update requires replacements or appended slides"
            )
        for old, new in replace_map.items():
            if not isinstance(old, str) or not old or not isinstance(new, str):
                raise ArtifactValidationError(
                    "PPTX replacements require non-empty string keys and string values"
                )
        data = self._captured_bytes(receipt)
        _validate_zip_container(data, self.limits, "PPTX")
        try:
            from pptx import Presentation
            from pptx.enum.shapes import MSO_SHAPE_TYPE

            presentation = Presentation(io.BytesIO(data))
            original_slide_count = len(presentation.slides)
            original_image_count = sum(
                shape.shape_type == MSO_SHAPE_TYPE.PICTURE
                for slide in presentation.slides
                for shape in slide.shapes
            )
            replacements_applied: dict[str, int] = {}
            for old in sorted(replace_map):
                count = 0
                for slide in presentation.slides:
                    for shape in slide.shapes:
                        if hasattr(shape, "text") and old in str(shape.text):
                            count += str(shape.text).count(old)
                            shape.text = str(shape.text).replace(old, replace_map[old])
                if count == 0:
                    raise ArtifactSchemaError(
                        f"PPTX replacement text not found: {old!r}"
                    )
                replacements_applied[old] = count
            required_text = list(replace_map.values())
            image_sources: list[SourceReceipt] = []
            for spec in append_slides:
                slide_text, slide_images = self._add_pptx_slide(
                    presentation, spec
                )
                required_text.extend(slide_text)
                image_sources.extend(slide_images)
            if len(presentation.slides) > self.limits.max_slides:
                raise ArtifactValidationError("PPTX update exceeds max_slides")
            if sum(len(slide.shapes) for slide in presentation.slides) > self.limits.max_shapes:
                raise ArtifactValidationError("PPTX update exceeds max_shapes")
            presentation.core_properties.modified = datetime(2000, 1, 1)
            output = io.BytesIO()
            presentation.save(output)
            output_data = self._canonical_zip(output.getvalue())
        except (ArtifactValidationError, ArtifactSchemaError):
            raise
        except Exception as error:  # noqa: BLE001 - presentation boundary
            raise ArtifactWriteError(f"python-pptx update failed: {error}") from error
        written = self._write_output(
            output_data,
            target_ref=relative,
            source_sha256=receipt.sha256,
            idempotency_key=idempotency_key,
            format="pptx",
            readback={},
        )
        try:
            self._assert_source_current(receipt)
            for image_source in image_sources:
                self._assert_source_current(image_source)
            readback = self.verify_pptx(
                relative,
                expected_sha256=written.sha256,
                expected_slide_count=original_slide_count + len(append_slides),
                required_text=required_text,
                expected_image_count=original_image_count + len(image_sources),
            )
        except LocalArtifactError as error:
            raise ArtifactPostWriteError(
                f"PPTX post-write verification failed: {error}",
                evidence=_post_write_evidence(written),
            ) from error
        readback["replacements_applied"] = replacements_applied
        readback["image_sha256s"] = [
            image_source.sha256 for image_source in image_sources
        ]
        if not readback.get("verified"):
            raise ArtifactPostWriteError(
                "PPTX readback differs from requested updates",
                evidence=_post_write_evidence(written, readback),
            )
        return OutputReceipt(
            target_ref=written.target_ref, path=written.path, format=written.format,
            sha256=written.sha256, size_bytes=written.size_bytes,
            source_sha256=written.source_sha256, idempotency_key=written.idempotency_key,
            readback=readback, reconciled=written.reconciled, replayed=written.replayed,
        )

    def copy_pptx(
        self,
        parsed: ParsedArtifact,
        *,
        target_ref: str,
        idempotency_key: str,
    ) -> OutputReceipt:
        if parsed.kind != "pptx":
            raise ArtifactSchemaError("PPTX copy requires a parsed presentation")
        _, relative, fmt = self._ensure_new_target(target_ref, parsed.source)
        if fmt != "pptx":
            raise ArtifactValidationError("PPTX target must use .pptx")
        data = self._captured_bytes(parsed.source)
        written = self._write_output(
            data, target_ref=relative, source_sha256=parsed.source.sha256,
            idempotency_key=idempotency_key, format="pptx", readback={},
        )
        self._assert_source_current(parsed.source)
        readback = self.parse(relative)
        verified = (
            readback.kind == "pptx"
            and readback.details.get("slide_count") == parsed.details.get("slide_count")
            and readback.details.get("text_sha256") == parsed.details.get("text_sha256")
        )
        if not verified:
            raise ArtifactWriteError("python-pptx output readback differs from source")
        return OutputReceipt(
            target_ref=written.target_ref, path=written.path, format=written.format,
            sha256=written.sha256, size_bytes=written.size_bytes,
            source_sha256=written.source_sha256, idempotency_key=written.idempotency_key,
            readback={"verified": True, **readback.details},
            reconciled=written.reconciled, replayed=written.replayed,
        )

    def transform_image(
        self,
        parsed: ParsedArtifact,
        *,
        target_ref: str,
        idempotency_key: str,
        width: int | None = None,
        height: int | None = None,
        output_format: str | None = None,
    ) -> OutputReceipt:
        if parsed.kind != "image":
            raise ArtifactSchemaError("image transform requires a parsed image")
        _, relative, fmt = self._ensure_new_target(target_ref, parsed.source)
        if fmt not in self.IMAGE_FORMATS:
            raise ArtifactValidationError("image target has an unsupported extension")
        if width is not None and (not isinstance(width, int) or width <= 0):
            raise ArtifactValidationError("image width must be positive")
        if height is not None and (not isinstance(height, int) or height <= 0):
            raise ArtifactValidationError("image height must be positive")
        source_data = self._captured_bytes(parsed.source)
        try:
            from PIL import Image
            with Image.open(io.BytesIO(source_data)) as image:
                image.load()
                target_size = (width or image.width, height or image.height)
                if target_size[0] * target_size[1] > self.limits.max_pixels:
                    raise ArtifactValidationError("image output exceeds max_pixels")
                transformed = image.copy()
                if target_size != image.size:
                    transformed = transformed.resize(target_size, Image.Resampling.LANCZOS)
                requested_format = (output_format or fmt).lower().lstrip(".")
                if requested_format == "jpg":
                    requested_format = "jpeg"
                target_format = "jpeg" if fmt == "jpg" else fmt
                if requested_format != target_format:
                    raise ArtifactValidationError(
                        "image output_format must match the target extension"
                    )
                save_format = requested_format.upper()
                if save_format == "JPG":
                    save_format = "JPEG"
                if save_format in {"JPEG", "JPG"} and transformed.mode not in {"RGB", "L"}:
                    transformed = transformed.convert("RGB")
                output = io.BytesIO()
                transformed.save(output, format=save_format)
                data = output.getvalue()
                transformed.close()
        except ArtifactValidationError:
            raise
        except Exception as error:  # noqa: BLE001 - image boundary
            raise ArtifactWriteError(f"Pillow image transform failed: {error}") from error
        written = self._write_output(
            data, target_ref=relative, source_sha256=parsed.source.sha256,
            idempotency_key=idempotency_key, format=fmt, readback={},
        )
        self._assert_source_current(parsed.source)
        readback = self.parse(relative)
        verified = readback.kind == "image" and readback.details.get("width") == target_size[0] and readback.details.get("height") == target_size[1]
        if not verified:
            raise ArtifactWriteError("Pillow output readback differs from requested size")
        return OutputReceipt(
            target_ref=written.target_ref, path=written.path, format=written.format,
            sha256=written.sha256, size_bytes=written.size_bytes,
            source_sha256=written.source_sha256, idempotency_key=written.idempotency_key,
            readback={"verified": True, **readback.details},
            reconciled=written.reconciled, replayed=written.replayed,
        )


__all__ = [
    "CORE_DISTRIBUTIONS", "ArtifactLimits", "ArtifactParseError", "ArtifactSchemaError",
    "ArtifactPostWriteError", "ArtifactValidationError", "ArtifactWriteError",
    "InjectedArtifactFailure",
    "LocalArtifactConnector", "LocalArtifactError", "OutputReceipt",
    "ParsedArtifact", "SourceChangedError", "SourceReceipt", "TableData",
    "installed_library_versions",
]
