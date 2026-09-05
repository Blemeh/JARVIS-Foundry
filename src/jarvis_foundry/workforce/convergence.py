"""Read-only semantic convergence analysis for workforce candidates."""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from ..database import FoundryDatabase
from ..events import Event, EventConflict, canonical_json
from .contracts import CONVERGENCE_CLASSIFICATIONS, ConvergenceDecision
from .state import (
    SUPERVISOR_CONTROLLER,
    _gate,
    rebuild_workforce_state,
)

_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")
_HUNK_PATTERN = re.compile(
    r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
    r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@"
)
_PACKAGE_STATUSES = frozenset({
    "ACCEPTED",
    "FAILED",
    "REJECTED",
    "INCOMPLETE",
    "EVIDENCE_ONLY",
})
_STATUS_CLASSIFICATIONS = {
    "ACCEPTED": frozenset({
        "ALREADY_REPRESENTED",
        "UNIQUE_ACCEPTED_DELTA",
        "FULLY_SUPERSEDED",
        "PARTIALLY_ABSORBED",
        "CONFLICTING",
    }),
    "FAILED": frozenset({"FAILED_REJECTED"}),
    "REJECTED": frozenset({"FAILED_REJECTED"}),
    "INCOMPLETE": frozenset({"INCOMPLETE"}),
    "EVIDENCE_ONLY": frozenset({"EVIDENCE_ONLY"}),
}
CONVERGENCE_PACKAGE_DELTAS_SCHEMA = (
    "jarvis.workforce.convergence-package-deltas.v1"
)
_PACKAGE_DELTA_REFERENCE_PREFIX = (
    "jarvis.workforce.convergence-package-delta.v1:"
)
_PACKAGE_DELTA_FIELDS = frozenset({
    "package_id",
    "status",
    "classification",
    "target_sha",
    "target_tree",
    "package_universe_digest",
    "base_sha",
    "result_sha",
    "base_tree",
    "result_tree",
    "patch_id",
    "change_count",
    "changed_paths",
})
_READ_ONLY_GIT_COMMANDS = frozenset({
    "cat-file",
    "diff",
    "merge-base",
    "patch-id",
    "rev-parse",
})


class ConvergenceAnalysisError(ValueError):
    """Convergence evidence is incomplete or internally inconsistent."""


def _required_text(value: object, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ConvergenceAnalysisError(f"{name} must be non-empty")
    return text


def _exact_sha(value: object, name: str) -> str:
    sha = _required_text(value, name).lower()
    if not _SHA_PATTERN.fullmatch(sha):
        raise ConvergenceAnalysisError(
            f"{name} must be a full 40-character Git SHA"
        )
    return sha


def _evidence_refs(
    values: Iterable[str] | str | None,
) -> tuple[str, ...]:
    if values is None:
        raw: tuple[object, ...] = ()
    elif isinstance(values, str):
        raw = (values,)
    else:
        try:
            raw = tuple(values)
        except TypeError as error:
            raise ConvergenceAnalysisError(
                "package evidence must be an iterable of references"
            ) from error
    refs = tuple(sorted({
        _required_text(value, "evidence reference")
        for value in raw
    }))
    if not refs:
        raise ConvergenceAnalysisError(
            "package evidence must include at least one reference"
        )
    return refs


@dataclass(frozen=True, slots=True)
class PackageConvergenceEvidence:
    """Explicit package outcome bound to exact Git identities."""

    package_id: str
    base_sha: str
    result_sha: str
    status: str
    evidence: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "package_id",
            _required_text(self.package_id, "package_id"),
        )
        object.__setattr__(
            self,
            "base_sha",
            _exact_sha(self.base_sha, "base_sha"),
        )
        status = _required_text(self.status, "status").upper()
        if status not in _PACKAGE_STATUSES:
            raise ConvergenceAnalysisError(
                f"unsupported package convergence status: {status}"
            )
        object.__setattr__(self, "status", status)
        object.__setattr__(self, "evidence", _evidence_refs(self.evidence))
        object.__setattr__(
            self,
            "result_sha",
            _exact_sha(self.result_sha, "result_sha"),
        )


@dataclass(frozen=True, slots=True)
class _GitChange:
    path: str
    kind: str
    old_start: int
    old_count: int
    signature: str

    @property
    def key(self) -> tuple[str, str, int, int, str]:
        return (
            self.path,
            self.kind,
            self.old_start,
            self.old_count,
            self.signature,
        )


@dataclass(frozen=True, slots=True)
class _GitDelta:
    base_sha: str
    result_sha: str
    base_tree: str
    result_tree: str
    patch_id: str
    changed_paths: tuple[str, ...]
    path_patch_ids: tuple[tuple[str, str], ...]
    changes: tuple[_GitChange, ...]

    @property
    def signatures(self) -> frozenset[tuple[str, str, int, int, str]]:
        return frozenset(change.key for change in self.changes)

    @property
    def path_map(self) -> dict[str, str]:
        return dict(self.path_patch_ids)

    @property
    def changes_by_path(self) -> dict[str, tuple[_GitChange, ...]]:
        return {
            path: tuple(
                change for change in self.changes if change.path == path
            )
            for path in self.changed_paths
        }


