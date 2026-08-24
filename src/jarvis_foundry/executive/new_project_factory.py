"""Greenfield NEW_PROJECT lifecycle: project creation without pre-existing resource.

WP4 seam: a NEW_PROJECT opportunity selected by V7A must be able to materialize
even when NO project resource exists yet. This module provides the bounded
factory that:

  opportunity/candidate → V7A work item → selection → greenfield resource
  → project.registered → resource_id/alias → implementation mission → tests
  → verifier → repair → portfolio outcome

It reuses existing authorities only: FoundryDatabase events, resources table,
ProjectIdentityResolver, MissionStepContract, verification. No second
orchestrator, scorer, or store is introduced.

Safety properties:
- bounded slug / path (alphanumeric, hyphen, length, no traversal)
- deterministic resource_id (hash of slug or remote when present)
- fail-closed on alias collision (different repo already bound)
- git operations are bounded and recorded, not silent
- STOP/veto checked before effect
- lineage is durably recorded (portfolio.new_project_created)
"""
from __future__ import annotations

import hashlib
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any, Mapping

from ..control.broker import emergency_stop_reason, matching_veto_reason
from ..database import FoundryDatabase
from ..events import Event
from ..mission.contracts import MissionStepContract, record_step_contract
from ..software_intelligence.project_identity import (
    ProjectIdentityResolver,
    normalize_remote,
)

# Bounded greenfield roots. In tests a caller may supply an explicit
# disposable parent; otherwise we use <db_parent>/greenfield/<slug>
# which is isolated and never touches production Supervisor home.
_ALLOWED_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9\-]{1,58}[a-z0-9]$")
_MAX_SLUG_LEN = 60
_DEFAULT_GREENFIELD_DIRNAME = "greenfield_projects"


def sanitize_slug(raw: str) -> str:
    """Validate and normalize a project slug.

    Returns lowercased canonical slug. Raises ValueError on violation.
    """
    if not isinstance(raw, str) or not raw:
        raise ValueError("project slug must be non-empty string")
    slug = raw.strip().lower()
    slug = re.sub(r"[^a-z0-9\-]+", "-", slug).strip("-")
    # collapse multiple hyphens
    slug = re.sub(r"-{2,}", "-", slug)
    if len(slug) < 3 or len(slug) > _MAX_SLUG_LEN:
        raise ValueError(f"slug length must be 3-{_MAX_SLUG_LEN}: {raw!r}")
    if not _ALLOWED_SLUG_RE.match(slug):
        raise ValueError(f"invalid project slug: {raw!r} -> {slug!r}")
    return slug


def derive_slug_from_item(item: Mapping[str, Any]) -> str:
    """Determine bounded slug from portfolio work item.

    Priority: metadata.project_slug > metadata.slug > project_id > item_id suffix
    """
    meta = dict(item.get("metadata") or {})
    for key in ("project_slug", "slug", "project_id"):
        val = meta.get(key) or item.get(key) or item.get("project_id")
        if isinstance(val, str) and val.strip():
            try:
                return sanitize_slug(val)
            except ValueError:
                continue
    # fallback: sanitize item_id tail
    item_id = str(item.get("item_id") or "")
    tail = item_id.split(":")[-1] if ":" in item_id else item_id
    if tail:
        try:
            return sanitize_slug(tail)
        except ValueError:
            pass
    # last resort: deterministic hash prefix
    digest = hashlib.sha256(item_id.encode("utf-8")).hexdigest()[:10]
    return sanitize_slug(f"proj-{digest}")


def _greenfield_root_for_slug(
    database: FoundryDatabase,
    slug: str,
    override: Path | str | None = None,
) -> Path:
    if override is not None:
        base = Path(override).expanduser().resolve()
        target = (base / slug).resolve()
        # ensure target is inside base (no traversal)
        try:
            target.relative_to(base)
        except ValueError as exc:
            raise ValueError(f"greenfield path escapes base: {target}") from exc
        return target
    # default: isolated beside the database file
    db_parent = Path(database.path).parent.resolve()
    base = db_parent / _DEFAULT_GREENFIELD_DIRNAME
    target = (base / slug).resolve()
    try:
        target.relative_to(base)
    except ValueError as exc:
        raise ValueError(f"greenfield path escapes base: {target}") from exc
    return target


