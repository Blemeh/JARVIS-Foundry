"""Deterministic project identity resolution over the existing resource registry.

One software project = one canonical resource. The registry is the existing
``resources`` table fed by the append-only ``project.registered`` event; this
module NEVER creates a second registry. It maps real-world aliases (local
paths, worktrees, git roots, remote URLs, hosted owner/repo forms) onto that
canonical identity and fails closed on ambiguity.

Design invariants:

- Determinism: the same alias set resolves to the same resource_id everywhere.
- Registry-first: canonical identity prefers an already-registered resource.
- Fail closed: ambiguous bindings (a path that now hosts a different
  repository, an alias claimed by another resource) raise instead of guessing.
- No schema change beyond one derived projection: aliases are durable
  ``memory.alias.bound`` events projected into ``tier_c_project_aliases``,
  rebuilt from events like every other projection.
- Worktree honesty: two worktrees of one repo share the PROJECT but keep their
  own WORKTREE identity (per-checkout HEAD/branch/dirty state).
- Fork honesty: only the FIRST remote (``origin`` preferred) becomes the
  canonical hosted slug. Additional remotes (upstream of a fork, mirrors,
  unrelated remotes) are recorded but never silently merged into the identity;
  treating a fork's upstream as the same project is an EXPLICIT durable
  ``bind_alias`` governance decision.
"""
from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..connectors.resources import (
    discover_resource,
    registration_event as _discover_registration_event,
)
from ..control.registration import inspect_git_resource
from ..database import FoundryDatabase
from ..events import Event

class ProjectIdentityError(ValueError):
    """Identity resolution failed closed: ambiguity or invalid input."""


class ProjectNotBoundError(LookupError):
    """Alias resolves to no registered project; registration is explicit."""


def normalize_remote(value: str) -> str:
    """Canonical hosted slug for any common remote URL form.

    Result is the hosted path tail: ``owner/repo`` (or nested
    ``group/sub/repo``), lowercased, ``.git`` stripped. All transport
    spellings of one repository (https, ssh, scp-style, bare slug)
    normalize to the SAME slug; a different path means a different repo.
    """
    import re
    from urllib.parse import urlsplit

    if not isinstance(value, str) or not value.strip():
        raise ProjectIdentityError("remote must be a non-empty string")
    text = value.strip()
    lowered = text.lower()
    if "://" in text:
        parsed = urlsplit(text)
        path = [seg for seg in parsed.path.split("/") if seg]
        if len(path) < 2:
            raise ProjectIdentityError(f"unrecognized remote form: {value!r}")
        return "/".join(path).lower().removesuffix(".git")
    if "@" in text and ":" in text:
        # scp-style git@host:owner/repo(.git)
        tail = text.rsplit(":", 1)[1]
        segments = [seg for seg in tail.split("/") if seg]
        if len(segments) < 2:
            raise ProjectIdentityError(f"unrecognized remote form: {value!r}")
        return "/".join(segments).lower().removesuffix(".git")
    if " " in text:
        raise ProjectIdentityError(f"unrecognized remote form: {value!r}")
    if lowered.count("/") < 1 or "://" in lowered:
        raise ProjectIdentityError(f"unrecognized remote form: {value!r}")
    return lowered.removesuffix(".git")


def _git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=15, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise ProjectIdentityError(f"git inspection failed: {exc}") from exc
    if result.returncode != 0:
        raise ProjectIdentityError(
            f"not a git repository: {result.stderr.strip()}")
    return result.stdout.strip()


def _parse_remotes(lines: list[str]) -> list[tuple[str, str]]:
    """[(name, url)] from ``git remote -v`` fetch lines, origin-first."""
    out: list[tuple[str, str]] = []
    for line in lines:
        if not line or "\t" not in line:
            continue
        name, rest = line.split("\t", 1)
        url = rest.split()[0] if rest.split() else ""
        mode = rest.split()[1] if len(rest.split()) > 1 else "(fetch)"
        if url and mode == "(fetch)":
            out.append((name, url))
    out.sort(key=lambda pair: pair[0] != "origin")
    return out


@dataclass(frozen=True)
class WorktreeIdentity:
    """Per-checkout truth for one binding of a project."""

    resolved_root: str
    head: str | None
    branch: str
    dirty: bool


