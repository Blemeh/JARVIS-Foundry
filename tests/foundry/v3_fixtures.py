"""V3 WP0 fixtures: scoring policy, v34 baseline database generator,
disposable bare git remote."""
from __future__ import annotations

import json
import subprocess
from functools import lru_cache
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "v3"
POLICY_PATH = FIXTURES / "policy_v3-policy-2026-08-21.json"
POLICY_VERSION = "v3-policy-2026-08-21"

# Canonical main at a743fde carries SCHEMA_VERSION = 34; the V2 candidate
# that would have claimed v35 never merged (owner waived the wait), so the
# V3 additive migration lands at 35. One constant, read by tests and code.
CANONICAL_V2_SCHEMA_VERSION = 34
V3_SCHEMA_VERSION = CANONICAL_V2_SCHEMA_VERSION + 1


def load_policy() -> dict:
    policy = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
    if policy["policy_version"] != POLICY_VERSION:
        raise ValueError("policy fixture version mismatch")
    return policy


def make_baseline_db(path: Path) -> Path:
    """Create a canonical-baseline (pre-V3) database at ``path``.

    Deterministic content: two missions and one scored candidate so
    migration/rebuild tests exercise non-empty projections.
    """
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
    from jarvis_foundry.database import FoundryDatabase
    from jarvis_foundry.events import Event

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    db = FoundryDatabase(path)
    db.initialize(target=CANONICAL_V2_SCHEMA_VERSION)
    db.append(Event.create(
        "mission.created",
        {"mission_id": "mission-v3base-a", "objective": "Fix the failing nightly integration",
         "resource_id": None, "state": "PLANNED",
         "acceptance_criteria": ["nightly green"],
         "created_from": None},
        source_type="supervisor", source_id="v3-fixture",
        idempotency_key="fixture:mission:a"))
    db.append(Event.create(
        "mission.created",
        {"mission_id": "mission-v3base-b", "objective": "Investigate unknown root cause of outage",
         "resource_id": None, "state": "COMPLETED",
         "acceptance_criteria": ["diagnosis recorded"],
         "created_from": None},
        source_type="supervisor", source_id="v3-fixture",
        idempotency_key="fixture:mission:b"))
    db.append(Event.create(
        "discovery.candidate_generated",
        {"candidate_id": "cand-v3base-1", "mission_id": "mission-v3base-a",
         "source": "maintenance", "objective": "Fix the failing nightly integration",
         "evidence": [], "duplicate_of": None},
        source_type="supervisor", source_id="v3-fixture",
        idempotency_key="fixture:cand:1"))
    db.append(Event.create(
        "discovery.candidate_scored",
        {"candidate_id": "cand-v3base-1", "score": 0.62, "weights": {}},
        source_type="supervisor", source_id="v3-fixture",
        idempotency_key="fixture:score:1"))
    return path


@lru_cache(maxsize=None)
def make_bare_remote(base_dir: Path) -> Path:
    """Create a disposable bare remote with one seed commit; return its path.

    Cached per directory: tests that push to it must observe each other's
    refs exactly as a shared real remote would.
    """
    base_dir = Path(base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    work = base_dir / "seed-worktree"
    bare = base_dir / "origin.git"
    if not work.exists():
        subprocess.run(["git", "init", "-q", "-b", "main", str(work)], check=True)
        subprocess.run(["git", "-C", str(work), "config", "user.email", "v3-fixture@local"], check=True)
        subprocess.run(["git", "-C", str(work), "config", "user.name", "v3-fixture"], check=True)
        (work / "README.md").write_text("v3 fixture seed\n", encoding="utf-8")
        subprocess.run(["git", "-C", str(work), "add", "README.md"], check=True)
        subprocess.run(["git", "-C", str(work), "commit", "-q", "-m", "seed"], check=True)
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(bare)], check=True)
        subprocess.run(["git", "-C", str(work), "push", "-q", str(bare), "main"], check=True)
    return bare


def ls_remote(remote: Path, ref: str = "main") -> str | None:
    out = subprocess.run(
        ["git", "ls-remote", str(remote), ref],
        capture_output=True, text=True, check=True)
    value = out.stdout.split()
    return value[0] if value else None