def _run_git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def create_greenfield_repository(
    path: Path,
    slug: str,
    *,
    actor: str = "software-intelligence",
) -> dict[str, Any]:
    """Create an isolated git repository at path if absent.

    Idempotent when the repo already exists and is the same logical project
    (same initial state). Fails closed if path hosts a different repository
    already bound to another resource (checked by caller via alias table).
    Returns {"path": str, "head": str, "created": bool}
    """
    path = Path(path).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    git_dir = path / ".git"
    created = False
    if not git_dir.exists():
        _run_git(path, "init", "-b", "main")
        # minimal scaffold: README + .gitignore
        readme = path / "README.md"
        if not readme.exists():
            readme.write_text(f"# {slug}\n\nGreenfield project scaffold for {slug}.\n", encoding="utf-8")
        gitignore = path / ".gitignore"
        if not gitignore.exists():
            gitignore.write_text("__pycache__/\n*.pyc\n", encoding="utf-8")
        _run_git(path, "config", "user.email", "jarvis@foundry.local")
        _run_git(path, "config", "user.name", "JARVIS Foundry")
        _run_git(path, "add", ".")
        try:
            _run_git(path, "commit", "-m", f"feat: initial scaffold for {slug} via NEW_PROJECT factory")
        except RuntimeError:
            # empty commit fallback if already staged differently
            _run_git(path, "commit", "--allow-empty", "-m", f"feat: initial scaffold for {slug}")
        created = True
    head = _run_git(path, "rev-parse", "HEAD")
    return {"path": str(path), "head": head, "created": created}


def ensure_new_project_resource(
    database: FoundryDatabase,
    item: Mapping[str, Any],
    decision_id: str,
    *,
    source_id: str = "v7-portfolio-bridge",
    greenfield_root_override: Path | str | None = None,
) -> str:
    """Ensure a durable resource exists for a NEW_PROJECT work item.

    If a resource with the derived slug alias already exists, it is reused
    (idempotent). Otherwise a bounded greenfield repository is created and
    registered through the canonical project identity path but with a
    mission-compatible flat capabilities map (write_registered_worktree_files).

    Returns the canonical resource_id. Emits durable lineage event
    portfolio.new_project_created on first creation.
    """
    slug = derive_slug_from_item(item)
    # Check for STOP/veto before any effect
    stop = emergency_stop_reason(database, database.path.parent / "EMERGENCY_STOP")
    if stop:
        raise RuntimeError(f"STOP active: {stop}")
    veto_targets = {str(item.get("item_id") or ""), slug, f"project:{slug}"}
    veto = matching_veto_reason(database, veto_targets)
    if veto:
        raise RuntimeError(f"owner veto active: {veto}")

    resolver = ProjectIdentityResolver(database)
    # Try resolve by already-bound alias (reuse)
    for alias_form in (f"slug:{slug}", f"remote:{slug}"):
        try:
            row = resolver.resolve_by_alias(alias_form)
            resource_id = str(row["resource_id"])
            _emit_lineage(database, item, slug, resource_id, decision_id, source_id, created=False)
            return resource_id
        except Exception:
            pass

    # No existing binding: create greenfield repo
    target_path = _greenfield_root_for_slug(database, slug, override=greenfield_root_override)

    # Fail closed if alias already claimed by different resource
    normalized_aliases = [f"slug:{slug}", f"path:{str(target_path).lower()}"]
    for alias in normalized_aliases:
        holder = _alias_lookup(database, alias)
        if holder is not None:
            raise ValueError(f"alias {alias!r} already belongs to {holder}; cannot create {slug}")

    repo_info = create_greenfield_repository(target_path, slug, actor=source_id)

    # Register with flat, mission-compatible capabilities (write_registered_worktree_files)
    # Use the canonical git inspect to obtain accurate HEAD/branch/status, but ensure
    # capabilities are flat and include the writer capability expected by MissionStepContract.
    from ..control.registration import inspect_git_resource, registration_event

    inspected = inspect_git_resource(target_path, writable=True)
    # Deterministic resource_id based on slug (stable) rather than random uuid,
    # but keep uniqueness per slug. Use hash of slug for determinism while still
    # being globally unique via prefix.
    deterministic_id = f"res-{hashlib.sha256(slug.encode('utf-8')).hexdigest()[:16]}"
    # Ensure capabilities contain the writer capability expected by the mission loop
    flat_caps = dict(inspected.get("capabilities") or {})
    if "write_registered_worktree_files" not in flat_caps:
        flat_caps["write_registered_worktree_files"] = "READY"
    resource = {
        "resource_id": deterministic_id,
        "kind": inspected.get("kind") or "git_worktree",
        "root_path": inspected.get("root_path") or str(target_path),
        "resolved_root": inspected.get("resolved_root") or str(target_path),
        "baseline": inspected.get("baseline") or {"head": repo_info.get("head")},
        "capabilities": flat_caps,
        "readiness_state": inspected.get("readiness_state") or "READY",
    }
    # Emit project.registered (idempotent on resource_id+payload hash)
    try:
        database.append(registration_event(resource, source_id=source_id))
    except Exception:
        # If already registered with same idempotency, treat as reuse
        existing = database.row("resources", "resource_id", deterministic_id)
        if existing is None:
            raise

    # Bind aliases durably (path + slug) via the canonical alias table
    for alias, kind in [
        (f"path:{str(target_path).lower()}", "local_path"),
        (f"slug:{slug}", "slug"),
    ]:
        try:
            resolver.bind_alias(deterministic_id, alias, actor=source_id, alias_kind=kind)
        except ValueError:
            # already bound to same resource is fine; otherwise fail closed above
            holder = _alias_lookup(database, alias)
            if holder not in (None, deterministic_id):
                raise

    resource_id = deterministic_id
    _emit_lineage(database, item, slug, resource_id, decision_id, source_id, created=True, repo_info=repo_info)
    return resource_id


