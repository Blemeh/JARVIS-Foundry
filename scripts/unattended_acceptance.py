"""Run the reusable disposable JARVIS unattended acceptance harness."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from jarvis_foundry.acceptance_harness.adversarial import run_false_pass_factory  # noqa: E402
from jarvis_foundry.acceptance_harness.contracts import ScenarioSpec  # noqa: E402
from jarvis_foundry.acceptance_harness.runner import AcceptanceRunner  # noqa: E402
from jarvis_foundry.database import FoundryDatabase  # noqa: E402


PROFILE_PATHS = {
    "ACCELERATED_12H_EQUIVALENT": ROOT / "config" / "unattended_scenarios" / "accelerated_12h_equivalent.yaml",
    "SHORT_REAL_TIME": ROOT / "config" / "unattended_scenarios" / "short_realtime.yaml",
    "QUICK_30M": ROOT / "config" / "unattended_scenarios" / "quick_30m.yaml",
    "STANDARD_6H": ROOT / "config" / "unattended_scenarios" / "standard_6h.yaml",
    "FULL_12H": ROOT / "config" / "unattended_scenarios" / "full_12h.yaml",
    "FULL_24H": ROOT / "config" / "unattended_scenarios" / "full_24h.yaml",
    "REBOOT_ASSISTED_24H": ROOT / "config" / "unattended_scenarios" / "reboot_assisted_24h.yaml",
}


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="unattended-acceptance")
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--scenario", type=Path)
    source.add_argument("--profile", choices=sorted(PROFILE_PATHS), default="ACCELERATED_12H_EQUIVALENT")
    parser.add_argument("--output", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--interrupt-after-steps", type=int)
    parser.add_argument("--no-process-probe", action="store_true")
    parser.add_argument("--no-fault-matrix", action="store_true")
    parser.add_argument("--no-false-pass", action="store_true")
    parser.add_argument("--no-interruption-probe", action="store_true")
    parser.add_argument("--false-pass-factory", action="store_true")
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--allow-long-profile", action="store_true")
    parser.add_argument("--canonical-database", type=Path)
    parser.add_argument("--canonical-stop-path", type=Path)
    parser.add_argument("--canonical-release-root", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.false_pass_factory:
        payload = run_false_pass_factory(args.output or (ROOT / "evidence" / "runs" / "false-pass-factory"))
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 0 if payload["verdict"] == "PASS" else 1
    source = args.scenario or PROFILE_PATHS[args.profile]
    scenario = ScenarioSpec.from_path(source)
    if args.validate:
        print(json.dumps(scenario.to_mapping(), indent=2, sort_keys=True))
        return 0
    if not args.allow_long_profile and scenario.duration_seconds > 1800 and scenario.clock_mode.value == "REAL_TIME":
        payload = {
            "verdict": "BLOCKED_EXTERNAL",
            "reason": "long real-time profiles are prepared only; owner approval is required to run them",
            "scenario_id": scenario.scenario_id,
            "duration_seconds": scenario.duration_seconds,
        }
        print(json.dumps(payload, indent=2, sort_keys=True))
        return 2
    output = args.output
    if output is None:
        output = ROOT / scenario.evidence_output
    canonical_database = (
        FoundryDatabase(args.canonical_database)
        if args.canonical_database is not None else None
    )
    runner = AcceptanceRunner(
        scenario,
        output,
        run_id=args.run_id,
        resume=args.resume,
        run_process_probe=not args.no_process_probe,
        run_fault_matrix=not args.no_fault_matrix,
        run_false_pass_factory=not args.no_false_pass,
        run_interruption_probe=not args.no_interruption_probe,
        interrupt_after_steps=args.interrupt_after_steps,
        canonical_database=canonical_database,
        canonical_stop_path=args.canonical_stop_path,
        canonical_release_root=args.canonical_release_root,
    )
    result = runner.run()
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("verdict") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
