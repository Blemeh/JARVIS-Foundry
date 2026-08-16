"""Bounded subprocess worker simulator for crash/hang/refusal faults.

The simulated worker is a real child process that writes heartbeats and
results into the isolated workspace; it never touches protected state.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess  # nosec B404 - bounded local proving worker
import sys
from pathlib import Path
from typing import Any

WORKER_SCRIPT = r"""
import json, sys, time
from pathlib import Path

out_dir = Path(sys.argv[1])
mode = sys.argv[2]
target_id = sys.argv[3]
deadline_s = int(sys.argv[4])
started = time.time()
for index in range(1, 1000):
    now = time.time()
    with (out_dir / "heartbeat.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"worker": target_id, "index": index, "at": now}) + "\n")
    if mode == "hang":
        time.sleep(0.2)
        continue
    if mode == "crash":
        if index == 2:
            raise SystemExit(99)
        time.sleep(0.1)
        continue
    if mode == "refuse":
        if index == 2:
            while time.time() - started < deadline_s:
                time.sleep(0.2)
            raise SystemExit(0)
        time.sleep(0.1)
        continue
    (out_dir / "result.json").write_text(
        json.dumps({"worker": target_id, "status": "COMPLETED", "result": "ok"}),
        encoding="utf-8",
    )
    raise SystemExit(0)
"""


def spawn_worker(
    workspace: Path,
    *,
    target_id: str,
    mode: str,
    deadline_s: int = 30,
) -> subprocess.Popen[bytes]:
    """Start a simulated worker; the caller owns termination."""
    if mode not in {"normal", "hang", "crash", "refuse"}:
        raise ValueError(f"unknown worker mode: {mode}")
    env = dict(os.environ)
    env.pop("OPENAI_API_KEY", None)
    env.pop("HERMES_HOME", None)
    return subprocess.Popen(  # nosec B603 - fixed list argv, local proving only
        [
            sys.executable,
            "-c",
            WORKER_SCRIPT,
            str(workspace),
            mode,
            target_id,
            str(deadline_s),
        ],
        cwd=str(workspace),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )


def read_heartbeats(workspace: Path) -> list[dict[str, Any]]:
    path = workspace / "heartbeat.jsonl"
    rows: list[dict[str, Any]] = []
    if not path.is_file():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def read_result(workspace: Path) -> dict[str, Any] | None:
    path = workspace / "result.json"
    if not path.is_file():
        return None
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else None


def stop_worker(process: subprocess.Popen[bytes], *, force_after_s: float = 2.0) -> dict[str, Any]:
    """Graceful terminate, then force kill on refusal."""
    outcome: dict[str, Any] = {"graceful": False, "forced": False, "returncode": None}
    try:
        process.terminate()
        process.wait(timeout=force_after_s)
        outcome["graceful"] = True
    except subprocess.TimeoutExpired:
        outcome["graceful"] = False
        process.kill()
        process.wait(timeout=10)
        outcome["forced"] = True
    except OSError:
        outcome["graceful"] = False
    try:
        outcome["returncode"] = process.returncode
    except Exception:  # noqa: BLE001 - pragma: no cover - defensive
        outcome["returncode"] = None
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            try:
                stream.close()
            except OSError:
                pass
    return outcome


def send_termination_signal(process: subprocess.Popen[bytes]) -> None:
    """Best-effort SIGTERM where the platform supports it."""
    if os.name != "nt":
        try:
            process.send_signal(signal.SIGTERM)
        except OSError:
            pass
