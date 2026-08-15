"""Machine-readable worker result ingestion with fail-closed validation."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis_foundry.orchestration.paths import sha256_file
from jarvis_foundry.orchestration.schemas import (
    WorkerResult,
    WorkerResultError,
    validate_worker_result,
)


@dataclass(frozen=True)
class IngestOutcome:
    valid: bool
    result: WorkerResult | None
    reasons: tuple[str, ...]
    result_hash: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "valid": self.valid,
            "reasons": list(self.reasons),
            "result_hash": self.result_hash,
            "result_status": self.result.status if self.result is not None else None,
        }


def ingest_result(path: Path) -> IngestOutcome:
    """Parse and validate a worker result file; malformed results are rejected."""
    if not path.is_file():
        return IngestOutcome(
            valid=False,
            result=None,
            reasons=("worker result file missing",),
            result_hash="",
        )
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        return IngestOutcome(
            valid=False,
            result=None,
            reasons=(f"worker result is not valid JSON: {exc}",),
            result_hash=sha256_file(path),
        )
    if not isinstance(raw, dict):
        return IngestOutcome(
            valid=False,
            result=None,
            reasons=("worker result must be a JSON object",),
            result_hash=sha256_file(path),
        )
    try:
        result = validate_worker_result(raw)
    except WorkerResultError as exc:
        return IngestOutcome(
            valid=False,
            result=None,
            reasons=tuple(exc.reasons),
            result_hash=sha256_file(path),
        )
    return IngestOutcome(
        valid=True,
        result=result,
        reasons=(),
        result_hash=sha256_file(path),
    )
