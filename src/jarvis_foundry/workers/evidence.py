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
import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from ..clock import isoformat
from .contracts import WorkerStepContract
from .envelope import ResultEnvelope
from .result_contract import RESULT_FILENAME


class EvidenceError(RuntimeError):
    pass


def _run(
    cmd: list[str],
    *,
    cwd: Path,
    timeout: float = 30,
) -> subprocess.CompletedProcess:
    from ..platform.windows_subprocess import headless_run_kwargs

    return subprocess.run(
        cmd, cwd=str(cwd), capture_output=True, text=True,
        timeout=timeout, check=False,
        **headless_run_kwargs(),
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
    # File-level untracked output is required for claim reconciliation.
    # Git's default collapses a new directory to ``?? dir/``, making a worker
    # that truthfully claims ``dir/artifact`` look like a false success.
    result = _run(
        ["git", "status", "--porcelain", "--untracked-files=all"], cwd=root)
    changed: list[str] = []
    for line in result.stdout.splitlines():
        rel = _porcelain_path(line)
        if rel:
            changed.append(rel)
    return tuple(sorted(set(changed)))


def _porcelain_path(line: str) -> str | None:
    """Parse a ``git status --porcelain`` path, including quoted names."""
    if len(line) < 4:
        return None
    raw = line[3:].strip().replace("\\", "/")
    if " -> " in raw:
        raw = raw.split(" -> ", 1)[1]
    if len(raw) >= 2 and raw[0] == '"' and raw[-1] == '"':
        raw = raw[1:-1]
    return raw or None


_CACHE_SEGMENTS = ("__pycache__", ".pytest_cache", ".mypy_cache")
_CONTROL_FILES = (RESULT_FILENAME,)
_OWNER_GOAL_RECEIPT = re.compile(
    r"\.jarvis/owner-goals/[0-9a-f]{10}/(?:plan|outcome)\.md"
)


def _expected_owner_goal_receipts(
    contract: WorkerStepContract,
) -> frozenset[str]:
    """Return only explicitly expected JARVIS owner-goal control receipts."""
    return frozenset(
        normalized
        for rel in contract.expected_artifacts
        if _OWNER_GOAL_RECEIPT.fullmatch(
            normalized := rel.replace("\\", "/"))
    )


def _is_expected_owner_goal_receipt(
    rel: str,
    expected_receipts: frozenset[str],
) -> bool:
    return rel.replace("\\", "/") in expected_receipts


def _is_machine_cache(rel: str) -> bool:
    """Deterministic tool caches must not condemn a worker's real work."""
    parts = Path(rel).parts
    return (any(part in _CACHE_SEGMENTS for part in parts)
            or rel.endswith(".pyc"))


def _is_control_file(rel: str) -> bool:
    """JARVIS control/claim files are excluded from the deliverable diff
    comparison. They carry worker claims, never deliverables."""
    return rel in _CONTROL_FILES


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
    file_hashes: dict[str, str] = field(default_factory=dict)
    captured_at: str = field(default_factory=isoformat)
    # Every path git reported dirty at capture, before any accounting
    # filter, plus the bytes recorded for each.  Scope containment uses
    # these to attribute observed changes to THIS run: a path already dirty
    # at capture and byte-identical now was not mutated by the worker and
    # must not be condemned (see ``EvidenceReconciler.reconcile``).
    raw_changed_files: tuple[str, ...] = field(default_factory=tuple)
    raw_file_hashes: dict[str, str] = field(default_factory=dict)


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

    def __init__(
        self,
        *,
        test_timeout_seconds: int = 120,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self.test_timeout_seconds = test_timeout_seconds
        self._monotonic = monotonic or time.monotonic

    def capture_before(self, contract: WorkerStepContract) -> BeforeState:
        root = contract.working_root.resolve()
        changed = git_changed_files(root)
        expected_receipts = _expected_owner_goal_receipts(contract)
        meaningful = tuple(
            rel for rel in changed
            if not _is_control_file(rel)
            and not _is_expected_owner_goal_receipt(rel, expected_receipts))
        # Record the bytes of EVERY dirty path, not only the meaningful
        # ones: scope containment proves a capture-listed path unchanged
        # before exempting it, so a receipt or cache that is already dirty
        # must carry its capture bytes too.  ``file_hashes`` keeps its
        # existing meaning (the claim-accounting baseline); the raw map is
        # used only by containment attribution, and it additionally
        # requires a REAL regular file -- a symlink has no stable bytes of
        # its own (reading one yields the target's), so a swapped-in link
        # must never be able to prove itself "unchanged" and waive the
        # resolved-path containment check.
        claim_hashes: dict[str, str] = {}
        raw_hashes: dict[str, str] = {}
        for rel in changed:
            path = root / rel
            if not path.is_file():
                continue
            try:
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                continue
            claim_hashes[rel] = digest
            if not path.is_symlink():
                raw_hashes[rel] = digest
        return BeforeState(
            head=git_head(root),
            diff_hash=git_diff_hash(root),
            changed_files=meaningful,
            file_hashes={
                rel: claim_hashes[rel] for rel in meaningful
                if rel in claim_hashes
            },
            raw_changed_files=changed,
            raw_file_hashes=raw_hashes,
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
        test_deadline_monotonic: float | None = None,
    ) -> EvidenceReport:
        root = contract.working_root.resolve()
        mismatches: list[str] = []
        containment: list[str] = []
        unrecorded: list[str] = []
        expected_receipts = _expected_owner_goal_receipts(contract)

        try:
            observed_head = git_head(root)
        except EvidenceError as error:
            observed_head = None
            mismatches.append(f"worktree unreadable: {error}")

        observed_diff_hash = git_diff_hash(root)
        observed_changed = git_changed_files(root)
        meaningful_changed = tuple(
            rel for rel in observed_changed
            if not _is_machine_cache(rel)
            and not _is_control_file(rel)
            and not _is_expected_owner_goal_receipt(
                rel, expected_receipts))
        observed_changed = tuple(
            rel for rel in observed_changed if not _is_control_file(rel))
        if delta_baseline is not None:
            delta_baseline = tuple(
                rel for rel in delta_baseline
                if not _is_control_file(rel)
                and not _is_expected_owner_goal_receipt(
                    rel, expected_receipts))

        if envelope.worker_status != "COMPLETED":
            mismatches.append(
                f"worker did not complete (status={envelope.worker_status}, "
                f"exit_state={envelope.exit_state})")

        # Scope: every attributable observed change must stay inside allowed
        # paths and never inside forbidden paths.
        #
        # Containment is judged on THIS run's own delta, exactly like claim
        # reconciliation below.  A registered worktree legitimately carries
        # untracked files from earlier missions (control receipts,
        # prescribed exhaust, prior unverified output).  When a pre-existing
        # path sits under a forbidden prefix it used to fail EVERY later run
        # on that resource -- with an EMPTY mismatch list, which the mission
        # bridge classifies as ``evidence_mismatch`` -- so a wholly correct
        # mission burned its entire repair budget (live 2026-10-04,
        # mission-4646c7023b05: ``docs/evidence/windows-ci-fix-20260930.md``,
        # produced by the previous day's mission, condemned three correct
        # attempts).  A capture-listed path is exempt ONLY when its recorded
        # bytes are proven byte-identical now; anything unreadable,
        # unhashable, or changed remains attributable and still condemns.
        allowed_roots = [
            self._resolve_scope_path(root, path)
            for path in (contract.allowed_paths or (root,))
        ]
        forbidden_roots = [
            self._resolve_scope_path(root, path)
            for path in contract.forbidden_paths
        ]
        scope_population = observed_changed
        baseline = tuple(dict.fromkeys(
            (*before.raw_changed_files, *(delta_baseline or ()))))
        if baseline:
            baseline_set = set(baseline)
            scope_population = tuple(
                rel for rel in observed_changed
                if rel not in baseline_set
                or not self._unchanged_since_capture(
                    root / rel, before.raw_file_hashes.get(rel)))
        for rel in scope_population:
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
        claimed = {
            rel for rel in envelope.files_changed
            if not _is_expected_owner_goal_receipt(rel, expected_receipts)
        }
        observed_set = set(meaningful_changed)
        if delta_baseline is not None:
            # Delta mode: the worker is accountable for files it added or
            # whose contents changed since capture. Overwriting an already
            # dirty path (repair of a failed step) is still this run's work.
            # Previously accepted sibling files that did not change are not.
            current_hashes: dict[str, str] = {}
            for rel in observed_set:
                path = root / rel
                if path.is_file():
                    try:
                        current_hashes[rel] = hashlib.sha256(
                            path.read_bytes()).hexdigest()
                    except OSError:
                        continue
            own_changes = {
                rel for rel, digest in current_hashes.items()
                if before.file_hashes.get(rel) != digest
            }
            # A worktree-scoped result file survives across mission steps.
            # Providers can therefore repeat a prior step's deliverable list
            # even when only the current step's outcome changed. A claim for
            # a path that was already dirty at capture and is byte-identical
            # now is stale metadata, not a new mutation. Drop only those
            # provably unchanged prior claims; a prior path whose bytes did
            # change remains accountable to this run and still fails closed if
            # the worker omitted it.
            stale_claims = {
                rel for rel in claimed
                if rel in before.file_hashes
                and current_hashes.get(rel) == before.file_hashes[rel]
            }
            claimed -= stale_claims
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
                    f"observed={sorted(observed_set)})")

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
            argv = command.split()
            timeout = float(self.test_timeout_seconds)
            lifecycle_limited = False
            lifecycle_exhausted = False
            if test_deadline_monotonic is not None:
                remaining = (
                    float(test_deadline_monotonic) - self._monotonic()
                )
                if remaining <= 0:
                    lifecycle_exhausted = True
                    result = subprocess.CompletedProcess(
                        argv,
                        124,
                        "",
                        "package lifecycle budget exhausted",
                    )
                else:
                    lifecycle_limited = remaining <= timeout
                    timeout = min(timeout, remaining)
            if not lifecycle_exhausted:
                try:
                    result = _run(argv, cwd=root, timeout=timeout)
                except subprocess.TimeoutExpired:
                    lifecycle_exhausted = lifecycle_limited
                    result = subprocess.CompletedProcess(
                        argv,
                        124,
                        "",
                        (
                            "package lifecycle budget exhausted"
                            if lifecycle_exhausted
                            else "timeout"
                        ),
                    )
            test_result = {
                "command": command,
                "exit": result.returncode,
                "stdout_tail": result.stdout[-400:],
                "stderr_tail": result.stderr[-400:],
            }
            if lifecycle_exhausted:
                test_result["lifecycle_budget_exhausted"] = True
            test_results.append(test_result)
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

    @staticmethod
    def _unchanged_since_capture(path: Path, recorded: str | None) -> bool:
        """True only when the capture recorded bytes and they still match.

        ``recorded is None`` (never captured, or captured as a non-regular
        file) fails closed, and so does a path that is no longer a regular
        file: content equality through a symlink proves nothing about the
        path the containment check would resolve.
        """
        if recorded is None:
            return False
        if path.is_symlink() or not path.is_file():
            return False
        try:
            current = hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return False
        return current == recorded

    @staticmethod
    def _resolve_scope_path(root: Path, path: Path) -> Path:
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = root / candidate
        return candidate.resolve(strict=False)
