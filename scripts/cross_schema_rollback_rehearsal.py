"""Exact disposable v38 -> v40 -> v38 rollback rehearsal on Windows.

The harness copies the frozen predecessor package and database snapshot into a
bounded C:/temp root, builds the requested clean source SHA as an immutable
candidate, and runs the rollback controller from that candidate package. It
never retargets the production task or writes the live Foundry home.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence


PREDECESSOR_SHA = "faa861c67399d5a3755d778224ff65513bd933a1"
PREDECESSOR_TREE = "6d66b73a75a07a95360cfa2a9c7bbfa72821137b"
PREDECESSOR_MANIFEST_SHA256 = (
    "97f8f510e9f8a02eae8dd5d63586309bcdacaa0d4268e777ec23644e41894726"
)
PREDECESSOR_PACKAGE_SHA256 = (
    "f9445bd1af1add8f4a2e4d3332ab13a9d5f321fa52e82dae1d7f2c3eaffc0c36"
)
SNAPSHOT_SHA256 = (
    "d96e4f0d4351ed86bd466e6b51ef650adf9e4ddaeceab28321f88ad5fcba3794"
)
DEFAULT_PREDECESSOR_RELEASE = Path(
    r"C:\Users\b\AppData\Local\JARVIS\releases"
    rf"\{PREDECESSOR_SHA}"
)
DEFAULT_SNAPSHOT = Path(
    r"C:\temp\JARVIS-Prompt3-Live-Promotion-20260907"
    r"\master\db\live-v38-backup.db"
)
DEFAULT_OUTPUT_ROOT = Path(
    r"C:\temp\JARVIS-Cross-Schema-Rollback-Rehearsal-20260908"
)
LIVE_INSTALL_ROOT = Path(r"C:\Users\b\AppData\Local\JARVIS")
LIVE_FOUNDRY_HOME = Path(r"C:\Users\b\AppData\Local\hermes\foundry")
ROOT_MARKER = "REHEARSAL_ROOT.json"
REPORT_NAME = "REHEARSAL_REPORT.json"
RUNNER_RESULT_PREFIX = "JARVIS_ROLLBACK_RESULT="
RUNNER_INTERRUPTED_PREFIX = "JARVIS_ROLLBACK_INTERRUPTED="


ROLLBACK_RUNNER = r"""
import json
import sys

from jarvis_foundry.deployment.rollback_controller import (
    RollbackInterruption,
    RollbackRequest,
    run_cross_schema_rollback,
)

payload = json.loads(open(sys.argv[1], encoding="utf-8").read())
request = RollbackRequest(**payload)
fault_point = sys.argv[2]
triggered = False


def interrupt(point, state):
    global triggered
    if fault_point != "-" and point == fault_point and not triggered:
        triggered = True
        raise RollbackInterruption(point)


try:
    result = run_cross_schema_rollback(
        request,
        fault_injector=interrupt if fault_point != "-" else None,
    )
except RollbackInterruption as error:
    print(
        "JARVIS_ROLLBACK_INTERRUPTED="
        + json.dumps({"fault_point": str(error)}, sort_keys=True),
        flush=True,
    )
    raise SystemExit(85)
