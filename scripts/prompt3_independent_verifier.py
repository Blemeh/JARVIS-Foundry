"""Read-only Prompt 3 independent verifier. Does not mutate live state."""
from __future__ import annotations

import json
import sqlite3
import subprocess
from pathlib import Path

HOME = Path(r"C:\Users\b\AppData\Local\hermes\foundry")
INSTALL = Path.home() / "AppData" / "Local" / "JARVIS"
GATES = Path("docs/evidence/PROMPT3_RECOVERY/LIVE_GATES.json")
OUT = Path("docs/evidence/PROMPT3_RECOVERY/INDEPENDENT_VERIFIER.json")


def sha(ref: str) -> str:
    return subprocess.check_output(["git", "rev-parse", ref], text=True).strip()


def main() -> int:
    con = sqlite3.connect(HOME / "foundry.db")
    con.row_factory = sqlite3.Row
    gates = json.loads(GATES.read_text(encoding="utf-8"))
    schema = con.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    current = (INSTALL / "CURRENT").read_text(encoding="utf-8").strip()
    previous = (INSTALL / "PREVIOUS").read_text(encoding="utf-8").strip()
    supervisor = dict(con.execute("SELECT * FROM supervisor_state").fetchone())
    stop = (HOME / "EMERGENCY_STOP").exists()
    prompt3 = [dict(r) for r in con.execute(
        "SELECT mission_id, state FROM missions WHERE mission_id LIKE '%prompt3%' "
        "OR created_from LIKE 'candidate:%' ORDER BY updated_at DESC")]
    complete = [m for m in prompt3 if m["state"] == "COMPLETE"]
    verdicts = {name: gate.get("verdict") for name, gate in gates["gates"].items()}
    failed_gates = [name for name, verdict in verdicts.items() if verdict != "PASS"]
    findings = []
    if sha("HEAD") != sha("main") or sha("main") != sha("origin/main"):
        findings.append("HEAD/main/origin diverge")
    if schema != 34:
        findings.append(f"schema {schema} != 34")
    if current != "b59836772d948bd7b2507949c42f3c7dfa75afaf" and current != sha("HEAD"):
        findings.append("CURRENT pointer unexpected")
    if stop:
        findings.append("STOP sentinel still active")
    if supervisor.get("status") != "RUNNING":
        findings.append("supervisor not RUNNING")
    if len(complete) < 2:
        findings.append("fewer than two Prompt-3 COMPLETE missions")
    if failed_gates:
        findings.append(f"gate FAIL: {failed_gates}")
    if gates["gates"]["M6A"].get("openhands_live_coding") != "NOT_READY":
        findings.append("OpenHands live coding overclaimed")
    production_paths = gates["gates"]["M6A"].get("production_paths")
    if production_paths != 1:
        findings.append("production mission path count != 1")
    result = {
        "role": "independent-verifier",
        "inspected": {
            "head": sha("HEAD"),
            "main": sha("main"),
            "origin_main": sha("origin/main"),
            "current_release": current,
            "previous_release": previous,
            "schema": schema,
            "supervisor": supervisor,
            "stop_active": stop,
            "prompt3_missions": prompt3[:12],
            "complete_count": len(complete),
            "perception_observed": con.execute(
                "SELECT COUNT(*) FROM events WHERE event_type='perception.observed'"
            ).fetchone()[0],
            "workflow_pattern_evidence": con.execute(
                "SELECT COUNT(*) FROM events WHERE event_type='workflow.pattern_evidence'"
            ).fetchone()[0],
            "procedure_effect_verified": con.execute(
                "SELECT COUNT(*) FROM events WHERE event_type='procedure.effect.verified'"
            ).fetchone()[0],
            "live_gates": str(GATES),
        },
        "gate_verdicts": verdicts,
        "findings": findings,
        "unresolved_critical": [],
        "unresolved_high": [],
        "verdict": "PASS" if not findings else "FAIL",
        "prompt4_ready": "YES" if not findings else "NO",
        "l6_unattended": "NOT YET GATED",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"wrote": str(OUT), "verdict": result["verdict"],
                      "findings": findings}, indent=2))
    return 0 if result["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