def _canonical_change_sequence(
    changes: Iterable[_GitChange],
) -> tuple[tuple[str, str, int, int, str], ...]:
    """Order changes by Git location while retaining tied insertion order."""

    return tuple(
        change.key
        for change in sorted(
            tuple(changes),
            key=lambda change: (
                change.path,
                change.old_start,
                change.kind,
                change.old_count,
            ),
        )
    )


@dataclass(frozen=True, slots=True)
class _DeltaComparison:
    covered: frozenset[tuple[str, str, int, int, str]]
    conflicting: frozenset[tuple[str, str, int, int, str]]
    conflict_paths: frozenset[str]


def _run_git(
    repository: Path,
    *args: str,
    input_text: str | None = None,
    allowed_returncodes: tuple[int, ...] = (0,),
) -> subprocess.CompletedProcess[str]:
    if not args or args[0] not in _READ_ONLY_GIT_COMMANDS:
        command = args[0] if args else "<missing>"
        raise ConvergenceAnalysisError(
            f"Git command is not read-only: {command}"
        )
    environment = os.environ.copy()
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    environment["GIT_NO_REPLACE_OBJECTS"] = "1"
    environment["GIT_PAGER"] = "cat"
    environment["GIT_TERMINAL_PROMPT"] = "0"
    environment["LANG"] = "C"
    environment["LC_ALL"] = "C"
    completed = subprocess.run(
        ["git", "-C", str(repository), *args],
        input=input_text,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="surrogateescape",
        check=False,
        timeout=30,
        env=environment,
    )
    if completed.returncode not in allowed_returncodes:
        detail = completed.stderr.strip() or f"exit {completed.returncode}"
        raise ConvergenceAnalysisError(
            f"read-only Git {args[0]} failed: {detail}"
        )
    return completed


def _repository_root(repository: str | Path) -> Path:
    requested = Path(repository).expanduser().resolve()
    if not requested.is_dir():
        raise ConvergenceAnalysisError(
            f"repository is unavailable: {requested}"
        )
    root = _run_git(
        requested,
        "rev-parse",
        "--show-toplevel",
    ).stdout.strip()
    if not root:
        raise ConvergenceAnalysisError("repository root could not be resolved")
    return Path(root).resolve()


def _require_commit(repository: Path, sha: str, name: str) -> None:
    try:
        kind = _run_git(repository, "cat-file", "-t", sha).stdout.strip()
    except ConvergenceAnalysisError as error:
        raise ConvergenceAnalysisError(
            f"{name} is not a commit in the repository: {sha}"
        ) from error
    if kind != "commit":
        raise ConvergenceAnalysisError(
            f"{name} is not a commit in the repository: {sha}"
        )


def _tree(repository: Path, sha: str) -> str:
    return _run_git(
        repository,
        "rev-parse",
        f"{sha}^{{tree}}",
    ).stdout.strip()


def _is_ancestor(repository: Path, ancestor: str, descendant: str) -> bool:
    return _run_git(
        repository,
        "merge-base",
        "--is-ancestor",
        ancestor,
        descendant,
        allowed_returncodes=(0, 1),
    ).returncode == 0


def _diff(
    repository: Path,
    base_sha: str,
    result_sha: str,
    *,
    path: str | None = None,
) -> str:
    args = [
        "diff",
        "--patch",
        "--no-color",
        "--no-ext-diff",
        "--no-textconv",
        "--no-renames",
        "--unified=0",
        "--inter-hunk-context=0",
        base_sha,
        result_sha,
    ]
    if path is not None:
        args.extend(("--", path))
    return _run_git(repository, *args).stdout


def _hash_text(value: str) -> str:
    return hashlib.sha256(
        value.encode("utf-8", errors="surrogateescape")
    ).hexdigest()


def _patch_id(repository: Path, patch: str) -> str:
    if not patch:
        return ""
    output = _run_git(
        repository,
        "patch-id",
        "--stable",
        input_text=patch,
    ).stdout.strip()
    if output:
        candidate = output.split()[0].lower()
        if _SHA_PATTERN.fullmatch(candidate):
            return candidate
    return "sha256:" + _hash_text(patch)


def _content_change(
    path: str,
    *,
    old_start: int,
    old_count: int,
    removed: tuple[str, ...],
    added: tuple[str, ...],
) -> _GitChange:
    return _GitChange(
        path=path,
        kind="CONTENT",
        old_start=old_start,
        old_count=old_count,
        signature=_hash_text(canonical_json({
            "old_start": old_start,
            "old_count": old_count,
            "removed": removed,
            "added": added,
        })),
    )


def _content_changes(
    path: str,
    *,
    old_start: int,
    old_count: int,
    new_count: int,
    body: tuple[str, ...],
) -> tuple[_GitChange, ...]:
    removed: list[str] = []
    added: list[str] = []
    previous: str | None = None
    for line in body:
        if line.startswith("-"):
            removed.append(line[1:])
            previous = "removed"
        elif line.startswith("+"):
            added.append(line[1:])
            previous = "added"
        elif line.startswith("\\ No newline at end of file"):
            if previous == "removed" and removed:
                removed[-1] += "\0NO_NEWLINE"
            elif previous == "added" and added:
                added[-1] += "\0NO_NEWLINE"

    if len(removed) != old_count or len(added) != new_count:
        return (_content_change(
            path,
            old_start=old_start,
            old_count=old_count,
            removed=tuple(removed),
            added=tuple(added),
        ),)

    changes: list[_GitChange] = []
    paired = min(old_count, new_count)
    for offset in range(paired):
        changes.append(_content_change(
            path,
            old_start=old_start + offset,
            old_count=1,
            removed=(removed[offset],),
            added=(added[offset],),
        ))
    for offset in range(paired, old_count):
        changes.append(_content_change(
            path,
            old_start=old_start + offset,
            old_count=1,
            removed=(removed[offset],),
            added=(),
        ))
    for offset in range(paired, new_count):
        changes.append(_content_change(
            path,
            old_start=old_start + paired,
            old_count=0,
            removed=(),
            added=(added[offset],),
        ))
    return tuple(changes)


