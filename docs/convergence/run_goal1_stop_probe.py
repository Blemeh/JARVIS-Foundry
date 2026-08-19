"""Bounded disposable Goal-1 STOP-kill probe. Never touches live Foundry home."""
from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(r"C:\temp\JARVIS-Runtime-Convergence-STOP")
sys.path.insert(0, str(ROOT / "src"))

from jarvis_foundry.cli.main import main as jarvis_main
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.paths import FoundryPaths
from jarvis_foundry.runtime.config import WorkerRuntimeConfig
from jarvis_foundry.runtime.worker_loop import run_cycle
from jarvis_foundry.supervisor.worker import WorkerController, load_adapter_pids, pid_alive
from jarvis_foundry.workers.adapters.base import run_subprocess_with_heartbeat
from jarvis_foundry.workers.adapters.hermes import HermesWorkerAdapter
from jarvis_foundry.workers.adapters.openhands import OpenHandsWorkerAdapter
from jarvis_foundry.workers.contracts import WorkerStepContract


def sleep_cmd(seconds: int) -> list[str]:
    return [sys.executable, "-c", f"import time; time.sleep({seconds})"]


def tree_cmd(marker: Path) -> list[str]:
    return [sys.executable, "-c", (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        f"open(r'{marker}', 'w', encoding='utf-8').write(str(child.pid))\n"
        "time.sleep(120)\n"
    )]


def write_sleeper(path: Path) -> None:
    path.write_text(
        "import subprocess, sys, time\n"
        "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        "time.sleep(120)\n",
        encoding="utf-8",
    )


def contract(root: Path) -> WorkerStepContract:
    return WorkerStepContract(
        run_id="probe-run", mission_id="probe-m", step_id="probe-s",
        attempt=1, role="builder", objective="probe", instructions="sleep",
        working_root=root,
    )


