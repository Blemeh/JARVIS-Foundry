"""Independently defined invariants for the historical replay contract."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

from jarvis_foundry.kernel.canonical import canonical_hash
from jarvis_foundry.kernel.compat.readers import LegacyRow
from jarvis_foundry.kernel.projections import ProjectionSnapshot


@dataclass(frozen=True)
class InvariantReport:
    """Row-level invariants defined independently of legacy projection code."""

    ok: bool
    event_count: int
    min_sequence: int | None
    max_sequence: int | None
    sequence_gaps: tuple[int, ...]
    duplicate_event_ids: tuple[str, ...]
    duplicate_idempotency_keys: tuple[str, ...]
    payload_hash_mismatches: tuple[str, ...]
    unsupported_schema_versions: tuple[int, ...]
    malformed_event_types: tuple[str, ...]
    empty_required_fields: tuple[tuple[int, str], ...]
    event_type_counts: Mapping[str, int]

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "event_count": self.event_count,
            "min_sequence": self.min_sequence,
            "max_sequence": self.max_sequence,
            "sequence_gaps": list(self.sequence_gaps),
            "duplicate_event_ids": list(self.duplicate_event_ids),
            "duplicate_idempotency_keys": list(self.duplicate_idempotency_keys),
            "payload_hash_mismatches": list(self.payload_hash_mismatches),
            "unsupported_schema_versions": list(self.unsupported_schema_versions),
            "malformed_event_types": list(self.malformed_event_types),
            "empty_required_fields": [list(item) for item in self.empty_required_fields],
            "event_type_counts": dict(sorted(self.event_type_counts.items())),
        }


@dataclass(frozen=True)
class ProjectionInvariantReport:
    ok: bool
    mismatches: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {"ok": self.ok, "mismatches": list(self.mismatches)}


def validate_rows(rows: Iterable[LegacyRow]) -> InvariantReport:
    """Validate row-level invariants over the legacy stream in sequence order."""
    ordered = list(rows)
    event_count = len(ordered)
    event_ids: list[str] = []
    idempotency_keys: list[str] = []
    schema_versions: set[int] = set()
    malformed_types: set[str] = set()
    empty_fields: list[tuple[int, str]] = []
    hash_mismatches: list[str] = []
    type_counts: Counter[str] = Counter()
    min_sequence: int | None = None
    max_sequence: int | None = None
    sequences: set[int] = set()

    for row in ordered:
        sequences.add(row.sequence)
        min_sequence = row.sequence if min_sequence is None else min(min_sequence, row.sequence)
        max_sequence = row.sequence if max_sequence is None else max(max_sequence, row.sequence)
        event_ids.append(row.event_id)
        idempotency_keys.append(row.idempotency_key)
        schema_versions.add(row.schema_version)
        type_counts[row.event_type] += 1
        if not row.event_type or "." not in row.event_type:
            malformed_types.add(row.event_type)
        for field_name in (
            "event_id",
            "event_type",
            "occurred_at",
            "recorded_at",
            "source_type",
            "source_id",
            "correlation_id",
            "idempotency_key",
        ):
            if not getattr(row, field_name):
                empty_fields.append((row.sequence, field_name))
        if row.payload_hash != canonical_hash(row.payload):
            hash_mismatches.append(row.event_id)

    if event_count:
        expected = set(range(min_sequence or 1, (max_sequence or 1) + 1))
        gaps = tuple(sorted(expected - sequences))
    else:
        gaps = ()
    id_counts = Counter(event_ids)
    key_counts = Counter(idempotency_keys)
    duplicate_ids = tuple(sorted(event_id for event_id, count in id_counts.items() if count > 1))
    duplicate_keys = tuple(sorted(key for key, count in key_counts.items() if count > 1))
    unsupported_versions = tuple(sorted(version for version in schema_versions if version != 1))
    report = InvariantReport(
        ok=(
            not gaps
            and not duplicate_ids
            and not duplicate_keys
            and not hash_mismatches
            and not unsupported_versions
            and not malformed_types
            and not empty_fields
        ),
        event_count=event_count,
        min_sequence=min_sequence,
        max_sequence=max_sequence,
        sequence_gaps=gaps,
        duplicate_event_ids=duplicate_ids,
        duplicate_idempotency_keys=duplicate_keys,
        payload_hash_mismatches=tuple(hash_mismatches),
        unsupported_schema_versions=unsupported_versions,
        malformed_event_types=tuple(sorted(malformed_types)),
        empty_required_fields=tuple(empty_fields),
        event_type_counts=dict(type_counts),
    )
    return report


def check_projection_snapshot(
    snapshot: ProjectionSnapshot,
    row_report: InvariantReport,
    *,
    projection_name: str = "legacy_history",
) -> ProjectionInvariantReport:
    """Compare replay projection state against the independent row report."""
    mismatches: list[str] = []
    state = snapshot.states.get(projection_name)
    if not isinstance(state, dict):
        mismatches.append(f"projection {projection_name!r} missing")
        return ProjectionInvariantReport(ok=False, mismatches=tuple(mismatches))
    if int(state["event_count"]) != row_report.event_count:
        mismatches.append(
            f"event_count {state['event_count']} != invariant {row_report.event_count}"
        )
    state_counts = dict(state["type_counts"])
    if state_counts != dict(row_report.event_type_counts):
        mismatches.append("type_counts do not match independent event_type_counts")
    if row_report.event_count:
        if state["first_sequence"] != row_report.min_sequence:
            mismatches.append(
                f"first_sequence {state['first_sequence']} != invariant {row_report.min_sequence}"
            )
        if state["last_sequence"] != row_report.max_sequence:
            mismatches.append(
                f"last_sequence {state['last_sequence']} != invariant {row_report.max_sequence}"
            )
    return ProjectionInvariantReport(ok=not mismatches, mismatches=tuple(mismatches))