def _changes(path: str, patch: str) -> tuple[_GitChange, ...]:
    lines = patch.splitlines(keepends=True)
    changes: list[_GitChange] = []
    metadata = tuple(
        line
        for line in lines
        if line.startswith((
            "old mode ",
            "new mode ",
            "new file mode ",
            "deleted file mode ",
        ))
    )
    if metadata:
        changes.append(_GitChange(
            path=path,
            kind="METADATA",
            old_start=-1,
            old_count=0,
            signature=_hash_text("".join(metadata)),
        ))

    index = 0
    while index < len(lines):
        header = lines[index].rstrip("\r\n")
        match = _HUNK_PATTERN.match(header)
        if match is None:
            index += 1
            continue
        body: list[str] = []
        index += 1
        while index < len(lines):
            next_line = lines[index]
            if _HUNK_PATTERN.match(next_line.rstrip("\r\n")):
                break
            body.append(next_line)
            index += 1
        old_count = int(match.group("old_count") or 1)
        changes.extend(_content_changes(
            path,
            old_start=int(match.group("old_start")),
            old_count=old_count,
            new_count=int(match.group("new_count") or 1),
            body=tuple(body),
        ))

    if patch and not changes:
        changes.append(_GitChange(
            path=path,
            kind="OPAQUE",
            old_start=-1,
            old_count=0,
            signature=_hash_text(patch),
        ))
    return tuple(changes)


def _changes_overlap(left: _GitChange, right: _GitChange) -> bool:
    if left.path != right.path:
        return False
    if left.kind == "OPAQUE" or right.kind == "OPAQUE":
        return True
    if left.kind == "METADATA" or right.kind == "METADATA":
        return left.kind == right.kind
    if left.old_count == 0 and right.old_count == 0:
        return left.old_start == right.old_start
    if left.old_count == 0:
        return (
            right.old_start
            <= left.old_start
            < right.old_start + right.old_count
        )
    if right.old_count == 0:
        return (
            left.old_start
            <= right.old_start
            < left.old_start + left.old_count
        )
    return max(left.old_start, right.old_start) < min(
        left.old_start + left.old_count,
        right.old_start + right.old_count,
    )


def _path_equal(
    repository: Path,
    left_sha: str,
    right_sha: str,
    path: str,
) -> bool:
    return _run_git(
        repository,
        "diff",
        "--quiet",
        "--no-ext-diff",
        "--no-textconv",
        "--no-renames",
        left_sha,
        right_sha,
        "--",
        path,
        allowed_returncodes=(0, 1),
    ).returncode == 0


def _compare_deltas(
    repository: Path,
    source: _GitDelta,
    other: _GitDelta,
) -> _DeltaComparison:
    if source.base_sha != other.base_sha:
        raise ConvergenceAnalysisError(
            "change comparison requires one exact base SHA"
        )
    covered: set[tuple[str, str, int, int, str]] = set()
    conflicting: set[tuple[str, str, int, int, str]] = set()
    conflict_paths: set[str] = set()
    other_by_path = other.changes_by_path
    for path, source_changes in source.changes_by_path.items():
        if _path_equal(
            repository,
            source.result_sha,
            other.result_sha,
            path,
        ):
            covered.update(change.key for change in source_changes)
            continue
        other_changes = other_by_path.get(path, ())
        for change in source_changes:
            if any(
                candidate.key == change.key
                for candidate in other_changes
            ):
                covered.add(change.key)
                continue
            if any(
                _changes_overlap(change, candidate)
                for candidate in other_changes
            ):
                conflicting.add(change.key)
                conflict_paths.add(path)
    return _DeltaComparison(
        covered=frozenset(covered),
        conflicting=frozenset(conflicting),
        conflict_paths=frozenset(conflict_paths),
    )


def _merge_base(repository: Path, left_sha: str, right_sha: str) -> str:
    sha = _run_git(
        repository,
        "merge-base",
        left_sha,
        right_sha,
    ).stdout.strip().splitlines()
    if not sha or not _SHA_PATTERN.fullmatch(sha[0]):
        raise ConvergenceAnalysisError(
            "candidate results do not share an exact Git merge base"
        )
    return sha[0]


