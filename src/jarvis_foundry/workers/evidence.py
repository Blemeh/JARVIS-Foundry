"""Evidence-vs-claim reconciliation (Hybrid Worker Integration §7).

After a worker finishes, JARVIS itself independently inspects observable
state: git HEAD, the working-tree diff, actually changed files, target
containment, expected artifacts and hashes, and (where provided) the exit
codes of deterministic test commands. A worker claim and observed reality
are separate fields; disagreement preserves both and flags the mismatch.

The External Fusion B3 regression is the canonical case: worker claimed
success, held-out evidence refuted the claim -> JARVIS must not PASS.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..clock import isoformat
from .contracts import WorkerStepContract
from .envelope import ResultEnvelope


class EvidenceError(RuntimeError):
    pass


def _run(cmd: list[str], *, cwd: Path, timeout: int = 30) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd, cwd=str(cwd), capture_output=True, text=True,
        timeout=timeout, check=False,
    )


def git_head(root: Path) -> str:
    result = _run(["git", "rev-parse", "HEAD"], cwd=root)
    if result.returncode != 0:
        raise EvidenceError(f"not a git worktree: {root} ({result.stderr.strip()[:200]})")
    return result.stdout.strip()


def git_diff_hash(root: Path) -> str:
    """Deterministic hash of the current working-tree diff (unstaged+staged)."""
    result = _run(["git", "diff", "HEAD", "--no-ext-diff"], cwd=root)
    if result.returncode != 0:
        raise EvidenceError(f"git diff failed in {root}: {result.stderr.strip()[:200]}")
    return hashlib.sha256(result.stdout.encode("utf-8")).hexdigest()


def git_changed_files(root: Path) -> tuple[str, ...]:
    result = _run(["git", "status", "--porcelain"], cwd=root)
    changed: list[str] = []
    for line in result.stdout.splitlines():
        if len(line) > 3:
            changed.append(line[3:].strip().replace("\\", "/"))
    return tuple(sorted(set(changed)))


_CACHE_SEGMENTS = ("__pycache__", ".pytest_cache", ".mypy_cache")


def _is_machine_cache(rel: str) -> bool:
    """Deterministic tool caches must not condemn a worker's real work."""
    parts = Path(rel).parts
    return (any(part in _CACHE_SEGMENTS for part in parts)
            or rel.endswith(".pyc"))


