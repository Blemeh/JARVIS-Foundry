"""V8 hybrid memory — governed curation pipeline (Letta M7 adapted, safer).

experience → candidate → provenance check → duplication check →
contradiction check (Tier C AND production Tier B claims) → value check →
defense scan → proposed update → validation stamp → commit.

High-risk changes (contradicting an accepted Tier B claim) require
independent verification. Blocked suspicious promotions emit
``memory.suspicious_blocked`` (audit) and never reach a recallable state.
"""
from __future__ import annotations

import json
from typing import Any

from ..database import FoundryDatabase
from ..events import Event
from ..knowledge import canonicalize_statement, contradicts as kb_contradicts
from .defense import DefenseScanner
from .lifecycle import TierCMemoryStore, InvalidTransition
from .recall import RecallEngine


class CurationPipeline:
    """Deterministic validation gates for Tier C promotion."""

    def __init__(self, db: FoundryDatabase, *, scanner: DefenseScanner | None = None) -> None:
        self.db = db
        self.memories = TierCMemoryStore(db)
        self.recall = RecallEngine(db)
        self.scanner = scanner or DefenseScanner()

    # ---- validation ------------------------------------------------------
    def validate(self, memory_id: str) -> dict[str, Any]:
        memory = self.memories.get(memory_id)
        if memory is None:
            raise KeyError(memory_id)
        findings: list[str] = []
        content = memory["content"] or ""
        # 1) provenance: observations need a source (traceability contract §10)
        if not memory["source_ids"] and not memory["evidence_ids"]:
            findings.append("missing_provenance")
        # 2) duplication: lexically near-identical accepted Tier C
        #    (threshold >= 0.4 on token overlap; single shared tokens are
        #    NOT duplicates)
        similar = self.recall.recall(content, limit=10)
        for hit in similar:
            if hit["memory_id"] != memory_id \
                    and _overlap(content, hit["content"]) >= 0.4:
                findings.append(f"duplicate_of:{hit['memory_id']}")
        # 3) contradiction vs production Tier B claims (evidence wins)
        contradictions: list[str] = []
        with self.db.connect() as conn:
            claims = conn.execute(
                "SELECT claim_id, statement FROM knowledge_claims "
                "WHERE status='ACCEPTED' ORDER BY rowid").fetchall()
            for claim in claims:
                if kb_contradicts(content, claim["statement"]):
                    contradictions.append(f"claim:{claim['claim_id']}")
        # 4) contradiction vs other Tier C
        for other in self.memories.list():
            if other["memory_id"] == memory_id:
                continue
            if other["state"] not in ("CORE", "EXTERNAL", "PROCEDURAL", "ACCEPTED_EXPERIENCE"):
                continue
            if kb_contradicts(content, other["content"]):
                contradictions.append(f"memory:{other['memory_id']}")
        # 5) defense scan (audit repair H1/C9: gate is deterministic)
        threats = self.scanner.scan(content)
        if threats:
            findings.append(f"suspicious_content:{','.join(sorted(set(threats)))}")
        return {
            "memory_id": memory_id,
            "passed": not findings,
            "findings": findings,
            "contradictions": contradictions,
            "threats": threats,
        }

    # ---- governed acceptance ---------------------------------------------
    def propose_candidate(self, memory_id: str, *, actor: str) -> dict[str, Any]:
        return self.memories.propose(memory_id, actor=actor, reason="auto-propose for validation")

    def validate_and_accept(self, memory_id: str, *, placement: str = "CORE",
                            actor: str = "curation", require_independent: bool = True,
                            independent_ok: bool = False) -> dict[str, Any]:
        """Validate, stamp, and accept through the governed path."""
        verdict = self.validate(memory_id)
        memory = self.memories.get(memory_id)
        if memory["state"] == "OBSERVED":
            self.memories.propose(memory_id, actor=actor, reason="auto-propose")
        if memory["state"] != "VALIDATING":
            self.memories.start_validation(memory_id, actor=actor, reason="validation")
        if not verdict["passed"]:
            if verdict["threats"]:
                self._audit("memory.suspicious_blocked",
                            {"memory_id": memory_id,
                             "threats": sorted(set(verdict["threats"])), "by": actor})
            raise ValueError(f"validation failed: {verdict['findings']}")
        if verdict["contradictions"]:
            if require_independent and not independent_ok:
                raise ValueError(
                    f"high-risk: contradicts accepted claim(s) {verdict['contradictions']}; "
                    "independent verification required")
        # validation stamp (event-derived; required by the lifecycle gate)
        event = Event.create(
            "memory.validation_passed",
            {"memory_id": memory_id, "verdict": verdict, "validated_by": actor},
            source_type="memory", source_id=actor,
            idempotency_key=f"memory.validation_passed:{memory_id}:{actor}")
        self.db.append(event)
        return self.memories.accept(memory_id, placement=placement, actor=actor,
                                    reason="validated by curation")

    # ---- consolidation ---------------------------------------------------
    def consolidate_duplicates(self, memory_ids: list[str], *, actor: str,
                               reason: str = "consolidate duplicates") -> list[str]:
        """Merge lexically near-identical accepted memories into the first
        (highest confidence) record; originals are superseded, never deleted.

        Deterministic boundary (honest): semantic dedup beyond lexical
        overlap is NOT claimed — an embedding hook would be needed.
        """
        accepted = []
        for mid in memory_ids:
            m = self.memories.get(mid)
            if m and m["state"] in ("CORE", "EXTERNAL", "PROCEDURAL", "ACCEPTED_EXPERIENCE"):
                accepted.append(m)
        if len(accepted) < 2:
            return []
        clusters: list[list[dict[str, Any]]] = []
        for mem in accepted:
            placed = False
            for cluster in clusters:
                if not kb_contradicts(cluster[0]["content"], mem["content"]) \
                        and _lexical_overlap(cluster[0]["content"], mem["content"]) >= 0.4:
                    cluster.append(mem)
                    placed = True
                    break
            if not placed:
                clusters.append([mem])
        merged: list[str] = []
        for cluster in clusters:
            if len(cluster) < 2:
                continue
            keeper = max(cluster, key=lambda m: m["confidence"])
            for other in cluster:
                if other["memory_id"] == keeper["memory_id"]:
                    continue
                self.memories.supersede(other["memory_id"], by_memory_id=keeper["memory_id"],
                                       actor=actor, reason=reason)
            merged.append(keeper["memory_id"])
        return merged

    # ---- helpers ---------------------------------------------------------
    def _audit(self, event_type: str, payload: dict[str, Any]) -> None:
        event = Event.create(
            event_type, payload, source_type="memory", source_id="curation",
            idempotency_key=f"{event_type}:{payload.get('memory_id', '')}:{json.dumps(payload, sort_keys=True)}")
        self.db.append(event)


def _lexical_overlap(first: str, second: str) -> float:
    from .recall import _tokens
    a, b = set(_tokens(first)), set(_tokens(second))
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, min(len(a), len(b)))


_overlap = _lexical_overlap