class ProjectIdentityResolver:
    """Resolve aliases -> one canonical resource using registry precedence."""

    def __init__(self, db: FoundryDatabase) -> None:
        self.db = db

    # -- registration ---------------------------------------------------

    def register_repository(self, local_root: str | Path, *,
                            actor: str = "software-intelligence",
                            writable: bool = True) -> dict[str, Any]:
        """Register (or refresh) a local repository checkout as a resource.

        Identity comes from the deterministic discovery ID keyed on the
        canonical endpoint: the first remote's hosted slug when the repo has
        remotes, else the resolved filesystem root so un-pushed local projects
        still get a stable ID. Re-registration at a changed HEAD updates
        baseline state through the full-payload idempotency key (the same
        contract WP0 registration uses); identical deliveries dedupe.
        """
        requested = Path(local_root).expanduser().resolve()
        inspected = inspect_git_resource(requested, writable=writable)
        root = Path(inspected["resolved_root"])
        remotes = _parse_remotes(list(inspected["baseline"]["remotes"]))
        slugs: list[str] = []
        for _, url in remotes:
            try:
                slug = normalize_remote(url)
            except ProjectIdentityError:
                continue
            if slug not in slugs:
                slugs.append(slug)
        canonical = f"git:{slugs[0]}" if slugs else str(root)
        record = discover_resource(
            "local_repository", canonical,
            provenance={
                "observation_mode": "local",
                "authority": "discovery-is-not-authority",
                "hosted_slugs": slugs,
                "remote_names": [name for name, _ in remotes],
                "registered_via": "software-intelligence/project-identity",
            })
        # overlay the real git baseline onto the discovery record
        baseline = dict(record["baseline"])
        baseline["git"] = inspected["baseline"]
        record["baseline"] = baseline
        self.db.append(_discover_registration_event(
            record, source_id="software-intelligence"))

        self._emit_alias(f"path:{str(root).lower()}", record["resource_id"],
                         "local_path", actor)
        if slugs:
            self._emit_alias(f"remote:{slugs[0]}", record["resource_id"],
                             "remote", actor)
            self._emit_alias(f"slug:{slugs[0]}", record["resource_id"],
                             "slug", actor)
        record["secondary_slugs_unbound"] = slugs[1:]
        record["identity_note"] = (
            "additional remotes are NOT merged into this identity; "
            "treat fork upstream/mirrors as the same project only via "
            "explicit bind_alias") if slugs[1:] else None
        return record

    def bind_alias(self, resource_id: str, alias: str, *,
                   actor: str = "software-intelligence",
                   alias_kind: str | None = None) -> None:
        """Durably bind an additional alias to an existing resource."""
        row = self.db.row("resources", "resource_id", resource_id)
        if row is None:
            raise ProjectNotBoundError(f"unknown resource: {resource_id}")
        normalized = alias.strip().lower()
        holder = self._alias_lookup(normalized)
        if holder is not None and holder != resource_id:
            raise ProjectIdentityError(
                f"alias {normalized!r} already belongs to {holder}; "
                "rebinding requires an explicit supersede decision")
        self._emit_alias(normalized, resource_id, alias_kind or "manual", actor)

    def bind_worktree(self, resource_id: str, path: str | Path, *,
                      actor: str = "software-intelligence") -> dict[str, Any]:
        """Bind a SECOND checkout of the same project to its resource.

        Verifies the new checkout's remotes do not contradict the project's
        bound hosted slug before binding; a checkout of a DIFFERENT repository
        is refused (fail closed).
        """
        requested = Path(path).expanduser().resolve(strict=False)
        current = self._live_slugs(requested)
        conflicting = [s for s in current
                       if (h := self._alias_lookup(f"remote:{s}")) not in (None, resource_id)]
        if conflicting:
            raise ProjectIdentityError(
                f"{requested} hosts remote(s) {conflicting} bound to other "
                "projects; refusing ambiguous worktree binding")
        self._emit_alias(f"path:{str(requested).lower()}", resource_id,
                         "local_path", actor)
        wt = self._worktree_identity(requested)
        return {"resource_id": resource_id, "worktree": wt.__dict__}

    # -- resolution -----------------------------------------------------

    def resolve_local(self, path: str | Path) -> dict[str, Any]:
        """Resolve a local checkout path to its canonical project identity.

        Raises ProjectNotBoundError when the exact root was never registered
        (a stale/moved path never silently becomes a different project) and
        ProjectIdentityError when the checkout now hosts a repository bound to
        a DIFFERENT resource than the alias claims.
        """
        requested = Path(path).expanduser().resolve(strict=False)
        key = f"path:{str(requested).lower()}"
        resource_id = self._alias_lookup(key)
        if resource_id is None:
            raise ProjectNotBoundError(
                f"no registered project bound to {requested}; "
                "register_repository() first — stale paths fail closed")
        row = self.db.row("resources", "resource_id", resource_id)
        if row is None:
            raise ProjectNotBoundError(
                f"alias bound to unknown resource {resource_id}; registry "
                "divergence requires explicit repair")
        current = self._live_slugs(requested)
        for slug in current:
            holder = self._alias_lookup(f"remote:{slug}")
            if holder not in (None, resource_id):
                raise ProjectIdentityError(
                    f"path {requested} now hosts {slug!r} bound to "
                    f"{holder}, not {resource_id}; refusing silent "
                    "repo swap (stale path)")
        # second identity signal: a valid checkout of the registered
        # repository must still contain the baseline HEAD recorded at
        # registration in its history. A directory whose history diverged
        # from day one (a DIFFERENT local repo moved onto the path) is
        # refused even when neither repo has remotes.
        baseline_head = self._registered_baseline_head(resource_id)
        if baseline_head:
            try:
                verified = self._history_contains(requested, baseline_head)
            except ProjectIdentityError:
                verified = False
            if not verified:
                raise ProjectIdentityError(
                    f"path {requested} does not contain the registered "
                    f"baseline commit {baseline_head[:12]} of "
                    f"{resource_id}; refusing silent repo swap (stale path)")
        wt = self._worktree_identity(requested)
        identity = dict(row)
        identity["worktree"] = wt.__dict__
        identity["aliases"] = self.aliases_for(resource_id)
        identity["live_slugs"] = current
        return identity

    def resolve_remote(self, value: str) -> dict[str, Any]:
        """Resolve any remote URL/slug form to the canonical project row."""
        slug = normalize_remote(value)
        resource_id = None
        for alias_form in (f"remote:{slug}", f"slug:{slug}"):
            resource_id = self._alias_lookup(alias_form)
            if resource_id is not None:
                break
        if resource_id is None:
            raise ProjectNotBoundError(
                f"no registered project bound to remote {slug!r}")
        row = self.db.row("resources", "resource_id", resource_id)
        if row is None:
            raise ProjectNotBoundError(
                f"alias bound to unknown resource {resource_id}")
        identity = dict(row)
        identity["aliases"] = self.aliases_for(resource_id)
        return identity

    def resolve_by_alias(self, alias: str) -> dict[str, Any]:
        normalized = alias.strip().lower()
        resource_id = self._alias_lookup(normalized)
        if resource_id is None:
            raise ProjectNotBoundError(f"unknown alias: {alias!r}")
        row = self.db.row("resources", "resource_id", resource_id)
        if row is None:
            raise ProjectNotBoundError(f"dangling alias for {resource_id}")
        identity = dict(row)
        identity["aliases"] = self.aliases_for(resource_id)
        return identity

    # -- introspection --------------------------------------------------

    def aliases_for(self, resource_id: str) -> list[str]:
        connection = self.db.connect(readonly=True)
        try:
            rows = connection.execute(
                "SELECT alias FROM tier_c_project_aliases "
                "WHERE resource_id = ? AND active = 1 ORDER BY alias",
                (resource_id,),
            ).fetchall()
            return [r[0] for r in rows]
        finally:
            connection.close()

    def all_projects(self) -> list[dict[str, Any]]:
        return [
            {"resource_id": r["resource_id"], "kind": r["kind"],
             "resolved_root": r["resolved_root"]}
            for r in self.db.all("resources")
        ]

    # -- internals ------------------------------------------------------

    def _live_slugs(self, root: Path) -> list[str]:
        """Best-effort hosted slugs of the checkout as it exists NOW."""
        try:
            lines = _git(root, "remote", "-v").splitlines()
        except ProjectIdentityError:
            return []
        slugs: list[str] = []
        for _, url in _parse_remotes(lines):
            try:
                slug = normalize_remote(url)
            except ProjectIdentityError:
                continue
            if slug not in slugs:
                slugs.append(slug)
        return slugs

    @staticmethod
    def _history_contains(root: Path, ancestor_sha: str) -> bool:
        """True iff ``root``'s HEAD contains ``ancestor_sha`` in its history.

        Raises ProjectIdentityError when git cannot verify (not a repo,
        unknown revision) so callers fail closed instead of guessing.
        """
        try:
            _git(root, "merge-base", "--is-ancestor", ancestor_sha, "HEAD")
        except ProjectIdentityError as exc:
            raise ProjectIdentityError(
                f"cannot verify repository history at {root}: {exc}") from exc
        return True

    def _registered_baseline_head(self, resource_id: str) -> str | None:
        """The git HEAD captured at registration time, when recorded."""
        row = self.db.row("resources", "resource_id", resource_id)
        if row is None:
            return None
        try:
            import json as _json
            baseline = _json.loads(row["baseline_json"] or "{}")
        except (ValueError, TypeError):
            return None
        git_baseline = baseline.get("git") or {}
        head = git_baseline.get("head")
        return str(head) if head else None

    def _worktree_identity(self, root: Path) -> WorktreeIdentity:
        try:
            head: str | None = _git(root, "rev-parse", "HEAD")
        except ProjectIdentityError:
            head = None
        branch = _git(root, "branch", "--show-current") or "DETACHED"
        status = _git(root, "status", "--porcelain=v1")
        return WorktreeIdentity(resolved_root=str(root), head=head,
                                branch=branch, dirty=bool(status.strip()))

    def _alias_lookup(self, alias: str) -> str | None:
        connection = self.db.connect(readonly=True)
        try:
            row = connection.execute(
                "SELECT resource_id FROM tier_c_project_aliases "
                "WHERE alias = ? AND active = 1",
                (alias,),
            ).fetchone()
            return row[0] if row else None
        finally:
            connection.close()

    def _emit_alias(self, alias: str, resource_id: str,
                    alias_kind: str, actor: str) -> None:
        digest = hashlib.sha256(alias.encode("utf-8")).hexdigest()
        self.db.append(Event.create(
            "memory.alias.bound",
            {"alias": alias, "resource_id": resource_id,
             "alias_kind": alias_kind},
            source_type="supervisor", source_id=actor,
            idempotency_key=f"memory.alias.bound:{digest[:24]}"))