print("JARVIS_ROLLBACK_RESULT=" + json.dumps(result, sort_keys=True), flush=True)
"""


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _release_package_digest(path: Path) -> dict[str, Any]:
    root = path.resolve()
    digest = hashlib.sha256(b"jarvis.release-package.v1\0")
    file_count = 0
    total_bytes = 0
    for entry in sorted(
        root.rglob("*"),
        key=lambda item: item.relative_to(root).as_posix(),
    ):
        if entry.is_symlink():
            raise RuntimeError(
                f"predecessor package contains a symbolic link: {entry}")
        if entry.is_dir():
            continue
        if not entry.is_file():
            raise RuntimeError(
                f"predecessor package contains an unsupported entry: {entry}")
        relative = entry.relative_to(root).as_posix().encode("utf-8")
        size = entry.stat().st_size
        digest.update(len(relative).to_bytes(8, "big"))
        digest.update(relative)
        digest.update(int(size).to_bytes(8, "big"))
        digest.update(bytes.fromhex(_sha256(entry)))
        file_count += 1
        total_bytes += int(size)
    return {
        "contract": "jarvis.release-package.v1",
        "sha256": digest.hexdigest(),
        "file_count": file_count,
        "total_bytes": total_bytes,
    }


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(dict(payload), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(temporary, path)


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise RuntimeError(f"expected a JSON object: {path}")
    return value


def _run(
    command: Sequence[str | Path],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float = 300,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        [str(value) for value in command],
        cwd=cwd,
        env=dict(env) if env is not None else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if check and result.returncode != 0:
        detail = (result.stderr or result.stdout or "process failed").strip()
        raise RuntimeError(
            f"command failed ({result.returncode}): "
            f"{' '.join(str(value) for value in command)}\n{detail[-3000:]}"
        )
    return result


def _git(repo: Path, *args: str) -> str:
    return _run(["git", "-C", repo, *args], timeout=60).stdout.strip()


def _same_path(left: Path, right: Path) -> bool:
    return os.path.normcase(str(left.resolve())) == os.path.normcase(
        str(right.resolve())
    )


def _validate_output_root(root: Path) -> Path:
    resolved = root.expanduser().resolve()
    temp_root = Path(r"C:\temp").resolve()
    if not resolved.is_relative_to(temp_root):
        raise RuntimeError("rehearsal output must stay under C:\\temp")
    if not resolved.name.startswith("JARVIS-Cross-Schema-Rollback-Rehearsal-"):
        raise RuntimeError("rehearsal output root has an unexpected name")
    for protected in (LIVE_INSTALL_ROOT.resolve(), LIVE_FOUNDRY_HOME.resolve()):
        if (
            _same_path(resolved, protected)
            or resolved.is_relative_to(protected)
            or protected.is_relative_to(resolved)
        ):
            raise RuntimeError(f"rehearsal output overlaps protected path: {protected}")
    return resolved


def _assert_clean_source(repo: Path, source_sha: str) -> dict[str, str]:
    head = _git(repo, "rev-parse", "HEAD")
    if head != source_sha:
        raise RuntimeError(f"source HEAD {head} != requested SHA {source_sha}")
    dirty = _git(repo, "status", "--porcelain")
    tracked_dirty = [
        line for line in dirty.splitlines()
        if line and not line.startswith("??")
    ]
    if tracked_dirty:
        raise RuntimeError(
            "source worktree has tracked changes: " + repr(tracked_dirty)
        )
    tree = _git(repo, "rev-parse", f"{source_sha}^{{tree}}")
    if len(tree) != 40:
        raise RuntimeError(f"source tree is invalid: {tree}")
    return {"source_sha": source_sha, "source_tree": tree}


def _clean_candidate_env(
    *,
    install_root: Path,
    home: Path | None = None,
    release_sha: str | None = None,
) -> dict[str, str]:
    environment = dict(os.environ)
    for name in (
        "JARVIS_HOME",
        "JARVIS_INSTALL_ROOT",
        "JARVIS_RELEASE_ID",
        "JARVIS_RELEASE_SHA",
        "JARVIS_ROLLBACK_RUNTIME_MARKER",
        "JARVIS_ROLLBACK_HEARTBEAT_SECONDS",
        "PLAYWRIGHT_BROWSERS_PATH",
        "PYTHONHOME",
        "PYTHONPATH",
        "VIRTUAL_ENV",
    ):
        environment.pop(name, None)
    environment["JARVIS_INSTALL_ROOT"] = str(install_root)
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    if home is not None:
        environment["JARVIS_HOME"] = str(home)
    if release_sha is not None:
        environment["JARVIS_RELEASE_ID"] = release_sha
        environment["JARVIS_RELEASE_SHA"] = release_sha
    return environment


def _candidate_code(
    candidate_python: Path,
    code: str,
    *arguments: str | Path,
    install_root: Path,
    home: Path,
    timeout: float = 300,
) -> subprocess.CompletedProcess[str]:
    return _run(
        [candidate_python, "-I", "-c", code, *arguments],
        cwd=home,
        env=_clean_candidate_env(
            install_root=install_root,
            home=home,
        ),
        timeout=timeout,
    )


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return int(probe.getsockname()[1])


def _task_command(*arguments: str) -> subprocess.CompletedProcess[str]:
    return _run(
        ["schtasks", *arguments],
        timeout=30,
        check=False,
    )


def _task_exists(task_name: str) -> bool:
    return _task_command("/query", "/tn", task_name).returncode == 0


def _delete_created_task(task_name: str) -> dict[str, Any]:
    if not _task_exists(task_name):
        return {"task_name": task_name, "deleted": False, "reason": "absent"}
    _task_command("/change", "/tn", task_name, "/disable")
    _task_command("/end", "/tn", task_name)
    result = _task_command("/delete", "/tn", task_name, "/f")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"could not delete staging task {task_name}: {detail}")
    return {"task_name": task_name, "deleted": True}


def _read_pointer(path: Path) -> str | None:
    if not path.is_file():
        return None
    return path.read_text(encoding="utf-8").strip().splitlines()[0].strip()


def _live_boundary() -> dict[str, Any]:
    current = _read_pointer(LIVE_INSTALL_ROOT / "CURRENT")
    previous = _read_pointer(LIVE_INSTALL_ROOT / "PREVIOUS")
    manifest = (
        LIVE_INSTALL_ROOT / "releases" / str(current) / "RELEASE.json"
        if current else None
    )
    task = _task_command(
        "/query", "/tn", "JARVIS-Foundry-Supervisor", "/xml")
    schema = None
    database_authority = None
    live_database = LIVE_FOUNDRY_HOME / "foundry.db"
    if live_database.is_file():
        connection = sqlite3.connect(
            f"file:{live_database.as_posix()}?mode=ro",
            uri=True,
            timeout=10,
        )
        try:
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) FROM schema_migrations"
            ).fetchone()
            schema = int(row[0] if row else 0)
            row = connection.execute(
                "SELECT rm.source_commit "
                "FROM release_slots rs "
                "JOIN release_manifests rm ON rm.release_id = rs.release_id "
                "WHERE rs.slot = 'live'"
            ).fetchone()
            database_authority = str(row[0]) if row else None
        finally:
            connection.close()
    return {
        "captured_at": _now(),
        "current": current,
        "previous": previous,
        "current_manifest_sha256": (
            _sha256(manifest) if manifest and manifest.is_file() else None
        ),
        "task_xml_sha256": (
            hashlib.sha256(task.stdout.encode("utf-8")).hexdigest()
            if task.returncode == 0 else None
        ),
        "database_schema": schema,
        "database_authority_source_sha": database_authority,
    }


def _prepare_root(
    *,
    output_root: Path,
    source_repo: Path,
    source_sha: str,
    predecessor_release: Path,
    snapshot: Path,
    reset: bool,
) -> dict[str, Any]:
    marker_path = output_root / ROOT_MARKER
    if output_root.exists() and reset:
        shutil.rmtree(output_root)
    if output_root.exists():
        if not marker_path.is_file():
            raise RuntimeError(
                f"existing rehearsal root is incomplete; rerun with --reset: "
                f"{output_root}"
            )
        marker = _read_json(marker_path)
        expected = {
            "source_sha": source_sha,
            "predecessor_sha": PREDECESSOR_SHA,
            "snapshot_sha256": SNAPSHOT_SHA256,
        }
        for key, value in expected.items():
            if marker.get(key) != value:
                raise RuntimeError(
                    f"existing rehearsal root {key} does not match: "
                    f"{marker.get(key)!r} != {value!r}"
                )
        return marker

    source_identity = _assert_clean_source(source_repo, source_sha)
    predecessor_manifest = predecessor_release / "RELEASE.json"
    if not predecessor_manifest.is_file():
        raise RuntimeError(
            f"predecessor RELEASE.json missing: {predecessor_manifest}")
    if _sha256(predecessor_manifest) != PREDECESSOR_MANIFEST_SHA256:
        raise RuntimeError("predecessor RELEASE.json hash is not the frozen input")
    predecessor_package = _release_package_digest(predecessor_release)
    if predecessor_package["sha256"] != PREDECESSOR_PACKAGE_SHA256:
        raise RuntimeError(
            "predecessor package hash is not the frozen input")
    predecessor = _read_json(predecessor_manifest)
    if (
        predecessor.get("source_sha") != PREDECESSOR_SHA
        or predecessor.get("git_tree") != PREDECESSOR_TREE
        or predecessor.get("schema_compatibility", {}).get("package_schema") != 38
    ):
        raise RuntimeError("predecessor release identity is not exact")
    if not snapshot.is_file() or _sha256(snapshot) != SNAPSHOT_SHA256:
        raise RuntimeError("v38 rollback snapshot hash is not the frozen input")

    install_root = output_root / "install"
    input_root = output_root / "inputs"
    releases = install_root / "releases"
    releases.mkdir(parents=True)
    input_root.mkdir(parents=True)
    copied_snapshot = input_root / "predecessor-v38.db"
    shutil.copy2(snapshot, copied_snapshot)
    if _sha256(copied_snapshot) != SNAPSHOT_SHA256:
        raise RuntimeError("copied rollback snapshot hash changed")
    copied_predecessor = releases / PREDECESSOR_SHA
    shutil.copytree(predecessor_release, copied_predecessor)
    if _sha256(copied_predecessor / "RELEASE.json") != PREDECESSOR_MANIFEST_SHA256:
        raise RuntimeError("copied predecessor package identity changed")
    copied_package = _release_package_digest(copied_predecessor)
    if copied_package != predecessor_package:
        raise RuntimeError("copied predecessor package bytes changed")

    (install_root / "CURRENT").write_text(
        PREDECESSOR_SHA + "\n", encoding="utf-8")
    previous_runtime = predecessor.get("previous_runtime") or {}
    previous_sha = str(previous_runtime.get("source_sha") or "").strip()
    if len(previous_sha) < 40:
        raise RuntimeError("predecessor manifest has no exact previous runtime")
    (install_root / "PREVIOUS").write_text(
        previous_sha + "\n", encoding="utf-8")

    source_path = source_repo / "src"
    sys.path.insert(0, str(source_path))
    from jarvis_foundry.deployment.release import build_release

    imported = Path(
        __import__("jarvis_foundry").__file__).resolve()
    if not imported.is_relative_to(source_path.resolve()):
        raise RuntimeError(
            f"build orchestration imported jarvis_foundry from {imported}")
    build = build_release(
        source_repo=source_repo,
        source_sha=source_sha,
        root=install_root,
    )
    candidate_manifest = install_root / "releases" / source_sha / "RELEASE.json"
    candidate = _read_json(candidate_manifest)
    if (
        candidate.get("source_sha") != source_sha
        or candidate.get("git_tree") != source_identity["source_tree"]
        or candidate.get("schema_compatibility", {}).get("package_schema") != 40
        or candidate.get("previous_runtime", {}).get("source_sha")
        != PREDECESSOR_SHA
    ):
        raise RuntimeError("candidate immutable release identity is not exact")
    marker = {
        "contract": "jarvis.cross-schema-rollback-rehearsal-root.v1",
        "created_at": _now(),
        **source_identity,
        "source_repo": str(source_repo),
        "predecessor_sha": PREDECESSOR_SHA,
        "predecessor_tree": PREDECESSOR_TREE,
        "predecessor_manifest_sha256": PREDECESSOR_MANIFEST_SHA256,
        "predecessor_package": predecessor_package,
        "predecessor_package_sha256": PREDECESSOR_PACKAGE_SHA256,
        "snapshot_sha256": SNAPSHOT_SHA256,
        "snapshot_path": str(copied_snapshot),
        "install_root": str(install_root),
        "foundry_home": str(output_root / "home"),
        "candidate_release": build,
        "candidate_manifest_sha256": _sha256(candidate_manifest),
    }
    _write_json(marker_path, marker)
    return marker


def _request_record(
    *,
    transaction_id: str,
    install_root: Path,
    home: Path,
    snapshot: Path,
    source_sha: str,
    task_name: str,
) -> dict[str, Any]:
    return {
        "transaction_id": transaction_id,
        "install_root": str(install_root),
        "foundry_home": str(home),
        "snapshot_path": str(snapshot),
        "snapshot_sha256": SNAPSHOT_SHA256,
        "failed_source_sha": source_sha,
        "predecessor_source_sha": PREDECESSOR_SHA,
        "predecessor_manifest_sha256": PREDECESSOR_MANIFEST_SHA256,
        "predecessor_package_sha256": PREDECESSOR_PACKAGE_SHA256,
        "task_name": task_name,
        "health_timeout_seconds": 150.0,
    }


def _create_disabled_task(
    *,
    task_name: str,
    install_root: Path,
    home: Path,
    candidate_python: Path,
    port: int,
    request_record: Mapping[str, Any],
) -> dict[str, Any]:
    if _task_exists(task_name):
        raise RuntimeError(f"staging task already exists: {task_name}")
    from jarvis_foundry.database import FoundryDatabase
    from jarvis_foundry.deployment.layout import headless_task_command
    from jarvis_foundry.deployment.rollback_controller import (
        RollbackRequest,
        WindowsRollbackHost,
    )
    from jarvis_foundry.installer import Installer

    command, prefix = headless_task_command(install_root)
    installer = Installer(
        FoundryDatabase(home / "foundry.db"),
        task_name=task_name,
        foundry_home=home,
        hermes_home=home.parent / "hermes-unused",
        python_exe=candidate_python,
        exec_command=command,
        exec_arguments=(
            f'{prefix} --home "{home}" --interval 0.25 '
            f"--command-center-port {port}"
        ),
    )
    xml = installer._task_xml()
    settings = "<Settings>"
    if xml.count(settings) != 1:
        raise RuntimeError("staging task XML has an unexpected Settings shape")
    xml = xml.replace(
        settings,
        settings + "\n    <Enabled>false</Enabled>",
        1,
    )
    xml_path = home.parent / f".task-{os.getpid()}.xml"
    xml_path.parent.mkdir(parents=True, exist_ok=True)
    xml_path.write_text(xml, encoding="utf-16")
    created = False
    try:
        result = _task_command(
            "/create", "/tn", task_name, "/xml", str(xml_path), "/f")
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise RuntimeError(
                f"could not create disabled staging task: {detail}")
        created = True
        request = RollbackRequest(**dict(request_record))
        contract = WindowsRollbackHost().scheduler_contract(request)
        if contract.get("enabled") is not False:
            raise RuntimeError("staging Scheduler task was not born disabled")
        return {
            "task_name": task_name,
            "xml_sha256": hashlib.sha256(xml.encode("utf-8")).hexdigest(),
            "contract": contract,
        }
    except BaseException:
        if created:
            _delete_created_task(task_name)
        raise
    finally:
        xml_path.unlink(missing_ok=True)


def _disable_task(task_name: str) -> None:
    if not _task_exists(task_name):
        raise RuntimeError(f"staging task disappeared: {task_name}")
    result = _task_command("/change", "/tn", task_name, "/disable")
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise RuntimeError(f"could not disable staging task: {detail}")


def _terminate_staging_runtime(request_record: Mapping[str, Any]) -> dict[str, Any]:
    from jarvis_foundry.deployment.rollback_controller import (
        RollbackRequest,
        WindowsRollbackHost,
    )

    request = RollbackRequest(**dict(request_record))
    host = WindowsRollbackHost()
    processes = host.owned_processes(request)
    terminations: list[dict[str, Any]] = []
    if processes:
        terminations.append(
            host.terminate_owned_processes(request, processes))
    remaining = host.owned_processes(request)
    if remaining:
        raise RuntimeError(
            f"staging runtime cleanup left owned processes: {remaining}")
    return {
        "observed": processes,
        "terminations": terminations,
        "remaining": remaining,
    }


def _prepare_failed_activation(
    *,
    request_record: Mapping[str, Any],
    candidate_python: Path,
    source_sha: str,
    port: int,
) -> dict[str, Any]:
    from jarvis_foundry.deployment.release import (
        read_current,
        read_previous,
        release_authority_report,
    )
    from jarvis_foundry.deployment.rollback_controller import (
        RollbackRequest,
        WindowsRollbackHost,
        inspect_database,
    )

    request = RollbackRequest(**dict(request_record))
    _disable_task(request.task_name)
    cleanup = _terminate_staging_runtime(request_record)
    restore_code = (
        "import json,sys;"
        "from jarvis_foundry.database import FoundryDatabase;"
        "db=FoundryDatabase(sys.argv[1]);"
        "print(json.dumps({'restored_events':db.restore_from(sys.argv[2])}))"
    )
    restored = _candidate_code(
        candidate_python,
        restore_code,
        request.foundry_home / "foundry.db",
        request.snapshot_path,
        install_root=request.install_root,
        home=request.foundry_home,
        timeout=300,
    )
    migrate_code = (
        "import json,sys;"
        "from jarvis_foundry.database import FoundryDatabase;"
        "db=FoundryDatabase(sys.argv[1]);"
        "print(json.dumps({'schema':db.initialize()}))"
    )
    migrated = _candidate_code(
        candidate_python,
        migrate_code,
        request.foundry_home / "foundry.db",
        install_root=request.install_root,
        home=request.foundry_home,
        timeout=300,
    )
    activate_code = (
        "import json,sys;"
        "from pathlib import Path;"
        "from jarvis_foundry.database import FoundryDatabase;"
        "from jarvis_foundry.deployment.release import set_current;"
        "result=set_current(sys.argv[1],root=Path(sys.argv[2]),"
        "database=FoundryDatabase(Path(sys.argv[3])/'foundry.db'),"
        "reason='exact disposable cross-schema rehearsal activation');"
        "print(json.dumps(result,sort_keys=True))"
    )
    activated = _candidate_code(
        candidate_python,
        activate_code,
        source_sha,
        request.install_root,
        request.foundry_home,
        install_root=request.install_root,
        home=request.foundry_home,
        timeout=300,
    )
    stop_code = (
        "import json,sys;"
        "from pathlib import Path;"
        "from jarvis_foundry.supervisor.estop import EmergencyStop;"
        "print(json.dumps(EmergencyStop(Path(sys.argv[1])/'EMERGENCY_STOP')"
        ".activate(sys.argv[2],source='rollback_rehearsal'),sort_keys=True))"
    )
    stopped = _candidate_code(
        candidate_python,
        stop_code,
        request.foundry_home,
        f"cross-schema rehearsal freeze for {request.transaction_id}",
        install_root=request.install_root,
        home=request.foundry_home,
        timeout=60,
    )
    frozen_once = _run(
        [
            candidate_python,
            "-I",
            "-m",
            "jarvis_foundry.deployment.launcher",
            "--home",
            request.foundry_home,
            "--once",
            "--interval",
            "0.1",
            "--command-center-port",
            str(port),
        ],
        cwd=request.foundry_home,
        env=_clean_candidate_env(
            install_root=request.install_root,
            home=request.foundry_home,
            release_sha=source_sha,
        ),
        timeout=240,
    )
    deadline = time.monotonic() + 10
    host = WindowsRollbackHost()
    while time.monotonic() < deadline and host.owned_processes(request):
        time.sleep(0.1)
    processes = host.owned_processes(request)
    if processes:
        raise RuntimeError(
            f"candidate one-shot left owned processes: {processes}")
    scheduler = host.scheduler_contract(request)
    database = inspect_database(request.foundry_home / "foundry.db")
    authority = release_authority_report(
        root=request.install_root,
        foundry_home=request.foundry_home,
    )
    if (
        scheduler.get("enabled") is not False
        or read_current(request.install_root) != source_sha
        or read_previous(request.install_root) != PREDECESSOR_SHA
        or database["schema"] != 40
        or database["authority"].get("source_sha") != source_sha
        or database["effects"].get("status") != "CLEAR"
        or database["work"].get("status") != "CLEAR"
        or authority.get("status") != "CONSISTENT"
    ):
        raise RuntimeError("failed-candidate rehearsal state is not admissible")
    return {
        "cleanup_before_activation": cleanup,
        "restore_stdout": restored.stdout.strip(),
        "migration_stdout": migrated.stdout.strip(),
        "activation_stdout": activated.stdout.strip(),
        "stop_stdout": stopped.stdout.strip(),
        "candidate_frozen_once": {
            "returncode": frozen_once.returncode,
            "stdout": frozen_once.stdout.strip(),
            "stderr": frozen_once.stderr.strip(),
        },
        "scheduler": scheduler,
        "database": database,
        "release_authority": authority,
        "current": read_current(request.install_root),
        "previous": read_previous(request.install_root),
    }


def _extract_prefixed_json(output: str, prefix: str) -> dict[str, Any]:
    for line in reversed(output.splitlines()):
        if line.startswith(prefix):
            value = json.loads(line[len(prefix):])
            if not isinstance(value, dict):
                break
            return value
    raise RuntimeError(f"controller output omitted {prefix.rstrip('=')}")


def _run_controller(
    *,
    candidate_python: Path,
    request_path: Path,
    install_root: Path,
    home: Path,
    fault_point: str | None,
    stdout_path: Path,
    stderr_path: Path,
) -> dict[str, Any]:
    argument = fault_point or "-"
    result = _run(
        [
            candidate_python,
            "-I",
            "-c",
            ROLLBACK_RUNNER,
            request_path,
            argument,
        ],
        cwd=home,
        env=_clean_candidate_env(
            install_root=install_root,
            home=home,
        ),
        timeout=900,
        check=False,
    )
    stdout_path.write_text(result.stdout, encoding="utf-8")
    stderr_path.write_text(result.stderr, encoding="utf-8")
    if fault_point is not None:
        if result.returncode != 85:
            raise RuntimeError(
                f"fault {fault_point} did not interrupt exactly: "
                f"exit={result.returncode} stderr={result.stderr[-1000:]}"
            )
        return {
            "returncode": result.returncode,
            "interruption": _extract_prefixed_json(
                result.stdout, RUNNER_INTERRUPTED_PREFIX),
        }
    if result.returncode != 0:
        raise RuntimeError(
            f"rollback controller failed: exit={result.returncode} "
            f"stderr={result.stderr[-3000:]}"
        )
    return {
        "returncode": result.returncode,
        "report": _extract_prefixed_json(
            result.stdout, RUNNER_RESULT_PREFIX),
    }


def _observe_interruption(
    request_record: Mapping[str, Any],
) -> dict[str, Any]:
    from jarvis_foundry.deployment.rollback_controller import (
        RollbackRequest,
        WindowsRollbackHost,
        inspect_database,
    )
    from jarvis_foundry.supervisor.estop import EmergencyStop

    request = RollbackRequest(**dict(request_record))
    state_path = (
        request.install_root
        / "rollback-transactions"
        / request.transaction_id
        / "STATE.json"
    )
    return {
        "state": _read_json(state_path),
        "scheduler": WindowsRollbackHost().scheduler_contract(request),
        "processes": WindowsRollbackHost().owned_processes(request),
        "database": inspect_database(
            request.foundry_home / "foundry.db",
            include_logical_digest=False,
        ),
        "stop": EmergencyStop(
            request.foundry_home / "EMERGENCY_STOP").read(),
        "current": _read_pointer(request.install_root / "CURRENT"),
        "previous": _read_pointer(request.install_root / "PREVIOUS"),
    }


def _verify_completion(
    *,
    request_record: Mapping[str, Any],
    controller_report: Mapping[str, Any],
    failed_activation: Mapping[str, Any],
) -> dict[str, Any]:
    from jarvis_foundry.deployment.rollback_controller import (
        COMPLETE,
        RollbackRequest,
        WindowsRollbackHost,
        inspect_database,
    )
    from jarvis_foundry.supervisor.estop import EmergencyStop

    request = RollbackRequest(**dict(request_record))
    state_path = (
        request.install_root
        / "rollback-transactions"
        / request.transaction_id
        / "STATE.json"
    )
    state = _read_json(state_path)
    database = inspect_database(request.foundry_home / "foundry.db")
    host = WindowsRollbackHost()
    scheduler = host.scheduler_contract(request)
    process_samples: list[dict[str, Any]] = []
    stable_process_censuses = 0
    processes: list[dict[str, Any]] = []
    deadline = time.monotonic() + 10.0
    while time.monotonic() < deadline:
        processes = host.owned_processes(request)
        process_samples.append({
            "observed_at": _now(),
            "processes": processes,
        })
        exact = bool(
            len(processes) == 1
            and processes[0].get("kind") == "supervisor"
            and processes[0].get("release_sha") == PREDECESSOR_SHA
        )
        stable_process_censuses = (
            stable_process_censuses + 1 if exact else 0
        )
        if stable_process_censuses >= 3:
            break
        time.sleep(0.2)
    forensic = inspect_database(
        Path(state["evidence"]["forensic_database"]["path"]))
    marker = _read_json(request.install_root / "ROLLBACK_RUNTIME.json")
    restored = state["evidence"]["restored_database"]
    snapshot = state["evidence"]["snapshot"]
    failed_database = failed_activation["database"]
    checks = {
        "controller_status": controller_report.get("status") == "ROLLBACK_COMPLETE",
        "phase_complete": state.get("phase") == COMPLETE,
        "current_predecessor": (
            _read_pointer(request.install_root / "CURRENT")
            == PREDECESSOR_SHA
        ),
        "previous_failed_candidate": (
            _read_pointer(request.install_root / "PREVIOUS")
            == request.failed_source_sha
        ),
        "restored_schema_38": database["schema"] == 38,
        "restored_snapshot_logical_match": (
            restored["logical"]["sha256"] == snapshot["logical"]["sha256"]
        ),
        "snapshot_pinned_inside_transaction": (
            snapshot.get("owned_copy") is True
            and Path(snapshot["path"]).is_relative_to(state_path.parent)
            and _sha256(Path(snapshot["path"])) == SNAPSHOT_SHA256
        ),
        "effect_work_continuity_preserved": (
            snapshot.get("rollback_continuity", {}).get("status")
            == "PRESERVED"
        ),
        "predecessor_package_anchored": (
            state["evidence"]["predecessor_identity"]
            .get("manifest_sha256") == PREDECESSOR_MANIFEST_SHA256
            and state["evidence"]["predecessor_identity"]
            .get("package", {}).get("sha256")
            == PREDECESSOR_PACKAGE_SHA256
        ),
        "forensic_failed_logical_match": (
            forensic["logical"]["sha256"]
            == failed_database["logical"]["sha256"]
        ),
        "forensic_schema_40": forensic["schema"] == 40,
        "authority_predecessor": (
            database["authority"].get("source_sha") == PREDECESSOR_SHA
        ),
        "scheduler_enabled_after_health": scheduler.get("enabled") is True,
        "one_predecessor_supervisor": (
            stable_process_censuses >= 3
        ),
        "stop_retained": EmergencyStop(
            request.foundry_home / "EMERGENCY_STOP").active(),
        "effects_clear": database["effects"].get("status") == "CLEAR",
        "work_clear": database["work"].get("status") == "CLEAR",
        "health_accepted": (
            state["evidence"]["final_health"].get("accepted") is True
        ),
        "trust_suspended": controller_report.get("trust_state") == "SUSPENDED",
        "runtime_bridge_complete": (
            marker.get("status") == "COMPLETE_FROZEN_RUNTIME"
            and marker.get("source_sha") == PREDECESSOR_SHA
        ),
        "active_transaction_cleared": not (
            request.install_root
            / "rollback-transactions"
            / "ACTIVE.json"
        ).exists(),
    }
    if not all(checks.values()):
        failed = [name for name, passed in checks.items() if not passed]
        raise RuntimeError(
            "completed rollback failed evidence checks: " + ", ".join(failed))
    return {
        "checks": checks,
        "state_path": str(state_path),
        "state_sha256": _sha256(state_path),
        "database": database,
        "scheduler": scheduler,
        "processes": processes,
        "process_samples": process_samples[-25:],
        "stable_process_censuses": stable_process_censuses,
        "forensic_database": forensic,
        "runtime_bridge": marker,
        "health": state["evidence"]["final_health"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the exact disposable cross-schema rollback rehearsal")
    parser.add_argument("--source-repo", type=Path, required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument(
        "--predecessor-release",
        type=Path,
        default=DEFAULT_PREDECESSOR_RELEASE,
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=DEFAULT_SNAPSHOT,
    )
    parser.add_argument(
        "--output-root",
        type=Path,
        default=DEFAULT_OUTPUT_ROOT,
    )
    parser.add_argument(
        "--faults",
        choices=("all", "none"),
        default="all",
        help="run all nine hard-interruption points after the baseline",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="delete only the validated disposable output root before preparing",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if os.name != "nt":
        raise SystemExit("BLOCKED: exact rehearsal requires Windows")
    source_repo = args.source_repo.expanduser().resolve()
    source_sha = str(args.source_sha).strip().lower()
    output_root = _validate_output_root(args.output_root)
    predecessor_release = args.predecessor_release.expanduser().resolve()
    snapshot = args.snapshot.expanduser().resolve()
    live_before = _live_boundary()
    report_path = output_root / "evidence" / REPORT_NAME
    report: dict[str, Any] = {
        "contract": "jarvis.cross-schema-rollback-rehearsal.v1",
        "started_at": _now(),
        "status": "RUNNING",
        "source_repo": str(source_repo),
        "source_sha": source_sha,
        "predecessor_sha": PREDECESSOR_SHA,
        "live_before": live_before,
        "live_mutated": False,
        "scenarios": {},
    }
    task_name = (
        f"JF-CrossSchema-Rollback-20260908-"
        f"{source_sha[:8]}-{os.getpid()}"
    )
    created_task = False
    latest_request: dict[str, Any] | None = None
    try:
        root = _prepare_root(
            output_root=output_root,
            source_repo=source_repo,
            source_sha=source_sha,
            predecessor_release=predecessor_release,
            snapshot=snapshot,
            reset=bool(args.reset),
        )
        install_root = Path(root["install_root"]).resolve()
        home = Path(root["foundry_home"]).resolve()
        home.mkdir(parents=True, exist_ok=True)
        copied_snapshot = Path(root["snapshot_path"]).resolve()
        candidate_python = (
            install_root
            / "releases"
            / source_sha
            / ".venv"
            / "Scripts"
            / "python.exe"
        )
        if not candidate_python.is_file():
            raise RuntimeError(f"candidate Python missing: {candidate_python}")
        port = _free_port()
        bootstrap_request = _request_record(
            transaction_id="bootstrap",
            install_root=install_root,
            home=home,
            snapshot=copied_snapshot,
            source_sha=source_sha,
            task_name=task_name,
        )
        report["root"] = root
        report["port"] = port
        report["task"] = _create_disabled_task(
            task_name=task_name,
            install_root=install_root,
            home=home,
            candidate_python=candidate_python,
            port=port,
            request_record=bootstrap_request,
        )
        created_task = True
        from jarvis_foundry.deployment.rollback_controller import FAULT_POINTS

        scenarios: list[tuple[str, str | None]] = [("baseline", None)]
        if args.faults == "all":
            scenarios.extend(
                (f"{index:02d}-{point}", point)
                for index, point in enumerate(FAULT_POINTS, start=1)
            )
        for index, (name, fault_point) in enumerate(scenarios):
            transaction_id = (
                f"real-{index:02d}-"
                + (fault_point or "baseline")
            )
            request_record = _request_record(
                transaction_id=transaction_id,
                install_root=install_root,
                home=home,
                snapshot=copied_snapshot,
                source_sha=source_sha,
                task_name=task_name,
            )
            latest_request = request_record
            scenario_dir = output_root / "evidence" / "scenarios" / name
            scenario_dir.mkdir(parents=True, exist_ok=True)
            request_path = scenario_dir / "request.json"
            _write_json(request_path, request_record)
            scenario: dict[str, Any] = {
                "name": name,
                "fault_point": fault_point,
                "started_at": _now(),
                "request_path": str(request_path),
            }
            report["scenarios"][name] = scenario
            _write_json(report_path, report)
            failed_activation = _prepare_failed_activation(
                request_record=request_record,
                candidate_python=candidate_python,
                source_sha=source_sha,
                port=port,
            )
            scenario["failed_activation"] = failed_activation
            if fault_point is not None:
                scenario["injected_run"] = _run_controller(
                    candidate_python=candidate_python,
                    request_path=request_path,
                    install_root=install_root,
                    home=home,
                    fault_point=fault_point,
                    stdout_path=scenario_dir / "interrupted.stdout.log",
                    stderr_path=scenario_dir / "interrupted.stderr.log",
                )
                scenario["interruption_observation"] = _observe_interruption(
                    request_record)
            completed = _run_controller(
                candidate_python=candidate_python,
                request_path=request_path,
                install_root=install_root,
                home=home,
                fault_point=None,
                stdout_path=scenario_dir / "completed.stdout.log",
                stderr_path=scenario_dir / "completed.stderr.log",
            )
            controller_report = completed["report"]
            scenario["completed_run"] = completed
            scenario["verification"] = _verify_completion(
                request_record=request_record,
                controller_report=controller_report,
                failed_activation=failed_activation,
            )
            scenario["cleanup"] = {
                "scheduler_disabled": True,
            }
            _disable_task(task_name)
            scenario["cleanup"]["runtime"] = _terminate_staging_runtime(
                request_record)
            scenario["status"] = "PASS"
            scenario["completed_at"] = _now()
            _write_json(report_path, report)

        report["final_cleanup"] = {
            "task": _delete_created_task(task_name),
        }
        created_task = False
        live_after = _live_boundary()
        stable_live_fields = (
            "current",
            "previous",
            "current_manifest_sha256",
            "task_xml_sha256",
            "database_schema",
            "database_authority_source_sha",
        )
        live_unchanged = all(
            live_before.get(field) == live_after.get(field)
            for field in stable_live_fields
        )
        if not live_unchanged:
            raise RuntimeError(
                "live boundary changed during rehearsal; no live write was requested")
        report["live_after"] = live_after
        report["live_mutated"] = False
        report["status"] = "PASS"
        report["completed_at"] = _now()
        _write_json(report_path, report)
        print(json.dumps({
            "status": "PASS",
            "report": str(report_path),
            "scenarios": len(report["scenarios"]),
            "live_mutated": False,
        }, indent=2, sort_keys=True))
        return 0
    except BaseException as error:
        report["status"] = "FAIL"
        report["failed_at"] = _now()
        report["error_class"] = type(error).__name__
        report["error"] = str(error)[:4000]
        if output_root.exists():
            _write_json(report_path, report)
        raise
    finally:
        cleanup: dict[str, Any] = {}
        if created_task:
            try:
                _disable_task(task_name)
            except Exception as error:
                cleanup["disable_error"] = (
                    f"{type(error).__name__}: {error}")
            if latest_request is not None:
                try:
                    cleanup["runtime"] = _terminate_staging_runtime(
                        latest_request)
                except Exception as error:
                    cleanup["runtime_error"] = (
                        f"{type(error).__name__}: {error}")
            try:
                cleanup["task"] = _delete_created_task(task_name)
            except Exception as error:
                cleanup["task_error"] = (
                    f"{type(error).__name__}: {error}")
        if cleanup:
            report["final_cleanup"] = cleanup
            if report.get("status") == "PASS":
                report["status"] = "FAIL"
                report["error_class"] = "CleanupError"
                report["error"] = "final staging cleanup was incomplete"
        elif "final_cleanup" not in report:
            report["final_cleanup"] = {}
        if output_root.exists():
            _write_json(report_path, report)


if __name__ == "__main__":
    raise SystemExit(main())
