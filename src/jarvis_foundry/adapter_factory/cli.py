"""Command-line interface for the isolated V12 adapter factory."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .builder import (
    AdapterFactory,
    disable_artifact,
    enable_artifact,
    inspect_solution,
    install_isolated_runtime,
    remove_artifact,
    remove_isolated_runtime,
)
from .model import SelectedSolutionDescriptor
from .runtime import invoke_typed_adapter, probe_artifact


def _object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected a JSON object: {path}")
    return value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build and exercise bounded JARVIS capability adapters")
    commands = parser.add_subparsers(dest="command", required=True)

    build = commands.add_parser("build")
    build.add_argument("descriptor", type=Path)
    build.add_argument("--output", type=Path, required=True)

    inspect = commands.add_parser("inspect")
    inspect.add_argument("descriptor", type=Path)

    install = commands.add_parser("install")
    install.add_argument("artifact", type=Path)
    install.add_argument("--environment", type=Path, required=True)
    install.add_argument("--project-root", type=Path, required=True)

    probe = commands.add_parser("probe")
    probe.add_argument("artifact", type=Path)
    probe.add_argument("--python", type=Path)
    probe.add_argument("--config", type=Path)

    invoke = commands.add_parser("invoke")
    invoke.add_argument("artifact", type=Path)
    invoke.add_argument("capability_id")
    invoke.add_argument("payload", type=Path)
    invoke.add_argument("--mission", default="adapter-factory-cli")
    invoke.add_argument("--workflow", default="workflow:adapter-factory-cli")
    invoke.add_argument("--step", default="invoke")
    invoke.add_argument("--python", type=Path)
    invoke.add_argument("--config", type=Path)
    invoke.add_argument("--owned-root", type=Path)
    invoke.add_argument("--stop-path", type=Path)

    disable = commands.add_parser("disable")
    disable.add_argument("artifact", type=Path)
    disable.add_argument("--reason", default="operator disabled candidate adapter")

    enable = commands.add_parser("enable")
    enable.add_argument("artifact", type=Path)

    remove = commands.add_parser("remove")
    remove.add_argument("artifact", type=Path)
    remove.add_argument("--expected-adapter-id", required=True)

    remove_runtime = commands.add_parser("remove-runtime")
    remove_runtime.add_argument("environment", type=Path)
    remove_runtime.add_argument("--expected-python", type=Path, required=True)

    args = parser.parse_args(argv)
    if args.command == "build":
        result = AdapterFactory(args.output).build(SelectedSolutionDescriptor.load(args.descriptor)).as_dict()
    elif args.command == "inspect":
        result = inspect_solution(SelectedSolutionDescriptor.load(args.descriptor))
    elif args.command == "install":
        result = install_isolated_runtime(
            args.artifact, environment_root=args.environment, project_root=args.project_root,
        )
    elif args.command == "probe":
        result = probe_artifact(
            args.artifact, python_executable=args.python,
            config=_object(args.config) if args.config else None,
        )
    elif args.command == "invoke":
        result = invoke_typed_adapter(
            args.artifact, args.capability_id, _object(args.payload),
            mission_id=args.mission, workflow_id=args.workflow, step_id=args.step,
            python_executable=args.python,
            config=_object(args.config) if args.config else None,
            allowed_root=args.owned_root, stop_path=args.stop_path,
        )
    elif args.command == "disable":
        result = disable_artifact(args.artifact, reason=args.reason)
    elif args.command == "enable":
        result = enable_artifact(args.artifact)
    elif args.command == "remove":
        result = remove_artifact(args.artifact, expected_adapter_id=args.expected_adapter_id)
    else:
        result = remove_isolated_runtime(args.environment, expected_python=args.expected_python)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
