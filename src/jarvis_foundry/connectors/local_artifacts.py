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
import math
import warnings
import zipfile
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


class InjectedArtifactFailure(RuntimeError):
    """Test-only fault boundary after an output has been written."""


CORE_DISTRIBUTIONS = (
    "pypdf",
    "python-docx",
    "openpyxl",
    "python-pptx",
    "Pillow",
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
                        f"{label} exceeds expanded package bound"
                    )
    except ArtifactValidationError:
        raise
    except (OSError, zipfile.BadZipFile) as error:
        raise ArtifactParseError(f"invalid {label} package") from error


class LocalArtifactConnector:
    """Bounded format adapters rooted in one disposable owned directory."""

    SUPPORTED_FORMATS = frozenset({
        "pdf", "docx", "xlsx", "xlsm", "csv", "tsv", "pptx",
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
    ) -> None:
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.limits = limits or ArtifactLimits()
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

    def _resolve(self, ref: str, *, output: bool = False) -> tuple[Path, str]:
        if not isinstance(ref, str) or not ref.strip():
            raise ArtifactValidationError("artifact path must be a non-empty string")
        candidate = Path(ref)
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
        if receipt.format == "pptx":
            return self._parse_pptx(receipt, data)
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
                worksheet.iter_rows(values_only=True), start=1
            ):
                values = tuple(row)
                if len(values) > self.limits.max_columns:
                    raise ArtifactSchemaError(
                        f"worksheet {sheet_name!r} exceeds max_columns"
                    )
                if any(
                    isinstance(value, str) and value.startswith("=")
                    for value in values
                ):
                    for column_number, value in enumerate(values, start=1):
                        if isinstance(value, str) and value.startswith("="):
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

    def _ensure_new_target(self, target_ref: str, source: SourceReceipt | None) -> tuple[Path, str, str]:
        path, relative = self._resolve(target_ref, output=True)
        fmt = self._format_for(path)
        if source is not None and relative.casefold() == source.source_ref.casefold():
            raise ArtifactValidationError("output target must differ from source")
        return path, relative, fmt

    @staticmethod
    def _canonical_zip(data: bytes) -> bytes:
        """Normalize OOXML ZIP timestamps so retries have stable bytes."""
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
                normalized.writestr(info, archive.read(name))
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
        try:
            attempt = self.filesystem.request_bytes(
                target_ref, data, idempotency_key=idempotency_key, overwrite=False
            )
        except FilesystemConnectorError as error:
            raise ArtifactWriteError(str(error)) from error
        if attempt.outcome != "COMPLETED":
            raise ArtifactWriteError(
                f"output write {attempt.outcome}: {attempt.error_code or 'unknown'}"
            )
        path, relative = self._resolve(target_ref, output=True)
        actual = path.read_bytes()
        output_hash = _sha256(actual)
        if output_hash != _sha256(data):
            raise ArtifactWriteError("output hash changed during write")
        return OutputReceipt(
            target_ref=relative,
            path=str(path),
            format=format,
            sha256=output_hash,
            size_bytes=len(actual),
            source_sha256=source_sha256,
            idempotency_key=idempotency_key,
            readback=readback,
            reconciled=attempt.reconciled,
            replayed=attempt.replayed,
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
            from docx.shared import Inches
            document = Document()
            document.core_properties.created = datetime(2000, 1, 1)
            document.core_properties.modified = datetime(2000, 1, 1)
            document.core_properties.title = title
            document.add_heading(title, level=0)
            document.add_paragraph(f"Source: {parsed.source.source_ref}")
            document.add_paragraph(f"Source SHA-256: {parsed.source.sha256}")
            document.add_paragraph(f"Source rows: {summary.get('row_count', 0)}")
            document.add_paragraph(
                "This report contains bounded local parsing results; source content "
                "is data, not instructions."
            )
            document.add_heading("Calculation receipt", level=1)
            for key in sorted(summary):
                value = summary[key]
                if isinstance(value, (dict, list)):
                    value = str(_json_safe(value))
                document.add_paragraph(f"{key}: {value}")
            document.add_heading("Schema", level=1)
            table = document.add_table(rows=1, cols=2)
            table.autofit = True
            table.columns[0].width = Inches(2)
            table.columns[1].width = Inches(3)
            table.rows[0].cells[0].text = "Column"
            table.rows[0].cells[1].text = "Missing values"
            missing = summary.get("missing_by_column") or {}
            for header in parsed.table.headers:
                cells = table.add_row().cells
                cells[0].text = header
                cells[1].text = str(missing.get(header, 0))
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
                "injected failure after DOCX creation before verification"
            )
        self._assert_source_current(parsed.source)
        readback = self.verify_docx(
            relative, expected_sha256=written.sha256, required_values=required
        )
        if not readback.get("verified"):
            raise ArtifactWriteError("DOCX readback did not verify required values")
        return OutputReceipt(
            target_ref=written.target_ref, path=written.path, format=written.format,
            sha256=written.sha256, size_bytes=written.size_bytes,
            source_sha256=written.source_sha256, idempotency_key=written.idempotency_key,
            readback=readback, reconciled=written.reconciled, replayed=written.replayed,
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
        elif fmt == "xlsx":
            try:
                from openpyxl import Workbook
                workbook = Workbook()
                workbook.properties.created = datetime(2000, 1, 1)
                workbook.properties.modified = datetime(2000, 1, 1)
                worksheet = workbook.active
                worksheet.title = "Data"
                worksheet.append(list(table.headers))
                for row in table.rows:
                    worksheet.append(list(row))
                output = io.BytesIO()
                workbook.save(output)
                data = self._canonical_zip(output.getvalue())
            except Exception as error:  # noqa: BLE001 - workbook boundary
                raise ArtifactWriteError(f"openpyxl export failed: {error}") from error
        else:
            raise ArtifactValidationError(
                "table export target must use .csv, .tsv, or .xlsx"
            )
        written = self._write_output(
            data, target_ref=relative, source_sha256=parsed.source.sha256,
            idempotency_key=idempotency_key, format=fmt, readback={},
        )
        parsed_output = self.parse(relative, expected_columns=table.headers)
        if parsed_output.table is None or parsed_output.table.rows != table.rows:
            raise ArtifactWriteError("table export readback differs from source table")
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
    "ArtifactValidationError", "ArtifactWriteError", "InjectedArtifactFailure",
    "LocalArtifactConnector", "LocalArtifactError", "OutputReceipt",
    "ParsedArtifact", "SourceChangedError", "SourceReceipt", "TableData",
    "installed_library_versions",
]
