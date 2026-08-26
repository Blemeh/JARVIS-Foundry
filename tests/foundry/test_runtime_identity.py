from __future__ import annotations

from jarvis_foundry.deployment.runtime_identity import (
    _collapse_interpreter_wrappers,
    normalize_process,
    parity_state,
    process_matches_home,
    release_sha_for_id,
    supervisor_processes,
)


def _record(
    pid: int,
    *,
    parent_pid: int,
    executable: str,
    create_time: int,
    home: str = r"C:\runtime\foundry",
    module: str = "jarvis_foundry.deployment.headless_launcher",
) -> dict:
    return {
        "pid": pid,
        "parent_pid": parent_pid,
        "module": module,
        "executable": executable,
        "command_line": (
            f'"{executable}" -m {module} --home "{home}"'),
        "release_sha": "a" * 40,
        "identity_key": f"{pid}:{module}",
        "creation_identity": {
            "pid": pid, "create_time": create_time, "exe": executable,
        },
        "identity_status": "VALID",
        "live": True,
    }


def test_headless_launcher_is_canonical_and_arbitrary_text_is_not():
    assert normalize_process({
        "ProcessId": 42,
        "ParentProcessId": 1,
        "ExecutablePath": r"C:\Python313\python.exe",
        "CommandLine": (
            r'"C:\Python313\python.exe" -m '
            r"jarvis_foundry.deployment.headless_launcher --home C:\runtime\foundry"),
    })["module"] == "jarvis_foundry.deployment.headless_launcher"
    assert normalize_process({
        "ProcessId": 43,
        "ParentProcessId": 1,
        "ExecutablePath": r"C:\Python313\python.exe",
        "CommandLine": r'python -c "print(\'jarvis_foundry.supervisor.run\')"',
    }) is None


def test_venv_redirector_pair_is_one_logical_authority():
    wrapper = _record(
        100, parent_pid=1,
        executable=r"C:\runtime\releases\aaaaaaaa\.venv\Scripts\pythonw.exe",
        create_time=10_000_000_000,
    )
    child = _record(
        101, parent_pid=100, executable=r"C:\Python313\pythonw.exe",
        create_time=10_000_010_000,
    )
    collapsed = _collapse_interpreter_wrappers([wrapper, child])
    assert [item["pid"] for item in collapsed] == [101]
    assert collapsed[0]["interpreter_wrapper_pid"] == 100


def test_independent_same_home_launches_remain_duplicate():
    left = _record(
        100, parent_pid=1,
        executable=r"C:\runtime\releases\aaaaaaaa\.venv\Scripts\pythonw.exe",
        create_time=10_000_000_000,
    )
    right = _record(
        200, parent_pid=1,
        executable=r"C:\runtime\releases\aaaaaaaa\.venv\Scripts\pythonw.exe",
        create_time=20_000_000_000,
    )
    assert len(_collapse_interpreter_wrappers([left, right])) == 2


def test_process_home_is_exactly_scoped():
    row = _record(
        1, parent_pid=0,
        executable=r"C:\Python313\python.exe",
        create_time=1,
    )
    assert process_matches_home(row, r"C:\runtime\foundry")
    assert not process_matches_home(row, r"C:\runtime\other")


def test_parity_states_fail_closed():
    assert parity_state(
        canonical_sha="a" * 40, installed_sha="a" * 40,
        runtime_shas=["a" * 40], supervisor_count=1) == "MATCH"
    assert parity_state(
        canonical_sha="b" * 40, installed_sha="a" * 40,
        runtime_shas=["a" * 40], supervisor_count=1) == "SOURCE_AHEAD"
    assert parity_state(
        canonical_sha=None, installed_sha="a" * 40,
        runtime_shas=["a" * 40], supervisor_count=1) == "CANONICAL_SOURCE_UNKNOWN"
    assert parity_state(
        canonical_sha="a" * 40, installed_sha="a" * 40,
        runtime_shas=["a" * 40], supervisor_count=2) == "DUPLICATE_SUPERVISOR"
    assert parity_state(
        canonical_sha="a" * 40, installed_sha="a" * 40,
        runtime_shas=[], supervisor_count=0) == "MISSING_RUNTIME"


def test_query_injection_does_not_infer_supervisor_from_executable_name():
    rows = supervisor_processes(
        query=lambda: [{
            "ProcessId": 10,
            "ParentProcessId": 1,
            "ExecutablePath": r"C:\Python313\pythonw.exe",
            "CommandLine": r'pythonw.exe -c "import jarvis_foundry"',
        }],
        attach_creation_identity=False,
    )
    assert rows == []


def test_release_id_resolves_only_to_matching_immutable_manifest(tmp_path):
    sha = "b" * 40
    release = tmp_path / "releases" / sha
    release.mkdir(parents=True)
    (release / "RELEASE.json").write_text(
        '{"source_sha":"%s","release_id":"rel-test"}' % sha,
        encoding="utf-8",
    )
    assert release_sha_for_id("rel-test", root=tmp_path) == sha
    assert release_sha_for_id("rel-missing", root=tmp_path) is None
    (release / "RELEASE.json").write_text(
        '{"source_sha":"%s","release_id":"rel-other"}' % sha,
        encoding="utf-8",
    )
    assert release_sha_for_id("rel-test", root=tmp_path) is None
