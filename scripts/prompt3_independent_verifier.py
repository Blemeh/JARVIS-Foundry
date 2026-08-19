"""Fresh read-only Prompt 3 independent verifier. Does not mutate live state.

full_suite success is never hardcoded. It must be proven from a real junit
and/or stdout log, or the suite may be executed via --run-suite. Missing
artifacts omit full_suite and fail the gate.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
HOME = Path(r"C:\Users\b\AppData\Local\hermes\foundry")
INSTALL = Path.home() / "AppData" / "Local" / "JARVIS"
EVIDENCE = REPO / "docs" / "evidence" / "PROMPT3_RECOVERY"
GATES = EVIDENCE / "LIVE_GATES.json"
OUT = EVIDENCE / "INDEPENDENT_VERIFIER.json"
STAMPED = EVIDENCE / "INDEPENDENT_VERIFIER_FRESH.json"
DEFAULT_JUNIT = EVIDENCE / "FULL_SUITE.junit.xml"
DEFAULT_LOG = EVIDENCE / "FULL_SUITE.stdout.log"
SUITE_CMD = [str(REPO / ".venv" / "Scripts" / "python.exe"), "-m", "pytest",
             "tests/foundry", "tests/convergence", "-q", "--tb=line"]


def sha(ref: str) -> str:
    return subprocess.check_output(["git", "rev-parse", ref], cwd=REPO, text=True).strip()


def _count(con: sqlite3.Connection, sql: str, params: tuple = ()) -> int:
    row = con.execute(sql, params).fetchone()
    return int(row[0] if row else 0)


def _ps_matching(needle: str) -> list[dict]:
    raw = subprocess.check_output(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            (
                "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*"
                + needle
                + "*' } | ForEach-Object { "
                "'{0}|{1}|{2}' -f $_.ProcessId, $_.ParentProcessId, $_.CommandLine }"
            ),
        ],
        text=True,
        stderr=subprocess.DEVNULL,
    )
    rows = []
    for line in raw.splitlines():
        line = line.strip()
        if not line or "|" not in line:
            continue
        pid, ppid, cmd = line.split("|", 2)
        rows.append({"pid": pid, "ppid": ppid, "cmd": cmd[:240]})
    return rows


def _attr_int(element: ET.Element, *keys: str) -> int | None:
    for key in keys:
        if key in element.attrib:
            return int(float(element.attrib[key]))
    return None


def parse_junit(path: Path) -> dict:
    tree = ET.parse(path)
    root = tree.getroot()
    suites = [root] if root.tag == "testsuite" else list(root.findall("testsuite"))
    if root.tag == "testsuites" and not suites:
        suites = list(root.iter("testsuite"))
    tests = _attr_int(root, "tests")
    failures = _attr_int(root, "failures")
    errors = _attr_int(root, "errors")
    skipped = _attr_int(root, "skipped")
    if tests is None or failures is None or errors is None:
        tests = failures = errors = skipped = 0
        for suite in suites:
            tests += _attr_int(suite, "tests") or 0
            failures += _attr_int(suite, "failures") or 0
            errors += _attr_int(suite, "errors") or 0
            skipped += _attr_int(suite, "skipped") or 0
    failed_cases: list[str] = []
    for case in root.iter("testcase"):
        if case.find("failure") is not None or case.find("error") is not None:
            failed_cases.append(f"{case.get('classname')}::{case.get('name')}")
    ok = tests > 0 and failures == 0 and errors == 0
    return {
        "path": str(path),
        "mtime_local": datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
        "tests": tests,
        "failures": failures,
        "errors": errors,
        "skipped": skipped or 0,
        "failed_cases": failed_cases[:20],
        "ok": ok,
        "exit_code": 0 if ok else 1,
    }


def load_full_suite(args: argparse.Namespace) -> dict | None:
    junit_path = Path(args.junit) if args.junit else Path(
        os.environ.get("PROMPT3_FULL_SUITE_JUNIT", DEFAULT_JUNIT)
    )
    log_path = Path(args.log) if args.log else Path(
        os.environ.get("PROMPT3_FULL_SUITE_LOG", DEFAULT_LOG)
    )
    if args.run_suite:
        junit_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = SUITE_CMD + [f"--junitxml={junit_path}"]
        env = os.environ.copy()
        env["PYTHONPATH"] = str(REPO / "src")
        with log_path.open("w", encoding="utf-8") as log:
            log.write("command: " + " ".join(cmd) + "\nPYTHONPATH=" + env["PYTHONPATH"] + "\n\n")
            log.flush()
            proc = subprocess.run(cmd, cwd=REPO, env=env, stdout=log, stderr=subprocess.STDOUT)
        recorded_exit = proc.returncode
    else:
        recorded_exit = None
    if not junit_path.is_file() and not log_path.is_file():
        return None
    payload: dict = {
        "command": " ".join(SUITE_CMD),
        "pythonpath": "src",
        "junit": str(junit_path) if junit_path.is_file() else None,
        "log": str(log_path) if log_path.is_file() else None,
        "source": "artifacts",
    }
    if junit_path.is_file():
        parsed = parse_junit(junit_path)
        payload.update(parsed)
        payload["source"] = "junit"
    else:
        payload["ok"] = False
        payload["exit_code"] = None
        payload["reason"] = "junit missing; cannot prove pytest success from log alone"
    if recorded_exit is not None:
        payload["process_exit_code"] = recorded_exit
        payload["source"] = "run-suite"
        if recorded_exit != 0:
            payload["ok"] = False
            payload["exit_code"] = recorded_exit
        elif not payload.get("ok"):
            payload["ok"] = False
    if log_path.is_file():
        payload["log_mtime_local"] = datetime.fromtimestamp(
            log_path.stat().st_mtime
        ).isoformat(timespec="seconds")
        payload["log_bytes"] = log_path.stat().st_size
    return payload


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prompt 3 independent verifier")
    parser.add_argument("--junit", help="Path to pytest junit xml")
    parser.add_argument("--log", help="Path to pytest stdout log")
    parser.add_argument(
        "--run-suite",
        action="store_true",
        help="Execute the full suite once and record junit+log before inspecting live state",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    ran_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    con = sqlite3.connect(HOME / "foundry.db")
    con.row_factory = sqlite3.Row
    gates = json.loads(GATES.read_text(encoding="utf-8"))
    schema = con.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    current = (INSTALL / "CURRENT").read_text(encoding="utf-8").strip()
    previous = (INSTALL / "PREVIOUS").read_text(encoding="utf-8").strip()
    supervisor = dict(con.execute("SELECT * FROM supervisor_state").fetchone())
    stop = (HOME / "EMERGENCY_STOP").exists()
    t193243 = dict(con.execute(
        "SELECT mission_id, state FROM missions WHERE mission_id=?",
        ("mission-prompt3-m6a-T193243Z",),
    ).fetchone() or {"mission_id": "mission-prompt3-m6a-T193243Z", "state": "MISSING"})
    t193251 = dict(con.execute(
        "SELECT mission_id, state FROM missions WHERE mission_id=?",
        ("mission-prompt3-m6a-T193251Z",),
    ).fetchone() or {"mission_id": "mission-prompt3-m6a-T193251Z", "state": "MISSING"})
    prompt3 = [dict(r) for r in con.execute(
        "SELECT mission_id, state FROM missions WHERE mission_id LIKE '%prompt3%' "
        "OR created_from LIKE 'candidate:%' ORDER BY updated_at DESC"
    )]
    complete = [m for m in prompt3 if m["state"] == "COMPLETE"]
    verdicts = {name: gate.get("verdict") for name, gate in gates["gates"].items()}
    failed_gates = [name for name, verdict in verdicts.items() if verdict != "PASS"]
    launchers = [
        row for row in _ps_matching("jarvis_foundry.deployment.launcher")
        if "Get-CimInstance" not in row["cmd"]
    ]
    sha_re = re.compile(r"releases\\([0-9a-f]{40})\\", re.I)
    release_ids = sorted({
        m.group(1).lower()
        for row in launchers
        for m in sha_re.finditer(row["cmd"])
    })
    findings = []
    unresolved_critical: list[str] = []
    unresolved_high: list[str] = []
    full_suite = load_full_suite(args)
    if full_suite is None:
        findings.append("full_suite omitted: no junit/log artifact; gate fails")
        unresolved_high.append("full pytest suite not proven (no junit/log)")
    elif not full_suite.get("ok"):
        findings.append(
            "full_suite not green: "
            f"exit_code={full_suite.get('exit_code')} "
            f"tests={full_suite.get('tests')} "
            f"failures={full_suite.get('failures')} "
            f"errors={full_suite.get('errors')} "
            f"reason={full_suite.get('reason')}"
        )
        unresolved_high.append("full pytest suite not proven green from artifacts")
    if sha("HEAD") != sha("main") or sha("main") != sha("origin/main"):
        findings.append("HEAD/main/origin diverge")
    if schema != 34:
        findings.append(f"schema {schema} != 34")
    if stop:
        findings.append("STOP sentinel still active")
    if supervisor.get("status") != "RUNNING":
        findings.append("supervisor not RUNNING")
    if int(supervisor.get("singleton") or 0) != 1:
        findings.append(f"supervisor singleton={supervisor.get('singleton')}")
    if len(complete) < 2:
        findings.append("fewer than two Prompt-3 COMPLETE missions")
    if failed_gates:
        findings.append(f"gate FAIL: {failed_gates}")
    if gates["gates"]["M6A"].get("openhands_live_coding") != "NOT_READY":
        findings.append("OpenHands live coding overclaimed")
    if gates["gates"]["M7D"].get("desktop") not in {"PROBING", "NOT_READY"}:
        findings.append("OpenAdapt desktop overclaimed")
    production_paths = gates["gates"]["M6A"].get("production_paths")
    if production_paths != 1:
        findings.append("production mission path count != 1")
    if t193243.get("state") == "COMPLETE":
        findings.append("raced T193243Z faked COMPLETE")
    if t193243.get("state") != "BLOCKED":
        unresolved_high.append(f"T193243Z state={t193243.get('state')} (expected honest BLOCKED)")
    if t193251.get("state") != "COMPLETE":
        findings.append(f"T193251Z state={t193251.get('state')} != COMPLETE")
    if len(release_ids) > 1:
        unresolved_high.append(
            "multiple release trees executing launcher concurrently: " + ",".join(release_ids)
        )
    if not release_ids:
        unresolved_high.append("no live launcher process found")
    live_matches_current = current.lower() in {rid.lower() for rid in release_ids}
    if not live_matches_current:
        unresolved_high.append(
            f"CURRENT pointer {current} is not the live launcher tree {release_ids} "
            "(capability is not CURRENT; live process not killed)"
        )
    capability_is_current = live_matches_current and not unresolved_high and not findings
    result = {
        "role": "independent-verifier",
        "ran_at": ran_at,
        "supersedes": ["2026-08-19T21:35:40", "2026-08-19T21:52:46", "2026-08-19T20:28:54Z"],
        "full_suite": full_suite,
        "identity": {
            "git_head": sha("HEAD"),
            "git_main": sha("main"),
            "origin_main": sha("origin/main"),
            "current_release_pointer": current,
            "previous_release_pointer": previous,
            "live_release_trees": release_ids,
            "live_matches_current": live_matches_current,
            "note": (
                "CURRENT/live are the installed capability identity. "
                "git HEAD may be docs-ahead of CURRENT; do not relabel live as HEAD "
                "or as an older release tree."
            ),
        },
        "inspected": {
            "head": sha("HEAD"),
            "main": sha("main"),
            "origin_main": sha("origin/main"),
            "current_release_pointer": current,
            "previous_release_pointer": previous,
            "schema": schema,
            "supervisor": supervisor,
            "stop_active": stop,
            "t193243": t193243,
            "t193251": t193251,
            "prompt3_missions": prompt3[:12],
            "complete_count": len(complete),
            "mission_states": [tuple(r) for r in con.execute(
                "SELECT state, COUNT(*) FROM missions GROUP BY state"
            ).fetchall()],
            "agent_runs": _count(con, "SELECT COUNT(*) FROM agent_runs"),
            "verifications": _count(con, "SELECT COUNT(*) FROM verifications"),
            "verification_verdicts": _count(con, "SELECT COUNT(*) FROM verification_verdicts"),
            "directives": _count(con, "SELECT COUNT(*) FROM directives"),
            "candidates": _count(con, "SELECT COUNT(*) FROM candidates"),
            "perception_observed": _count(
                con, "SELECT COUNT(*) FROM events WHERE event_type='perception.observed'"
            ),
            "workflow_pattern_evidence": _count(
                con, "SELECT COUNT(*) FROM events WHERE event_type='workflow.pattern_evidence'"
            ),
            "procedure_effect_verified": _count(
                con, "SELECT COUNT(*) FROM events WHERE event_type='procedure.effect.verified'"
            ),
            "procedure_execution_requested": _count(
                con, "SELECT COUNT(*) FROM events WHERE event_type='procedure.execution.requested'"
            ),
            "verification_passed": _count(
                con, "SELECT COUNT(*) FROM events WHERE event_type='verification.passed'"
            ),
            "verification_failed": _count(
                con, "SELECT COUNT(*) FROM events WHERE event_type='verification.failed'"
            ),
            "worker_manifests": _count(con, "SELECT COUNT(*) FROM worker_manifests"),
            "tier_c_memories": _count(con, "SELECT COUNT(*) FROM tier_c_memories"),
            "tier_c_procedures": _count(con, "SELECT COUNT(*) FROM tier_c_procedures"),
            "live_launchers": launchers,
            "live_release_trees": release_ids,
            "live_gates": str(GATES),
        },
        "gate_verdicts": verdicts,
        "findings": findings,
        "unresolved_critical": unresolved_critical,
        "unresolved_high": unresolved_high,
        "capability_is_current": capability_is_current,
        "verdict": (
            "PASS" if (not findings and not unresolved_critical and not unresolved_high) else "FAIL"
        ),
        "prompt4_ready": (
            "YES"
            if (
                not findings
                and not unresolved_critical
                and not unresolved_high
                and live_matches_current
                and all(verdicts.get(name) == "PASS" for name in ("M6A", "M6B", "M7A", "M7B", "M7C", "M7D", "M8"))
            )
            else "NO"
        ),
        "l6_unattended": "NOT YET GATED",
        "openhands_live_coding": gates["gates"]["M6A"].get("openhands_live_coding"),
        "openadapt_desktop": gates["gates"]["M7D"].get("desktop"),
    }
    payload = json.dumps(result, indent=2, default=str)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(payload, encoding="utf-8")
    STAMPED.write_text(payload, encoding="utf-8")
    print(json.dumps({
        "wrote": [str(OUT), str(STAMPED)],
        "ran_at": ran_at,
        "verdict": result["verdict"],
        "prompt4_ready": result["prompt4_ready"],
        "findings": findings,
        "unresolved_high": unresolved_high,
        "identity": result["identity"],
        "full_suite_ok": None if full_suite is None else full_suite.get("ok"),
    }, indent=2))
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
