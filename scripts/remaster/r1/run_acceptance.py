"""CLI for deterministic R1 evaluation of an arbitrary external adapter."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from acceptance import (
    DEFAULT_CHALLENGE_NONCE,
    DEFAULT_EVALUATOR_AUTHORITY_ID,
    DEFAULT_REQUEST_ID,
    DEFAULT_TIMEOUT_SECONDS,
    ValidationError,
    formatted_json,
    run_external_adapter,
    write_result,
)


REPO_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PURPOSE_ROOT = REPO_ROOT / "docs" / "remaster" / "r1"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Evaluate a versioned external JARVIS purpose adapter")
    parser.add_argument("--purpose-root", type=Path, default=DEFAULT_PURPOSE_ROOT)
    parser.add_argument("--evaluation-time", required=True, help="timezone-aware ISO-8601 acceptance time")
    parser.add_argument("--request-id", default=DEFAULT_REQUEST_ID)
    parser.add_argument("--proof-scope", choices=("HARNESS_SELF_TEST", "PRODUCT_LIVE"), default="HARNESS_SELF_TEST")
    parser.add_argument("--challenge-nonce", default=DEFAULT_CHALLENGE_NONCE)
    parser.add_argument("--evaluator-authority-id", default=DEFAULT_EVALUATOR_AUTHORITY_ID)
    parser.add_argument("--observer-id", default=None, help="evaluator-selected ID from the frozen observer registry")
    parser.add_argument("--run-root", type=Path, default=None, help="isolated evidence root; omitted roots are cleaned automatically")
    parser.add_argument("--temporary-parent", type=Path, default=None)
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--output", type=Path, default=None, help="canonical verdict path; stdout when omitted")
    parser.add_argument("adapter_command", nargs=argparse.REMAINDER, help="command after --; no shell is used")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    adapter_command = list(args.adapter_command)
    if adapter_command and adapter_command[0] == "--":
        adapter_command.pop(0)
    try:
        result = run_external_adapter(
            adapter_command,
            args.purpose_root,
            args.evaluation_time,
            run_root=args.run_root,
            request_id=args.request_id,
            timeout_seconds=args.timeout_seconds,
            temporary_parent=args.temporary_parent,
            requested_proof_scope=args.proof_scope,
            challenge_nonce=args.challenge_nonce,
            evaluator_authority_id=args.evaluator_authority_id,
            observer_id=args.observer_id,
        )
    except ValidationError as exc:
        print(f"acceptance configuration rejected: {exc}", file=sys.stderr)
        return 2
    if args.output is None:
        sys.stdout.write(formatted_json(result))
    else:
        write_result(args.output, result)
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
