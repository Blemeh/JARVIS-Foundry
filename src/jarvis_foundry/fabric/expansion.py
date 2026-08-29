"""Controlled creation of missing internal capabilities.

Candidates land under the owned root. They never overwrite production
``src/``. A poor candidate is rejected before registration.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from typing import Any, Mapping

from ..database import FoundryDatabase
from .catalog import record_gap, register_capability
from .model import (
    CapabilitySpec,
    FabricError,
    GapKind,
    GapRecord,
    GapStatus,
    Kind,
    Semantic,
    UnattendedMode,
)

CANDIDATE_ROOT = "fabric-candidates"


def _owned(path: Path, root: Path) -> Path:
    resolved = path.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as error:
        raise FabricError("candidate path escapes owned root") from error
    return resolved


def specify_gap(
    database: FoundryDatabase,
    *,
    needed_action: str,
    missing: GapKind,
    detail: str,
) -> GapRecord:
    digest = hashlib.sha256(f"{needed_action}:{missing}:{detail}".encode("utf-8")).hexdigest()[:12]
    gap = GapRecord(
        gap_id=f"gap.{digest}",
        needed_action=needed_action,
        missing=missing,
        status=GapStatus.SPECIFIED,
        detail=detail,
    )
    return record_gap(database, gap)


def write_candidate(
    owned_root: Path,
    gap: GapRecord,
    *,
    source: str,
    module_name: str = "tool",
) -> Path:
    root = Path(owned_root).resolve()
    dest = _owned(root / CANDIDATE_ROOT / gap.gap_id / f"{module_name}.py", root)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(source, encoding="utf-8")
    meta = dest.with_suffix(".json")
    meta.write_text(json.dumps({
        "gap_id": gap.gap_id,
        "needed_action": gap.needed_action,
        "sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return dest


def load_candidate(path: Path) -> Any:
    spec = importlib.util.spec_from_file_location("fabric_candidate", path)
    if spec is None or spec.loader is None:
        raise FabricError(f"cannot load candidate {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_hash_candidate(path: Path, sample: Path) -> dict[str, Any]:
    """Focused test for a sha256 file hasher candidate."""
    expected = hashlib.sha256(sample.read_bytes()).hexdigest()
    module = load_candidate(path)
    run = getattr(module, "run", None)
    if not callable(run):
        return {"passed": False, "reason": "candidate has no run()"}
    actual = run(str(sample))
    if actual != expected:
        return {
            "passed": False,
            "reason": "candidate hash does not match hashlib",
            "expected": expected,
            "actual": actual,
        }
    return {"passed": True, "sha256": actual}


HASH_CANDIDATE_SOURCE = '''\
import hashlib
from pathlib import Path

def run(path: str) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()
'''

POOR_HASH_CANDIDATE_SOURCE = '''\
def run(path: str) -> str:
    return "not-a-hash"
'''


def acquire_hash_capability(
    database: FoundryDatabase,
    owned_root: Path,
    *,
    sample_bytes: bytes = b"jarvis-fabric-sample",
    source: str = HASH_CANDIDATE_SOURCE,
) -> dict[str, Any]:
    gap = specify_gap(
        database,
        needed_action="hash.file.sha256",
        missing=GapKind.SCRIPT,
        detail="no registered sha256 hasher",
    )
    candidate = write_candidate(owned_root, gap, source=source)
    sample = _owned(owned_root / CANDIDATE_ROOT / gap.gap_id / "sample.bin", Path(owned_root))
    sample.write_bytes(sample_bytes)
    proof = test_hash_candidate(candidate, sample)
    if not proof["passed"]:
        rejected = GapRecord(
            gap_id=gap.gap_id,
            needed_action=gap.needed_action,
            missing=gap.missing,
            status=GapStatus.REJECTED,
            detail=str(proof.get("reason") or "candidate failed focused test"),
            candidate_path=str(candidate),
        )
        record_gap(database, rejected)
        return {"status": "REJECTED", "gap": rejected.as_dict(), "proof": proof}

    spec = CapabilitySpec(
        capability_id="cap.hash.file.sha256",
        version="1.0.0",
        kind=Kind.SCRIPT,
        action_kind="hash.file.sha256",
        semantic=Semantic.READ,
        executor=str(candidate),
        unattended=UnattendedMode.ALLOWED,
        limitations=("owned-root files only",),
    )
    register_capability(database, spec, note="candidate passed focused hashlib test")
    registered = GapRecord(
        gap_id=gap.gap_id,
        needed_action=gap.needed_action,
        missing=gap.missing,
        status=GapStatus.REGISTERED,
        detail="script registered after focused test",
        candidate_path=str(candidate),
    )
    record_gap(database, registered)
    return {"status": "REGISTERED", "gap": registered.as_dict(), "spec": spec.as_dict(), "proof": proof}
