"""Observed-outcome ranking and explicit procedure version lineage."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Iterable


@dataclass(frozen=True, slots=True)
class QualityObservation:
    procedure_id: str
    version: str
    run_id: str
    success: bool
    repaired: bool
    duration_ms: int
    cost_usd: float
    verifier_outcome: str
    verified_at: str

    def __post_init__(self) -> None:
        for name in ("procedure_id", "version", "run_id", "verifier_outcome", "verified_at"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty")
        if self.verifier_outcome not in {"PASS", "FAIL", "BLOCKED", "NOT_TESTED"}:
            raise ValueError("invalid verifier outcome")
        if isinstance(self.duration_ms, bool) or not isinstance(self.duration_ms, int) or self.duration_ms < 0:
            raise ValueError("duration_ms must be non-negative")
        if isinstance(self.cost_usd, bool) or not isinstance(self.cost_usd, (int, float)) or self.cost_usd < 0:
            raise ValueError("cost_usd must be non-negative")
        datetime.fromisoformat(self.verified_at.replace("Z", "+00:00"))


@dataclass(frozen=True, slots=True)
class QualityRank:
    procedure_id: str
    version: str
    observations: int
    success_rate: float
    repair_rate: float
    verifier_pass_rate: float
    mean_duration_ms: float
    mean_cost_usd: float
    score: float

    def as_dict(self) -> dict[str, object]:
        return {
            "procedure_id": self.procedure_id,
            "version": self.version,
            "observations": self.observations,
            "success_rate": round(self.success_rate, 6),
            "repair_rate": round(self.repair_rate, 6),
            "verifier_pass_rate": round(self.verifier_pass_rate, 6),
            "mean_duration_ms": round(self.mean_duration_ms, 3),
            "mean_cost_usd": round(self.mean_cost_usd, 6),
            "score": round(self.score, 6),
        }


def rank_observations(observations: Iterable[QualityObservation]) -> tuple[QualityRank, ...]:
    grouped: dict[tuple[str, str], list[QualityObservation]] = {}
    for item in observations:
        grouped.setdefault((item.procedure_id, item.version), []).append(item)
    ranks: list[QualityRank] = []
    for (procedure_id, version), items in grouped.items():
        count = len(items)
        success_rate = sum(item.success for item in items) / count
        repair_rate = sum(item.repaired for item in items) / count
        verifier_pass_rate = sum(item.verifier_outcome == "PASS" for item in items) / count
        mean_duration = sum(item.duration_ms for item in items) / count
        mean_cost = sum(item.cost_usd for item in items) / count
        score = (
            success_rate * 0.45
            + verifier_pass_rate * 0.35
            + repair_rate * 0.10
            + (1.0 / (1.0 + mean_duration / 1000.0)) * 0.07
            + (1.0 / (1.0 + mean_cost)) * 0.03
        )
        ranks.append(QualityRank(
            procedure_id=procedure_id, version=version, observations=count,
            success_rate=success_rate, repair_rate=repair_rate,
            verifier_pass_rate=verifier_pass_rate, mean_duration_ms=mean_duration,
            mean_cost_usd=mean_cost, score=score,
        ))
    return tuple(sorted(ranks, key=lambda item: (-item.score, item.procedure_id, item.version)))


@dataclass(frozen=True, slots=True)
class VersionRecord:
    procedure_id: str
    version: str
    content_hash: str
    status: str
    supersedes: str | None = None


class VersionRegistry:
    """Keep in-flight versions traceable while selection follows promotion."""

    def __init__(self) -> None:
        self._history: list[VersionRecord] = []

    @property
    def history(self) -> tuple[VersionRecord, ...]:
        return tuple(self._history)

    def add(self, record: VersionRecord) -> None:
        if any(item.procedure_id == record.procedure_id and item.version == record.version for item in self._history):
            raise ValueError("procedure version already recorded")
        if record.status not in {"CANDIDATE", "PROVEN_ONCE", "TRUSTED", "SUPERSEDED", "REJECTED"}:
            raise ValueError("invalid version status")
        self._history.append(record)

    def supersede(self, procedure_id: str, old_version: str, new_version: str) -> None:
        old = self._find(procedure_id, old_version)
        new = self._find(procedure_id, new_version)
        if old.status == "SUPERSEDED":
            return
        if new.supersedes != old_version:
            raise ValueError("new version must explicitly supersede old version")
        self._replace(old, VersionRecord(old.procedure_id, old.version, old.content_hash, "SUPERSEDED", old.supersedes))

    def select(self, procedure_id: str) -> VersionRecord:
        eligible = [item for item in self._history if item.procedure_id == procedure_id and item.status in {"PROVEN_ONCE", "TRUSTED"}]
        if not eligible:
            raise KeyError(procedure_id)
        return max(eligible, key=lambda item: tuple(int(part) for part in item.version.lstrip("v").split(".")))

    def rollback(self, procedure_id: str, version: str) -> VersionRecord:
        chosen = self._find(procedure_id, version)
        if chosen.status not in {"PROVEN_ONCE", "TRUSTED", "SUPERSEDED"}:
            raise ValueError("rollback target has no proof")
        for index, item in enumerate(self._history):
            if item.procedure_id == procedure_id and item.status in {"TRUSTED", "PROVEN_ONCE"} and item.version != version:
                self._history[index] = VersionRecord(item.procedure_id, item.version, item.content_hash, "SUPERSEDED", item.supersedes)
        replacement = VersionRecord(chosen.procedure_id, chosen.version, chosen.content_hash, "TRUSTED", chosen.supersedes)
        self._replace(chosen, replacement)
        return replacement

    def _find(self, procedure_id: str, version: str) -> VersionRecord:
        for item in self._history:
            if item.procedure_id == procedure_id and item.version == version:
                return item
        raise KeyError((procedure_id, version))

    def _replace(self, current: VersionRecord, replacement: VersionRecord) -> None:
        index = self._history.index(current)
        self._history[index] = replacement


__all__ = ["QualityObservation", "QualityRank", "VersionRecord", "VersionRegistry", "rank_observations"]
