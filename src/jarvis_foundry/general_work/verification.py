"""Independent package verification for recorded General Work plans."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from ..clock import isoformat
from ..connectors.local_artifacts import (
    LocalArtifactConnector,
    LocalArtifactError,
)
from ..database import FoundryDatabase
from ..events import canonical_json
from .ledger import load_recorded_plan, resolve_delivery_target
from .model import (
    ArtifactAssertion,
    ConsistencyAssertion,
    DeliverableSpec,
    GeneralWorkPlan,
)


VERIFICATION_SCHEMA = "v13.general-work-package-verification.v1"
_TEXT_FORMATS = frozenset({"text", "txt", "md", "markdown"})
_STRUCTURAL_ONLY_FORMATS = frozenset({"docx", "pdf", "pptx", "xlsx", "xlsm"})


class GeneralWorkVerificationError(ValueError):
    """The package verifier cannot safely evaluate the requested plan."""


@dataclass(frozen=True, slots=True)
class PackageVerification:
    plan_id: str
    plan_hash: str
    status: str
    verified_at: str
    source_findings: tuple[Mapping[str, Any], ...]
    artifact_findings: tuple[Mapping[str, Any], ...]
    consistency_findings: tuple[Mapping[str, Any], ...]
    artifacts: tuple[Mapping[str, Any], ...]
    quality_coverage: tuple[Mapping[str, Any], ...]
    visual_status: tuple[Mapping[str, Any], ...]
    errors: tuple[str, ...]

    def stable_payload(self) -> dict[str, Any]:
        return {
            "schema": VERIFICATION_SCHEMA,
            "plan_id": self.plan_id,
            "plan_hash": self.plan_hash,
            "status": self.status,
            "source_findings": [dict(item) for item in self.source_findings],
            "artifact_findings": [dict(item) for item in self.artifact_findings],
            "consistency_findings": [
                dict(item) for item in self.consistency_findings
            ],
            "artifacts": [dict(item) for item in self.artifacts],
            "quality_coverage": [
                dict(item) for item in self.quality_coverage
            ],
            "visual_status": [dict(item) for item in self.visual_status],
            "errors": list(self.errors),
        }

    @property
    def verification_hash(self) -> str:
        return hashlib.sha256(
            canonical_json(self.stable_payload()).encode("utf-8")
        ).hexdigest()

    def to_payload(self) -> dict[str, Any]:
        return {
            **self.stable_payload(),
            "verified_at": self.verified_at,
            "verification_hash": self.verification_hash,
        }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _json_path(value: Any, selector: str) -> Any:
    path = selector.strip()
    if path in {"", "$"}:
        return value
    if path.startswith("$."):
        parts = path[2:].split(".")
    elif path.startswith("/"):
        parts = [
            item.replace("~1", "/").replace("~0", "~")
            for item in path[1:].split("/")
            if item
        ]
    else:
        parts = path.split(".")
    current = value
    for part in parts:
        if isinstance(current, Mapping):
            if part not in current:
                raise GeneralWorkVerificationError(
                    f"JSON selector key is absent: {part}"
                )
            current = current[part]
        elif isinstance(current, list):
            try:
                current = current[int(part)]
            except (ValueError, IndexError) as error:
                raise GeneralWorkVerificationError(
                    f"JSON selector index is invalid: {part}"
                ) from error
        else:
            raise GeneralWorkVerificationError(
                f"JSON selector cannot descend through {part}"
            )
    return current


def _artifact_snapshot(
    connector: LocalArtifactConnector,
    root: Path,
    deliverable: DeliverableSpec,
) -> dict[str, Any]:
    path = resolve_delivery_target(root, deliverable.path)
    base: dict[str, Any] = {
        "deliverable_id": deliverable.deliverable_id,
        "path": deliverable.path,
        "format": deliverable.format,
        "exists": path.is_file(),
        "size_bytes": path.stat().st_size if path.is_file() else 0,
        "sha256": _sha256(path) if path.is_file() else None,
        "kind": "missing",
        "details": {},
        "table": None,
        "text": "",
    }
    if not path.is_file():
        return base
    if deliverable.format in _TEXT_FORMATS:
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as error:
            raise GeneralWorkVerificationError(
                f"text deliverable is not UTF-8: {deliverable.path}"
            ) from error
        base.update(
            {
                "kind": "text",
                "details": {
                    "text_chars": len(text),
                    "text_sha256": hashlib.sha256(
                        text.encode("utf-8")
                    ).hexdigest(),
                },
                "text": text,
            }
        )
        return base
    parsed = connector.parse(
        deliverable.path,
        allow_formulas=deliverable.format in {"xlsx", "xlsm"},
        allow_external_links=False,
    )
    table = None
    if parsed.table is not None:
        table = {
            "headers": list(parsed.table.headers),
            "rows": [list(row) for row in parsed.table.rows],
            "row_count": parsed.table.row_count,
            "column_count": parsed.table.column_count,
        }
    details = dict(parsed.details)
    text = str(details.get("text") or "")
    base.update(
        {
            "kind": parsed.kind,
            "details": details,
            "table": table,
            "text": text,
        }
    )
    return base


def _cell_value(root: Path, snapshot: Mapping[str, Any], selector: str) -> Any:
    if "!" not in selector:
        raise GeneralWorkVerificationError(
            "cell selector must use Sheet!A1"
        )
    sheet_name, cell_ref = selector.split("!", 1)
    from openpyxl import load_workbook

    workbook = load_workbook(
        resolve_delivery_target(root, str(snapshot["path"])),
        data_only=False,
        read_only=True,
        keep_links=False,
    )
    try:
        if sheet_name not in workbook.sheetnames:
            raise GeneralWorkVerificationError(
                f"workbook sheet is absent: {sheet_name}"
            )
        return workbook[sheet_name][cell_ref].value
    finally:
        workbook.close()


def _selector_value(
    root: Path,
    snapshot: Mapping[str, Any],
    selector: str,
) -> Any:
    value = selector.strip()
    details = snapshot.get("details") or {}
    table = snapshot.get("table")
    if value in {"", "text"}:
        return snapshot.get("text")
    if value == "sha256":
        return snapshot.get("sha256")
    if value == "size_bytes":
        return snapshot.get("size_bytes")
    if value == "row_count":
        return table.get("row_count") if isinstance(table, Mapping) else None
    if value in {"page_count", "slide_count", "paragraph_count"}:
        return details.get(value)
    if value.startswith("detail:"):
        return _json_path(details, value.removeprefix("detail:"))
    if value.startswith("json:"):
        return _json_path(
            details.get("value"),
            value.removeprefix("json:"),
        )
    if value.startswith("cell:"):
        return _cell_value(root, snapshot, value.removeprefix("cell:"))
    if value.startswith("table:"):
        if not isinstance(table, Mapping):
            raise GeneralWorkVerificationError(
                "table selector requires a tabular deliverable"
            )
        parts = value.split(":", 2)
        if len(parts) != 3:
            raise GeneralWorkVerificationError(
                "table selector must use table:<row>:<column>"
            )
        try:
            row_number = int(parts[1])
        except ValueError as error:
            raise GeneralWorkVerificationError(
                "table selector row must be an integer"
            ) from error
        headers = list(table.get("headers") or ())
        if parts[2] not in headers or row_number < 1:
            raise GeneralWorkVerificationError(
                "table selector is outside the output table"
            )
        rows = list(table.get("rows") or ())
        if row_number > len(rows):
            raise GeneralWorkVerificationError(
                "table selector row is outside the output table"
            )
        return rows[row_number - 1][headers.index(parts[2])]
    if value.startswith("regex:"):
        pattern = value.removeprefix("regex:")
        match = re.search(pattern, str(snapshot.get("text") or ""))
        if match is None:
            raise GeneralWorkVerificationError(
                f"regex selector did not match: {pattern}"
            )
        return match.group(1) if match.lastindex else match.group(0)
    raise GeneralWorkVerificationError(
        f"unsupported artifact selector: {selector}"
    )


def _artifact_assertion(
    root: Path,
    snapshot: Mapping[str, Any],
    assertion: ArtifactAssertion,
) -> dict[str, Any]:
    try:
        if assertion.kind == "exists":
            actual = bool(snapshot.get("exists"))
            passed = actual is bool(assertion.expected)
        elif assertion.kind == "sha256":
            actual = snapshot.get("sha256")
            expected = str(assertion.expected).removeprefix("sha256:").lower()
            passed = actual == expected
        elif assertion.kind == "nonempty":
            actual = int(snapshot.get("size_bytes") or 0) > 0
            passed = actual is bool(assertion.expected)
        elif assertion.kind == "text_contains":
            actual = str(snapshot.get("text") or "")
            expected_values = (
                list(assertion.expected)
                if isinstance(assertion.expected, tuple)
                else [assertion.expected]
            )
            passed = all(str(value) in actual for value in expected_values)
            actual = {
                "text_sha256": hashlib.sha256(
                    actual.encode("utf-8")
                ).hexdigest(),
                "contains": [str(value) for value in expected_values],
            }
        elif assertion.kind == "row_count":
            actual = _selector_value(root, snapshot, "row_count")
            passed = actual == assertion.expected
        elif assertion.kind == "page_count":
            actual = _selector_value(root, snapshot, "page_count")
            passed = actual == assertion.expected
        elif assertion.kind == "slide_count":
            actual = _selector_value(root, snapshot, "slide_count")
            passed = actual == assertion.expected
        elif assertion.kind == "cell_equals":
            actual = _selector_value(
                root,
                snapshot,
                f"cell:{assertion.selector}",
            )
            passed = actual == assertion.expected
        else:
            actual = _selector_value(
                root,
                snapshot,
                f"json:{assertion.selector}",
            )
            passed = actual == assertion.expected
        return {
            "assertion_id": assertion.assertion_id,
            "deliverable_id": assertion.deliverable_id,
            "kind": assertion.kind,
            "required": assertion.required,
            "status": "PASS" if passed else "FAIL",
            "expected": assertion.expected,
            "actual": actual,
        }
    except Exception as error:  # noqa: BLE001 - findings must be durable
        return {
            "assertion_id": assertion.assertion_id,
            "deliverable_id": assertion.deliverable_id,
            "kind": assertion.kind,
            "required": assertion.required,
            "status": "FAIL",
            "expected": assertion.expected,
            "actual": None,
            "error": f"{type(error).__name__}: {error}",
        }


def _consistency_assertion(
    root: Path,
    snapshots: Mapping[str, Mapping[str, Any]],
    assertion: ConsistencyAssertion,
) -> dict[str, Any]:
    try:
        left = _selector_value(
            root,
            snapshots[assertion.left_deliverable_id],
            assertion.left_selector,
        )
        right = _selector_value(
            root,
            snapshots[assertion.right_deliverable_id],
            assertion.right_selector,
        )
        if assertion.operator == "equals":
            passed = left == right
        else:
            passed = str(right) in str(left)
        return {
            "assertion_id": assertion.assertion_id,
            "left_deliverable_id": assertion.left_deliverable_id,
            "left_selector": assertion.left_selector,
            "operator": assertion.operator,
            "right_deliverable_id": assertion.right_deliverable_id,
            "right_selector": assertion.right_selector,
            "required": assertion.required,
            "status": "PASS" if passed else "FAIL",
            "left": left,
            "right": right,
        }
    except Exception as error:  # noqa: BLE001 - findings must be durable
        return {
            "assertion_id": assertion.assertion_id,
            "left_deliverable_id": assertion.left_deliverable_id,
            "left_selector": assertion.left_selector,
            "operator": assertion.operator,
            "right_deliverable_id": assertion.right_deliverable_id,
            "right_selector": assertion.right_selector,
            "required": assertion.required,
            "status": "FAIL",
            "left": None,
            "right": None,
            "error": f"{type(error).__name__}: {error}",
        }


def _visual_status(
    root: Path,
    snapshot: Mapping[str, Any],
    deliverable: DeliverableSpec,
) -> dict[str, Any]:
    policy = deliverable.metadata.get("visual_verification")
    if not isinstance(policy, Mapping):
        return {
            "deliverable_id": deliverable.deliverable_id,
            "required": False,
            "status": (
                "NOT_TESTED"
                if deliverable.format in _STRUCTURAL_ONLY_FORMATS
                else "NOT_APPLICABLE"
            ),
        }
    required = bool(policy.get("required", False))
    evidence_ref = policy.get("evidence_path")
    if not isinstance(evidence_ref, str) or not evidence_ref.strip():
        return {
            "deliverable_id": deliverable.deliverable_id,
            "required": required,
            "status": "FAIL" if required else "NOT_TESTED",
            "error": "visual evidence path is absent",
        }
    try:
        evidence_path = resolve_delivery_target(root, evidence_ref)
        evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
        passed = (
            isinstance(evidence, Mapping)
            and evidence.get("status") == "PASS"
            and evidence.get("artifact_sha256") == snapshot.get("sha256")
            and evidence.get("clipping_detected") is False
        )
        return {
            "deliverable_id": deliverable.deliverable_id,
            "required": required,
            "status": "PASS" if passed else "FAIL",
            "evidence_path": evidence_ref,
            "evidence_sha256": _sha256(evidence_path),
        }
    except Exception as error:  # noqa: BLE001 - visual evidence fails closed
        return {
            "deliverable_id": deliverable.deliverable_id,
            "required": required,
            "status": "FAIL" if required else "NOT_TESTED",
            "evidence_path": evidence_ref,
            "error": f"{type(error).__name__}: {error}",
        }


def verify_plan_package(
    database: FoundryDatabase,
    plan: GeneralWorkPlan,
    *,
    resource_root: str | Path,
) -> PackageVerification:
    root = Path(resource_root).expanduser().resolve()
    source_findings: list[dict[str, Any]] = []
    errors: list[str] = []
    for source in plan.sources:
        try:
            path = (root / Path(*source.path.split("/"))).resolve(strict=False)
            path.relative_to(root)
            exists = path.is_file()
            actual_hash = _sha256(path) if exists else None
            actual_size = path.stat().st_size if exists else 0
            passed = (
                exists
                and actual_hash == source.sha256
                and actual_size == source.size_bytes
            )
            source_findings.append(
                {
                    "source_id": source.source_id,
                    "path": source.path,
                    "required_sha256": source.sha256,
                    "actual_sha256": actual_hash,
                    "required_size_bytes": source.size_bytes,
                    "actual_size_bytes": actual_size,
                    "status": "PASS" if passed else "FAIL",
                }
            )
        except Exception as error:  # noqa: BLE001 - source failures are findings
            source_findings.append(
                {
                    "source_id": source.source_id,
                    "path": source.path,
                    "status": "FAIL",
                    "error": f"{type(error).__name__}: {error}",
                }
            )

    connector = LocalArtifactConnector(root)
    snapshots: dict[str, dict[str, Any]] = {}
    for deliverable in plan.deliverables:
        try:
            snapshots[deliverable.deliverable_id] = _artifact_snapshot(
                connector,
                root,
                deliverable,
            )
        except (LocalArtifactError, GeneralWorkVerificationError, OSError) as error:
            snapshots[deliverable.deliverable_id] = {
                "deliverable_id": deliverable.deliverable_id,
                "path": deliverable.path,
                "format": deliverable.format,
                "exists": False,
                "size_bytes": 0,
                "sha256": None,
                "kind": "invalid",
                "details": {},
                "table": None,
                "text": "",
                "error": f"{type(error).__name__}: {error}",
            }

    artifact_findings = [
        _artifact_assertion(
            root,
            snapshots[assertion.deliverable_id],
            assertion,
        )
        for assertion in plan.artifact_assertions
    ]
    consistency_findings = [
        _consistency_assertion(root, snapshots, assertion)
        for assertion in plan.consistency_assertions
    ]
    assertions_by_deliverable: dict[str, list[ArtifactAssertion]] = {
        deliverable.deliverable_id: [] for deliverable in plan.deliverables
    }
    for assertion in plan.artifact_assertions:
        assertions_by_deliverable[assertion.deliverable_id].append(assertion)
    quality_coverage: list[dict[str, Any]] = []
    for deliverable in plan.deliverables:
        substantive = [
            assertion.assertion_id
            for assertion in assertions_by_deliverable[deliverable.deliverable_id]
            if assertion.required and assertion.kind not in {"exists", "nonempty"}
        ]
        passed = bool(substantive)
        quality_coverage.append(
            {
                "deliverable_id": deliverable.deliverable_id,
                "declared_requirements": list(deliverable.quality_requirements),
                "substantive_assertion_ids": substantive,
                "status": "PASS" if passed else "FAIL",
            }
        )
    visuals = [
        _visual_status(root, snapshots[deliverable.deliverable_id], deliverable)
        for deliverable in plan.deliverables
    ]

    artifact_failures = [
        finding
        for finding in artifact_findings
        if finding.get("required") and finding.get("status") != "PASS"
    ]
    consistency_failures = [
        finding
        for finding in consistency_findings
        if finding.get("required") and finding.get("status") != "PASS"
    ]
    source_failures = [
        finding for finding in source_findings if finding.get("status") != "PASS"
    ]
    invalid_artifacts = [
        snapshot
        for snapshot in snapshots.values()
        if not snapshot.get("exists") or snapshot.get("kind") == "invalid"
    ]
    quality_failures = [
        finding for finding in quality_coverage if finding["status"] != "PASS"
    ]
    visual_failures = [
        finding
        for finding in visuals
        if finding.get("required") and finding.get("status") != "PASS"
    ]
    if source_failures:
        errors.append("one or more source identities changed")
    if invalid_artifacts:
        errors.append("one or more deliverables are absent or malformed")
    if artifact_failures:
        errors.append("one or more required artifact assertions failed")
    if consistency_failures:
        errors.append("one or more cross-format consistency checks failed")
    if quality_failures:
        errors.append("one or more deliverables lack substantive quality proof")
    if visual_failures:
        errors.append("one or more required visual checks failed")
    status = "PASS" if not errors else "FAIL"
    artifacts = tuple(
        {
            "deliverable_id": snapshot["deliverable_id"],
            "path": snapshot["path"],
            "format": snapshot["format"],
            "exists": snapshot["exists"],
            "size_bytes": snapshot["size_bytes"],
            "sha256": snapshot["sha256"],
            "kind": snapshot["kind"],
            "details": snapshot.get("details") or {},
            "table": snapshot.get("table"),
            "source_ids": list(
                next(
                    item.source_ids
                    for item in plan.deliverables
                    if item.deliverable_id == snapshot["deliverable_id"]
                )
            ),
            **(
                {"error": snapshot["error"]}
                if snapshot.get("error")
                else {}
            ),
        }
        for snapshot in (
            snapshots[deliverable.deliverable_id]
            for deliverable in plan.deliverables
        )
    )
    return PackageVerification(
        plan_id=plan.plan_id,
        plan_hash=plan.plan_hash,
        status=status,
        verified_at=isoformat(),
        source_findings=tuple(source_findings),
        artifact_findings=tuple(artifact_findings),
        consistency_findings=tuple(consistency_findings),
        artifacts=artifacts,
        quality_coverage=tuple(quality_coverage),
        visual_status=tuple(visuals),
        errors=tuple(errors),
    )


def verify_recorded_package(
    database: FoundryDatabase,
    plan_id: str,
    *,
    resource_root: str | Path,
    allowed_action_kinds: tuple[str, ...] | None = None,
) -> PackageVerification:
    plan = load_recorded_plan(
        database,
        plan_id,
        allowed_action_kinds=allowed_action_kinds,
    )
    return verify_plan_package(
        database,
        plan,
        resource_root=resource_root,
    )


__all__ = [
    "VERIFICATION_SCHEMA",
    "GeneralWorkVerificationError",
    "PackageVerification",
    "verify_plan_package",
    "verify_recorded_package",
]
