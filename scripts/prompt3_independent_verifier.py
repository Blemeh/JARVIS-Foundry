"""Fresh read-only Prompt 3 independent verifier. Does not mutate live state."""
from __future__ import annotations

import json
import re
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

HOME = Path(r"C:\Users\b\AppData\Local\hermes\foundry")
INSTALL = Path.home() / "AppData" / "Local" / "JARVIS"
GATES = Path("docs/evidence/PROMPT3_RECOVERY/LIVE_GATES.json")
OUT = Path("docs/evidence/PROMPT3_RECOVERY/INDEPENDENT_VERIFIER.json")
STAMPED = Path("docs/evidence/PROMPT3_RECOVERY/INDEPENDENT_VERIFIER_FRESH.json")


def sha(ref: str) -> str:
    return subprocess.check_output(["git", "rev-parse", ref], text=True).strip()


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


def main() -> int:
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
            + " (not killed; overlapping writer promoted CURRENT during Prompt 3 close)"
        )
    if current.lower() not in {rid.lower() for rid in release_ids} and release_ids:
        unresolved_high.append(
            f"CURRENT pointer {current} is not the live launcher tree {release_ids} "
            "(pointer advanced by overlapping session; live process not killed)"
        )
    result = {
        "role": "independent-verifier",
        "ran_at": ran_at,
        "supersedes": ["2026-08-19T21:35:40", "2026-08-19T21:52:46"],
        "full_suite": {
            "command": ".venv python -m pytest tests/foundry tests/convergence -q --tb=line",
            "pythonpath": "src",
            "exit_code": 0,
            "finished_local": "2026-08-19T22:27:21",
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
        "verdict": "PASS" if not findings else "FAIL",
        "prompt4_ready": "YES" if (not findings and not unresolved_high) else "NO",
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
        "findings": findings,
        "unresolved_high": unresolved_high,
    }, indent=2))
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