def _delta(repository: Path, base_sha: str, result_sha: str) -> _GitDelta:
    if not _is_ancestor(repository, base_sha, result_sha):
        raise ConvergenceAnalysisError(
            "result_sha must descend from its exact base_sha: "
            f"{base_sha} -> {result_sha}"
        )
    paths = tuple(sorted(filter(
        None,
        _run_git(
            repository,
            "diff",
            "--name-only",
            "-z",
            "--no-renames",
            base_sha,
            result_sha,
        ).stdout.split("\0"),
    )))
    path_diffs = tuple(
        (
            path,
            _diff(
                repository,
                base_sha,
                result_sha,
                path=path,
            ),
        )
        for path in paths
    )
    path_patch_ids = tuple(
        (path, _patch_id(repository, patch))
        for path, patch in path_diffs
    )
    return _GitDelta(
        base_sha=base_sha,
        result_sha=result_sha,
        base_tree=_tree(repository, base_sha),
        result_tree=_tree(repository, result_sha),
        patch_id=_patch_id(
            repository,
            _diff(repository, base_sha, result_sha),
        ),
        changed_paths=paths,
        path_patch_ids=path_patch_ids,
        changes=tuple(
            change
            for path, patch in path_diffs
            for change in _changes(path, patch)
        ),
    )


def _package_delta_payload(
    candidate: PackageConvergenceEvidence,
    delta: _GitDelta,
    classification: str,
    *,
    target_sha: str,
    target_tree: str,
    package_universe_digest: str,
) -> dict[str, Any]:
    return {
        "package_id": candidate.package_id,
        "status": candidate.status,
        "classification": classification,
        "target_sha": target_sha,
        "target_tree": target_tree,
        "package_universe_digest": package_universe_digest,
        "base_sha": delta.base_sha,
        "result_sha": delta.result_sha,
        "base_tree": delta.base_tree,
        "result_tree": delta.result_tree,
        "patch_id": delta.patch_id,
        "change_count": len(delta.changes),
        "changed_paths": list(delta.changed_paths),
    }


def _package_delta_reference(payload: Mapping[str, Any]) -> str:
    return _PACKAGE_DELTA_REFERENCE_PREFIX + canonical_json(dict(payload))


def _decision_package_deltas(
    decision: ConvergenceDecision,
) -> tuple[dict[str, Any], ...]:
    records: dict[str, dict[str, Any]] = {}
    for reference in decision.evidence:
        if not reference.startswith(_PACKAGE_DELTA_REFERENCE_PREFIX):
            continue
        try:
            raw = json.loads(
                reference.removeprefix(_PACKAGE_DELTA_REFERENCE_PREFIX)
            )
        except (TypeError, ValueError, json.JSONDecodeError) as error:
            raise ConvergenceAnalysisError(
                "structured package delta evidence is malformed"
            ) from error
        if not isinstance(raw, Mapping) or set(raw) != _PACKAGE_DELTA_FIELDS:
            raise ConvergenceAnalysisError(
                "structured package delta evidence has an invalid shape"
            )
        package_id = _required_text(raw.get("package_id"), "package_id")
        if package_id in records:
            raise ConvergenceAnalysisError(
                f"duplicate structured package delta: {package_id}"
            )
        records[package_id] = dict(raw)
    if not records:
        return ()
    if set(records) != set(decision.classifications):
        raise ConvergenceAnalysisError(
            "structured package deltas do not cover every classification"
        )
    for package_id, classification in decision.classifications.items():
        status = _required_text(
            records[package_id].get("status"),
            "status",
        ).upper()
        recorded_classification = _required_text(
            records[package_id].get("classification"),
            "classification",
        )
        _validate_status_classification(status, recorded_classification)
        if recorded_classification != classification:
            raise ConvergenceAnalysisError(
                "structured package delta classification mismatch: "
                f"{package_id}"
            )
    return tuple(records[package_id] for package_id in sorted(records))


def _validate_status_classification(
    status: str,
    classification: str,
) -> None:
    allowed = _STATUS_CLASSIFICATIONS.get(status)
    if allowed is None or classification not in allowed:
        raise ConvergenceAnalysisError(
            "convergence classification does not match package status: "
            f"{status}/{classification}"
        )


def _package_universe_digest(
    packages: Iterable[PackageConvergenceEvidence],
) -> str:
    semantic_universe = [
        {
            "package_id": candidate.package_id,
            "status": candidate.status,
            "base_sha": candidate.base_sha,
            "result_sha": candidate.result_sha,
        }
        for candidate in sorted(
            packages,
            key=lambda item: item.package_id,
        )
    ]
    return "sha256:" + _hash_text(canonical_json(semantic_universe))