def _alias_lookup(database: FoundryDatabase, alias: str) -> str | None:
    with database.connect(readonly=True) as conn:
        row = conn.execute(
            "SELECT resource_id FROM tier_c_project_aliases WHERE alias = ? AND active = 1",
            (alias.strip().lower(),),
        ).fetchone()
        return str(row[0]) if row else None


def _emit_lineage(
    database: FoundryDatabase,
    item: Mapping[str, Any],
    slug: str,
    resource_id: str,
    decision_id: str,
    source_id: str,
    *,
    created: bool,
    repo_info: dict[str, Any] | None = None,
) -> None:
    candidate_id = str(item.get("candidate_id") or item.get("metadata", {}).get("candidate_id") or "")
    # derive project_id: prefer metadata.project_id else slug
    project_id = str(item.get("project_id") or item.get("metadata", {}).get("project_id") or slug)
    payload = {
        "item_id": str(item.get("item_id") or ""),
        "candidate_id": candidate_id,
        "project_id": project_id,
        "slug": slug,
        "resource_id": resource_id,
        "decision_id": decision_id,
        "created": bool(created),
        "local_path": str(repo_info.get("path")) if repo_info else None,
        "head": str(repo_info.get("head")) if repo_info else None,
    }
    database.append(Event.create(
        "portfolio.new_project_created",
        payload,
        source_type="supervisor",
        source_id=source_id,
        idempotency_key=f"portfolio.new_project_created:{decision_id}:{slug}",
    ))


def new_project_mission_contract(
    *,
    mission_id: str,
    resource_id: str,
    objective: str,
    slug: str,
    item: Mapping[str, Any],
    decision_id: str,
    capability: str = "write_registered_worktree_files",
) -> MissionStepContract:
    """Build the greenfield implementation contract (not generic report).

    Target is NEW_PROJECT_IMPLEMENTATION.md at the greenfield root, but the
    instruction explicitly requires implementing the MVP with tests and
    verifier repair cycle inside the greenfield repository. This satisfies
    WP4: real product lifecycle seam while still using Supervisor/mission/
    effect/verifier authorities.
    """
    artifact = "NEW_PROJECT_IMPLEMENTATION.md"
    provenance = {
        "decision_id": decision_id,
        "portfolio_item_id": str(item.get("item_id") or ""),
        "portfolio_class": "NEW_PROJECT",
        "project_slug": slug,
        "candidate_id": str(item.get("candidate_id") or ""),
    }
    return MissionStepContract(
        mission_id=mission_id,
        step_id="new-project-implementation",
        version=1,
        resource_id=resource_id,
        capability=capability,
        target_relative_path=artifact,
        instructions=(
            f"NEW_PROJECT {slug}: {objective}\n"
            f"Repository: greenfield project {slug} at resource {resource_id}.\n"
            f"Implement the MVP inside the registered resource root per the "
            f"project contract. Create functional code + offline unit tests "
            f"(injectable fake bridge when needed) + verification criteria. "
            f"Run tests; if verifier reports FAIL, repair and re-verify with a fresh verifier. "
            f"Create {artifact} stating: (1) objective, (2) what was verified with evidence, "
            f"(3) VERIFIED_BY portfolio-worker. Under 1200 bytes. Do NOT treat this report as the product; "
            f"the product is the actual code under src/ or equivalent."
        ),
        expected_effects=(f"{artifact} exists", f"greenfield project {slug} implemented"),
        rollback={"method": "delete_file", "reversible": True},
        risk_class="C1",
        verification_criteria=(
            f"{artifact} exists",
            "report states the objective",
            "functional tests pass (offline)",
        ),
        expected_artifacts=(artifact,),
        forbidden_paths=(),
        recorded_by="supervisor",
        source="portfolio",
        provenance=provenance,
    )
