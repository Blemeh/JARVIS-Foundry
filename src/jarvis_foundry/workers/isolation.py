"""Disposable worker state and private held-out boundaries.

Hermes worker execution uses a mission/run-specific disposable
``HERMES_HOME``; OpenHands uses a disposable ``OPENHANDS_DATA_DIR``. Owner
pre-existing state (the live Hermes profile, ``~/.openhands``) is never
touched: adapters refuse paths inside those roots, and cleanup removes only
run-specific disposable state, idempotently.

OpenHands containment (Phase 3): OpenHands redirects ALL persistence
(settings, secrets, profiles, workspaces, conversations parent) when the
``OH_PERSISTENCE_DIR`` environment variable is set (verified against the
installed runtime: ``persistence/store.py`` ``_get_persistence_dir`` and
``_get_profile_persistence_dir`` both read it first). Disposable state
therefore sets BOTH ``OH_PERSISTENCE_DIR`` and ``OPENHANDS_DATA_DIR`` to the
run-specific dir, and a containment snapshot diff proves no entry appeared
in the owner's ``~/.openhands`` during the run. The adapter fails closed if
containment cannot be established.

``HeldOutBoundary`` builds the §12 layout:

    mission/
      worker-visible/
        worktree/
        task-contract/
        public-tests/
      controller-private/
        held-out-tests/
        expected-properties/
        verifier-evidence/

and proves the private half is unreachable from the worker's home and
working root (no env inheritance of private paths).
"""
from __future__ import annotations

import os
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ..clock import isoformat


class IsolationError(ValueError):
    pass


def _is_within(path: Path, base: Path) -> bool:
    try:
        path.resolve().relative_to(base.resolve())
        return True
    except ValueError:
        return False


def _refuse_inside(disposable: Path, protected_roots: tuple[Path, ...], label: str) -> None:
    for root in protected_roots:
        if root.exists() and _is_within(disposable, root):
            raise IsolationError(
                f"refusing {label} inside protected owner root {root}")


@dataclass(frozen=True, slots=True)
class DisposableWorkerState:
    """Run-specific disposable state for one worker run.

    Created under a caller-provided lab root (never under the live Hermes
    profile or ``~/.openhands``). ``cleanup`` is safe and idempotent and
    removes ONLY this run's directories.
    """

    run_id: str
    framework: str
    root: Path
    hermes_home: Path | None = None
    openhands_data_dir: Path | None = None
    created_at: str = field(default_factory=isoformat)

    @classmethod
    def create(
        cls,
        *,
        run_id: str,
        framework: str,
        lab_root: Path,
        protected_roots: tuple[Path, ...] = (),
    ) -> "DisposableWorkerState":
        root = lab_root.resolve() / f"{run_id}-{uuid.uuid4().hex[:8]}"
        _refuse_inside(root, protected_roots, "disposable worker state")
        root.mkdir(parents=True, exist_ok=False)
        hermes_home = root / "hermes-home"
        openhands_data_dir = root / "openhands-data"
        if framework == "hermes":
            hermes_home.mkdir(parents=True, exist_ok=True)
        if framework == "openhands":
            openhands_data_dir.mkdir(parents=True, exist_ok=True)
        return cls(
            run_id=run_id, framework=framework, root=root,
            hermes_home=hermes_home if framework == "hermes" else None,
            openhands_data_dir=openhands_data_dir if framework == "openhands" else None,
        )

    def cleanup(self) -> bool:
        """Remove this run's disposable root. Idempotent."""
        if not self.root.exists():
            return False
        shutil.rmtree(self.root, ignore_errors=True)
        return not self.root.exists()


def snapshot_openhands_tree(home_root: Path) -> dict[str, str]:
    """Capture a recursive snapshot (rel_path -> sha256) of a tree.

    Used to diff OpenHands persistence state before vs after a run so
    probe-owned entries are distinguishable from owner entries. Missing
    roots return an empty snapshot (the owner dir is absent, which is a
    valid pristine state).
    """
    import hashlib

    snapshot: dict[str, str] = {}
    if not home_root.exists():
        return snapshot
    for path in home_root.rglob("*"):
        if path.is_file():
            rel = str(path.relative_to(home_root)).replace("\\", "/")
            try:
                snapshot[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
            except OSError:
                continue
    return snapshot


def diff_openhands_containment(
    before: dict[str, str],
    after: dict[str, str],
    *,
    home_root: Path,
) -> tuple[str, ...]:
    """Return the list of probe-owned changes in the owner OpenHands state.

    A new file (in ``after`` but not ``before``) is a probe-owned write and
    an uncontained-persistence violation. A missing file (in ``before`` but
    not ``after``) is ALSO a violation (owner state must never be removed).
    An unchanged file hash is owner state, untouched. Existing entries that
    changed content are flagged as modified owner state.
    """
    violations: list[str] = []
    rel_root = str(home_root.resolve()).lower()
    for rel in sorted(set(after) - set(before)):
        violations.append(f"new:{rel}")
    for rel in sorted(set(before) - set(after)):
        violations.append(f"removed:{rel}")
    for rel in sorted(set(before) & set(after)):
        if before[rel] != after[rel]:
            violations.append(f"modified:{rel}")
    return tuple(violations)


@dataclass(frozen=True, slots=True)
class HeldOutBoundary:
    """Worker-visible vs controller-private mission layout (§12)."""

    mission_id: str
    root: Path
    worker_visible: Path
    worktree: Path
    task_contract: Path
    public_tests: Path
    controller_private: Path
    held_out_tests: Path
    expected_properties: Path
    verifier_evidence: Path

    @classmethod
    def create(
        cls,
        *,
        mission_id: str,
        lab_root: Path,
        protected_roots: tuple[Path, ...] = (),
    ) -> "HeldOutBoundary":
        root = lab_root.resolve() / mission_id
        _refuse_inside(root, protected_roots, "held-out boundary")
        worker_visible = root / "worker-visible"
        worktree = worker_visible / "worktree"
        task_contract = worker_visible / "task-contract"
        public_tests = worker_visible / "public-tests"
        controller_private = root / "controller-private"
        held_out_tests = controller_private / "held-out-tests"
        expected_properties = controller_private / "expected-properties"
        verifier_evidence = controller_private / "verifier-evidence"
        for directory in (worktree, task_contract, public_tests,
                          held_out_tests, expected_properties,
                          verifier_evidence):
            directory.mkdir(parents=True, exist_ok=True)
        return cls(
            mission_id=mission_id, root=root,
            worker_visible=worker_visible, worktree=worktree,
            task_contract=task_contract, public_tests=public_tests,
            controller_private=controller_private,
            held_out_tests=held_out_tests,
            expected_properties=expected_properties,
            verifier_evidence=verifier_evidence,
        )

    def assert_private_unreachable(self, *candidate_roots: Path) -> tuple[str, ...]:
        """Prove private data cannot be reached from worker-visible roots.

        Checks both filesystem containment (private dir is not inside any
        worker root) and that no inherited environment variable names the
        private path. Returns the list of failures (empty = isolated).
        """
        failures: list[str] = []
        for candidate in candidate_roots:
            if _is_within(self.controller_private, candidate):
                failures.append(
                    f"controller-private data is inside worker-visible root {candidate}")
        private_repr = str(self.controller_private.resolve()).lower()
        for key, value in os.environ.items():
            if private_repr and private_repr in str(value).lower():
                failures.append(f"environment variable {key} exposes the private path")
        return tuple(failures)

    def cleanup(self) -> bool:
        if not self.root.exists():
            return False
        shutil.rmtree(self.root, ignore_errors=True)
        return not self.root.exists()
