"""Outcome-driven planning for reusable local General Work packages."""

from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Mapping, Sequence

from ..clock import isoformat
from ..connectors.local_artifacts import (
    ArtifactParseError,
    ArtifactSchemaError,
    ArtifactValidationError,
    LocalArtifactConnector,
    ParsedArtifact,
    TableData,
)
from ..events import canonical_json
from .model import (
    ArtifactAssertion,
    ConsistencyAssertion,
    DeliverableSpec,
    GeneralWorkPlan,
    SourceIdentity,
    WorkStep,
)


PLANNER_SCHEMA = "v13.general-work-planner.v1"
_OUTPUT_FORMATS = ("xlsx", "docx", "pdf", "pptx")
_TEXTUAL_KINDS = frozenset({"text", "docx", "pdf", "pptx", "json"})
_SKIP_DIRECTORY_NAMES = frozenset({".git", ".jarvis-general-work", "__pycache__"})


class GeneralWorkPlanningError(ValueError):
    """The requested outcome cannot be compiled into a safe work plan."""


def _text(value: object, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GeneralWorkPlanningError(f"{field_name} must be non-empty text")
    result = value.strip()
    if any(ord(character) < 32 for character in result):
        raise GeneralWorkPlanningError(
            f"{field_name} contains a control character"
        )
    return result


def _strings(value: object, field_name: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise GeneralWorkPlanningError(f"{field_name} must be a sequence")
    result = tuple(_text(item, f"{field_name} item") for item in value)
    if len(set(result)) != len(result):
        raise GeneralWorkPlanningError(f"{field_name} cannot contain duplicates")
    return result


def _freeze_mapping(
    value: object,
    field_name: str,
) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise GeneralWorkPlanningError(f"{field_name} must be an object")
    copied: dict[str, Any] = {}
    for key, item in value.items():
        if not isinstance(key, str):
            raise GeneralWorkPlanningError(
                f"{field_name} keys must be strings"
            )
        if isinstance(item, Mapping):
            copied[key] = _freeze_mapping(item, f"{field_name}.{key}")
        elif isinstance(item, (list, tuple)):
            copied[key] = tuple(item)
        elif item is None or isinstance(item, (str, bool, int, float)):
            copied[key] = item
        else:
            raise GeneralWorkPlanningError(
                f"{field_name}.{key} must be JSON-compatible"
            )
    return MappingProxyType(copied)


def _slug(value: str, *, fallback: str = "work-package") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return (slug or fallback)[:64].rstrip("-")


def _plain_title(value: str) -> str:
    first = re.split(r"[\r\n.!?]", value, maxsplit=1)[0]
    title = re.sub(r"[^A-Za-z0-9 ]+", " ", first)
    title = re.sub(r"\s+", " ", title).strip()
    return (title or "General Work Package")[:90].rstrip()


def _relative_ref(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError as error:
        raise GeneralWorkPlanningError(
            f"source escapes the registered resource root: {path}"
        ) from error


def _json_table_bytes(table: TableData) -> bytes:
    records = [
        {
            header: row[index]
            for index, header in enumerate(table.headers)
        }
        for row in table.rows
    ]
    return (
        json.dumps(
            {"records": records},
            ensure_ascii=True,
            indent=2,
            sort_keys=False,
        )
        + "\n"
    ).encode("utf-8")


def _bounded_text(value: str, *, limit: int = 900) -> str:
    lines = [
        re.sub(r"\s+", " ", line).strip()
        for line in value.splitlines()
        if line.strip()
    ]
    result = "\n".join(lines)
    if len(result) <= limit:
        return result
    return result[: limit - 3].rstrip() + "..."


def _decimal(value: Any) -> Decimal | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


@dataclass(frozen=True, slots=True)
class GeneralWorkRequest:
    objective_id: str
    resource_id: str
    desired_outcome: str
    source_paths: tuple[str, ...]
    delivery_root: str = "delivery"
    requested_formats: tuple[str, ...] = ()
    excluded_formats: tuple[str, ...] = ()
    output_stem: str | None = None
    title: str | None = None
    plan_id: str | None = None
    campaign_id: str | None = None
    column_aliases: Mapping[str, str] = field(default_factory=dict)
    select_columns: tuple[str, ...] = ()
    deduplicate_by: tuple[str, ...] = ()
    sort_by: tuple[str, ...] = ()
    source_column: str | None = None
    quality_requirements: tuple[str, ...] = ()
    style_requirements: Mapping[str, Any] = field(default_factory=dict)
    ambiguities: tuple[str, ...] = ()
    visual_verification: bool = False
    max_source_files: int = 16

    def __post_init__(self) -> None:
        for name in ("objective_id", "resource_id", "desired_outcome"):
            object.__setattr__(self, name, _text(getattr(self, name), name))
        object.__setattr__(
            self,
            "source_paths",
            _strings(self.source_paths, "source_paths"),
        )
        delivery_root = _text(self.delivery_root, "delivery_root").replace(
            "\\", "/"
        )
        path = PurePosixPath(delivery_root)
        if path.is_absolute() or ".." in path.parts or ":" in delivery_root:
            raise GeneralWorkPlanningError(
                "delivery_root must stay below the resource root"
            )
        object.__setattr__(
            self,
            "delivery_root",
            "/".join(part for part in path.parts if part not in {"", "."}),
        )
        for name in (
            "requested_formats",
            "excluded_formats",
            "select_columns",
            "deduplicate_by",
            "sort_by",
            "quality_requirements",
            "ambiguities",
        ):
            value = _strings(getattr(self, name), name)
            if name in {"requested_formats", "excluded_formats"}:
                value = tuple(item.casefold().lstrip(".") for item in value)
                unsupported = sorted(set(value).difference(_OUTPUT_FORMATS))
                if unsupported:
                    raise GeneralWorkPlanningError(
                        f"{name} contains unsupported formats: {unsupported}"
                    )
            object.__setattr__(self, name, value)
        if set(self.requested_formats).intersection(self.excluded_formats):
            raise GeneralWorkPlanningError(
                "a format cannot be both requested and excluded"
            )
        aliases = _freeze_mapping(self.column_aliases, "column_aliases")
        if any(
            not isinstance(value, str) or not value.strip()
            for value in aliases.values()
        ):
            raise GeneralWorkPlanningError(
                "column_aliases values must be non-empty text"
            )
        object.__setattr__(self, "column_aliases", aliases)
        object.__setattr__(
            self,
            "style_requirements",
            _freeze_mapping(self.style_requirements, "style_requirements"),
        )
        for name in ("output_stem", "title", "plan_id", "campaign_id"):
            value = getattr(self, name)
            if value is not None:
                object.__setattr__(self, name, _text(value, name))
        if self.source_column is not None:
            object.__setattr__(
                self,
                "source_column",
                _text(self.source_column, "source_column"),
            )
        if not isinstance(self.visual_verification, bool):
            raise GeneralWorkPlanningError(
                "visual_verification must be boolean"
            )
        if (
            not isinstance(self.max_source_files, int)
            or isinstance(self.max_source_files, bool)
            or not 1 <= self.max_source_files <= 32
        ):
            raise GeneralWorkPlanningError(
                "max_source_files must be between 1 and 32"
            )


@dataclass(frozen=True, slots=True)
class GeneralWorkPlanning:
    plan: GeneralWorkPlan
    inferred_formats: tuple[str, ...]
    rationale: tuple[str, ...]
    source_profiles: tuple[Mapping[str, Any], ...]

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema": PLANNER_SCHEMA,
            "plan_id": self.plan.plan_id,
            "plan_hash": self.plan.plan_hash,
            "inferred_formats": list(self.inferred_formats),
            "rationale": list(self.rationale),
            "source_profiles": [
                dict(profile) for profile in self.source_profiles
            ],
            "plan": self.plan.to_payload(),
        }


class GeneralWorkPlanner:
    """Compile a bounded owner outcome into an immutable execution DAG."""

    def __init__(self, resource_root: str | Path) -> None:
        self.root = Path(resource_root).expanduser().resolve()
        if not self.root.is_dir():
            raise GeneralWorkPlanningError(
                f"resource root is not a directory: {self.root}"
            )
        self.connector = LocalArtifactConnector(self.root)

    def _resolve_sources(
        self,
        request: GeneralWorkRequest,
    ) -> tuple[str, ...]:
        files: list[Path] = []
        delivery_root = PurePosixPath(request.delivery_root)
        for raw in request.source_paths:
            candidate = Path(raw)
            path = (
                candidate.expanduser().resolve()
                if candidate.is_absolute()
                else (self.root / candidate).resolve()
            )
            _relative_ref(self.root, path)
            current = self.root
            for part in path.relative_to(self.root).parts:
                current = current / part
                if current.exists() and current.is_symlink():
                    raise GeneralWorkPlanningError(
                        f"source traverses a symbolic link: {raw}"
                    )
            if path.is_file():
                files.append(path)
                continue
            if not path.is_dir():
                raise GeneralWorkPlanningError(
                    f"source is absent or unsupported: {raw}"
                )
            discovered_files: list[Path] = []
            for current_root, directory_names, file_names in os.walk(
                path,
                followlinks=False,
            ):
                current_path = Path(current_root)
                retained_directories: list[str] = []
                for directory_name in directory_names:
                    directory = current_path / directory_name
                    if directory.is_symlink():
                        raise GeneralWorkPlanningError(
                            "source directory contains a symbolic link: "
                            f"{_relative_ref(self.root, directory)}"
                        )
                    retained_directories.append(directory_name)
                directory_names[:] = retained_directories
                for file_name in file_names:
                    discovered = current_path / file_name
                    if discovered.is_symlink():
                        raise GeneralWorkPlanningError(
                            "source directory contains a symbolic link: "
                            f"{_relative_ref(self.root, discovered)}"
                        )
                    if discovered.is_file():
                        discovered_files.append(discovered)
            for discovered in sorted(
                discovered_files,
                key=lambda item: item.as_posix().casefold(),
            ):
                relative = PurePosixPath(_relative_ref(self.root, discovered))
                if any(
                    part in _SKIP_DIRECTORY_NAMES for part in relative.parts
                ):
                    continue
                if (
                    len(relative.parts) >= len(delivery_root.parts)
                    and relative.parts[: len(delivery_root.parts)]
                    == delivery_root.parts
                ):
                    continue
                files.append(discovered)
                if len(files) > request.max_source_files:
                    raise GeneralWorkPlanningError(
                        "source directory exceeds max_source_files"
                    )
        refs = tuple(
            dict.fromkeys(_relative_ref(self.root, path) for path in files)
        )
        if not refs:
            raise GeneralWorkPlanningError("no source files were discovered")
        if len(refs) > request.max_source_files:
            raise GeneralWorkPlanningError(
                "source set exceeds max_source_files"
            )
        return refs

    def _capture_sources(
        self,
        refs: Sequence[str],
    ) -> tuple[
        tuple[SourceIdentity, ...],
        tuple[ParsedArtifact, ...],
        tuple[Mapping[str, Any], ...],
    ]:
        identities: list[SourceIdentity] = []
        parsed_sources: list[ParsedArtifact] = []
        profiles: list[Mapping[str, Any]] = []
        for index, ref in enumerate(refs, start=1):
            try:
                parsed = self.connector.parse(ref)
            except (
                ArtifactParseError,
                ArtifactSchemaError,
                ArtifactValidationError,
                OSError,
            ) as error:
                raise GeneralWorkPlanningError(
                    f"source inspection failed for {ref}: "
                    f"{type(error).__name__}: {error}"
                ) from error
            source_id = (
                f"source-{index:03d}-"
                f"{hashlib.sha256(ref.encode('utf-8')).hexdigest()[:10]}"
            )
            identities.append(
                SourceIdentity(
                    source_id=source_id,
                    path=ref,
                    sha256=parsed.source.sha256,
                    format=parsed.source.format,
                    size_bytes=parsed.source.size_bytes,
                    captured_at=parsed.source.captured_at,
                )
            )
            profile: dict[str, Any] = {
                "source_id": source_id,
                "path": ref,
                "format": parsed.source.format,
                "kind": parsed.kind,
                "sha256": parsed.source.sha256,
                "size_bytes": parsed.source.size_bytes,
            }
            if parsed.table is not None:
                summary = self.connector.summarize_table(parsed)
                profile.update(
                    {
                        "headers": list(parsed.table.headers),
                        "row_count": parsed.table.row_count,
                        "column_count": parsed.table.column_count,
                        "missing_by_column": summary["missing_by_column"],
                    }
                )
            else:
                for name in (
                    "text_chars",
                    "line_count",
                    "paragraph_count",
                    "page_count",
                    "slide_count",
                    "shape_count",
                ):
                    if parsed.details.get(name) is not None:
                        profile[name] = parsed.details[name]
            parsed_sources.append(parsed)
            profiles.append(MappingProxyType(profile))
        return tuple(identities), tuple(parsed_sources), tuple(profiles)

    @staticmethod
    def _infer_formats(
        request: GeneralWorkRequest,
        parsed_sources: Sequence[ParsedArtifact],
    ) -> tuple[tuple[str, ...], tuple[str, ...]]:
        if request.requested_formats:
            selected = [
                value
                for value in _OUTPUT_FORMATS
                if value in request.requested_formats
                and value not in request.excluded_formats
            ]
            return tuple(selected), ("used the explicitly requested formats",)

        outcome = request.desired_outcome.casefold()
        has_table = any(parsed.table is not None for parsed in parsed_sources)
        has_text = any(
            parsed.kind in _TEXTUAL_KINDS for parsed in parsed_sources
        )
        denied = set(request.excluded_formats)
        if re.search(
            (
                r"\b(no|without)\s+"
                r"(?:(?:pdf|printable copy)\s+(?:or|and)\s+)?"
                r"(slides?|presentation|deck)\b"
            ),
            outcome,
        ):
            denied.add("pptx")
        if re.search(
            (
                r"\b(no|without)\s+"
                r"(?:(?:slides?|presentation|deck)\s+(?:or|and)\s+)?"
                r"(pdf|printable copy)\b"
            ),
            outcome,
        ):
            denied.add("pdf")
        selected: set[str] = set()
        rationale: list[str] = []
        if has_table:
            selected.add("xlsx")
            rationale.append(
                "tabular sources require an editable workbook for detailed use"
            )
        if has_text or any(
            word in outcome
            for word in (
                "report",
                "memo",
                "written",
                "reconstruct",
                "consolidate",
                "procedure",
            )
        ):
            selected.add("docx")
            rationale.append(
                "the outcome needs a structured document for sustained reading"
            )
        if any(
            word in outcome
            for word in ("printable", "pdf", "signed copy", "final copy")
        ):
            selected.add("pdf")
            rationale.append(
                "the outcome calls for a stable printable representation"
            )
        if any(
            word in outcome
            for word in ("presentation", "slides", "slide deck", "briefing")
        ):
            selected.add("pptx")
            rationale.append(
                "the outcome includes a live or asynchronous briefing"
            )
        if "package" in outcome:
            if has_table:
                selected.update({"docx", "pdf"})
            if "briefing" in outcome or "presentation" in outcome:
                selected.add("pptx")
        selected.difference_update(denied)
        if denied:
            rationale.append(
                "excluded formats were removed from the inferred package"
            )
        if not selected:
            selected.add("docx" if has_text else "xlsx")
            rationale.append(
                "selected the smallest useful default for the captured sources"
            )
        ordered = tuple(value for value in _OUTPUT_FORMATS if value in selected)
        return ordered, tuple(dict.fromkeys(rationale))

    @staticmethod
    def _table_summary(table: TableData) -> dict[str, Any]:
        missing = {
            header: sum(
                row[index] in (None, "")
                for row in table.rows
            )
            for index, header in enumerate(table.headers)
        }
        numeric_sums: dict[str, str] = {}
        for index, header in enumerate(table.headers):
            values = [
                _decimal(row[index])
                for row in table.rows
                if row[index] not in (None, "")
            ]
            if values and all(value is not None for value in values):
                numeric_sums[header] = format(
                    sum((value for value in values if value is not None), Decimal()),
                    "f",
                )
        return {
            "row_count": table.row_count,
            "column_count": table.column_count,
            "headers": list(table.headers),
            "missing_by_column": missing,
            "numeric_sums": numeric_sums,
            "preview_rows": [
                list(row[:8]) for row in table.rows[:8]
            ],
        }

    @staticmethod
    def _text_sections(
        parsed_sources: Sequence[ParsedArtifact],
    ) -> list[dict[str, Any]]:
        sections: list[dict[str, Any]] = []
        for parsed in parsed_sources:
            if parsed.table is not None:
                continue
            text = str(parsed.details.get("text") or "")
            if not text and parsed.kind == "json":
                text = json.dumps(
                    parsed.details.get("value"),
                    ensure_ascii=True,
                    indent=2,
                    sort_keys=True,
                )
            excerpt = _bounded_text(text)
            if not excerpt:
                excerpt = (
                    f"Captured {parsed.kind} source with "
                    f"{parsed.source.size_bytes} bytes."
                )
            sections.append(
                {
                    "heading": _plain_title(
                        Path(parsed.source.source_ref).stem
                    ),
                    "paragraphs": [excerpt],
                }
            )
        return sections

    @staticmethod
    def _visual_metadata(
        request: GeneralWorkRequest,
        deliverable_id: str,
    ) -> dict[str, Any]:
        return {
            "visual_verification": {
                "required": request.visual_verification,
                "evidence_path": (
                    f"{request.delivery_root}/evidence/"
                    f"{deliverable_id}.json"
                ),
            }
        }

    def plan(self, request: GeneralWorkRequest) -> GeneralWorkPlanning:
        refs = self._resolve_sources(request)
        sources, parsed_sources, profiles = self._capture_sources(refs)
        inferred_formats, rationale = self._infer_formats(
            request,
            parsed_sources,
        )
        table_sources = tuple(
            parsed for parsed in parsed_sources if parsed.table is not None
        )
        text_sources = tuple(
            parsed for parsed in parsed_sources if parsed.table is None
        )
        if "xlsx" in inferred_formats and not table_sources:
            raise GeneralWorkPlanningError(
                "an XLSX deliverable requires at least one tabular source"
            )

        title = _plain_title(request.title or request.desired_outcome)
        stem = _slug(request.output_stem or title)
        seed = {
            "schema": PLANNER_SCHEMA,
            "objective_id": request.objective_id,
            "resource_id": request.resource_id,
            "desired_outcome": request.desired_outcome,
            "delivery_root": request.delivery_root,
            "sources": [
                {
                    "path": source.path,
                    "sha256": source.sha256,
                    "format": source.format,
                    "size_bytes": source.size_bytes,
                }
                for source in sources
            ],
            "formats": list(inferred_formats),
            "column_aliases": dict(request.column_aliases),
            "select_columns": list(request.select_columns),
            "deduplicate_by": list(request.deduplicate_by),
            "sort_by": list(request.sort_by),
            "source_column": request.source_column,
            "title": title,
            "stem": stem,
            "quality_requirements": list(request.quality_requirements),
            "style_requirements": dict(request.style_requirements),
            "ambiguities": list(request.ambiguities),
            "visual_verification": request.visual_verification,
        }
        request_hash = hashlib.sha256(
            canonical_json(seed).encode("utf-8")
        ).hexdigest()
        plan_id = request.plan_id or f"general-work:{request_hash[:24]}"
        campaign_id = (
            request.campaign_id
            or f"campaign-general-work:{request_hash[:24]}"
        )

        source_by_ref = {
            source.path: source for source in sources
        }
        inspect_steps = tuple(
            WorkStep(
                step_id=f"inspect-{index:03d}",
                description=f"Inspect captured source {source.path}",
                action_kind="file.inspect",
                payload={
                    "target": source.path,
                    "preview_bytes": 0,
                },
                dependencies=(),
                source_ids=(source.source_id,),
                output_ids=(),
                verification_assertion_ids=(),
            )
            for index, source in enumerate(sources, start=1)
        )
        steps: list[WorkStep] = list(inspect_steps)
        deliverables: list[DeliverableSpec] = []
        artifact_assertions: list[ArtifactAssertion] = []
        consistency_assertions: list[ConsistencyAssertion] = []
        tabular_source_ids = tuple(
            source_by_ref[parsed.source.source_ref].source_id
            for parsed in table_sources
        )
        all_source_ids = tuple(source.source_id for source in sources)
        inspect_ids_by_source = {
            source.source_id: inspect_steps[index].step_id
            for index, source in enumerate(sources)
        }

        normalized_step_id: str | None = None
        primary_table: TableData | None = None
        primary_ref: str | None = None
        needs_normalized_table = bool(
            table_sources
            and (
                len(table_sources) > 1
                or request.column_aliases
                or request.select_columns
                or request.deduplicate_by
                or request.sort_by
                or request.source_column
            )
        )
        if needs_normalized_table:
            primary_table, _transformation = self.connector.compose_tables(
                table_sources,
                column_aliases=request.column_aliases,
                select_columns=request.select_columns,
                deduplicate_by=request.deduplicate_by,
                sort_by=request.sort_by,
                source_column=request.source_column,
            )
            primary_ref = (
                f"{request.delivery_root}/data/{stem}-normalized.json"
            )
            normalized_step_id = "normalize-data"
            assertion_id = "normalized-row-count"
            deliverables.append(
                DeliverableSpec(
                    deliverable_id="normalized-data",
                    path=primary_ref,
                    format="json",
                    source_ids=tabular_source_ids,
                    producer_step_id=normalized_step_id,
                    verification_assertion_ids=(assertion_id,),
                    metadata={"role": "supporting-data"},
                    quality_requirements=(
                        "normalized rows match captured tabular sources",
                    ),
                )
            )
            artifact_assertions.append(
                ArtifactAssertion(
                    assertion_id=assertion_id,
                    deliverable_id="normalized-data",
                    kind="row_count",
                    expected=primary_table.row_count,
                    selector="",
                )
            )
            steps.append(
                WorkStep(
                    step_id=normalized_step_id,
                    description="Normalize and merge captured tabular sources",
                    action_kind="artifact.table.merge",
                    payload={
                        "sources": [
                            parsed.source.source_ref
                            for parsed in table_sources
                        ],
                        "target": primary_ref,
                        "column_aliases": dict(request.column_aliases),
                        "select_columns": list(request.select_columns),
                        "deduplicate_by": list(request.deduplicate_by),
                        "sort_by": list(request.sort_by),
                        "source_column": request.source_column,
                        "trim_strings": True,
                    },
                    dependencies=tuple(
                        inspect_ids_by_source[source_id]
                        for source_id in tabular_source_ids
                    ),
                    source_ids=tabular_source_ids,
                    output_ids=("normalized-data",),
                    verification_assertion_ids=(assertion_id,),
                    max_retries=1,
                )
            )
        elif table_sources:
            primary_table = table_sources[0].table
            primary_ref = table_sources[0].source.source_ref

        table_summary = (
            self._table_summary(primary_table)
            if primary_table is not None
            else None
        )
        marker = (
            f"Records: {table_summary['row_count']}"
            if table_summary is not None
            else f"Sources: {len(sources)}"
        )
        base_dependencies = (
            (normalized_step_id,)
            if normalized_step_id is not None
            else tuple(step.step_id for step in inspect_steps)
        )
        document_dependencies = tuple(
            dict.fromkeys(
                [
                    *base_dependencies,
                    *(step.step_id for step in inspect_steps),
                ]
            )
        )

        deliverable_ids_by_format: dict[str, str] = {}
        if "xlsx" in inferred_formats:
            assert primary_ref is not None and table_summary is not None
            deliverable_id = "workbook"
            step_id = "build-workbook"
            path = f"{request.delivery_root}/spreadsheets/{stem}.xlsx"
            assertion_id = "workbook-row-count"
            deliverable_ids_by_format["xlsx"] = deliverable_id
            artifact_assertions.append(
                ArtifactAssertion(
                    assertion_id,
                    deliverable_id,
                    "row_count",
                    table_summary["row_count"],
                    "",
                )
            )
            deliverables.append(
                DeliverableSpec(
                    deliverable_id,
                    path,
                    "xlsx",
                    tabular_source_ids,
                    step_id,
                    (assertion_id,),
                    metadata=self._visual_metadata(request, deliverable_id),
                    quality_requirements=(
                        "workbook reopens with the normalized row count",
                    ),
                    style_requirements=request.style_requirements,
                )
            )
            steps.append(
                WorkStep(
                    step_id,
                    "Create the editable workbook",
                    "artifact.xlsx.create",
                    {
                        "source": primary_ref,
                        "sources": [
                            parsed.source.source_ref
                            for parsed in table_sources
                        ],
                        "target": path,
                        "sheet_name": "Data",
                    },
                    base_dependencies,
                    tabular_source_ids,
                    (deliverable_id,),
                    (assertion_id,),
                    max_retries=1,
                )
            )

        document_sections: list[dict[str, Any]] = []
        if table_summary is not None:
            numeric = table_summary["numeric_sums"]
            summary_lines = [
                marker,
                f"Columns: {table_summary['column_count']}",
            ]
            if numeric:
                summary_lines.extend(
                    f"{name} total: {value}"
                    for name, value in list(numeric.items())[:4]
                )
            document_sections.append(
                {
                    "heading": "Data summary",
                    "paragraphs": ["\n".join(summary_lines)],
                    "table": {
                        "headers": table_summary["headers"][:8],
                        "rows": table_summary["preview_rows"],
                    },
                }
            )
        document_sections.extend(self._text_sections(text_sources))
        source_refs = [source.path for source in sources]

        if "docx" in inferred_formats:
            deliverable_id = "document"
            step_id = "build-document"
            path = f"{request.delivery_root}/documents/{stem}.docx"
            assertion_id = "document-content"
            deliverable_ids_by_format["docx"] = deliverable_id
            artifact_assertions.append(
                ArtifactAssertion(
                    assertion_id,
                    deliverable_id,
                    "text_contains",
                    (title, marker),
                    "",
                )
            )
            deliverables.append(
                DeliverableSpec(
                    deliverable_id,
                    path,
                    "docx",
                    all_source_ids,
                    step_id,
                    (assertion_id,),
                    metadata=self._visual_metadata(request, deliverable_id),
                    quality_requirements=(
                        "document contains the package summary and source lineage",
                    ),
                    style_requirements=request.style_requirements,
                )
            )
            steps.append(
                WorkStep(
                    step_id,
                    "Create the structured document",
                    "artifact.docx.compose",
                    {
                        "title": title,
                        "opening": [
                            request.desired_outcome,
                            (
                                f"This package consolidates {len(sources)} "
                                f"captured source files. {marker}"
                            ),
                        ],
                        "sections": document_sections
                        or [
                            {
                                "heading": "Source coverage",
                                "paragraphs": [marker],
                            }
                        ],
                        "sources": source_refs,
                        "required_values": [marker],
                        "target": path,
                    },
                    document_dependencies,
                    all_source_ids,
                    (deliverable_id,),
                    (assertion_id,),
                    max_retries=1,
                )
            )

        if "pdf" in inferred_formats:
            deliverable_id = "pdf"
            step_id = "build-pdf"
            path = f"{request.delivery_root}/pdf/{stem}.pdf"
            count_assertion = "pdf-page-count"
            content_assertion = "pdf-content"
            deliverable_ids_by_format["pdf"] = deliverable_id
            page_lines = [
                request.desired_outcome,
                marker,
                f"Captured sources: {len(sources)}",
            ]
            if table_summary is not None:
                page_lines.append(
                    "Columns: " + ", ".join(table_summary["headers"][:10])
                )
                for name, value in list(
                    table_summary["numeric_sums"].items()
                )[:4]:
                    page_lines.append(f"{name} total: {value}")
            for section in self._text_sections(text_sources)[:4]:
                page_lines.append(str(section["heading"]))
                page_lines.append(str(section["paragraphs"][0]))
            pages = ("\n".join(page_lines),)
            artifact_assertions.extend(
                (
                    ArtifactAssertion(
                        count_assertion,
                        deliverable_id,
                        "page_count",
                        len(pages),
                        "",
                    ),
                    ArtifactAssertion(
                        content_assertion,
                        deliverable_id,
                        "text_contains",
                        marker,
                        "",
                    ),
                )
            )
            deliverables.append(
                DeliverableSpec(
                    deliverable_id,
                    path,
                    "pdf",
                    all_source_ids,
                    step_id,
                    (count_assertion, content_assertion),
                    metadata=self._visual_metadata(request, deliverable_id),
                    quality_requirements=(
                        "printable copy reopens with the package summary",
                    ),
                    style_requirements=request.style_requirements,
                )
            )
            steps.append(
                WorkStep(
                    step_id,
                    "Create the printable package",
                    "artifact.pdf.create",
                    {
                        "title": title,
                        "pages": list(pages),
                        "sources": source_refs,
                        "target": path,
                    },
                    document_dependencies,
                    all_source_ids,
                    (deliverable_id,),
                    (count_assertion, content_assertion),
                    max_retries=1,
                )
            )

        if "pptx" in inferred_formats:
            deliverable_id = "presentation"
            step_id = "build-presentation"
            path = f"{request.delivery_root}/presentations/{stem}.pptx"
            count_assertion = "presentation-slide-count"
            content_assertion = "presentation-content"
            deliverable_ids_by_format["pptx"] = deliverable_id
            summary_body = [marker, f"Captured sources: {len(sources)}"]
            if table_summary is not None:
                summary_body.extend(
                    f"{name} total: {value}"
                    for name, value in list(
                        table_summary["numeric_sums"].items()
                    )[:4]
                )
            slides: list[dict[str, Any]] = [
                {
                    "layout": "title",
                    "title": title,
                    "body": request.desired_outcome,
                },
                {
                    "layout": "title_and_content",
                    "title": "Work package summary",
                    "body": summary_body,
                },
            ]
            if table_summary is not None:
                slides.append(
                    {
                        "layout": "title_and_content",
                        "title": "Data coverage",
                        "body": [
                            "Columns: "
                            + ", ".join(table_summary["headers"][:8]),
                            marker,
                        ],
                    }
                )
            elif text_sources:
                slides.append(
                    {
                        "layout": "title_and_content",
                        "title": "Source coverage",
                        "body": [
                            Path(parsed.source.source_ref).name
                            for parsed in text_sources[:8]
                        ],
                    }
                )
            artifact_assertions.extend(
                (
                    ArtifactAssertion(
                        count_assertion,
                        deliverable_id,
                        "slide_count",
                        len(slides),
                        "",
                    ),
                    ArtifactAssertion(
                        content_assertion,
                        deliverable_id,
                        "text_contains",
                        marker,
                        "",
                    ),
                )
            )
            deliverables.append(
                DeliverableSpec(
                    deliverable_id,
                    path,
                    "pptx",
                    all_source_ids,
                    step_id,
                    (count_assertion, content_assertion),
                    metadata=self._visual_metadata(request, deliverable_id),
                    quality_requirements=(
                        "presentation reopens with the package summary",
                    ),
                    style_requirements=request.style_requirements,
                )
            )
            steps.append(
                WorkStep(
                    step_id,
                    "Create the briefing presentation",
                    "artifact.pptx.create",
                    {
                        "slides": slides,
                        "sources": source_refs,
                        "target": path,
                    },
                    document_dependencies,
                    all_source_ids,
                    (deliverable_id,),
                    (count_assertion, content_assertion),
                    max_retries=1,
                )
            )

        if not deliverables:
            raise GeneralWorkPlanningError(
                "the requested outcome produced no supported deliverables"
            )
        if "xlsx" in deliverable_ids_by_format and "docx" in deliverable_ids_by_format:
            consistency_assertions.append(
                ConsistencyAssertion(
                    "workbook-document-record-count",
                    deliverable_ids_by_format["xlsx"],
                    "row_count",
                    "contains",
                    deliverable_ids_by_format["docx"],
                    r"regex:Records: ([0-9]+)",
                )
            )
        if "docx" in deliverable_ids_by_format and "pdf" in deliverable_ids_by_format:
            consistency_assertions.append(
                ConsistencyAssertion(
                    "document-pdf-record-count",
                    deliverable_ids_by_format["docx"],
                    r"regex:(?:Records|Sources): ([0-9]+)",
                    "equals",
                    deliverable_ids_by_format["pdf"],
                    r"regex:(?:Records|Sources): ([0-9]+)",
                )
            )
        if "pdf" in deliverable_ids_by_format and "pptx" in deliverable_ids_by_format:
            consistency_assertions.append(
                ConsistencyAssertion(
                    "pdf-presentation-record-count",
                    deliverable_ids_by_format["pdf"],
                    r"regex:(?:Records|Sources): ([0-9]+)",
                    "equals",
                    deliverable_ids_by_format["pptx"],
                    r"regex:(?:Records|Sources): ([0-9]+)",
                )
            )

        quality_requirements = tuple(
            dict.fromkeys(
                (
                    "preserve every captured source byte-for-byte",
                    "reopen every produced artifact independently",
                    "verify cross-format values before completion",
                    *request.quality_requirements,
                )
            )
        )
        plan = GeneralWorkPlan(
            plan_id=plan_id,
            objective_id=request.objective_id,
            campaign_id=campaign_id,
            resource_id=request.resource_id,
            desired_outcome=request.desired_outcome,
            delivery_root=request.delivery_root,
            sources=sources,
            deliverables=tuple(deliverables),
            steps=tuple(steps),
            artifact_assertions=tuple(artifact_assertions),
            consistency_assertions=tuple(consistency_assertions),
            created_at=isoformat(),
            metadata={
                "schema": PLANNER_SCHEMA,
                "request_hash": request_hash,
                "title": title,
                "output_stem": stem,
                "inferred_formats": list(inferred_formats),
                "rationale": list(rationale),
                "source_profiles": [dict(profile) for profile in profiles],
                "transformations": {
                    "column_aliases": dict(request.column_aliases),
                    "select_columns": list(request.select_columns),
                    "deduplicate_by": list(request.deduplicate_by),
                    "sort_by": list(request.sort_by),
                    "source_column": request.source_column,
                },
                "organization": {
                    "delivery_root": request.delivery_root,
                    "subdirectories": [
                        str(PurePosixPath(item.path).parent)
                        for item in deliverables
                    ],
                },
            },
            quality_requirements=quality_requirements,
            ambiguities=request.ambiguities,
            source_preservation="PRESERVE_ORIGINALS",
        )
        return GeneralWorkPlanning(
            plan=plan,
            inferred_formats=inferred_formats,
            rationale=rationale,
            source_profiles=profiles,
        )


__all__ = [
    "PLANNER_SCHEMA",
    "GeneralWorkPlanner",
    "GeneralWorkPlanning",
    "GeneralWorkPlanningError",
    "GeneralWorkRequest",
]
