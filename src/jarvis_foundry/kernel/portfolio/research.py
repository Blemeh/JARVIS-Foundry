"""Traceable research with provenance, contradiction, and injection isolation."""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from dataclasses import replace
from pathlib import Path

from jarvis_foundry.kernel.canonical import canonical_hash
from jarvis_foundry.kernel.clock import Clock, SystemClock
from jarvis_foundry.kernel.errors import EvidenceError
from jarvis_foundry.kernel.portfolio.budget import Budget
from jarvis_foundry.kernel.portfolio.errors import IntegrityError, SourceUnavailableError
from jarvis_foundry.kernel.portfolio.provenance import (
    canonical_objective,
    excerpt,
    is_fresh,
    sha256_bytes,
)
from jarvis_foundry.kernel.portfolio.records import (
    ResearchClaim,
    ResearchSource,
    ResearchSynthesis,
)
from jarvis_foundry.kernel.portfolio.store import PortfolioStore

CONTROL_TOKEN_PATTERN = re.compile(r"\b(STOP|PAUSE|VETO|REDIRECT|REPRIORITIZE)\s*:")
MAX_SOURCE_BYTES = 262_144


def _parse_uncertainty(text: str) -> float:
    match = re.search(r"uncertain\s+([0-9]*\.?[0-9]+)", text)
    if not match:
        return 0.0
    try:
        return max(0.0, min(1.0, float(match.group(1))))
    except ValueError:
        return 0.0


