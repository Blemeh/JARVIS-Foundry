"""Owner-context compatibility bridge for V11 scoring.

The V11 repository seed remains byte-compatible. V13 durable owner-context
records are projected from the ledger and merged into the same small advisory
shape for existing scoring callers. Owner directives, STOP, vetoes, and the
ledger remain authoritative.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from ..database import FoundryDatabase
from ..events import canonical_json
from ..hybrid_memory.blocks import CoreMemoryRegistry


_HASH = re.compile(r"^[0-9a-f]{64}$")
_BLOCKS = {
    "goals": "strategic_goals",
    "projects": "current_projects",
    "standing_rejections": "active_constraints",
}


@dataclass(frozen=True, slots=True)
class OwnerContextItem:
    """One explicit repository-derived scoring input."""

    text: str
    source_ref: str
    source_hash: str

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "OwnerContextItem":
        text = str(value.get("text") or "").strip()
        source_ref = str(value.get("source_ref") or "").strip()
        source_hash = str(value.get("source_hash") or "").strip().lower()
        if not text:
            raise ValueError("owner-context text is required")
        if not source_ref:
            raise ValueError("owner-context source_ref is required")
        if not _HASH.fullmatch(source_hash):
            raise ValueError("owner-context source_hash must be sha256 hex")
        return cls(text=text, source_ref=source_ref, source_hash=source_hash)

    def as_dict(self) -> dict[str, str]:
        return {
            "text": self.text,
            "source_ref": self.source_ref,
            "source_hash": self.source_hash,
        }


def _normalized(items: Sequence[OwnerContextItem | Mapping[str, Any]]) -> list[dict[str, str]]:
    rows = [
        item if isinstance(item, OwnerContextItem) else OwnerContextItem.from_mapping(item)
        for item in items
    ]
    # Stable ordering makes a repeated seed byte-identical and reviewable.
    return [row.as_dict() for row in sorted(
        rows, key=lambda row: (row.source_ref, row.source_hash, row.text)
    )]


def seed_owner_context(
    database: FoundryDatabase,
    *,
    goals: Sequence[OwnerContextItem | Mapping[str, Any]] = (),
    projects: Sequence[OwnerContextItem | Mapping[str, Any]] = (),
    standing_rejections: Sequence[OwnerContextItem | Mapping[str, Any]] = (),
    actor: str = "v11-a2-owner-context",
) -> dict[str, list[dict[str, str]]]:
    """Replace the three bounded Tier-C seed blocks with provenanced items.

    This function must be called only with repository-authoritative inputs.
    Retrieved/external content is not accepted as an authority source by this
    API; callers supply an exact repository reference and content hash.
    """

    payload = {
        "goals": _normalized(goals),
        "projects": _normalized(projects),
        "standing_rejections": _normalized(standing_rejections),
    }
    registry = CoreMemoryRegistry(database)
    registry.ensure_defaults(actor=actor)
    for kind, label in _BLOCKS.items():
        value = canonical_json(payload[kind])
        current = registry.get_block(label)
        if current is not None and str(current.get("value") or "") == value:
            continue
        registry.replace(
            label,
            value,
            actor=actor,
            reason="V11 A2 repository-authoritative owner-context seed",
        )
    return payload


def load_owner_context(database: FoundryDatabase) -> dict[str, Any]:
    """Read legacy seeds plus current durable V13 scoring context."""

    registry = CoreMemoryRegistry(database)
    result: dict[str, Any] = {
        "goals": [],
        "projects": [],
        "standing_rejections": [],
        "warnings": [],
        "authority": "ADVISORY_TIER_C_REPOSITORY_SEED",
    }
    for kind, label in _BLOCKS.items():
        block = registry.get_block(label)
        if block is None or not str(block.get("value") or "").strip():
            continue
        try:
            raw = json.loads(str(block["value"]))
            if not isinstance(raw, list):
                raise ValueError("seed block must contain a list")
            result[kind] = [OwnerContextItem.from_mapping(row).as_dict() for row in raw]
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            result["warnings"].append(f"{label}:{type(error).__name__}")
    try:
        from ..owner_context import project_owner_context

        projection = project_owner_context(database)
        mappings = {
            "goals": {"GOAL"},
            "projects": {"PROGRAM", "PROJECT", "ACTIVE_OBJECTIVE"},
            "standing_rejections": {"CONSTRAINT", "VETO", "REJECTED_APPROACH"},
        }
        added = 0
        for output_kind, record_kinds in mappings.items():
            existing = {
                (
                    str(item.get("text") or ""),
                    str(item.get("source_ref") or ""),
                    str(item.get("source_hash") or ""),
                )
                for item in result[output_kind]
                if isinstance(item, dict)
            }
            for record in projection.current:
                if record.kind not in record_kinds:
                    continue
                item = {
                    "text": record.text,
                    "source_ref": record.provenance.source_ref,
                    "source_hash": record.provenance.content_hash,
                }
                identity = (
                    item["text"], item["source_ref"], item["source_hash"],
                )
                if identity in existing:
                    continue
                result[output_kind].append(item)
                existing.add(identity)
                added += 1
        result["warnings"].extend(projection.warnings)
        if added:
            result["authority"] = (
                "ADVISORY_OWNER_CONTEXT_V1_WITH_LEGACY_TIER_C"
            )
    except Exception as error:
        # The compatibility read must never let advisory context break the
        # existing V11 judgment path.
        result["warnings"].append(
            f"durable_owner_context:{type(error).__name__}",
        )
    return result


__all__ = ["OwnerContextItem", "load_owner_context", "seed_owner_context"]