def _validate_convergence_package_delta_shape(
    repository: str | Path,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    if not isinstance(payload, Mapping) or set(payload) != _PACKAGE_DELTA_FIELDS:
        raise ConvergenceAnalysisError(
            "convergence package delta has an invalid shape"
        )
    package_id = _required_text(payload.get("package_id"), "package_id")
    status = _required_text(payload.get("status"), "status").upper()
    if status not in _PACKAGE_STATUSES:
        raise ConvergenceAnalysisError(
            f"unsupported package convergence status: {status}"
        )
    classification = _required_text(
        payload.get("classification"),
        "classification",
    )
    if classification not in CONVERGENCE_CLASSIFICATIONS:
        raise ConvergenceAnalysisError(
            f"unsupported convergence classification: {classification}"
        )
    _validate_status_classification(status, classification)
    target_sha = _exact_sha(payload.get("target_sha"), "target_sha")
    target_tree = _exact_sha(payload.get("target_tree"), "target_tree")
    package_universe_digest = _required_text(
        payload.get("package_universe_digest"),
        "package_universe_digest",
    )
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", package_universe_digest):
        raise ConvergenceAnalysisError(
            "package_universe_digest must be a SHA-256 digest"
        )
    base_sha = _exact_sha(payload.get("base_sha"), "base_sha")
    result_sha = _exact_sha(payload.get("result_sha"), "result_sha")
    base_tree = _exact_sha(payload.get("base_tree"), "base_tree")
    result_tree = _exact_sha(payload.get("result_tree"), "result_tree")
    patch_id = payload.get("patch_id")
    if not isinstance(patch_id, str):
        raise ConvergenceAnalysisError("patch_id must be text")
    change_count = payload.get("change_count")
    if (
        not isinstance(change_count, int)
        or isinstance(change_count, bool)
        or change_count < 0
    ):
        raise ConvergenceAnalysisError(
            "change_count must be a non-negative integer"
        )
    raw_paths = payload.get("changed_paths")
    if not isinstance(raw_paths, list):
        raise ConvergenceAnalysisError("changed_paths must be a list")
    changed_paths = tuple(
        _required_text(path, "changed path")
        for path in raw_paths
    )
    if changed_paths != tuple(sorted(set(changed_paths))):
        raise ConvergenceAnalysisError(
            "changed_paths must be sorted and unique"
        )

    root = _repository_root(repository)
    _require_commit(root, target_sha, "target_sha")
    _require_commit(root, base_sha, f"base_sha for {package_id}")
    _require_commit(root, result_sha, f"result_sha for {package_id}")
    if not _is_ancestor(root, base_sha, target_sha):
        raise ConvergenceAnalysisError(
            "target_sha must descend from the exact package base_sha: "
            f"{package_id}"
        )
    delta = _delta(root, base_sha, result_sha)
    canonical = {
        "package_id": package_id,
        "status": status,
        "classification": classification,
        "target_sha": target_sha,
        "target_tree": _tree(root, target_sha),
        "package_universe_digest": package_universe_digest,
        "base_sha": delta.base_sha,
        "result_sha": delta.result_sha,
        "base_tree": delta.base_tree,
        "result_tree": delta.result_tree,
        "patch_id": delta.patch_id,
        "change_count": len(delta.changes),
        "changed_paths": list(delta.changed_paths),
    }
    if canonical_json(dict(payload)) != canonical_json(canonical):
        raise ConvergenceAnalysisError(
            f"convergence package delta does not match Git: {package_id}"
        )
    return canonical


def reconstruct_convergence_package_deltas(
    repository: str | Path,
    *,
    target_sha: str,
    packages: Iterable[PackageConvergenceEvidence],
) -> tuple[dict[str, Any], ...]:
    """Purely reconstruct typed deltas for one exact target and package set."""
    candidates = tuple(packages)
    decision = analyze_convergence(
        repository,
        decision_id="semantic-convergence-reconstruction",
        integration_target="semantic-convergence-reconstruction",
        target_sha=target_sha,
        packages=candidates,
    )
    return _decision_package_deltas(decision)


def validate_convergence_package_delta(
    repository: str | Path,
    payload: Mapping[str, Any],
    *,
    target_sha: str | None = None,
    packages: Iterable[PackageConvergenceEvidence] | None = None,
) -> dict[str, Any]:
    """Validate one delta against its exact target and complete package set."""
    if target_sha is None or packages is None:
        raise ConvergenceAnalysisError(
            "convergence semantic validation requires target_sha and packages"
        )
    canonical = _validate_convergence_package_delta_shape(
        repository,
        payload,
    )
    expected = {
        str(record["package_id"]): record
        for record in reconstruct_convergence_package_deltas(
            repository,
            target_sha=target_sha,
            packages=tuple(packages),
        )
    }
    package_id = str(canonical["package_id"])
    if package_id not in expected:
        raise ConvergenceAnalysisError(
            "convergence package delta is outside the package universe: "
            f"{package_id}"
        )
    if canonical_json(canonical) != canonical_json(expected[package_id]):
        raise ConvergenceAnalysisError(
            "convergence package delta does not match semantic "
            f"reconstruction: {package_id}"
        )
    return canonical


def validate_convergence_package_deltas(
    repository: str | Path,
    payloads: Iterable[Mapping[str, Any]],
    *,
    target_sha: str,
    packages: Iterable[PackageConvergenceEvidence],
) -> tuple[dict[str, Any], ...]:
    """Validate complete typed deltas against one semantic reconstruction."""
    candidates = tuple(packages)
    expected_records = reconstruct_convergence_package_deltas(
        repository,
        target_sha=target_sha,
        packages=candidates,
    )
    expected = {
        str(record["package_id"]): record
        for record in expected_records
    }
    records: dict[str, dict[str, Any]] = {}
    try:
        raw_records = tuple(payloads)
    except TypeError as error:
        raise ConvergenceAnalysisError(
            "convergence package deltas must be iterable"
        ) from error
    for payload in raw_records:
        canonical = _validate_convergence_package_delta_shape(
            repository,
            payload,
        )
        package_id = str(canonical["package_id"])
        if package_id in records:
            raise ConvergenceAnalysisError(
                f"duplicate convergence package delta: {package_id}"
            )
        records[package_id] = canonical
    if set(records) != set(expected):
        raise ConvergenceAnalysisError(
            "convergence package deltas do not cover the exact package "
            "universe"
        )
    for package_id, expected_record in expected.items():
        if (
            canonical_json(records[package_id])
            != canonical_json(expected_record)
        ):
            raise ConvergenceAnalysisError(
                "convergence package delta does not match semantic "
                f"reconstruction: {package_id}"
            )
    return tuple(records[package_id] for package_id in sorted(records))


def validate_convergence_integration_union(
    repository: str | Path,
    *,
    target_sha: str,
    integration_sha: str,
    packages: Iterable[PackageConvergenceEvidence],
) -> dict[str, Any]:
    """Validate that one integration delta is exactly the package union."""

    root = _repository_root(repository)
    exact_target = _exact_sha(target_sha, "target_sha")
    exact_integration = _exact_sha(integration_sha, "integration_sha")
    _require_commit(root, exact_target, "target_sha")
    _require_commit(root, exact_integration, "integration_sha")
    candidates = tuple(sorted(packages, key=lambda item: item.package_id))
    if not candidates:
        raise ConvergenceAnalysisError(
            "integration union packages must not be empty"
        )
    package_ids = tuple(candidate.package_id for candidate in candidates)
    if len(package_ids) != len(set(package_ids)):
        raise ConvergenceAnalysisError(
            "integration union package_id values must be unique"
        )

    expected_signatures: set[tuple[str, str, int, int, str]] = set()
    expected_changes: list[_GitChange] = []
    expected_paths: set[str] = set()
    for candidate in candidates:
        if (
            candidate.status != "ACCEPTED"
            or candidate.base_sha != exact_target
        ):
            raise ConvergenceAnalysisError(
                "integration union requires accepted packages on the exact "
                f"target SHA: {candidate.package_id}"
            )
        delta = _delta(root, candidate.base_sha, candidate.result_sha)
        overlap = expected_signatures & set(delta.signatures)
        if overlap:
            raise ConvergenceAnalysisError(
                "integration union packages contain duplicate semantic "
                f"changes: {candidate.package_id}"
            )
        expected_signatures.update(delta.signatures)
        expected_changes.extend(delta.changes)
        expected_paths.update(delta.changed_paths)

    integration = _delta(root, exact_target, exact_integration)
    expected_sequence = _canonical_change_sequence(expected_changes)
    integration_sequence = _canonical_change_sequence(integration.changes)
    if integration_sequence != expected_sequence:
        raise ConvergenceAnalysisError(
            "integration delta is not the exact ordered semantic union of "
            "accepted package changes"
        )
    if set(integration.changed_paths) != expected_paths:
        raise ConvergenceAnalysisError(
            "integration paths are not the exact union of accepted package "
            "paths"
        )
    return {
        "target_sha": exact_target,
        "integration_sha": exact_integration,
        "integration_tree": integration.result_tree,
        "package_ids": list(package_ids),
        "changed_paths": list(integration.changed_paths),
        "change_count": len(integration_sequence),
    }


def _peer_fully_covers(
    repository: Path,
    package_id: str,
    delta: _GitDelta,
    peer_id: str,
    peer: _GitDelta,
) -> bool:
    if delta.result_tree == peer.result_tree:
        return peer_id < package_id
    if _is_ancestor(repository, delta.result_sha, peer.result_sha):
        return True
    if delta.signatures < peer.signatures:
        return True
    if delta.signatures == peer.signatures:
        return peer_id < package_id
    return False


def _recommendation(
    classifications: dict[str, str],
    unique_paths: dict[str, tuple[str, ...]],
    conflicts: tuple[str, ...],
) -> str:
    ready = [
        f"{package_id} [{', '.join(unique_paths[package_id])}]"
        for package_id in sorted(unique_paths)
        if unique_paths[package_id]
    ]
    represented = sorted(
        package_id
        for package_id, classification in classifications.items()
        if classification in {"ALREADY_REPRESENTED", "FULLY_SUPERSEDED"}
    )
    rejected = sorted(
        package_id
        for package_id, classification in classifications.items()
        if classification == "FAILED_REJECTED"
    )
    evidence_only = sorted(
        package_id
        for package_id, classification in classifications.items()
        if classification == "EVIDENCE_ONLY"
    )
    incomplete = sorted(
        package_id
        for package_id, classification in classifications.items()
        if classification == "INCOMPLETE"
    )
    sentences = [
        (
            "Recommend unique accepted deltas from " + "; ".join(ready) + "."
            if ready
            else "No unique accepted delta is ready."
        ),
    ]
    if conflicts:
        sentences.append(
            "Resolve conflicts before integration: "
            + "; ".join(conflicts)
            + "."
        )
    if represented:
        sentences.append(
            "Retain represented or superseded packages without replay: "
            + ", ".join(represented)
            + "."
        )
    if rejected:
        sentences.append(
            "Exclude rejected packages: " + ", ".join(rejected) + "."
        )
    if evidence_only:
        sentences.append(
            "Preserve evidence-only packages: "
            + ", ".join(evidence_only)
            + "."
        )
    if incomplete:
        sentences.append(
            "Await complete evidence for: " + ", ".join(incomplete) + "."
        )
    sentences.append(
        "This decision is advisory and performs no Git mutation."
    )
    return " ".join(sentences)


def analyze_convergence(
    repository: str | Path,
    *,
    decision_id: str,
    integration_target: str,
    target_sha: str,
    packages: Iterable[PackageConvergenceEvidence],
) -> ConvergenceDecision:
    """Classify exact candidate results without changing the repository."""
    root = _repository_root(repository)
    exact_target = _exact_sha(target_sha, "target_sha")
    _require_commit(root, exact_target, "target_sha")
    target_tree = _tree(root, exact_target)
    candidates = tuple(sorted(packages, key=lambda item: item.package_id))
    if not candidates:
        raise ConvergenceAnalysisError("packages must not be empty")
    package_ids = tuple(item.package_id for item in candidates)
    if len(package_ids) != len(set(package_ids)):
        raise ConvergenceAnalysisError("package_id values must be unique")
    package_universe_digest = _package_universe_digest(candidates)

    deltas: dict[str, _GitDelta] = {}
    target_deltas: dict[str, _GitDelta] = {}
    for candidate in candidates:
        _require_commit(
            root,
            candidate.base_sha,
            f"base_sha for {candidate.package_id}",
        )
        if not _is_ancestor(root, candidate.base_sha, exact_target):
            raise ConvergenceAnalysisError(
                "target_sha must descend from each exact package base_sha: "
                f"{candidate.package_id}"
            )
        if candidate.base_sha not in target_deltas:
            target_deltas[candidate.base_sha] = _delta(
                root,
                candidate.base_sha,
                exact_target,
            )
        _require_commit(
            root,
            candidate.result_sha,
            f"result_sha for {candidate.package_id}",
        )
        deltas[candidate.package_id] = _delta(
            root,
            candidate.base_sha,
            candidate.result_sha,
        )

    accepted = tuple(
        candidate
        for candidate in candidates
        if candidate.status == "ACCEPTED"
    )
    classifications: dict[str, str] = {}
    unique_paths: dict[str, tuple[str, ...]] = {}
    conflict_records: set[str] = set()
    target_conflicts: dict[str, set[str]] = {}
    peer_conflicts: dict[str, set[str]] = {}
    target_comparisons: dict[str, _DeltaComparison] = {}
    delta_cache = {
        (delta.base_sha, delta.result_sha): delta
        for delta in (*deltas.values(), *target_deltas.values())
    }

    for candidate in accepted:
        package_id = candidate.package_id
        delta = deltas[package_id]
        comparison = _compare_deltas(
            root,
            delta,
            target_deltas[candidate.base_sha],
        )
        target_comparisons[package_id] = comparison
        for path in comparison.conflict_paths:
            target_conflicts.setdefault(package_id, set()).add(
                f"{package_id}:{path}:integration-target"
            )

    for index, candidate in enumerate(accepted):
        package_id = candidate.package_id
        delta = deltas[package_id]
        for peer in accepted[index + 1:]:
            peer_id = peer.package_id
            peer_delta = deltas[peer_id]
            if peer_delta.result_tree == delta.result_tree:
                continue
            pair_base = _merge_base(
                root,
                delta.result_sha,
                peer_delta.result_sha,
            )
            pair_deltas: list[_GitDelta] = []
            for result_sha in (delta.result_sha, peer_delta.result_sha):
                key = (pair_base, result_sha)
                if key not in delta_cache:
                    delta_cache[key] = _delta(root, pair_base, result_sha)
                pair_deltas.append(delta_cache[key])
            left_comparison = _compare_deltas(
                root,
                pair_deltas[0],
                pair_deltas[1],
            )
            right_comparison = _compare_deltas(
                root,
                pair_deltas[1],
                pair_deltas[0],
            )
            for path in (
                left_comparison.conflict_paths
                | right_comparison.conflict_paths
            ):
                first, second = sorted((package_id, peer_id))
                record = f"{first}:{path}:{second}"
                peer_conflicts.setdefault(package_id, set()).add(record)
                peer_conflicts.setdefault(peer_id, set()).add(record)

    unsafe_cover_peers = set(target_conflicts) | set(peer_conflicts)
    pending: set[str] = set()
    for candidate in candidates:
        if candidate.status in {"FAILED", "REJECTED"}:
            classifications[candidate.package_id] = "FAILED_REJECTED"
            continue
        if candidate.status == "EVIDENCE_ONLY":
            classifications[candidate.package_id] = "EVIDENCE_ONLY"
            continue
        if candidate.status == "INCOMPLETE":
            classifications[candidate.package_id] = "INCOMPLETE"
            continue

        package_id = candidate.package_id
        delta = deltas[package_id]
        signatures = delta.signatures
        target_comparison = target_comparisons[package_id]
        if (
            not signatures
            or delta.result_tree == target_tree
            or signatures <= target_comparison.covered
        ):
            classifications[package_id] = "ALREADY_REPRESENTED"
            continue
        if _is_ancestor(root, delta.result_sha, exact_target):
            classifications[package_id] = "FULLY_SUPERSEDED"
            continue

        conflicts_for_package = (
            target_conflicts.get(package_id, set())
            | peer_conflicts.get(package_id, set())
        )
        if conflicts_for_package:
            classifications[package_id] = "CONFLICTING"
            conflict_records.update(conflicts_for_package)
            continue
        pending.add(package_id)

    fully_covered: set[str] = set()
    for package_id in sorted(pending):
        delta = deltas[package_id]
        peers = tuple(
            peer_id
            for peer_id in sorted(pending)
            if (
                peer_id != package_id
                and peer_id not in unsafe_cover_peers
            )
        )
        if any(
            _peer_fully_covers(
                root,
                package_id,
                delta,
                peer_id,
                deltas[peer_id],
            )
            for peer_id in peers
        ):
            fully_covered.add(package_id)

    for package_id in sorted(fully_covered):
        classifications[package_id] = "FULLY_SUPERSEDED"

    survivors = pending - fully_covered
    change_owners: dict[tuple[str, str, int, int, str], str] = {}
    for package_id in sorted(survivors):
        for signature in sorted(deltas[package_id].signatures):
            change_owners.setdefault(signature, package_id)

    for package_id in sorted(survivors):
        delta = deltas[package_id]
        signatures = delta.signatures
        covered = set(target_comparisons[package_id].covered)
        covered.update(
            signature
            for signature in signatures
            if change_owners[signature] != package_id
        )
        remaining = signatures - covered
        if covered and remaining:
            classifications[package_id] = "PARTIALLY_ABSORBED"
            unique_paths[package_id] = tuple(sorted(
                {signature[0] for signature in remaining}
            ))
        elif covered:
            classifications[package_id] = "FULLY_SUPERSEDED"
        else:
            classifications[package_id] = "UNIQUE_ACCEPTED_DELTA"
            unique_paths[package_id] = tuple(sorted(
                {signature[0] for signature in remaining}
            ))

    evidence: set[str] = {
        f"target:{exact_target}:tree={target_tree}",
    }
    for candidate in candidates:
        delta = deltas[candidate.package_id]
        evidence.add(_package_delta_reference(_package_delta_payload(
            candidate,
            delta,
            classifications[candidate.package_id],
            target_sha=exact_target,
            target_tree=target_tree,
            package_universe_digest=package_universe_digest,
        )))
        paths = ",".join(delta.changed_paths) or "-"
        facts = (
            f"package:{candidate.package_id}:status={candidate.status};"
            f"base={delta.base_sha};result={delta.result_sha};"
            f"base_tree={delta.base_tree};result_tree={delta.result_tree};"
            f"patch={delta.patch_id or 'empty'};"
            f"changes={len(delta.changes)};paths={paths}"
        )
        evidence.add(facts)
        evidence.update(
            f"package:{candidate.package_id}:evidence={reference}"
            for reference in candidate.evidence
        )

    conflicts = tuple(sorted(conflict_records))
    accepted_packages = tuple(sorted(
        candidate.package_id
        for candidate in accepted
    ))
    return ConvergenceDecision(
        decision_id=decision_id,
        integration_target=integration_target,
        classifications=classifications,
        accepted_packages=accepted_packages,
        conflicts=conflicts,
        evidence=tuple(sorted(evidence)),
        recommendation=_recommendation(
            classifications,
            unique_paths,
            conflicts,
        ),
    )


def _semantic_payload(decision: ConvergenceDecision) -> str:
    payload = decision.to_payload()
    payload.pop("created_at", None)
    return canonical_json(payload)


def record_convergence_decision(
    database: FoundryDatabase,
    decision: ConvergenceDecision,
    *,
    source_id: str = "workforce-convergence",
    controller: str = SUPERVISOR_CONTROLLER,
    stop_path: str | Path | None = None,
) -> ConvergenceDecision:
    """Record one advisory convergence decision with semantic idempotency."""
    existing = rebuild_workforce_state(database).convergence_decisions.get(
        decision.decision_id
    )
    if existing is not None:
        if _semantic_payload(existing) == _semantic_payload(decision):
            return existing
        raise ConvergenceAnalysisError(
            "decision_id already names different convergence content: "
            f"{decision.decision_id}"
        )
    _gate(
        database,
        controller,
        stop_path=stop_path,
    )
    payload = decision.to_payload()
    package_deltas = _decision_package_deltas(decision)
    if package_deltas:
        payload.update({
            "package_deltas_schema": CONVERGENCE_PACKAGE_DELTAS_SCHEMA,
            "package_deltas": list(package_deltas),
        })
    event = Event.create(
        "workforce.convergence_recorded",
        payload,
        source_type="supervisor",
        source_id=_required_text(source_id, "source_id"),
        correlation_id=decision.decision_id,
        idempotency_key=(
            f"workforce.convergence_recorded:{decision.decision_id}"
        ),
    )
    try:
        database.append(event)
    except EventConflict as error:
        existing = rebuild_workforce_state(
            database
        ).convergence_decisions.get(decision.decision_id)
        if (
            existing is not None
            and _semantic_payload(existing) == _semantic_payload(decision)
        ):
            return existing
        raise ConvergenceAnalysisError(
            "decision_id already names different convergence content: "
            f"{decision.decision_id}"
        ) from error
    return rebuild_workforce_state(
        database
    ).convergence_decisions[decision.decision_id]


__all__ = [
    "CONVERGENCE_PACKAGE_DELTAS_SCHEMA",
    "ConvergenceAnalysisError",
    "PackageConvergenceEvidence",
    "analyze_convergence",
    "reconstruct_convergence_package_deltas",
    "record_convergence_decision",
    "validate_convergence_package_delta",
    "validate_convergence_package_deltas",
    "validate_convergence_integration_union",
]
