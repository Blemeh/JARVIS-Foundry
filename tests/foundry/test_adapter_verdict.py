"""V4-S2 (R1a): HermesOneshotAdapter must derive the role-session verdict
from an EXPLICIT STRUCTURED VERDICT in the session output — never from
the process exit code (DIR-2026-08-12 verifier-truth item 1).

Parse rules (deterministic, bounded to the first 20 stdout lines, applied
AFTER the failure-report classification):
1. a line matching ``VERDICT: PASS|FAIL|BLOCKED|NOT_TESTED`` -> that verdict;
2. a JSON object line with a valid ``verdict`` field -> that verdict;
3. otherwise -> NOT_TESTED (never PASS).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from jarvis_foundry.roles.adapter import (
    HermesOneshotAdapter,
    ProviderUnavailable,
)
from jarvis_foundry.roles.manifest import ContextManifest
from jarvis_foundry.roles.results import COMPLETED, FAILED

STAGED_MAIN = '''\
"""Staged Hermes CLI for V4-S2 verdict parsing (never the live gateway)."""
import argparse
import json
import os
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(prog="hermes_cli.main")
    parser.add_argument("-z", dest="prompt", required=True)
    parser.add_argument("--provider", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--usage-file")
    args = parser.parse_args()

    mode = os.environ.get("JF_STAGED_MODE", "default")
    if mode == "fail_report":
        print("API call failed after 3 retries: Connection error.")
        return 0
    if mode == "down":
        print("[staged-gateway] unavailable", file=sys.stderr)
        return 3
    verdict = os.environ.get("JF_STAGED_VERDICT", "")
    if mode == "verdict":
        print("[staged-gateway] transcript line for %s (%s/%s)"
              % (args.prompt[:40], args.provider, args.model))
        print("VERDICT: %s" % verdict)
        return 0
    if mode == "json_verdict":
        print(json.dumps({"verdict": verdict, "findings": ["staged"]}))
        return 0
    if mode == "late_verdict":
        for i in range(25):
            print("line %d of the transcript" % i)
        print("VERDICT: %s" % verdict)
        return 0
    # default / no_verdict: a transcript WITHOUT any structured verdict
    print("[staged-gateway] session transcript for %s (%s/%s)"
          % (args.prompt[:40], args.provider, args.model))
    if args.usage_file:
        Path(args.usage_file).write_text(json.dumps({
            "estimated_cost_usd": 0.0, "model": args.model,
        }), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''


def make_staged_gateway(root: Path) -> Path:
    package = root / "hermes_cli"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text(
        '"""Staged Hermes CLI package (V4-S2)."""\n', encoding="utf-8")
    (package / "main.py").write_text(STAGED_MAIN, encoding="utf-8")
    return root


@pytest.fixture()
def adapter(tmp_path: Path):
    staged_root = make_staged_gateway(tmp_path / "gateway")
    isolated_home = tmp_path / "isolated-home"
    isolated_home.mkdir()
    a = HermesOneshotAdapter(
        hermes_root=staged_root, hermes_home=isolated_home,
        enabled=True, timeout_seconds=60, python_exe=sys.executable,
    )
    yield a
    for key in list(os.environ):
        if key.startswith("JF_STAGED_"):
            del os.environ[key]


def manifest(role: str = "verifier") -> ContextManifest:
    return ContextManifest(
        run_id="run-v1", role=role, mission_id="mission-1",
        step_id="step-1", resource_id="resource-1", capability="verify",
        provider="opencode_go", model="deepseek-v4-flash",
        isolation_group="verifier", tool_policy=("artifact_inspection",),
        budget_usd=0.0, duration_seconds=60, instructions="verify step-1",
    )


def dispatch(adapter: HermesOneshotAdapter, *, mode: str, verdict: str = ""):
    if mode:
        os.environ["JF_STAGED_MODE"] = mode
    if verdict:
        os.environ["JF_STAGED_VERDICT"] = verdict
    return adapter.dispatch(manifest(), heartbeat=lambda: None)


def test_verdict_line_pass(tmp_path, adapter):
    result = dispatch(adapter, mode="verdict", verdict="PASS")
    assert result.status == COMPLETED
    assert result.verdict == "PASS"


def test_verdict_line_fail(tmp_path, adapter):
    result = dispatch(adapter, mode="verdict", verdict="FAIL")
    assert result.status == COMPLETED
    assert result.verdict == "FAIL"


def test_verdict_line_blocked(tmp_path, adapter):
    result = dispatch(adapter, mode="verdict", verdict="BLOCKED")
    assert result.status == COMPLETED
    assert result.verdict == "BLOCKED"


def test_verdict_line_case_insensitive(tmp_path, adapter):
    os.environ["JF_STAGED_MODE"] = "verdict"
    os.environ["JF_STAGED_VERDICT"] = "pass"
    result = dispatch(adapter, mode="verdict", verdict="pass")
    assert result.verdict == "PASS"


def test_missing_verdict_is_not_test_never_pass(tmp_path, adapter):
    """Exit 0 WITHOUT a structured verdict must be NOT_TESTED — the
    directive's core rule: a verifier is never PASS merely because its
    process exited zero."""
    result = dispatch(adapter, mode="no_verdict")
    assert result.status == COMPLETED
    assert result.verdict == "NOT_TESTED"


def test_json_verdict_object(tmp_path, adapter):
    result = dispatch(adapter, mode="json_verdict", verdict="BLOCKED")
    assert result.status == COMPLETED
    assert result.verdict == "BLOCKED"


def test_verdict_beyond_scan_window_is_not_honored(tmp_path, adapter):
    """The scan is bounded to the first 20 lines; a verdict buried deeper
    is not honored (NOT_TESTED)."""
    result = dispatch(adapter, mode="late_verdict", verdict="PASS")
    assert result.status == COMPLETED
    assert result.verdict == "NOT_TESTED"


def test_failure_report_still_classified_before_verdict_parse(tmp_path, adapter):
    """The exit-0 failure-report classification takes precedence: a
    transcript that STARTS with a failure line is provider unavailability
    even if a later line says VERDICT: PASS."""
    os.environ["JF_STAGED_MODE"] = "fail_report"
    os.environ["JF_STAGED_VERDICT"] = "PASS"
    with pytest.raises(ProviderUnavailable):
        dispatch(adapter, mode="fail_report", verdict="PASS")


def test_nonzero_exit_still_honest_failure(tmp_path, adapter):
    result = dispatch(adapter, mode="down")
    assert result.status == FAILED
    assert result.error_class == "hermes_oneshot_failed"


def test_builder_role_also_parsed(tmp_path, adapter):
    result = dispatch(adapter, mode="verdict", verdict="FAIL")
    assert result.role == "verifier"
    # clear the staged env so the builder session has no verdict line
    del os.environ["JF_STAGED_MODE"]
    del os.environ["JF_STAGED_VERDICT"]
    result2 = adapter.dispatch(manifest(role="builder"),
                               heartbeat=lambda: None)
    # builder with no verdict line: NOT_TESTED (evidence records what the
    # session actually delivered; acceptance is gated on the verifier only)
    assert result2.verdict == "NOT_TESTED"
