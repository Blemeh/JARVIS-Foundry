"""Evidence-only discovery: candidates are created only from observations."""

from __future__ import annotations

import re
from dataclasses import dataclass

from jarvis_foundry.kernel.canonical import canonical_hash
from jarvis_foundry.kernel.errors import EvidenceError
from jarvis_foundry.kernel.portfolio.errors import PortfolioError
from jarvis_foundry.kernel.portfolio.provenance import canonical_objective
from jarvis_foundry.kernel.portfolio.records import DiscoveredCandidate, Observation
from jarvis_foundry.kernel.portfolio.store import PortfolioStore

CONTROL_TOKEN_PATTERN = re.compile(
    r"^\s*(owner\s+)?(stop|pause|veto|redirect|reprioritize)\s*:.*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DiscoveryResult:
    candidate_id: str | None
    created: bool
    duplicate_of: str | None
    reason: str


def sanitize_objective(text: str) -> str:
    """Strip control-like phrasing but keep the evidence-backed work statement."""
    kept: list[str] = []
    for line in text.splitlines():
        if CONTROL_TOKEN_PATTERN.match(line):
            remainder = line.split(":", 1)[1] if ":" in line else ""
            if remainder.strip():
                kept.append(remainder.strip())
            continue
        kept.append(line)
    return " ".join(kept).strip()


class DiscoveryService:
    def __init__(self, store: PortfolioStore) -> None:
        self.store = store

    def discover(
        self, observation: Observation, *, idempotency_key: str | None = None
    ) -> DiscoveryResult:
        if not observation.evidence_refs:
            raise EvidenceError(
                f"observation {observation.observation_id} has no recorded evidence"
            )
        if not observation.meaningful_change:
            return DiscoveryResult(
                candidate_id=None,
                created=False,
                duplicate_of=None,
                reason="no meaningful change in recorded observation; refusing busy work",
            )
        objective = sanitize_objective(observation.observed_value)
        if not objective:
            return DiscoveryResult(
                candidate_id=None,
                created=False,
                duplicate_of=None,
                reason="observation contains no evidence-backed work statement",
            )
        candidate_id = f"cand-{canonical_hash([canonical_objective(objective), observation.source_id, observation.change_hash or observation.content_hash])[:12]}"
        existing = self.store.latest("discovered_candidate", candidate_id)
        if existing is not None and isinstance(existing, DiscoveredCandidate):
            return DiscoveryResult(
                candidate_id=existing.candidate_id,
                created=False,
                duplicate_of=existing.candidate_id,
                reason="duplicate candidate suppressed by evidence fingerprint",
            )
        candidate = DiscoveredCandidate(
            candidate_id=candidate_id,
            observation_id=observation.observation_id,
            project_id=observation.project_id,
            objective=objective,
            evidence_refs=observation.evidence_refs,
            status="CANDIDATE",
            duplicate_of=None,
            created_at=observation.captured_at,
            authority="NONE",
        )
        self.store.append_discovery(
            candidate, idempotency_key=idempotency_key or f"discovered_candidate:{candidate_id}"
        )
        return DiscoveryResult(
            candidate_id=candidate_id,
            created=True,
            duplicate_of=None,
            reason="candidate created from recorded evidence observation",
        )

    def reject(self, candidate_id: str, reason: str) -> DiscoveredCandidate:
        existing = self.store.latest("discovered_candidate", candidate_id)
        if existing is None or not isinstance(existing, DiscoveredCandidate):
            raise PortfolioError(f"unknown discovery candidate: {candidate_id}")
        if existing.status == "REJECTED":
            return existing
        rejected = DiscoveredCandidate(
            candidate_id=existing.candidate_id,
            observation_id=existing.observation_id,
            project_id=existing.project_id,
            objective=existing.objective,
            evidence_refs=existing.evidence_refs,
            status="REJECTED",
            duplicate_of=existing.duplicate_of,
            created_at=existing.created_at,
            authority=existing.authority,
            rejection_reason=reason,
        )
        self.store.append_discovery(
            rejected, idempotency_key=f"discovered_candidate:{candidate_id}:rejected"
        )
        return rejected
