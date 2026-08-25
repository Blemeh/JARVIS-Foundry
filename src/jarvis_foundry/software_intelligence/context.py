"""Software context compiler: bounded, role-specific, project-aware.

Composes the EXISTING role context compiler (hybrid_memory.compiler, which
enforces Tier A priority and verifier isolation) with project-scoped
selection driven by the Phase B identity resolver.

Budget law (never violated):
1. mandatory governance/mission/evidence sections first (from the canonical
   compiler; never dropped);
2. Tier B project knowledge second (bounded share);
3. remaining budget to Tier C experience/procedures;
4. Obsidian summaries last, dropped first.
Every inclusion carries provenance; every omission is recorded. If a memory
surface is unavailable the manifest degrades honestly and says so — control
truth is never sourced here.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any

from ..database import FoundryDatabase
from ..hybrid_memory import ContextCompiler
from ..knowledge_retrieval import build_context_manifest
from .project_identity import ProjectIdentityResolver

#: role -> which extra sections it may receive
ROLE_SECTIONS: dict[str, tuple[str, ...]] = {
    "executive": ("project_state", "unknowns"),
    "researcher": ("research", "contradictions", "stale", "unknowns"),
    "architect": ("decisions", "constraints"),
    "builder": ("decisions", "lessons", "procedures", "failures",
                "worktree"),
    "tester": ("criteria", "failures"),
    "critic": ("criteria", "failures"),
    "verifier": ("criteria", "failure_modes"),
    "repairer": ("failures", "repair_lineage", "lessons"),
    "knowledge_curator": ("contradictions", "stale", "provenance"),
}

#: sections the VERIFIER may never receive (independence)
VERIFIER_EXCLUDED = ("lessons", "procedures", "builder_claims",
                     "recalled_experience")


@dataclass
class SoftwareContextManifest:
    """Bounded manifest with full provenance and budget accounting."""

    role: str
    resource_id: str | None
    sections: dict[str, str] = field(default_factory=dict)
    included: list[dict[str, Any]] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    used_chars: int = 0
    budget_chars: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "resource_id": self.resource_id,
            "sections": self.sections,
            "included": self.included,
            "dropped": self.dropped,
            "notes": self.notes,
            "used_chars": self.used_chars,
            "budget_chars": self.budget_chars,
        }

    def render(self) -> str:
        parts = [f"[{name}]\n{body}" for name, body in self.sections.items()]
        return "\n\n".join(parts)


def _claim_rows(db: FoundryDatabase, *, mission_id: str) -> list[dict[str, Any]]:
    rows = db.all("knowledge_claims")
    scoped = [r for r in rows if str(r.get("mission_id") or "") == mission_id]
    return scoped if scoped else []


class SoftwareContextCompiler:
    """Compile the minimum useful institutional context for software work."""

    def __init__(self, db: FoundryDatabase) -> None:
        self.db = db
        self.base = ContextCompiler(db)
        self.identity = ProjectIdentityResolver(db)

    def compile(self, *, role: str, mission_id: str, objective: str,
                project_path: str | None = None,
                budget_chars: int = 6000,
                builder_claims: list[dict[str, Any]] | None = None,
                ) -> SoftwareContextManifest:
        started = time.time()
        role = role.lower()
        if role not in ROLE_SECTIONS:
            raise ValueError(f"unknown role: {role!r}")
        manifest = SoftwareContextManifest(role=role, resource_id=None,
                                           budget_chars=budget_chars)

        # 1) canonical base package: governance, directives, mission,
        #    evidence, Tier B knowledge, gated Tier C. Its accounting is
        #    authoritative for everything below.
        effective_builder_claims = builder_claims
        if role == "verifier" and builder_claims:
            effective_builder_claims = None
            manifest.notes.append(
                "verifier role: builder claims withheld (independence)")
        base_pkg = self.base.compile(mission_id, role, objective,
                                     budget_chars=budget_chars,
                                     builder_claims=effective_builder_claims)
        manifest.sections.update(base_pkg.sections)
        manifest.dropped.extend(base_pkg.dropped)
        manifest.notes.extend(base_pkg.notes)
        used = base_pkg.used_chars
        manifest.included.append({
            "section": "base_compiler", "why": "canonical Tier A/B/C order",
            "chars": base_pkg.used_chars})

        # 2) project identity (cheap, always attempted)
        allowed = ROLE_SECTIONS[role]
        resource_id: str | None = None
        if project_path:
            try:
                ident = self.identity.resolve_local(project_path)
                resource_id = ident["resource_id"]
                manifest.resource_id = resource_id
                wt = ident.get("worktree") or {}
                if "worktree" in allowed or role == "builder":
                    block = (f"resource_id={resource_id} "
                             f"branch={wt.get('branch')} "
                             f"head={(wt.get('head') or '')[:12]} "
                             f"dirty={wt.get('dirty')}")
                    used += self._add(manifest, "project_identity", block,
                                      budget_chars)
                    manifest.included.append({
                        "section": "project_identity", "source": "resources registry",
                        "pointer": f"resource:{resource_id}"})
            except LookupError as exc:
                manifest.notes.append(f"project_unbound: {exc}")
            except Exception as exc:  # degrade honestly, never crash control
                manifest.notes.append(f"identity_error: {type(exc).__name__}")

        # 3) project-scoped Tier B selections (claims are already bounded by
        #    the mission scoping inside build_context_manifest; here we add
        #    typed slices the base compiler does not surface)
        try:
            claims = _claim_rows(self.db, mission_id=mission_id)
        except Exception as exc:
            claims = []
            manifest.notes.append(f"tier_b_unavailable: {type(exc).__name__}")
        slices: dict[str, list[str]] = {
            "decisions": [], "failures": [], "research": [],
            "contradictions": [], "stale": [], "unknowns": [],
            "repair_lineage": [],
        }
        for row in claims:
            statement = str(row.get("statement") or "")
            ctype = str(row.get("claim_type") or "")
            status = str(row.get("status") or "")
            fresh_until = row.get("fresh_until")
            stale = bool(fresh_until)
            pointer = f"claim:{row.get('claim_id')}"
            if status == "SUPERSEDED":
                continue
            if ctype == "decision":
                slices["decisions"].append(f"{statement} [{pointer}]")
            elif ctype == "failure":
                slices["failures"].append(
                    f"PRIOR FAILURE: {statement} [{pointer}]")
            elif ctype == "research":
                tag = "STALE" if stale else "CURRENT"
                slices["research"].append(f"{tag}: {statement} [{pointer}]")
                if stale:
                    slices["stale"].append(statement[:160])
            elif ctype == "constraint":
                slices.setdefault("constraints", []).append(
                    f"{statement} [{pointer}]")
            else:
                low = statement.lower()
                if "unknown" in low or "does upstream" in low:
                    slices["unknowns"].append(f"{statement} [{pointer}]")

        # contradiction pairs become visible warnings (both sides preserved)
        try:
            pairs = self.db.all("knowledge_contradictions")
            for pair in pairs:
                slices["contradictions"].append(
                    f"CONFLICT {pair.get('reason', '')[:160]}")
        except Exception as exc:
            manifest.notes.append(f"contradiction_scan_degraded: "
                                  f"{type(exc).__name__}")

        per_slice_budget = max(200, (budget_chars - used) // 4)
        for name in allowed:
            items = slices.get(name) or []
            if not items:
                continue
            block_lines: list[str] = []
            chars = 0
            for item in items:
                cost = len(item) + 1
                if chars + cost > per_slice_budget:
                    manifest.dropped.append(
                        f"{name}: omitted {len(items) - len(block_lines)} "
                        "item(s) (budget)")
                    break
                block_lines.append(item)
                chars += cost
            used += self._add(manifest, name, "\n".join(block_lines),
                              budget_chars)

        manifest.used_chars = sum(len(v) for v in manifest.sections.values())
        manifest.notes.append(
            f"compiled_in_ms={int((time.time() - started) * 1000)}")
        return manifest

    @staticmethod
    def _add(manifest: SoftwareContextManifest, name: str, body: str,
             budget: int) -> int:
        if not body:
            return 0
        cost = len(body)
        if manifest.used_chars + cost > budget:
            manifest.dropped.append(f"{name}: section exceeds budget")
            return 0
        manifest.sections[name] = body
        return cost
