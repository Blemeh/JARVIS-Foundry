"""V8 hybrid memory — bounded role context compiler.

Assembles role context from STRUCTURED state (never a dump): mandatory
governance → owner directives → mission contract → evidence → Tier B
knowledge → Tier C (core blocks, recall, procedures, labeled builder
claims). Tier C is always labeled ADVISORY and is dropped first under
budget pressure; governance/directives/evidence are never starved.
Verifier role receives NO Tier C recall (only explicitly labeled builder
claims) — verification independence is structural.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..clock import isoformat
from ..database import FoundryDatabase
from ..knowledge_retrieval import build_context_manifest
from ..supervisor.estop import EmergencyStop
from .blocks import CoreMemoryRegistry
from .lifecycle import TierCMemoryStore
from .procedures import ProcedureRegistry
from .recall import RecallEngine

# Priority order; Tier C sections may be dropped under budget pressure.
SECTION_ORDER = (
    "mandatory_governance", "owner_directives", "mission_contract",
    "evidence", "tier_b_knowledge", "core_memory", "recalled_experience",
    "procedures", "builder_claims",
)

# Sections that must NEVER be dropped (Tier A authority surface).
MANDATORY_SECTIONS = ("mandatory_governance", "owner_directives", "mission_contract", "evidence")


class RoleContextPackage:
    """Immutable compiled package with budget accounting."""

    def __init__(self, sections: dict[str, str], used_chars: int, budget_chars: int,
                 dropped: list[str], notes: list[str]) -> None:
        self.sections = sections
        self.used_chars = used_chars
        self.budget_chars = budget_chars
        self.dropped = dropped
        self.notes = notes

    def section(self, name: str) -> str | None:
        return self.sections.get(name)

    def as_dict(self) -> dict[str, Any]:
        return {"sections": self.sections, "used_chars": self.used_chars,
                "budget_chars": self.budget_chars, "dropped": self.dropped,
                "notes": self.notes}


class ContextCompiler:
    """Production role context compiler (Tier A/B/C hierarchy enforced)."""

    def __init__(self, db: FoundryDatabase, *, stop_path: str | Path | None = None,
                 core_limit: int = 2000) -> None:
        self.db = db
        self.stop_path = Path(stop_path) if stop_path else None
        self.memories = TierCMemoryStore(db)
        self.recall = RecallEngine(db)
        self.blocks = CoreMemoryRegistry(db, default_limit=core_limit)
        self.procedures = ProcedureRegistry(db)

    # ---- Tier A sections -------------------------------------------------
    def _stop_active(self) -> dict[str, Any]:
        stop = EmergencyStop(self.stop_path) if self.stop_path else None
        sentinel = stop.active() if stop else False
        with self.db.connect() as conn:
            active = conn.execute(
                "SELECT directive_id, command_type, exact_text FROM directives "
                "WHERE active = 1 AND command_type IN ('STOP_EVERYTHING', 'PAUSE') "
                "ORDER BY rowid").fetchall()
        directives = [dict(r) for r in active]
        return {"sentinel_active": sentinel, "directives": directives,
                "active": sentinel or bool(directives)}

    def _governance_section(self) -> str:
        stop = self._stop_active()
        lines = []
        if stop["active"]:
            lines.append("EMERGENCY STOP ACTIVE — no autonomous action may proceed.")
            for d in stop["directives"]:
                lines.append(f"  directive {d['directive_id']}: {d['command_type']} — {d['exact_text']}")
            if stop["sentinel_active"]:
                lines.append("  durable STOP sentinel file is present.")
        else:
            lines.append("OPERATING NORMAL — no active STOP/PAUSE directive.")
        lines.append("Tier A outranks Tier B outranks Tier C. Memory is advisory; "
                     "evidence and directives are authoritative.")
        return "\n".join(lines)

    def _directives_section(self) -> str:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT directive_id, command_type, target, exact_text FROM directives "
                "WHERE active = 1 ORDER BY rowid").fetchall()
        if not rows:
            return "(no active directives)"
        return "\n".join(
            f"- {r['directive_id']} [{r['command_type']}] {r['target'] or 'GLOBAL'}: {r['exact_text']}"
            for r in rows)

    def _mission_section(self, mission_id: str) -> str:
        with self.db.connect() as conn:
            row = conn.execute(
                "SELECT mission_id, objective, state, next_action FROM missions "
                "WHERE mission_id=?", (mission_id,)).fetchone()
            verdicts = conn.execute(
                "SELECT subject_id, status FROM verification_verdicts "
                "WHERE mission_id=? AND verdict IS NOT NULL ORDER BY rowid DESC LIMIT 5",
                (mission_id,)).fetchall()
        lines = []
        if row:
            lines.append(f"mission {row['mission_id']}: state={row['state']} objective={row['objective']}")
            if row["next_action"]:
                lines.append(f"next_action: {row['next_action']}")
        else:
            lines.append(f"mission {mission_id}: (no mission record)")
        for v in verdicts:
            lines.append(f"verdict {v['subject_id']}: {v['status']}")
        return "\n".join(lines)

    def _evidence_section(self, mission_id: str) -> str:
        with self.db.connect() as conn:
            rows = conn.execute(
                "SELECT bundle_id, objective FROM evidence_bundles "
                "WHERE mission_id=? ORDER BY rowid DESC LIMIT 20", (mission_id,)).fetchall()
        if not rows:
            return "(no evidence bundles for this mission)"
        return "\n".join(f"- {r['bundle_id']}: {r['objective']}" for r in rows)

    # ---- Tier B section --------------------------------------------------
    def _tier_b_section(self, mission_id: str, objective: str, max_chars: int) -> str:
        with self.db.connect() as conn:
            manifest = build_context_manifest(conn, mission_id, objective,
                                              max_chars=max_chars)
        lines = []
        for claim in manifest.active_claims:
            lines.append(f"- [{claim['confidence']}] {claim['statement']} "
                         f"(fresh_until={claim.get('fresh_until') or 'never'})")
        for claim in manifest.stale_claims:
            lines.append(f"- STALE: {claim['statement']}")
        for conflict in manifest.conflicts:
            lines.append(f"- CONFLICT: {conflict.get('reason', 'contradiction')}")
        if not lines:
            return "(no Tier B knowledge)"
        if manifest.truncated:
            lines.append("(truncated: more claims exist)")
        return "\n".join(lines)[:max_chars]

    # ---- Tier C sections -------------------------------------------------
    def _core_section(self, budget_chars: int) -> tuple[str | None, list[str]]:
        blocks = sorted(
            (b for b in [self.blocks.get_block(label) for label in self.blocks.labels()]
             if b and b["value"]),
            key=lambda b: (b["label"] != "memory_index", -len(b["value"])))
        lines: list[str] = []
        used = 0
        dropped: list[str] = []
        for block in blocks:
            rendered = f"[{block['label']} ({len(block['value'])}/{block['char_limit']} chars)] {block['value']}"
            if used + len(rendered) > budget_chars:
                dropped.append(f"core_memory:{block['label']}")
                continue
            lines.append(rendered)
            used += len(rendered)
        if not lines:
            return None, dropped
        return "\n".join(lines), dropped
    def _recall_section(self, objective: str, limit: int) -> str | None:
        hits = self.recall.recall(objective, limit=limit)
        if not hits:
            return None
        lines = []
        for hit in hits:
            lines.append(
                f"- [{hit['relevance']}] ({hit['memory_type']}/{hit['state']}/"
                f"{hit['freshness']}) {hit['summary']} "
                f"[evidence: {','.join(hit['evidence_refs']) or 'none'}] "
                f"[contradicts: {hit['contradicts'] or 'none'}]")
        return "\n".join(lines)

    def _procedures_section(self, objective: str, limit: int) -> str | None:
        hits = self.procedures.search(objective, limit=limit)
        if not hits:
            return None
        lines = []
        for hit in hits:
            lines.append(f"- {hit['name']} ({hit['procedure_id']}): {hit['description']}")
        return "\n".join(lines)

    # ---- compile ---------------------------------------------------------
    def compile(self, mission_id: str, role: str, objective: str,
                *, budget_chars: int = 6000, evidence: list[dict[str, Any]] | None = None,
                builder_claims: list[dict[str, Any]] | None = None,
                tier_c_enabled: bool = True) -> RoleContextPackage:
        """Compile a bounded, priority-ordered role context package.

        ``role``: executive | builder | verifier | curator.
        ``evidence``: caller-supplied raw artifact refs (locator/kind/hash).
        ``builder_claims``: builder-authored claims rendered ONLY under the
        UNVERIFIED_BUILDER_CLAIM label — never as evidence.
        """
        role = role.lower()
        sections: dict[str, str] = {}
        dropped: list[str] = []
        notes: list[str] = []

        # 1) mandatory Tier A (never dropped, never starved)
        sections["mandatory_governance"] = self._governance_section()
        sections["owner_directives"] = self._directives_section()
        sections["mission_contract"] = self._mission_section(mission_id)

        # 2) evidence (raw artifact refs + bundled evidence)
        ev_lines = self._evidence_section(mission_id)
        for item in evidence or []:
            ev_lines += (("\n" if ev_lines else "") +
                         f"- {item.get('locator', '?')} [{item.get('kind', '?')}] "
                         f"hash={item.get('content_hash', '?')}")
        sections["evidence"] = ev_lines

        # 3) Tier B knowledge (bounded; production manifest machinery)
        tier_b_budget = max(400, budget_chars // 3)
        sections["tier_b_knowledge"] = self._tier_b_section(mission_id, objective, tier_b_budget)

        # 4) Tier C — advisory, role-gated, budget-droppable. Budget =
        #    whatever remains after Tier A/B (no floor: under starvation ALL
        #    Tier C is dropped, recorded, never silently truncated).
        used_so_far = sum(len(v) for v in sections.values())
        tier_c_budget = max(0, budget_chars - used_so_far)
        if tier_c_enabled and tier_c_budget > 0:
            try:
                core, core_dropped = self._core_section(tier_c_budget)
                if core:
                    sections["core_memory"] = "ADVISORY — " + core
                dropped.extend(core_dropped)
                if role != "verifier":
                    recalled = self._recall_section(objective, limit=5)
                    if recalled:
                        sections["recalled_experience"] = "ADVISORY RECALL (context, not authority):\n" + recalled
                    procs = self._procedures_section(objective, limit=3)
                    if procs:
                        sections["procedures"] = "ADVISORY PROCEDURES (experiential guidance):\n" + procs
                else:
                    notes.append("verifier role: Tier C recall excluded (verifier independence)")
            except Exception as exc:
                # Tier C failure degrades memory, never control (audit K)
                notes.append(f"tier_c_unavailable: {type(exc).__name__}")
        elif tier_c_enabled:
            notes.append("tier_c budget exhausted after Tier A/B sections")
        else:
            notes.append("tier_c disabled for this package")

        # 5) builder claims — labeled, never evidence
        if builder_claims:
            lines = []
            for claim in builder_claims:
                lines.append(f"UNVERIFIED_BUILDER_CLAIM by {claim.get('author', 'unknown')}: "
                             f"{claim.get('content', '')}")
            sections["builder_claims"] = "\n".join(lines)

        used = sum(len(v) for v in sections.values())
        return RoleContextPackage(sections=sections, used_chars=used,
                                  budget_chars=budget_chars, dropped=dropped, notes=notes)
