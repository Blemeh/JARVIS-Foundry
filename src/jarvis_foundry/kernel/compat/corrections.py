"""Additive-only correction ledger for historical replay."""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis_foundry.kernel.canonical import canonical_hash
from jarvis_foundry.kernel.compat.errors import CompatReadError
from jarvis_foundry.kernel.events import LedgerEvent
from jarvis_foundry.kernel.ledger import AppendResult, Ledger

CORRECTION_EVENT_TYPE = "compat.correction.recorded"
CORRECTION_LEDGER_SCHEMA = "jarvis-r5-correction-ledger-v1"


@dataclass(frozen=True)
class Correction:
    correction_id: str
    target_event_id: str
    reason: str
    evidence_ref: str
    evidence_sha256: str
    corrected_payload: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "correction_id": self.correction_id,
            "target_event_id": self.target_event_id,
            "reason": self.reason,
            "evidence_ref": self.evidence_ref,
            "evidence_sha256": self.evidence_sha256,
            "corrected_payload": dict(self.corrected_payload),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Correction:
        return Correction(
            correction_id=str(payload["correction_id"]),
            target_event_id=str(payload["target_event_id"]),
            reason=str(payload["reason"]),
            evidence_ref=str(payload["evidence_ref"]),
            evidence_sha256=str(payload["evidence_sha256"]),
            corrected_payload=dict(payload.get("corrected_payload", {})),
        )


class CorrectionLedger:
    """Durable JSON ledger documenting every additive correction."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def load(self) -> list[Correction]:
        if not self.path.is_file():
            return []
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        if payload.get("schema") != CORRECTION_LEDGER_SCHEMA:
            raise CompatReadError(f"unsupported correction ledger schema: {payload.get('schema')}")
        return [Correction.from_dict(item) for item in payload.get("corrections", [])]

    def save(self, corrections: Iterable[Correction]) -> None:
        ordered = sorted(corrections, key=lambda item: item.correction_id)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "schema": CORRECTION_LEDGER_SCHEMA,
                    "corrections": [item.to_dict() for item in ordered],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    def append(self, correction: Correction) -> None:
        corrections = self.load()
        if any(item.correction_id == correction.correction_id for item in corrections):
            raise CompatReadError(f"correction id already recorded: {correction.correction_id}")
        self.save([*corrections, correction])

    def apply_to_ledger(
        self,
        ledger: Ledger,
        corrections: Iterable[Correction],
        *,
        recorded_at: str = "1970-01-01T00:00:00Z",
    ) -> list[AppendResult]:
        """Append correction events only; legacy rows are never modified."""
        ordered = sorted(corrections, key=lambda item: item.correction_id)
        results: list[AppendResult] = []
        for correction in ordered:
            payload: dict[str, Any] = {
                "correction_id": correction.correction_id,
                "target_event_id": correction.target_event_id,
                "reason": correction.reason,
                "evidence_ref": correction.evidence_ref,
                "evidence_sha256": correction.evidence_sha256,
                "corrected_payload": dict(correction.corrected_payload),
            }
            event = LedgerEvent.create(
                event_type=CORRECTION_EVENT_TYPE,
                payload=payload,
                source_type="r5-corrections",
                source_id="compat.correction-ledger",
                idempotency_key=f"compat.correction:{correction.correction_id}",
                event_id=f"correction-{correction.correction_id}",
                occurred_at=recorded_at,
                recorded_at=recorded_at,
            )
            results.append(ledger.append(event))
        return results


def correction_event_payload_hash(correction: Correction, recorded_at: str) -> str:
    payload: dict[str, Any] = {
        "correction_id": correction.correction_id,
        "target_event_id": correction.target_event_id,
        "reason": correction.reason,
        "evidence_ref": correction.evidence_ref,
        "evidence_sha256": correction.evidence_sha256,
        "corrected_payload": dict(correction.corrected_payload),
    }
    return canonical_hash(payload)
