from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict
from pathlib import Path

from ..database import FoundryDatabase
from ..installer import (
    V1_TASK_NAME,
    WORKER_LOOP_TASK_NAME,
    Installer,
    WorkerLoopInstaller,
)
from ..paths import FoundryPaths
from ..supervisor.estop import EmergencyStop
from ..supervisor.service import Supervisor


def _parser() -> argparse.ArgumentParser:
    home_option = argparse.ArgumentParser(add_help=False)
    home_option.add_argument(
        "--home", type=Path, default=argparse.SUPPRESS,
        help="override the Foundry state directory",
    )
    parser = argparse.ArgumentParser(prog="jarvis", description="JARVIS Foundry local control", parents=[home_option])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init", parents=[home_option])
    status = sub.add_parser("status", parents=[home_option])
    status.add_argument("--task", default=None)
    status.add_argument("--v1", action="store_true")
    status.add_argument("--worker-loop", action="store_true")
    sub.add_parser("doctor", parents=[home_option])
    stop = sub.add_parser("stop", parents=[home_option])
    stop.add_argument("--reason", default="owner requested STOP")
    sub.add_parser("resume", parents=[home_option])
    events = sub.add_parser("events", parents=[home_option])
    events.add_argument("--limit", type=int, default=50)
    install = sub.add_parser("install", parents=[home_option])
    install.add_argument("--task", default=None,
                         help="task name (default: staging-safe name; --v1 uses the real V1 task)")
    install.add_argument("--v1", action="store_true",
                         help="install the real V1 Supervisor task (requires the WP7 gate PASS verdict)")
    install.add_argument("--worker-loop", action="store_true",
                         help="install the separate worker-loop task disabled")
    install.add_argument("--config", type=Path,
                         help="absolute worker-loop YAML config path")
    install.add_argument("--force", action="store_true", help="replace an existing task")
    uninstall = sub.add_parser("uninstall", parents=[home_option])
    uninstall.add_argument("--task", default=None)
    uninstall.add_argument("--v1", action="store_true")
    uninstall.add_argument("--worker-loop", action="store_true")
    uninstall.add_argument("--config", type=Path)
    activate = sub.add_parser("activate", parents=[home_option])
    activate.add_argument("--task", default=None)
    activate.add_argument("--v1", action="store_true")
    activate.add_argument("--worker-loop", action="store_true")
    activate.add_argument("--config", type=Path)
    deactivate = sub.add_parser("deactivate", parents=[home_option])
    deactivate.add_argument("--task", default=None)
    deactivate.add_argument("--v1", action="store_true")
    deactivate.add_argument("--worker-loop", action="store_true")
    deactivate.add_argument("--config", type=Path)
    status_prod = sub.add_parser("production-status", parents=[home_option])
    status_prod.add_argument("--source-repo", type=Path, default=None)
    release = sub.add_parser("release", parents=[home_option])
    release_sub = release.add_subparsers(dest="release_command", required=True)
    build = release_sub.add_parser("build")
    build.add_argument("--source-repo", type=Path, required=True)
    build.add_argument("--sha", required=True)
    build.add_argument("--install-root", type=Path, default=None)
    rel_activate = release_sub.add_parser("activate")
    rel_activate.add_argument("--sha", required=True)
    rel_activate.add_argument("--install-root", type=Path, default=None)
    rel_activate.add_argument("--worker-config", type=Path, default=None)
    rel_activate.add_argument("--spawn-worker-loop", action="store_true")
    rollback = release_sub.add_parser("rollback")
    rollback.add_argument("--install-root", type=Path, default=None)
    setup = sub.add_parser("setup", help="configure machine-local LLM provider")
    setup.add_argument("--show", action="store_true", help="show current provider config")
    setup.add_argument("--test", action="store_true", help="test current provider config")
    setup.add_argument(
        "--apply",
        action="store_true",
        help="sync provider credentials into Hermes home .env",
    )
    backup = release_sub.add_parser("backup-db")
    backup.add_argument("--destination", type=Path, required=True)
    migrate = release_sub.add_parser("migrate-clone")
    migrate.add_argument("--destination", type=Path, required=True)
    restore = release_sub.add_parser("restore-db")
    restore.add_argument("--source", type=Path, required=True)
    return parser