def main() -> int:
    stamp = time.strftime("%Y%m%dT%H%M%S")
    home = Path(rf"C:\temp\JARVIS-Goal1-STOP-Probe-{stamp}")
    home.mkdir(parents=True)
    live_home = Path(r"C:\Users\b\AppData\Local\hermes\foundry")
    live_stop_before = (live_home / "EMERGENCY_STOP").exists()
    db = FoundryDatabase(home / "foundry.db")
    db.initialize()
    paths = FoundryPaths(home)
    report: dict = {
        "home": str(home),
        "cli_stop": str(paths.emergency_stop),
        "live_stop_before": live_stop_before,
        "live_home_untouched_rule": str(live_home),
    }

    decoy = subprocess.Popen(sleep_cmd(180), cwd=str(home))
    report["decoy_pid"] = decoy.pid

    marker = home / "child.pid"
    controller = WorkerController(db, lease_seconds=900)
    owned = controller.spawn(
        "probe-tree", tree_cmd(marker), cwd=home,
        worker_type="probe", mission_id="probe-m", step_id="probe-s",
        stop_path=paths.emergency_stop,
    )
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and not marker.exists():
        time.sleep(0.05)
    child_pid = int(marker.read_text(encoding="utf-8"))
    table = load_adapter_pids(controller.pid_table_path())
    report["ownership_registered"] = {
        "run_id": "probe-tree",
        "root_pid": owned.process.pid,
        "child_pid": child_pid,
        "table": table.get("probe-tree"),
    }

    hb_errors: list[str] = []
    hb_result: dict = {}

    def _hb() -> None:
        try:
            result = run_subprocess_with_heartbeat(
                sleep_cmd(120),
                cwd=str(home),
                env=os.environ.copy(),
                timeout_seconds=60,
                heartbeat=lambda: None,
                interval_seconds=30,
                run_id="probe-hb",
                workers=controller,
                stop_path=paths.emergency_stop,
                pid_table=controller.pid_table_path(),
                worker_type="probe",
                stop_poll_seconds=1.0,
            )
            hb_result["returncode"] = result.returncode
        except Exception as error:  # noqa: BLE001
            hb_errors.append(f"{type(error).__name__}: {error}")

    thread = threading.Thread(target=_hb, daemon=True)
    thread.start()
    deadline = time.monotonic() + 8
    while time.monotonic() < deadline and not controller.status("probe-hb").alive:
        time.sleep(0.05)
    report["heartbeat_child_registered"] = controller.status("probe-hb").alive

    started = time.monotonic()
    jarvis_main(["--home", str(home), "stop", "--reason", "goal1 disposable probe"])
    stop_written = time.monotonic() - started
    observed = paths.emergency_stop.exists()
    # Candidate code (not live Supervisor) terminates owned trees on STOP.
    controller.terminate_all()
    thread.join(timeout=8)
    elapsed_kill = time.monotonic() - started
    report["stop"] = {
        "written_s": stop_written,
        "observed": observed,
        "path": str(paths.emergency_stop),
        "kill_elapsed_s": elapsed_kill,
        "owned_root_alive": pid_alive(owned.process.pid),
        "owned_child_alive": pid_alive(child_pid),
        "hb_alive": controller.status("probe-hb").alive,
        "hb_returncode": hb_result.get("returncode"),
        "hb_errors": hb_errors,
        "pid_table_after": load_adapter_pids(controller.pid_table_path()),
    }

    cfg = WorkerRuntimeConfig(enabled=True, stop_sentinel_path="EMERGENCY_STOP")
    frozen = run_cycle(db, paths, cfg, loop_id="probe-1", instance_id="probe")
    report["new_dispatch"] = frozen

    report["decoy_survived"] = decoy.poll() is None
    if decoy.poll() is None:
        decoy.kill()
        decoy.wait(timeout=5)

    # Adapter control probes with disposable wrappers (no live Hermes profile).
    sleeper = home / "sleeper.py"
    write_sleeper(sleeper)
    wrapper = home / "fake_python.cmd"
    wrapper.write_text(
        f"@echo off\r\n\"{sys.executable}\" \"{sleeper}\"\r\n",
        encoding="utf-8",
    )
    hermes_root = home / "hermes-src"
    hermes_root.mkdir()
    hermes_home = home / "hermes-home"
    hermes_home.mkdir()
    hermes = HermesWorkerAdapter(
        hermes_root=hermes_root, hermes_home=hermes_home, enabled=True,
        timeout_seconds=60, python_exe=str(wrapper),
        stop_path=paths.emergency_stop, pid_table=controller.pid_table_path(),
        workers=WorkerController(db, lease_seconds=900),
    )
    # STOP already active: heartbeat runner should refuse to run long.
    (home / "work-h").mkdir()
    hermes_started = time.monotonic()
    try:
        envelope = hermes.dispatch(contract(home / "work-h"), heartbeat=lambda: None)
        hermes_status = {
            "elapsed_s": time.monotonic() - hermes_started,
            "worker_status": envelope.worker_status,
            "exit_state": envelope.exit_state,
            "summary": envelope.summary[-200:],
        }
    except Exception as error:  # noqa: BLE001
        hermes_status = {
            "elapsed_s": time.monotonic() - hermes_started,
            "error": f"{type(error).__name__}: {error}",
        }
    report["hermes"] = hermes_status

    oh_exe = home / "fake_openhands.cmd"
    oh_exe.write_text(
        f"@echo off\r\n\"{sys.executable}\" \"{sleeper}\"\r\n",
        encoding="utf-8",
    )
    data_dir = home / "oh-data"
    data_dir.mkdir()
    (home / "work-oh").mkdir()
    oh = OpenHandsWorkerAdapter(
        openhands_exe=oh_exe, data_dir=data_dir, enabled=True,
        timeout_seconds=60, stop_path=paths.emergency_stop,
        pid_table=controller.pid_table_path(),
        workers=WorkerController(db, lease_seconds=900),
    )
    oh._owner_openhands = home / "owner-openhands"
    oh._owner_openhands.mkdir()
    oh_started = time.monotonic()
    try:
        envelope = oh.dispatch(contract(home / "work-oh"), heartbeat=lambda: None)
        oh_status = {
            "elapsed_s": time.monotonic() - oh_started,
            "worker_status": envelope.worker_status,
            "exit_state": envelope.exit_state,
            "summary": (envelope.summary or "")[-200:],
            "blockers": list(envelope.blockers),
        }
    except Exception as error:  # noqa: BLE001
        oh_status = {
            "elapsed_s": time.monotonic() - oh_started,
            "error": f"{type(error).__name__}: {error}",
        }
    report["openhands"] = oh_status

    # Fresh STOP-latency samples on a cleared sentinel using the same runner.
    samples = []
    for i in range(5):
        sample_stop = home / f"lat-{i}"
        t0 = time.monotonic()

        def arm(path=sample_stop) -> None:
            time.sleep(0.15)
            path.write_text("{}", encoding="utf-8")

        threading.Thread(target=arm, daemon=True).start()
        run_subprocess_with_heartbeat(
            sleep_cmd(60), cwd=str(home), env=os.environ.copy(),
            timeout_seconds=20, heartbeat=lambda: None, interval_seconds=30,
            run_id=f"lat-{i}", stop_path=sample_stop,
            pid_table=home / "lat-pids.json", stop_poll_seconds=1.0,
        )
        samples.append(time.monotonic() - t0)
    samples.sort()
    report["latency"] = {
        "min": min(samples), "median": statistics.median(samples),
        "max": max(samples), "n": len(samples), "samples": samples,
    }

    report["live_stop_after"] = (live_home / "EMERGENCY_STOP").exists()
    report["pass"] = (
        observed
        and elapsed_kill <= 10
        and not report["stop"]["owned_root_alive"]
        and not report["stop"]["owned_child_alive"]
        and report["decoy_survived"]
        and frozen.get("outcome") == "FROZEN"
        and report["latency"]["max"] <= 10
        and not report["live_stop_after"]
        and not live_stop_before
        and "error" not in hermes_status
        and hermes_status.get("elapsed_s", 99) <= 10
        and "error" not in oh_status
        and oh_status.get("elapsed_s", 99) <= 10
    )
    out = ROOT / "docs" / "convergence" / "GOAL1_STOP_OWNERSHIP_PROBE_20260819.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, default=str))
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