def file_hashes(paths: tuple[Path, ...], *, limit: int = 200) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for path in paths:
        if not path.is_file():
            continue
        try:
            hashes[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            continue
        if len(hashes) >= limit:
            break
    return hashes


@dataclass(frozen=True, slots=True)
class BeforeState:
    head: str
    diff_hash: str
    changed_files: tuple[str, ...]
    captured_at: str = field(default_factory=isoformat)


@dataclass(frozen=True, slots=True)
class EvidenceReport:
    run_id: str
    agree: bool
    mismatches: tuple[str, ...] = field(default_factory=tuple)
    observed_head: str | None = None
    observed_diff_hash: str | None = None
    observed_changed_files: tuple[str, ...] = field(default_factory=tuple)
    containment_violations: tuple[str, ...] = field(default_factory=tuple)
    unrecorded_mutations: tuple[str, ...] = field(default_factory=tuple)
    missing_artifacts: tuple[str, ...] = field(default_factory=tuple)
    artifact_hash_mismatches: tuple[str, ...] = field(default_factory=tuple)
    test_results: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    persistence_observations: tuple[str, ...] = field(default_factory=tuple)

    def to_payload(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "agree": self.agree,
            "mismatches": list(self.mismatches),
            "observed_head": self.observed_head,
            "observed_diff_hash": self.observed_diff_hash,
            "observed_changed_files": list(self.observed_changed_files),
            "containment_violations": list(self.containment_violations),
            "unrecorded_mutations": list(self.unrecorded_mutations),
            "missing_artifacts": list(self.missing_artifacts),
            "artifact_hash_mismatches": list(self.artifact_hash_mismatches),
            "test_results": list(self.test_results),
            "persistence_observations": list(self.persistence_observations),
        }


class EvidenceReconciler:
    """Independently captures and reconciles observable worker state."""

    def __init__(self, *, test_timeout_seconds: int = 120) -> None:
        self.test_timeout_seconds = test_timeout_seconds

    def capture_before(self, contract: WorkerStepContract) -> BeforeState:
        root = contract.working_root.resolve()
        return BeforeState(
            head=git_head(root),
            diff_hash=git_diff_hash(root),
            changed_files=git_changed_files(root),
        )

    def reconcile(
        self,
        contract: WorkerStepContract,
        envelope: ResultEnvelope,
        *,
        before: BeforeState,
        test_commands: tuple[str, ...] = (),
        expected_artifact_hashes: dict[str, str] | None = None,
        persistence_scan_roots: tuple[Path, ...] = (),
        delta_baseline: tuple[str, ...] | None = None,
    ) -> EvidenceReport:
        root = contract.working_root.resolve()
        mismatches: list[str] = []
        containment: list[str] = []
        unrecorded: list[str] = []

        try:
            observed_head = git_head(root)
        except EvidenceError as error:
            observed_head = None
            mismatches.append(f"worktree unreadable: {error}")

        observed_diff_hash = git_diff_hash(root)
        observed_changed = git_changed_files(root)
        meaningful_changed = tuple(
            rel for rel in observed_changed
            if not _is_machine_cache(rel))

        if envelope.worker_status != "COMPLETED":
            mismatches.append(
                f"worker did not complete (status={envelope.worker_status}, "
                f"exit_state={envelope.exit_state})")

        # Scope: every observed change must stay inside allowed paths and
        # never inside forbidden paths.
        allowed_roots = [p.resolve() for p in (contract.allowed_paths or (root,))]
        forbidden_roots = [p.resolve() for p in contract.forbidden_paths]
        for rel in observed_changed:
            path = (root / rel).resolve()
            if not any(self._is_within(path, base) for base in allowed_roots):
                containment.append(f"{rel} escapes allowed scope")
            for forbidden in forbidden_roots:
                if self._is_within(path, forbidden):
                    containment.append(
                        f"{rel} touches forbidden path {forbidden}")
                    break

        # Claimed changes must match observed reality (B3 false-success rule:
        # claim "done" with no real change is a mismatch).
        claimed = set(envelope.files_changed)
        observed_set = set(meaningful_changed)
        if delta_baseline is not None:
            # Delta mode (verifier role): the worker is only accountable for
            # changes IT introduced since the captured baseline; the
            # builder's uncommitted work is not the verifier's claim.
            own_changes = observed_set - set(delta_baseline)
            unrecorded = sorted(own_changes - claimed)
            if claimed != own_changes:
                mismatches.append(
                    "claimed files_changed disagree with the worker's own "
                    f"delta (claimed={sorted(claimed)}, "
                    f"observed delta={sorted(own_changes)})")
        else:
            unrecorded = sorted(observed_set - claimed)
            if claimed != observed_set:
                mismatches.append(
                    "claimed files_changed disagree with observed worktree "
                    "diff "
                    f"(claimed={sorted(claimed)}, "
                    f"observed={sorted(observed_changed)})")

        # Diff hash honesty: a claimed diff hash must match the observed one.
        if envelope.diff_hash and envelope.diff_hash != observed_diff_hash:
            mismatches.append(
                "claimed diff hash disagrees with observed working-tree diff")

        # HEAD discipline: a completed builder run must not have advanced
        # HEAD on its own (JARVIS commits, workers only mutate the tree).
        if observed_head is not None and observed_head != before.head:
            mismatches.append(
                f"HEAD advanced during worker run ({before.head} -> {observed_head})")

        # Expected artifacts + hashes.
        missing: list[str] = []
        hash_mismatch: list[str] = []
        for rel in contract.expected_artifacts:
            artifact = (root / rel).resolve()
            if not artifact.is_file():
                missing.append(rel)
            elif expected_artifact_hashes and rel in expected_artifact_hashes:
                actual = hashlib.sha256(artifact.read_bytes()).hexdigest()
                if actual != expected_artifact_hashes[rel]:
                    hash_mismatch.append(rel)
        if missing:
            mismatches.append(f"expected artifacts missing: {sorted(missing)}")
        if hash_mismatch:
            mismatches.append(f"artifact hashes mismatch: {sorted(hash_mismatch)}")

        # Deterministic public/held-out test commands: exit 0 is the only
        # acceptable result; failures are mismatches that forbid PASS.
        test_results: list[dict[str, Any]] = []
        for command in test_commands:
            try:
                result = _run(command.split(), cwd=root, timeout=self.test_timeout_seconds)
            except subprocess.TimeoutExpired:
                result = subprocess.CompletedProcess(command.split(), 124, "", "timeout")
            test_results.append({
                "command": command,
                "exit": result.returncode,
                "stdout_tail": result.stdout[-400:],
                "stderr_tail": result.stderr[-400:],
            })
            if result.returncode != 0:
                mismatches.append(f"test command failed (exit {result.returncode}): {command}")

        # Persistence outside declared state (disposable homes etc.).
        persistence: list[str] = []
        for scan_root in persistence_scan_roots:
            if not scan_root.exists():
                continue
            for path in scan_root.rglob("*"):
                if path.is_file():
                    if any(self._is_within(path, base) for base in forbidden_roots):
                        containment.append(f"{path} touches forbidden path")
                        continue
                    if not any(self._is_within(path, base) for base in allowed_roots):
                        persistence.append(str(path))
        if persistence:
            mismatches.append(
                "worker state persisted outside declared roots "
                f"({len(persistence)} file(s) observed)")

        agree = not mismatches and not containment and not unrecorded
        return EvidenceReport(
            run_id=envelope.run_id,
            agree=agree,
            mismatches=tuple(mismatches),
            observed_head=observed_head,
            observed_diff_hash=observed_diff_hash,
            observed_changed_files=tuple(observed_changed),
            containment_violations=tuple(containment),
            unrecorded_mutations=tuple(unrecorded),
            missing_artifacts=tuple(missing),
            artifact_hash_mismatches=tuple(hash_mismatch),
            test_results=tuple(test_results),
            persistence_observations=tuple(persistence),
        )

    @staticmethod
    def _is_within(path: Path, base: Path) -> bool:
        try:
            path.relative_to(base)
            return True
        except ValueError:
            return False
