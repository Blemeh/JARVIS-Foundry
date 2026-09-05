"""Sealed one-package entrypoint for bounded workforce subprocesses."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from ..database import FoundryDatabase
from ..mission.contracts import latest_contract
from ..paths import FoundryPaths
from ..runtime._stack import build_worker_stack
from ..runtime.config import WorkerRuntimeConfig
from ..runtime.mission_session import build_canonical_mission_loop
from ..runtime.worker_loop import (
    _adapter_runtime_environment,
    _commit_envelope,
    _pin_routing_to_hermes,
)
from ..supervisor.leases import LeaseManager
from .gateway import WorkforceGateway
from .state import rebuild_workforce_state


def _nested_adapter_pid_table(paths: FoundryPaths, run_id: str) -> Path:
    identity = hashlib.sha256(run_id.encode("utf-8")).hexdigest()
    return (
        paths.home
        / "workforce-adapter-pids"
        / f"{identity}.json"
    ).resolve()


def run_package(
    *,
    home: str | Path,
    package_id: str,
    run_id: str,
    config_path: str | Path | None,
) -> dict[str, object]:
    """Execute one already-admitted package through the canonical MissionLoop."""

    paths = FoundryPaths.discover(home)
    database = FoundryDatabase(paths.database)
    database.initialize()
    config = WorkerRuntimeConfig.from_yaml(config_path)
    if not config.enabled:
        return {
            "package_id": package_id,
            "outcome": "BLOCKED",
            "reason": "worker runtime configuration is disabled",
        }
    state = rebuild_workforce_state(database)
    package_state = state.package(package_id)
    package = package_state.package
    sessions = state.sessions_for(package_id)
    if not sessions:
        raise RuntimeError("workforce package has no admitted session")
    latest = max(sessions, key=lambda item: item.attempt)
    session = next(
        (
            candidate
            for candidate in sessions
            if candidate.run_id == run_id
        ),
        None,
    )
    if session is None:
        raise RuntimeError(
            f"sealed runner references an unknown package attempt: {run_id}"
        )
    if latest.run_id != session.run_id:
        raise RuntimeError(
            f"sealed runner references a superseded package attempt: {run_id}"
        )
    if session.status not in {"RUNNING", "HEALTHY"}:
        raise RuntimeError(
            "sealed runner requires the exact admitted attempt to be RUNNING"
        )
    if package_state.status != "RUNNING":
        raise RuntimeError(
            "sealed runner requires an admitted RUNNING package"
        )
    contract = latest_contract(
        database,
        str(package.mission_id),
        step_id=str(package.step_id),
        allow_invalid=False,
    )
    if contract is None or (
        contract.contract_id != package.mission_step_contract_id
        or contract.content_hash() != package.mission_step_contract_hash
    ):
        raise RuntimeError(
            "workforce package no longer matches its mission contract"
        )

    stack = build_worker_stack(
        database,
        paths,
        config,
        run_id=session.run_id,
        pid_table=_nested_adapter_pid_table(paths, session.run_id),
    )
    try:
        _pin_routing_to_hermes(stack, config)
        loop = build_canonical_mission_loop(
            database,
            paths,
            config,
            stack=stack,
            leases=LeaseManager(database),
            source_id=f"workforce-runner:{package_id}",
        )
        gateway = WorkforceGateway(
            database,
            stop_path=config.resolve_stop_path(paths.home),
            worker_config_path=config_path,
            source_id="workforce-runner",
        )
        with _adapter_runtime_environment(paths, config):
            result = gateway.run_inline(
                package_id,
                loop,
                expected_run_id=run_id,
                commit_envelope=_commit_envelope(database, contract),
            )
        return {
            "package_id": package_id,
            "run_id": result.binding.session.run_id,
            "outcome": result.outcome,
            "package_status": result.package_status,
            "verdict_id": result.verdict_id,
            "reason": result.reason,
        }
    finally:
        stack.close()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jarvis-workforce-runner",
        description="Run one admitted workforce package",
    )
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--package-id", required=True)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--config", type=Path, default=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = run_package(
            home=args.home,
            package_id=args.package_id,
            run_id=args.run_id,
            config_path=args.config,
        )
    except Exception as error:
        report = {
            "package_id": args.package_id,
            "run_id": args.run_id,
            "outcome": "BLOCKED",
            "reason": f"{type(error).__name__}: {str(error)[-500:]}",
        }
    print(json.dumps(report, sort_keys=True))
    return 0 if report.get("outcome") != "BLOCKED" else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["main", "run_package"]
