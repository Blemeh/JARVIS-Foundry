"""Activation continuity: installed worker-loop coordinates survive release.

A plain ``release activate --sha ...`` must never silently convert an
autonomous engine (Supervisor-spawned worker loop) into heartbeat-only.
Absent flags inherit the installed Supervisor task's durable settings;
explicit flags always win; no existing task fails closed to OFF.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

from jarvis_foundry.installer import (  # noqa: E402
    inherit_task_arguments,
)


# 1. Plain activation preserves an enabled engine.
def test_plain_activation_preserves_spawn_and_config() -> None:
    previous = (
        "wscript-marker --home \"C:/hermes/foundry\" --spawn-worker-loop "
        "--worker-loop-config \"C:/hermes/foundry/worker.yaml\" "
        "--command-center-port 8767"
    )
    spawn, config, port = inherit_task_arguments(
        previous, spawn_worker_loop=None, worker_config=None,
        command_center_port=None)
    assert spawn is True
    assert config == "C:/hermes/foundry/worker.yaml"
    assert port == 8767


# 2. Explicit disable wins over an enabled installation.
def test_explicit_no_spawn_disables_despite_previous() -> None:
    previous = "--home \"C:/x\" --spawn-worker-loop"
    spawn, _, _ = inherit_task_arguments(
        previous, spawn_worker_loop=False, worker_config=None,
        command_center_port=None)
    assert spawn is False


# 3. Explicit enable wins over a heartbeat-only installation.
def test_explicit_spawn_enables_despite_previous() -> None:
    previous = "--home \"C:/x\" --command-center-port 8767"
    spawn, _, port = inherit_task_arguments(
        previous, spawn_worker_loop=True, worker_config=None,
        command_center_port=None)
    assert spawn is True
    assert port == 8767


# 4. No existing task fails closed (heartbeat-only, default port).
def test_no_previous_task_fails_closed() -> None:
    spawn, config, port = inherit_task_arguments(
        None, spawn_worker_loop=None, worker_config=None,
        command_center_port=None)
    assert spawn is False
    assert config is None
    assert port is None


# 5. Explicit worker config overrides the inherited one.
def test_explicit_config_overrides_inherited() -> None:
    previous = "--spawn-worker-loop --worker-loop-config \"C:/old.yaml\""
    spawn, config, _ = inherit_task_arguments(
        previous, spawn_worker_loop=None,
        worker_config="C:/new.yaml", command_center_port=None)
    assert spawn is True
    assert config == "C:/new.yaml"


# 6. Substring lookalikes must not enable spawn
#    (--no-spawn-worker-loop contains "spawn-worker-loop" as text).
def test_no_spawn_flag_text_does_not_enable() -> None:
    previous = "--home \"C:/x\" --no-spawn-worker-loop"
    spawn, _, _ = inherit_task_arguments(
        previous, spawn_worker_loop=None, worker_config=None,
        command_center_port=None)
    assert spawn is False


# 7. Malformed port in a previous task fails closed, keeps the rest.
def test_malformed_port_fails_closed() -> None:
    previous = "--spawn-worker-loop --command-center-port abc"
    spawn, _, port = inherit_task_arguments(
        previous, spawn_worker_loop=None, worker_config=None,
        command_center_port=None)
    assert spawn is True
    assert port is None


# 8. Live no_eligible_worker shape: evidence-less config forces both
#    frameworks NOT_READY, so routing honestly reports no eligible worker.
def test_evidence_less_config_routes_to_no_eligible_worker(tmp_path) -> None:
    from jarvis_foundry.workers.contracts import (
        WORKER_HERMES,
        WorkerStepContract,
    )
    from jarvis_foundry.workers.router import NOT_READY, WorkerRouter

    router = WorkerRouter()
    router.mark_ready(WORKER_HERMES, state=NOT_READY,
                      note="no positive containment evidence in runtime "
                           "config",
                      evidence=())
    step = WorkerStepContract(
        run_id="run-1", mission_id="mission-1", step_id="step-1",
        attempt=1, role="builder", objective="o", instructions="i",
        working_root=tmp_path, task_signals={},
    )
    decision = router.route(step)
    assert decision.eligible_workers == ()
    assert decision.selected_worker == ""
    assert decision.reason == "no eligible worker (all unavailable or vetoed)"


# 9. The same contract becomes dispatchable when evidence-backed
#    readiness arrives (the enablement transition; no code change).
def test_evidence_backed_readiness_makes_worker_eligible(tmp_path) -> None:
    from jarvis_foundry.workers.contracts import (
        WORKER_HERMES,
        WorkerStepContract,
    )
    from jarvis_foundry.workers.router import READY, WorkerRouter

    router = WorkerRouter()
    step = WorkerStepContract(
        run_id="run-1", mission_id="mission-1", step_id="step-1",
        attempt=1, role="builder", objective="o", instructions="i",
        working_root=tmp_path, task_signals={},
    )
    router.mark_ready(WORKER_HERMES, state=READY,
                      note="readiness from runtime config",
                      evidence=("provider-readiness-probe:opencode-go:READY",))
    decision = router.route(step)
    assert decision.selected_worker == WORKER_HERMES


# 10. HTTP 429 classifies as RATE_LIMITED (durable provider WAIT, never
#     worker disappearance; the v12 resilience test owns the WAIT half).
def test_http_429_classifies_rate_limited() -> None:
    from jarvis_foundry.roles.adapter import classify_provider_failure

    assert classify_provider_failure(
        "provider call failed: HTTP 429 Too Many Requests") == "RATE_LIMITED"


# 11. The standalone worker-loop task cannot be the enablement path.
def test_standalone_worker_task_cannot_be_enabled(tmp_path) -> None:
    from jarvis_foundry.database import FoundryDatabase
    from jarvis_foundry.installer import (
        InstallerError,
        WorkerLoopInstaller,
    )

    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    installer = WorkerLoopInstaller(
        db, foundry_home=tmp_path, hermes_home=tmp_path)
    try:
        installer.set_enabled(True)
    except InstallerError:
        pass
    else:
        raise AssertionError("standalone worker task must refuse enablement")


# 12. Entity-escaped quotes in real schtasks XML round-trip to usable
#     coordinates (ElementTree decodes entities; regression for the
#     &quot;-in-config defect).
def test_entity_escaped_task_xml_selects_supervisor_exec() -> None:
    from jarvis_foundry.installer import (
        _run_schtasks,
        existing_task_arguments,
        inherit_task_arguments,
    )
    import jarvis_foundry.installer as installer_mod
    import subprocess

    xml = (
        "<?xml version=\"1.0\" encoding=\"UTF-16\"?>"
        "<Task xmlns=\"http://schemas.microsoft.com/windows/2004/02/mit/task\">"
        "<Actions><Exec>"
        "<Command>wscript.exe</Command>"
        "<Arguments>//B //Nologo &quot;C:/bin/jarvis-supervisor-headless.vbs&quot; "
        "--home &quot;C:/hermes/foundry&quot; --spawn-worker-loop "
        "--worker-loop-config &quot;C:/w.yaml&quot; --command-center-port 8767"
        "</Arguments></Exec>"
        "<Exec><Command>helper.exe</Command>"
        "<Arguments>--helper-flag</Arguments></Exec>"
        "</Actions></Task>"
    )
    real_run = _run_schtasks
    installer_mod._run_schtasks = lambda *a: subprocess.CompletedProcess(
        args=a, returncode=0, stdout=xml, stderr="")
    try:
        previous = existing_task_arguments("JARVIS-Foundry-Supervisor")
    finally:
        installer_mod._run_schtasks = real_run
    assert previous is not None and "--helper-flag" not in previous
    assert "&quot;" not in previous
    spawn, config, port = inherit_task_arguments(
        previous, spawn_worker_loop=None, worker_config=None,
        command_center_port=None)
    assert spawn is True
    assert config == "C:/w.yaml"
    assert port == 8767


# 13. Unparseable previous arguments fail closed instead of raising
#     (activation must never abort after pointer swap).
def test_unparseable_previous_args_fail_closed() -> None:
    from jarvis_foundry.installer import inherit_task_arguments

    spawn, config, port = inherit_task_arguments(
        "--home \"C:/some/dir\\\" --spawn-worker-loop",
        spawn_worker_loop=None, worker_config=None,
        command_center_port=None)
    assert (spawn, config, port) == (False, None, None)


# 14. Flag-like text inside quoted paths is not misread as coordinates.
def test_quoted_flag_like_text_is_not_parsed() -> None:
    from jarvis_foundry.installer import inherit_task_arguments

    spawn, config, port = inherit_task_arguments(
        "--home \"C:/data --command-center-port 9999/app\"",
        spawn_worker_loop=None, worker_config=None,
        command_center_port=None)
    assert spawn is False
    assert config is None
    assert port is None


# 15. Equals-form coordinates in a hand-crafted task are inherited.
def test_equals_form_coordinates_inherited() -> None:
    from jarvis_foundry.installer import inherit_task_arguments

    spawn, config, port = inherit_task_arguments(
        "--home \"C:/x\" --spawn-worker-loop "
        "--worker-loop-config=C:/w.yaml --command-center-port=9999",
        spawn_worker_loop=None, worker_config=None,
        command_center_port=None)
    assert spawn is True
    assert config == "C:/w.yaml"
    assert port == 9999


# 16. Supervisor-Exec selection matches launcher identity in Arguments
#     (installer puts wscript.exe in Command).
def test_supervisor_exec_matched_via_arguments_identity(
    monkeypatch,
) -> None:
    from jarvis_foundry.installer import existing_task_arguments
    import subprocess

    xml = (
        "<?xml version=\"1.0\" encoding=\"UTF-16\"?>"
        "<Task xmlns=\"http://schemas.microsoft.com/windows/2004/02/mit/task\">"
        "<Actions><Exec><Command>helper.exe</Command>"
        "<Arguments>--helper-flag --spawn-worker-loop</Arguments></Exec>"
        "<Exec><Command>C:/Windows/System32/wscript.exe</Command>"
        "<Arguments>//B //Nologo \"C:/bin/jarvis-supervisor-headless.vbs\" "
        "--home \"C:/hermes/foundry\" --command-center-port 8767"
        "</Arguments></Exec>"
        "</Actions></Task>"
    )
    monkeypatch.setattr(
        "jarvis_foundry.installer._run_schtasks",
        lambda *a: subprocess.CompletedProcess(
            args=a, returncode=0, stdout=xml, stderr=""))
    previous = existing_task_arguments("JARVIS-Foundry-Supervisor")
    assert previous is not None and "--helper-flag" not in previous
    assert "--command-center-port 8767" in previous


# 17. Retrying activation of the CURRENT release succeeds without
#     touching pointers (no false previous_runtime mismatch).
def test_retry_current_release_is_idempotent(tmp_path) -> None:
    import json

    from jarvis_foundry.deployment.release import set_current

    root = tmp_path / "JARVIS"
    for sha in ("a" * 40, "b" * 40):
        directory = root / "releases" / sha
        python_dir = directory / ".venv" / "Scripts"
        python_dir.mkdir(parents=True)
        (python_dir / "python.exe").write_text("fake", encoding="utf-8")
        (directory / "RELEASE.json").write_text(json.dumps({
            "release_id": f"rel-{sha[:8]}",
            "source_sha": sha,
            "git_commit": sha,
            "git_tree": "1" * 40,
            "python_version": "3.13.0",
            "package_version": "0.1.0",
            "schema_compatibility": {
                "package_schema": 41, "opens_min": 41, "opens_max": 41},
            "provenance": {"builder": "tests",
                           "source_repo": str(tmp_path / "source"),
                           "git_commit": sha, "git_tree": "1" * 40},
            "previous_runtime": None,
            "created_at": "2026-08-19T00:00:00Z",
        }), encoding="utf-8")
    (root / "CURRENT").write_text("a" * 40, encoding="utf-8")
    (root / "PREVIOUS").write_text("b" * 40, encoding="utf-8")
    # Point b at a as its built predecessor, then activate normally.
    manifest = json.loads(
        (root / "releases" / ("b" * 40) / "RELEASE.json").read_text(
            encoding="utf-8"))
    manifest["previous_runtime"] = {
        "source_sha": "a" * 40, "release_id": "rel-aaaaaaaa",
        "git_commit": "a" * 40, "git_tree": "1" * 40, "schema": 41}
    (root / "releases" / ("b" * 40) / "RELEASE.json").write_text(
        json.dumps(manifest), encoding="utf-8")
    pointed = set_current("b" * 40, root=root)
    assert pointed == {"current": "b" * 40, "previous": "a" * 40}
    # Retry of the now-CURRENT release is a safe no-op.
    retry = set_current("b" * 40, root=root)
    assert retry == {"current": "b" * 40, "previous": "a" * 40}
    assert (root / "CURRENT").read_text(encoding="utf-8").strip() == "b" * 40
    assert (root / "PREVIOUS").read_text(encoding="utf-8").strip() == "a" * 40


# 18. Idempotent retry heals a diverged DB live slot instead of
#     blessing split authority (DB restore/recreation case).
def test_retry_heals_diverged_db_slot(tmp_path) -> None:
    import json

    from jarvis_foundry.database import FoundryDatabase
    from jarvis_foundry.deployment.release import (
        _database_active_release,
        set_current,
    )

    root = tmp_path / "JARVIS2"
    for sha in ("c" * 40, "d" * 40):
        directory = root / "releases" / sha
        python_dir = directory / ".venv" / "Scripts"
        python_dir.mkdir(parents=True)
        (python_dir / "python.exe").write_text("fake", encoding="utf-8")
        (directory / "RELEASE.json").write_text(json.dumps({
            "release_id": f"rel-{sha[:8]}",
            "source_sha": sha,
            "git_commit": sha,
            "git_tree": "1" * 40,
            "python_version": "3.13.0",
            "package_version": "0.1.0",
            "schema_compatibility": {
                "package_schema": 41, "opens_min": 41, "opens_max": 41},
            "provenance": {"builder": "tests",
                           "source_repo": str(tmp_path / "source"),
                           "git_commit": sha, "git_tree": "1" * 40},
            "previous_runtime": None,
            "created_at": "2026-08-19T00:00:00Z",
        }), encoding="utf-8")
    (root / "CURRENT").write_text("d" * 40, encoding="utf-8")
    home = tmp_path / "home2"
    home.mkdir(parents=True)
    database = FoundryDatabase(home / "foundry.db")
    database.initialize(target=41)
    # Pointers say d, DB slot is empty (restored DB): retry heals the slot.
    result = set_current("d" * 40, root=root, database=database,
                         reason="test retry heal")
    assert result["current"] == "d" * 40
    assert _database_active_release(database)["source_sha"] == "d" * 40
    # Second retry with converged authority is a clean no-op.
    again = set_current("d" * 40, root=root, database=database,
                        reason="test retry converged")
    assert again["current"] == "d" * 40
    assert _database_active_release(database)["source_sha"] == "d" * 40
