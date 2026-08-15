"""Structured, append-only findings registry for R8 missions."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis_foundry.kernel.canonical import canonical_hash


@dataclass(frozen=True)
class Finding:
    finding_id: str
    severity: str
    title: str
    detail: str
    evidence: tuple[str, ...]
    reproduced_command: str
    mission_id: str
    status: str = "OPEN"
    created_at: str = ""
    repair_mission_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "severity": self.severity,
            "title": self.title,
            "detail": self.detail,
            "evidence": list(self.evidence),
            "reproduced_command": self.reproduced_command,
            "mission_id": self.mission_id,
            "status": self.status,
            "created_at": self.created_at,
            "repair_mission_id": self.repair_mission_id,
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> Finding:
        return Finding(
            finding_id=str(payload["finding_id"]),
            severity=str(payload["severity"]),
            title=str(payload["title"]),
            detail=str(payload["detail"]),
            evidence=tuple(str(item) for item in payload.get("evidence", [])),
            reproduced_command=str(payload.get("reproduced_command", "")),
            mission_id=str(payload.get("mission_id", "")),
            status=str(payload.get("status", "OPEN")),
            created_at=str(payload.get("created_at", "")),
            repair_mission_id=payload.get("repair_mission_id"),
        )


class FindingsRegistry:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def record(self, finding: Finding) -> Finding:
        line = json.dumps(finding.to_dict(), sort_keys=True, separators=(",", ":"))
        digest = canonical_hash(finding.to_dict())
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(f"{line}|{digest}\n")
        return finding

    def all(self) -> list[Finding]:
        if not self.path.is_file():
            return []
        findings: list[Finding] = []
        for line_no, raw in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
            if not raw.strip():
                continue
            marker = raw.rfind("|")
            if marker < 0:
                raise ValueError(f"findings line {line_no} has no hash marker")
            body, digest = raw[:marker], raw[marker + 1 :]
            payload = json.loads(body)
            if canonical_hash(payload) != digest:
                raise ValueError(f"findings line {line_no} hash mismatch")
            findings.append(Finding.from_dict(payload))
        return findings

    def open_findings(self) -> list[Finding]:
        return [finding for finding in self.all() if finding.status == "OPEN"]

    def mark_repaired(self, finding_id: str, repair_mission_id: str) -> Finding:
        updated: Finding | None = None
        rows: list[str] = []
        for raw in self.path.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            marker = raw.rfind("|")
            body = raw[:marker]
            payload = json.loads(body)
            if payload["finding_id"] == finding_id:
                payload["status"] = "REPAIRED"
                payload["repair_mission_id"] = repair_mission_id
                updated = Finding.from_dict(payload)
                digest = canonical_hash(payload)
                rows.append(
                    f"{json.dumps(payload, sort_keys=True, separators=(',', ':'))}|{digest}"
                )
            else:
                rows.append(raw)
        if updated is None:
            raise KeyError(f"no finding with id {finding_id}")
        self.path.write_text("\n".join(rows) + "\n", encoding="utf-8")
        return updated