class ResearchService:
    def __init__(
        self,
        store: PortfolioStore,
        *,
        snapshot_root: str | Path,
        clock: Clock | None = None,
    ) -> None:
        self.store = store
        self.snapshot_root = Path(snapshot_root)
        self.snapshot_root.mkdir(parents=True, exist_ok=True)
        self._clock = clock if clock is not None else SystemClock()

    def run(
        self,
        *,
        question: str,
        project_id: str,
        value_statement: str,
        sources: list[ResearchSource],
        budget: Budget,
    ) -> ResearchSynthesis:
        if not question or not project_id or not value_statement:
            raise EvidenceError("research question must link to a project and value statement")
        for source in sources:
            if not source.source_id or not source.captured_at or not source.locator:
                raise EvidenceError(
                    f"source {source.source_id} lacks identity, timestamp, or locator"
                )
        captured: list[ResearchSource] = []
        claims: list[ResearchClaim] = []
        unknowns: list[str] = []
        injection_attempts = 0
        now = self._clock.now_utc()
        for source in sources:
            budget.consume(calls=1)
            try:
                content = self.capture_source(source)
                updated = source
            except SourceUnavailableError:
                updated = replace(source, status="BLOCKED")
                content = b""
            except IntegrityError:
                updated = replace(source, status="TAMPERED")
                content = b""
            captured.append(updated)
            if updated.status != "FRESH":
                continue
            text = content.decode("utf-8", errors="replace")
            fresh = is_fresh(updated.captured_at, now, updated.freshness_seconds)
            for line in text.splitlines():
                injection_attempts += len(CONTROL_TOKEN_PATTERN.findall(line))
            extracted, unknowns_for_source, injected = self._extract_claims(
                text, updated.source_id, fresh
            )
            claims.extend(extracted)
            unknowns.extend(unknowns_for_source)
            injection_attempts += injected

        identity = [
            {
                "source_id": source.source_id,
                "locator": source.locator,
                "captured_at": source.captured_at,
                "content_hash": source.content_hash,
                "trust_class": source.trust_class,
                "freshness_seconds": source.freshness_seconds,
                "status": source.status,
            }
            for source in captured
        ]
        research_key = f"research:{canonical_hash([question, project_id, value_statement, identity, budget.budget_key])[:16]}"
        research_id = f"research-{canonical_hash(research_key)[:16]}"
        existing = self.store.latest("research_synthesis", research_id)
        if existing is not None and isinstance(existing, ResearchSynthesis):
            return existing

        contradictions = self._contradictions(claims)
        authoritative_stale = any(
            source.trust_class in {"official", "owner-controlled"}
            and (
                source.status != "FRESH"
                or not is_fresh(source.captured_at, now, source.freshness_seconds)
            )
            for source in captured
        )
        if authoritative_stale:
            status = "BLOCKED"
            decision_input = "BLOCKED: refresh required before decision input"
        elif contradictions:
            status = "READY"
            decision_input = (
                f"hold until contradiction resolved ({len(contradictions)} claims preserved)"
            )
        elif not claims:
            status = "WAITING"
            decision_input = "WAITING: no evidence-backed statement captured"
        else:
            status = "READY"
            decision_input = "proceed with bounded evidence-backed step"
        synthesis = ResearchSynthesis(
            research_id=research_id,
            question=question,
            project_id=project_id,
            value_statement=value_statement,
            sources=tuple(captured),
            claims=tuple(claims),
            contradictions=tuple(contradictions),
            unknowns=tuple(unknowns),
            decision_input=decision_input,
            status=status,
            authority="UNTRUSTED_DATA",
            injection_attempts=injection_attempts,
            snapshot_dir=str(self.snapshot_root),
            created_at=now,
            idempotency_key=research_key,
        )
        self.store.append_research(synthesis, idempotency_key=research_key)
        return synthesis

    def capture_source(self, source: ResearchSource) -> bytes:
        content: bytes
        if source.locator.startswith(("http://", "https://")):
            try:
                request = urllib.request.Request(
                    source.locator, headers={"User-Agent": "jarvis-r7-research/0.1"}
                )
                with urllib.request.urlopen(  # nosec B310 - scheme pre-checked to http/https
                    request, timeout=8
                ) as response:
                    content = response.read(MAX_SOURCE_BYTES + 1)
            except (urllib.error.URLError, urllib.error.HTTPError, OSError) as exc:
                raise SourceUnavailableError(
                    f"bounded fetch failed for {source.source_id}"
                ) from exc
        else:
            path = Path(source.locator)
            if not path.is_file():
                raise SourceUnavailableError(f"source file missing: {source.locator}")
            content = path.read_bytes()
            if len(content) > MAX_SOURCE_BYTES:
                content = content[:MAX_SOURCE_BYTES]
        digest = sha256_bytes(content)
        if source.content_hash and source.content_hash != digest:
            raise IntegrityError(
                f"source {source.source_id} content hash changed: expected {source.content_hash}, got {digest}"
            )
        snapshot = self.snapshot_root / digest
        if not snapshot.is_file():
            snapshot.write_bytes(content)
        text = content.decode("utf-8", errors="replace")
        object.__setattr__(source, "content_hash", digest)
        object.__setattr__(source, "content_excerpt", excerpt(text, 240))
        object.__setattr__(source, "status", "FRESH")
        return content

    def _extract_claims(
        self, text: str, source_id: str, freshness_ok: bool
    ) -> tuple[list[ResearchClaim], list[str], int]:
        claims: list[ResearchClaim] = []
        unknowns: list[str] = []
        injected = 0
        for line in text.splitlines():
            injected += len(CONTROL_TOKEN_PATTERN.findall(line))
            if line.startswith(("Unknown:", "UNKNOWN:")):
                unknowns.append(line.split(":", 1)[1].strip())
                continue
            polarity = "NEUTRAL"
            statement = line.strip()
            if statement.startswith("[SUPPORTS]"):
                polarity = "SUPPORTS"
                statement = statement[len("[SUPPORTS]") :].strip()
            elif statement.startswith("[CONTRADICTS]"):
                polarity = "CONTRADICTS"
                statement = statement[len("[CONTRADICTS]") :].strip()
            elif statement.lower().startswith("claim:"):
                statement = statement.split(":", 1)[1].strip()
            else:
                continue
            if not statement:
                continue
            claim_id = f"claim-{canonical_hash([source_id, canonical_objective(statement), polarity])[:12]}"
            claims.append(
                ResearchClaim(
                    claim_id=claim_id,
                    source_id=source_id,
                    statement=statement,
                    polarity=polarity,
                    freshness_ok=freshness_ok,
                    citation=f"{source_id}#{claim_id}",
                    uncertainty=_parse_uncertainty(statement),
                )
            )
        return claims, unknowns, injected

    def _contradictions(self, claims: list[ResearchClaim]) -> list[ResearchClaim]:
        by_statement: dict[str, dict[str, list[ResearchClaim]]] = {}
        for claim in claims:
            key = canonical_objective(claim.statement)
            by_statement.setdefault(key, {}).setdefault(claim.polarity, []).append(claim)
        contradictions: list[ResearchClaim] = []
        seen: set[str] = set()
        for groups in by_statement.values():
            if "SUPPORTS" in groups and "CONTRADICTS" in groups:
                for claim in [*groups["SUPPORTS"], *groups["CONTRADICTS"]]:
                    if claim.claim_id not in seen:
                        contradictions.append(claim)
                        seen.add(claim.claim_id)
        return contradictions


__all__ = ["ResearchService", "ResearchSynthesis"]
