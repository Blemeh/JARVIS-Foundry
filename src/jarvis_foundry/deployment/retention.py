"""Safe retention for immutable release folders.

Every promotion adds ``<install>/releases/<source_sha>/`` (a full venv, about
1 GB). Nothing ever removed them, so a long-lived install accumulates well over a
hundred. This module plans - and, only when asked, applies - a conservative
prune. It never touches pointers, the database, the Scheduler or anything outside
``<install>/releases``.

A release is **protected** (never a prune candidate) when it is:

* ``CURRENT`` or ``PREVIOUS`` (the rollback target);
* named by the CURRENT release's own ``RELEASE.json`` (``previous_runtime``) or by
  ``GENERATION.json`` (the runtime the last transition replaced);
* named by ``ROLLBACK_RUNTIME.json`` (a frozen predecessor launch);
* the database's live release slot (``database_active_source_sha``), when the
  caller supplies it;
* one of the ``keep_recent`` newest releases (by ``RELEASE.json`` ``created_at``,
  falling back to the folder mtime);
* explicitly passed in ``extra_protected``.

Planning fails closed: with no readable ``CURRENT`` pointer nothing can be
proven safe, so the plan is empty and says why. Applying re-validates every
target (inside ``releases/``, a real directory, not protected) immediately before
deleting it and writes an audit record under ``<install>/logs``.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import stat
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .layout import current_pointer, install_root, previous_pointer, rollback_runtime_marker

_SHA = re.compile(r"^[0-9a-f]{40}$")


@dataclass(frozen=True)
class ReleaseEntry:
    sha: str
    path: str
    created_at: str
    size_bytes: int | None = None


@dataclass
class RetentionPlan:
    root: str
    keep_recent: int
    protected: dict[str, list[str]] = field(default_factory=dict)  # sha -> reasons
    candidates: list[ReleaseEntry] = field(default_factory=list)
    refusal: str | None = None

    @property
    def candidate_bytes(self) -> int:
        return sum(entry.size_bytes or 0 for entry in self.candidates)

    def to_payload(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["candidate_bytes"] = self.candidate_bytes
        return payload


def _read_pointer(path: Path) -> str | None:
    try:
        lines = path.read_text(encoding="utf-8").strip().splitlines()
    except OSError:
        return None
    value = lines[0].strip() if lines else ""
    return value if _SHA.match(value) else None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _shas_in(value: Any) -> Iterable[str]:
    """Every 40-hex SHA mentioned anywhere in a JSON-ish value."""
    if isinstance(value, str):
        if _SHA.match(value):
            yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _shas_in(item)
    elif isinstance(value, list):
        for item in value:
            yield from _shas_in(item)


def _created_at(release: Path) -> str:
    created = _read_json(release / "RELEASE.json").get("created_at")
    if isinstance(created, str) and created:
        return created
    try:
        return datetime.fromtimestamp(release.stat().st_mtime, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except OSError:
        return ""


def _tree_size(path: Path) -> int:
    total = 0
    for base, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(base, name))
            except OSError:
                pass
    return total


def plan_release_retention(
    root: Path | str | None = None,
    *,
    keep_recent: int = 3,
    database_active: Iterable[str] = (),
    extra_protected: Iterable[str] = (),
    measure: bool = False,
) -> RetentionPlan:
    base = install_root(Path(root) if root is not None else None)
    plan = RetentionPlan(root=str(base), keep_recent=max(0, int(keep_recent)))
    releases = base / "releases"
    if not releases.is_dir():
        plan.refusal = f"no releases directory at {releases}"
        return plan

    protected: dict[str, list[str]] = {}

    def protect(sha: str | None, reason: str) -> None:
        if sha:
            protected.setdefault(sha, []).append(reason)

    current = _read_pointer(current_pointer(base))
    if current is None:
        plan.refusal = "CURRENT pointer is missing or unreadable; refusing to plan (fail closed)"
        return plan
    protect(current, "CURRENT")
    protect(_read_pointer(previous_pointer(base)), "PREVIOUS (rollback target)")
    for sha in _shas_in(_read_json(releases / current / "RELEASE.json").get("previous_runtime")):
        protect(sha, "named by CURRENT RELEASE.json previous_runtime")
    for sha in _shas_in(_read_json(base / "GENERATION.json")):
        protect(sha, "named by GENERATION.json")
    for sha in _shas_in(_read_json(rollback_runtime_marker(base))):
        protect(sha, "named by ROLLBACK_RUNTIME.json")
    for sha in database_active:
        protect(sha, "database live release slot")
    for sha in extra_protected:
        protect(sha, "explicitly protected")

    entries = [
        ReleaseEntry(path.name, str(path), _created_at(path))
        for path in releases.iterdir()
        if path.is_dir() and not path.is_symlink() and _SHA.match(path.name)
    ]
    entries.sort(key=lambda entry: (entry.created_at, entry.sha), reverse=True)
    for entry in entries[: plan.keep_recent]:
        protect(entry.sha, f"one of the {plan.keep_recent} most recent releases")

    plan.protected = {sha: reasons for sha, reasons in protected.items()}
    candidates = [entry for entry in entries if entry.sha not in protected]
    if measure:
        candidates = [
            ReleaseEntry(e.sha, e.path, e.created_at, _tree_size(Path(e.path))) for e in candidates
        ]
    plan.candidates = candidates
    return plan


def _make_writable(function, path, _exc_info) -> None:  # pragma: no cover - platform specific
    os.chmod(path, stat.S_IWRITE)
    function(path)


def apply_release_retention(
    plan: RetentionPlan,
    *,
    stop_active: bool = False,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Delete the plan's candidates, re-validating each one first."""
    if plan.refusal:
        raise ValueError(f"cannot apply a refused plan: {plan.refusal}")
    if stop_active:
        raise RuntimeError("owner STOP is active; release retention is not applied during STOP")
    base = Path(plan.root)
    releases = (base / "releases").resolve()
    deleted: list[str] = []
    skipped: list[dict[str, str]] = []
    for entry in plan.candidates:
        path = Path(entry.path)
        try:
            resolved = path.resolve()
        except OSError as error:
            skipped.append({"sha": entry.sha, "reason": f"unresolvable: {error}"})
            continue
        if entry.sha in plan.protected:
            skipped.append({"sha": entry.sha, "reason": "protected"})
        elif path.is_symlink() or not path.is_dir():
            skipped.append({"sha": entry.sha, "reason": "not a real directory"})
        elif resolved.parent != releases or resolved.name != entry.sha:
            skipped.append({"sha": entry.sha, "reason": "outside the releases directory"})
        else:
            try:
                shutil.rmtree(resolved, onerror=_make_writable)
                deleted.append(entry.sha)
            except OSError as error:
                skipped.append({"sha": entry.sha, "reason": f"delete failed: {type(error).__name__}: {error}"[:200]})
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    audit = {
        "kind": "release_retention",
        "applied_at": stamp,
        "root": plan.root,
        "keep_recent": plan.keep_recent,
        "protected": plan.protected,
        "deleted": deleted,
        "skipped": skipped,
    }
    logs = base / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / f"release-retention-{stamp}.json").write_text(
        json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return audit