def _installer(args, paths: FoundryPaths) -> Installer:
    if args.v1 and args.worker_loop:
        raise ValueError("--v1 and --worker-loop are mutually exclusive")
    if args.worker_loop:
        return WorkerLoopInstaller(
            FoundryDatabase(paths.database),
            task_name=args.task or WORKER_LOOP_TASK_NAME,
            foundry_home=paths.home,
            hermes_home=FoundryPaths.hermes_home(),
            worker_config_path=getattr(args, "config", None),
        )
    task_name = args.task
    if args.v1:
        task_name = V1_TASK_NAME
    elif task_name is None:
        task_name = f"{V1_TASK_NAME}-staging"
    exec_command = None
    exec_arguments = None
    if task_name == V1_TASK_NAME:
        from ..deployment.layout import headless_task_command
        exec_command, prefix = headless_task_command()
        exec_arguments = f'{prefix} --home "{paths.home}"'
    return Installer(
        FoundryDatabase(paths.database),
        task_name=task_name,
        foundry_home=paths.home,
        hermes_home=FoundryPaths.hermes_home(),
        profile={"start_record": str(paths.database), "model_roles": "default"},
        exec_command=exec_command,
        exec_arguments=exec_arguments,
    )


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "setup":
        from ..deployment.path_setup import bootstrap_global_cli

        bootstrap_global_cli()
    args = _parser().parse_args(argv)
    # --home uses SUPPRESS as its default, so it may be absent entirely —
    # a bare `jarvis <cmd>` must resolve the default home, not crash.
    paths = FoundryPaths.discover(getattr(args, "home", None))
    database = FoundryDatabase(paths.database)
    estop = EmergencyStop(paths.emergency_stop)

    if args.command == "init":
        print(json.dumps({"schema_version": database.initialize(), "home": str(paths.home)}, indent=2))
    elif args.command == "stop":
        print(json.dumps(estop.activate(args.reason), indent=2))
    elif args.command == "resume":
        print(json.dumps({"sentinel_cleared": estop.clear(), "reorientation_required": True}, indent=2))
    elif args.command == "doctor":
        print(json.dumps(Supervisor(database, paths.emergency_stop).doctor(), indent=2))
    elif args.command == "events":
        if not paths.database.is_file():
            print(json.dumps([], indent=2))
            return 0
        items = database.events(readonly=True)[-args.limit:]
        print(json.dumps([asdict(event) for event in items], indent=2))
    elif args.command == "status":
        if not paths.database.is_file():
            print(json.dumps({
                "home": str(paths.home), "stop": estop.active(),
                "error": "database_not_initialized",
                "hint": "run jarvis init (or leave the live Supervisor home alone)",
            }, indent=2))
            return 1
        installer = _installer(args, paths)
        with database.connect(readonly=True) as connection:
            row = connection.execute(
                "SELECT * FROM supervisor_state WHERE singleton = 1").fetchone()
            supervisor = dict(row) if row is not None else {}
            counts = {
                table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                for table in (
                    "events", "missions", "resources", "directives", "leases",
                    "agent_runs", "evidence_bundles", "verification_verdicts", "budgets",
                    "obsidian_writes",
                )
            }
        print(json.dumps({
            "home": str(paths.home), "stop": estop.active(),
            "supervisor": supervisor, "counts": counts,
            "installation": installer.status(),
        }, indent=2))
    elif args.command == "install":
        database.initialize()
        print(json.dumps(_installer(args, paths).install(force=args.force), indent=2))
    elif args.command == "uninstall":
        print(json.dumps(_installer(args, paths).uninstall(), indent=2))
    elif args.command == "activate":
        print(json.dumps(_installer(args, paths).set_enabled(True), indent=2))
    elif args.command == "deactivate":
        print(json.dumps(_installer(args, paths).set_enabled(False), indent=2))
    elif args.command == "production-status":
        from ..deployment.status import production_status
        print(json.dumps(production_status(
            home=paths.home,
            source_repo=getattr(args, "source_repo", None),
        ), indent=2, default=str))
    elif args.command == "setup":
        from .setup import run_apply, run_interactive_setup, run_show, run_test

        if args.show:
            return run_show()
        if args.test:
            return run_test()
        if args.apply:
            return run_apply()
        return run_interactive_setup()
    elif args.command == "release":
        return _release_command(args, paths)
    return 0


def _release_command(args, paths: FoundryPaths) -> int:
    from ..deployment.layout import install_root as resolve_install_root
    from ..deployment.release import (
        backup_database,
        build_release,
        migrate_clone,
        restore_database,
        rollback_current,
        set_current,
    )
    from ..installer import V1_TASK_NAME, Installer
    from ..paths import FoundryPaths as Paths

    root = getattr(args, "install_root", None)
    if args.release_command == "build":
        print(json.dumps(build_release(
            source_repo=args.source_repo, source_sha=args.sha, root=root,
        ), indent=2, default=str))
        return 0
    if args.release_command == "activate":
        os.environ.setdefault("JARVIS_INSTALL_ROOT", str(resolve_install_root(root)))
        pointers = set_current(args.sha, root=root)
        from ..deployment.layout import headless_task_command
        from ..deployment.release import validate_release
        identity = validate_release(args.sha, root=root, require_schema_match=False)
        task_command, task_prefix = headless_task_command(root)
        spawn = bool(getattr(args, "spawn_worker_loop", False))
        worker_config = getattr(args, "worker_config", None)
        extra = ""
        if spawn:
            extra += " --spawn-worker-loop"
        if worker_config:
            extra += f' --worker-loop-config "{Path(worker_config).resolve()}"'
        installer = Installer(
            FoundryDatabase(paths.database),
            task_name=V1_TASK_NAME,
            foundry_home=paths.home,
            hermes_home=Paths.hermes_home(),
            python_exe=identity["python_exe"],
            exec_command=task_command,
            exec_arguments=f'{task_prefix} --home "{paths.home}"{extra}',
            profile={
                "start_record": str(paths.database),
                "spawn_worker_loop": "true" if spawn else "false",
                "worker_config_path": str(Path(worker_config).resolve()) if worker_config else "",
                "release_sha": args.sha,
            },
        )
        print(json.dumps({"pointers": pointers, "task": installer.retarget_existing()}, indent=2))
        return 0
    if args.release_command == "rollback":
        print(json.dumps(rollback_current(root=root, foundry_home=paths.home), indent=2))
        return 0
    if args.release_command == "backup-db":
        print(json.dumps({"backup": str(backup_database(paths.database, args.destination))}, indent=2))
        return 0
    if args.release_command == "migrate-clone":
        print(json.dumps(migrate_clone(paths.database, args.destination), indent=2))
        return 0
    if args.release_command == "restore-db":
        print(json.dumps({"events": restore_database(paths.database, args.source)}, indent=2))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
