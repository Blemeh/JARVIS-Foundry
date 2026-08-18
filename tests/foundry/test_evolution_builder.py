"""V6-S3: isolated candidate builder.

Candidates build ONLY inside registered, canonical candidate roots with
their own runtime homes. The build environment is sanitized (no
HERMES_HOME/JARVIS_HOME/credential variables), JARVIS_HOME is scoped
inside the root, build commands are secret-scanned at the boundary,
roots are locked per-build with stale-marker reaping, outside-root
writes are detected and fail the build, and the release pointer tables
are unreachable from the candidate process. Partial builds, crashes,
timeouts, concurrency and cross-candidate collisions are handled
deterministically.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.evolution.builder import (
    BuilderError,
    build_candidate,
    candidate_builds,
    register_candidate_root,
    registered_roots,
)
from jarvis_foundry.evolution.records import record_improvement
from jarvis_foundry.migrations import SCHEMA_VERSION
from jarvis_foundry.reconcile import reconcile

PY = sys.executable


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    # the DB lives in its own runtime home so candidate roots can be
    # SIBLINGS of it (roots inside the runtime home are refused)
    runtime_home = tmp_path / "runtime"
    runtime_home.mkdir(exist_ok=True)
    database = FoundryDatabase(runtime_home / "foundry.db")
    database.initialize()
    return database


def _improvement(db: FoundryDatabase) -> str:
    return record_improvement(
        db,
        problem="suite runtime exceeds the 5-minute budget on this host",
        evidence_refs=["var/evidence/v6-s0/suite-v6-s0-baseline.txt"],
        provenance={"source": "v6-s0 baseline measurement",
                    "observed_at": "2026-08-13T15:00:00Z"},
        hypothesis="Splitting fault-injection tests reduces wall-clock "
                   "runtime",
        expected_mechanism="parallel session isolation removes serialized "
                           "daemon-spawn waits",
        baseline_metric={"name": "suite_runtime_s", "value": 313.92,
                         "unit": "seconds",
                         "source": "suite-v6-s0-baseline.txt"},
        mmi={"minimum_delta": 30.0, "direction": "decrease",
             "unit": "seconds"},
        guardrails={"correctness": "suite green", "safety": "no live "
                    "mutation", "cost": "no paid calls",
                    "latency": "no regression"},
        workload={"ref": "tests/foundry",
                  "hash": "139f26833c9690b521460664f6ab9de0f0cbdbb61b9d547850004bf4c44dafef"},
        observation={"requirement": "3 full-suite runs", "cycles": 3},
        declared_risk="R2",
        surfaces=["tests/foundry", "pyproject.toml"],
        budget={"max_cost_usd": 0.0, "capability_scope": ["pytest"]},
        reversibility={"plan": "git revert", "rollback_threshold": "5m"},
    )


def _write_script(root: Path, name: str, body: str) -> Path:
    path = root / name
    path.write_text(body, encoding="utf-8")
    return path


_OK_SCRIPT = (
    "import json, pathlib\n"
    "pathlib.Path('out.txt').write_text('built ok')\n"
    "print('BUILD_OK')\n"
)


# ---------------------------------------------------------------------------
# root registration
# ---------------------------------------------------------------------------

def test_register_root_canonical_and_idempotent(db: FoundryDatabase,
                                                tmp_path: Path) -> None:
    root = tmp_path / "candidate-a"
    register_candidate_root(db, root, controller="supervisor")
    assert root.resolve() in registered_roots(db)
    register_candidate_root(db, root, controller="supervisor")
    roots = registered_roots(db)
    assert len(roots) == 1
    events = [e for e in db.events()
              if e.event_type == "candidate_root.registered"]
    assert len(events) == 1


def test_register_root_inside_runtime_home_refused(
        db: FoundryDatabase, tmp_path: Path) -> None:
    # the runtime home itself (where the DB lives) is a live-like
    # surface: registering it or anything inside it is refused
    with pytest.raises(BuilderError):
        register_candidate_root(db, tmp_path / "runtime",
                                controller="supervisor")
    with pytest.raises(BuilderError):
        register_candidate_root(db, tmp_path / "runtime" / "nested",
                                controller="supervisor")


def test_build_requires_registered_root(db: FoundryDatabase,
                                        tmp_path: Path) -> None:
    root = tmp_path / "unregistered"
    root.mkdir()
    with pytest.raises(BuilderError):
        build_candidate(db, improvement_id=_improvement(db),
                        build_command=[PY, "-c", "pass"],
                        candidate_root=root, controller="supervisor")


def test_build_requires_known_improvement(db: FoundryDatabase,
                                          tmp_path: Path) -> None:
    root = tmp_path / "candidate-a"
    register_candidate_root(db, root, controller="supervisor")
    with pytest.raises(BuilderError):
        build_candidate(db, improvement_id="improvement-nope",
                        build_command=[PY, "-c", "pass"],
                        candidate_root=root, controller="supervisor")


def test_secret_bearing_command_refused(db: FoundryDatabase,
                                        tmp_path: Path) -> None:
    root = tmp_path / "candidate-a"
    register_candidate_root(db, root, controller="supervisor")
    with pytest.raises(BuilderError):
        build_candidate(db, improvement_id=_improvement(db),
                        build_command=[PY, "-c",
                                       "print('api_key=sk-live-9999')"],
                        candidate_root=root, controller="supervisor")
    # root registration + improvement record only — zero build events
    assert len(db.events()) == 2
    assert not any(e.event_type.startswith("candidate.build")
                   for e in db.events())


# ---------------------------------------------------------------------------
# build lifecycle
# ---------------------------------------------------------------------------

def test_successful_build_records_artifacts(db: FoundryDatabase,
                                            tmp_path: Path) -> None:
    root = tmp_path / "candidate-a"
    register_candidate_root(db, root, controller="supervisor")
    script = _write_script(root, "build.py", _OK_SCRIPT)
    improvement_id = _improvement(db)
    build_id = build_candidate(db, improvement_id=improvement_id,
                               build_command=[PY, str(script)],
                               candidate_root=root,
                               controller="supervisor")
    row = db.row("candidate_builds", "build_id", build_id)
    assert row is not None
    assert row["status"] == "COMPLETED"
    assert row["exit_code"] == 0
    artifacts = json.loads(row["artifact_hashes_json"])
    assert "out.txt" in artifacts
    assert len(artifacts) >= 2  # out.txt + build.py
    types = {e.event_type for e in db.events()}
    assert "candidate.build_started" in types
    assert "candidate.build_completed" in types
    assert "candidate.build_failed" not in types


def test_failed_build_records_exit_code(db: FoundryDatabase,
                                        tmp_path: Path) -> None:
    root = tmp_path / "candidate-a"
    register_candidate_root(db, root, controller="supervisor")
    script = _write_script(root, "bad.py",
                           "import sys\nsys.exit(2)\n")
    build_id = build_candidate(db, improvement_id=_improvement(db),
                               build_command=[PY, str(script)],
                               candidate_root=root,
                               controller="supervisor")
    row = db.row("candidate_builds", "build_id", build_id)
    assert row is not None
    assert row["status"] == "FAILED"
    assert row["exit_code"] == 2
    types = {e.event_type for e in db.events()}
    assert "candidate.build_failed" in types


def test_timeout_fails_build(db: FoundryDatabase, tmp_path: Path) -> None:
    root = tmp_path / "candidate-a"
    register_candidate_root(db, root, controller="supervisor")
    script = _write_script(root, "slow.py",
                           "import time\ntime.sleep(30)\n")
    build_id = build_candidate(db, improvement_id=_improvement(db),
                               build_command=[PY, str(script)],
                               candidate_root=root, timeout_s=1,
                               controller="supervisor")
    row = db.row("candidate_builds", "build_id", build_id)
    assert row is not None
    assert row["status"] == "FAILED"
    assert "timeout" in (row["reason"] or "")


# ---------------------------------------------------------------------------
# isolation
# ---------------------------------------------------------------------------

def test_build_environment_sanitized(db: FoundryDatabase,
                                     tmp_path: Path) -> None:
    root = tmp_path / "candidate-a"
    register_candidate_root(db, root, controller="supervisor")
    os.environ["HERMES_HOME"] = "C:/should/not/leak"
    os.environ["JARVIS_HOME"] = "C:/should/not/leak"
    os.environ["TEST_API_KEY"] = "sk-live-should-not-leak"
    script = _write_script(
        root, "env.py",
        "import json, os\n"
        "json.dump(dict(os.environ), open('env.json','w'))\n")
    build_candidate(db, improvement_id=_improvement(db),
                    build_command=[PY, str(script)],
                    candidate_root=root, controller="supervisor")
    env = json.loads((root / "env.json").read_text(encoding="utf-8"))
    assert "HERMES_HOME" not in env
    assert "TEST_API_KEY" not in env
    # the candidate runtime home is scoped INSIDE the root
    assert str(root.resolve()) in env.get("JARVIS_HOME", "")
    assert "runtime-home" in env.get("JARVIS_HOME", "")


def test_outside_write_detected_and_fails_build(db: FoundryDatabase,
                                                tmp_path: Path) -> None:
    root = tmp_path / "candidate-a"
    register_candidate_root(db, root, controller="supervisor")
    script = _write_script(
        root, "escape.py",
        "import pathlib\n"
        f"pathlib.Path(r'{tmp_path.as_posix()}/outside.txt').write_text('x')\n")
    build_id = build_candidate(db, improvement_id=_improvement(db),
                               build_command=[PY, str(script)],
                               candidate_root=root,
                               controller="supervisor")
    row = db.row("candidate_builds", "build_id", build_id)
    assert row is not None
    assert row["status"] == "FAILED"
    assert "outside" in (row["reason"] or "")


def test_release_pointers_unreachable_from_candidate(
        db: FoundryDatabase, tmp_path: Path) -> None:
    """The candidate process never receives the runtime DB path or any
    release-slot surface: its whole runtime home lives inside the root
    and the environment is stripped of foundry paths."""
    root = tmp_path / "candidate-a"
    register_candidate_root(db, root, controller="supervisor")
    script = _write_script(
        root, "probe.py",
        "import json, os\n"
        "json.dump({'cwd': os.getcwd(), 'home': os.environ.get('JARVIS_HOME', '')}, open('probe.json','w'))\n")
    build_candidate(db, improvement_id=_improvement(db),
                    build_command=[PY, str(script)],
                    candidate_root=root, controller="supervisor")
    probe = json.loads((root / "probe.json").read_text(encoding="utf-8"))
    assert str(root.resolve()) in probe["cwd"]
    assert str(root.resolve()) in probe["home"]
    assert "foundry.db" not in probe["home"]


def test_stale_marker_reaped_then_build_proceeds(
        db: FoundryDatabase, tmp_path: Path) -> None:
    root = tmp_path / "candidate-a"
    register_candidate_root(db, root, controller="supervisor")
    # plant a stale marker (dead pid) simulating a crashed builder
    marker = root / ".candidate-build.json"
    marker.write_text(json.dumps(
        {"build_id": "build-crashed-0001", "pid": 999999999}),
        encoding="utf-8")
    script = _write_script(root, "build.py", _OK_SCRIPT)
    build_id = build_candidate(db, improvement_id=_improvement(db),
                               build_command=[PY, str(script)],
                               candidate_root=root,
                               controller="supervisor")
    assert build_id != "build-crashed-0001"
    reap = [e for e in db.events()
            if e.event_type == "candidate.build_reaped"]
    assert len(reap) == 1
    assert reap[0].payload["build_id"] == "build-crashed-0001"
    row = db.row("candidate_builds", "build_id", "build-crashed-0001")
    assert row is not None and row["status"] == "REAPED"
    assert db.row("candidate_builds", "build_id", build_id)["status"] == \
        "COMPLETED"


def test_live_marker_refuses_concurrent_build(
        db: FoundryDatabase, tmp_path: Path) -> None:
    root = tmp_path / "candidate-a"
    register_candidate_root(db, root, controller="supervisor")
    marker = root / ".candidate-build.json"
    marker.write_text(json.dumps(
        {"build_id": "build-running-0001", "pid": os.getpid()}),
        encoding="utf-8")
    with pytest.raises(BuilderError):
        build_candidate(db, improvement_id=_improvement(db),
                        build_command=[PY, "-c", "pass"],
                        candidate_root=root, controller="supervisor")


def test_parallel_builds_on_distinct_roots_isolated(
        db: FoundryDatabase, tmp_path: Path) -> None:
    import threading
    roots = []
    for index in range(2):
        root = tmp_path / f"candidate-{index}"
        register_candidate_root(db, root, controller="supervisor")
        _write_script(root, "build.py",
                      f"import pathlib\n"
                      f"pathlib.Path('tag.txt').write_text('tag-{index}')\n")
        roots.append(root)
    improvement_id = _improvement(db)
    results: list[str] = []
    lock = threading.Lock()

    def run(root: Path, index: int) -> None:
        build_id = build_candidate(
            db, improvement_id=improvement_id,
            build_command=[PY, str(root / "build.py")],
            candidate_root=root, controller="supervisor")
        with lock:
            results.append(f"{index}:{build_id}")

    threads = [threading.Thread(target=run, args=(roots[0], 0)),
               threading.Thread(target=run, args=(roots[1], 1))]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(results) == 2
    builds = candidate_builds(db, improvement_id)
    assert len(builds) == 2
    for build in builds:
        assert build["status"] == "COMPLETED"
    assert (roots[0] / "tag.txt").read_text() == "tag-0"
    assert (roots[1] / "tag.txt").read_text() == "tag-1"


# ---------------------------------------------------------------------------
# projection integrity
# ---------------------------------------------------------------------------

def test_projection_and_schema(db: FoundryDatabase, tmp_path: Path) -> None:
    root = tmp_path / "candidate-a"
    register_candidate_root(db, root, controller="supervisor")
    script = _write_script(root, "build.py", _OK_SCRIPT)
    build_candidate(db, improvement_id=_improvement(db),
                    build_command=[PY, str(script)],
                    candidate_root=root, controller="supervisor")
    report = reconcile(db)
    assert report.consistent is True
    before = db.count("candidate_builds")
    db.rebuild_projections()
    assert db.count("candidate_builds") == before
    with db.connect() as connection:
        version = connection.execute(
            "SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    assert version == SCHEMA_VERSION  # pinned to the constant, >= 23
    assert SCHEMA_VERSION >= 23


# ---------------------------------------------------------------------------
# round-2 repairs (deleg_df8957c2 findings)
# ---------------------------------------------------------------------------

def test_register_ancestor_of_runtime_home_refused(
        db: FoundryDatabase, tmp_path: Path) -> None:
    """S3 r2: a root that CONTAINS the runtime home is refused (its
    subtree would include the DB — builds could write beside it)."""
    with pytest.raises(BuilderError):
        register_candidate_root(db, tmp_path, controller="supervisor")


def test_decoy_db_file_in_parent_detected(
        db: FoundryDatabase, tmp_path: Path) -> None:
    """S3 r2: the outside-write exclusion is the EXACT runtime-DB
    family — a decoy foundry.db or foundry.db.backup written by the
    candidate is a new parent entry and fails the build."""
    root = tmp_path / "candidate-a"
    register_candidate_root(db, root, controller="supervisor")
    for decoy in ("foundry.db", "foundry.db.backup"):
        clean = tmp_path / f"candidate-{len(decoy)}"
        register_candidate_root(db, clean, controller="supervisor")
        script = _write_script(
            clean, "decoy.py",
            "import pathlib\n"
            f"pathlib.Path(r'{tmp_path.as_posix()}/{decoy}').write_text('x')\n")
        build_id = build_candidate(
            db, improvement_id=_improvement(db),
            build_command=[PY, str(script)],
            candidate_root=clean, controller="supervisor")
        row = db.row("candidate_builds", "build_id", build_id)
        assert row is not None and row["status"] == "FAILED"
        assert "outside write" in (row["reason"] or "")


def test_symlink_content_not_hashed_as_artifact(
        db: FoundryDatabase, tmp_path: Path) -> None:
    """S3 r2: symlinks/junctions inside the root are never hashed as
    artifacts (their content lives outside the root)."""
    root = tmp_path / "candidate-a"
    register_candidate_root(db, root, controller="supervisor")
    outside_dir = tmp_path / "outside-content"
    outside_dir.mkdir()
    (outside_dir / "evil.txt").write_text("secret outside bytes")
    link = root / "link"
    try:
        os.system(f'cmd /c mklink /J "{link}" "{outside_dir}" >nul 2>&1')
    except Exception:  # noqa: BLE001 — junction may be unavailable
        pytest.skip("junctions unavailable on this host")
    if not link.exists():
        pytest.skip("junction creation failed")
    script = _write_script(root, "build.py", _OK_SCRIPT)
    build_id = build_candidate(db, improvement_id=_improvement(db),
                               build_command=[PY, str(script)],
                               candidate_root=root,
                               controller="supervisor")
    row = db.row("candidate_builds", "build_id", build_id)
    assert row is not None and row["status"] == "COMPLETED"
    artifacts = json.loads(row["artifact_hashes_json"])
    assert not any(name.startswith("link/") for name in artifacts)
    assert "link" not in artifacts
    assert "out.txt" in artifacts
