"""Run one bounded, real-provider Hermes worker probe in disposable roots."""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from jarvis_foundry.runtime.provider_config import load_provider_settings
from jarvis_foundry.workers.adapters.hermes import HermesWorkerAdapter
from jarvis_foundry.workers.contracts import WorkerStepContract
from jarvis_foundry.workers.result_contract import result_instruction


def _git(root: Path, *args: str) -> None:
    completed = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True,
        timeout=30, check=False)
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--working-root", type=Path, required=True)
    parser.add_argument("--hermes-root", type=Path, required=True)
    parser.add_argument("--hermes-python", type=Path, required=True)
    parser.add_argument("--timeout", type=int, default=300)
    args = parser.parse_args()

    working_root = args.working_root.resolve()
    if working_root.exists() and any(working_root.iterdir()):
        raise RuntimeError(f"working root must be new or empty: {working_root}")
    working_root.mkdir(parents=True, exist_ok=True)
    (working_root / "README.md").write_text(
        "# Disposable JARVIS real-worker probe\n", encoding="utf-8")
    _git(working_root, "init")
    _git(working_root, "config", "user.email", "jarvis-probe@local.invalid")
    _git(working_root, "config", "user.name", "JARVIS Probe")
    _git(working_root, "add", "README.md")
    _git(working_root, "commit", "-m", "probe baseline")

    hermes_home = working_root.parent / f"{working_root.name}-hermes-home"
    hermes_home.mkdir(parents=True, exist_ok=False)
    settings = load_provider_settings()
    instructions = result_instruction(
        "Create probe.txt in the working root containing exactly "
        "JARVIS_REAL_WORKER_PROBE_OK followed by one newline. Do not change "
        "README.md. Then satisfy the JARVIS result contract.",
        working_root,
    )
    contract = WorkerStepContract(
        run_id="real-worker-probe-run",
        mission_id="real-worker-probe-mission",
        step_id="real-worker-probe-step",
        attempt=1,
        role="builder",
        objective="Prove real Hermes provider execution in an isolated repository",
        instructions=instructions,
        working_root=working_root,
        allowed_paths=(working_root,),
        provider=settings.provider,
        model=settings.model,
        expected_artifacts=("probe.txt",),
        verification_criteria=("probe.txt contains JARVIS_REAL_WORKER_PROBE_OK",),
    )
    adapter = HermesWorkerAdapter(
        hermes_root=args.hermes_root.resolve(),
        hermes_home=hermes_home,
        enabled=True,
        timeout_seconds=args.timeout,
        python_exe=str(args.hermes_python.resolve()),
        provider_settings=settings,
    )
    envelope = adapter.dispatch(contract, heartbeat=lambda: None)
    probe = working_root / "probe.txt"
    payload = {
        "provider": settings.provider,
        "model": settings.model,
        "worker_status": envelope.worker_status,
        "exit_state": envelope.exit_state,
        "verdict": envelope.verdict,
        "files_changed": list(envelope.files_changed),
        "probe_exists": probe.is_file(),
        "probe_content_ok": (
            probe.read_text(encoding="utf-8") == "JARVIS_REAL_WORKER_PROBE_OK\n"
            if probe.is_file() else False),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0 if (
        payload["worker_status"] == "COMPLETED"
        and payload["probe_content_ok"]
        and "probe.txt" in payload["files_changed"]
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
