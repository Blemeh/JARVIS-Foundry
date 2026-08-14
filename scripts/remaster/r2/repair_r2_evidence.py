"""Deterministic repair of R2 forensic evidence after verifier round 1 FAIL.

The fresh R2 verifier (round 1, FAIL) found evidence-accuracy defects: wrong
line ranges, wrong handler counts, and an overstated "zero runtime callers"
claim. This script applies only the exact verified corrections and rewrites the
two evidence files with the same indentation they used.
"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
FINDINGS = ROOT / "docs" / "remaster" / "evidence" / "r2" / "R2_FORENSIC_FINDINGS.json"
METRICS = ROOT / "docs" / "remaster" / "evidence" / "r2" / "R2_METRICS.json"


def set_ranges(entry: dict, path: str, ranges: list[list[int]]) -> None:
    for p in entry["paths"]:
        if p["path"] == path:
            p["line_ranges"] = ranges
            return
    raise KeyError(f"path not found: {path}")


def main() -> None:
    with FINDINGS.open(encoding="utf-8") as fh:
        data = json.load(fh)
    findings = {f["id"]: f for f in data["findings"]}

    # R2-F001: handler count 95 -> 100 distinct; rebuild_projections 130-141 -> 104-112.
    f = findings["R2-F001"]
    f["measured_evidence"] = f["measured_evidence"].replace(
        "95 exact event_type handlers plus 3 startswith families",
        "91 event_type equality branches plus 10 literal types inside 3 in-tuple/set branches (100 distinct handled types) plus 3 startswith families",
    ).replace(
        "rebuild_projections (database.py:130-141)",
        "rebuild_projections (database.py:104-112)",
    )
    set_ranges(f, "src/jarvis_foundry/database.py", [[104, 112]])

    # R2-F003: _safe_table 91-99 -> 91-97 in command_center.py broad-exception list.
    f = findings["R2-F003"]
    set_ranges(f, "src/jarvis_foundry/command_center.py", [[91, 97]])

    # R2-F005: FixtureSTT 40-46 -> 40-45; FixtureTTS 48-59 -> 48-51;
    # deliver_local 75-79 -> 75-77; test references with method.
    f = findings["R2-F005"]
    f["measured_evidence"] = f["measured_evidence"].replace(
        "voice.py:40-46 FixtureSTT", "voice.py:40-45 FixtureSTT"
    ).replace(
        "voice.py:48-59 FixtureTTS", "voice.py:48-51 FixtureTTS"
    ).replace(
        "briefings.py:75-79 deliver_local", "briefings.py:75-77 deliver_local"
    ).replace(
        "Executive entry points have 343 test-side references and zero runtime callers",
        "Executive entry points have 344 substring test-side references (word-boundary method: 311) and no caller reachable from the installed watchdog path",
    )
    set_ranges(f, "src/jarvis_foundry/voice.py", [[40, 45], [48, 51], [61, 150]])
    set_ranges(f, "src/jarvis_foundry/briefings.py", [[75, 77]])

    # R2-F006: "zero runtime callers" overstated; real call sites exist inside
    # unreachable modules. Correct to "no caller reachable from the installed
    # watchdog path" and list the exact call sites.
    f = findings["R2-F006"]
    f["measured_evidence"] = f["measured_evidence"].replace(
        "Source-tree call-site scan finds zero runtime callers; the only references are tests (343 matches) and docstrings.",
        "Source-tree call-site scan finds no caller reachable from the installed watchdog path; the only references are tests (344 substring matches, word-boundary 311), docstrings, and call sites inside other unreachable executive modules (supervisor/evolution_control.py:122/161, evolution/cycle.py:182/186/230, v3_loop.py:208/250), so none of the 17 executive entry points is reachable from the installed runtime.",
    )
    set_ranges(f, "src/jarvis_foundry/research_queue.py", [[48, 117]])
    set_ranges(f, "src/jarvis_foundry/supervisor/evolution_control.py", [[48, 215]])

    # R2-F012: exact handler count 95 -> 100; database rebuild span corrected.
    f = findings["R2-F012"]
    f["measured_evidence"] = f["measured_evidence"].replace(
        "projections.py handles 95 exact types plus 3 startswith families",
        "projections.py handles 100 distinct exact types (91 equality branches plus 10 literal types inside 3 in-tuple/set branches) plus 3 startswith families",
    ).replace(
        "rebuild_projections (database.py:130-141)",
        "rebuild_projections (database.py:104-112)",
    )
    set_ranges(f, "src/jarvis_foundry/database.py", [[104, 112]])

    # R2-F013: resolve_provider_auth 281-318 -> 281-324.
    f = findings["R2-F013"]
    f["measured_evidence"] = f["measured_evidence"].replace(
        "control/capability_broker.py:281-318", "control/capability_broker.py:281-324"
    )
    set_ranges(f, "src/jarvis_foundry/control/capability_broker.py", [[281, 324]])

    # R2-F014: heartbeat 67-99 -> 67-90.
    f = findings["R2-F014"]
    f["measured_evidence"] = f["measured_evidence"].replace(
        "heartbeat (service.py:67-99)", "heartbeat (service.py:67-90)"
    )

    # R2-F016: _safe_table 91-99 -> 91-97.
    f = findings["R2-F016"]
    f["measured_evidence"] = f["measured_evidence"].replace(
        "_safe_table (command_center.py:91-99)", "_safe_table (command_center.py:91-97)"
    )
    set_ranges(f, "src/jarvis_foundry/command_center.py", [[91, 97], [153, 154]])

    # R2-F020: deliver_local 75-79 -> 75-77; voice fixture spans corrected.
    f = findings["R2-F020"]
    f["measured_evidence"] = f["measured_evidence"].replace(
        "briefings.py:75-79", "briefings.py:75-77"
    )
    set_ranges(f, "src/jarvis_foundry/voice.py", [[40, 45], [48, 51], [61, 150]])
    set_ranges(f, "src/jarvis_foundry/briefings.py", [[75, 77]])

    # R2-F021: Event.create 44-68 -> 44-71; validate 73-81 -> 73-82.
    f = findings["R2-F021"]
    f["measured_evidence"] = f["measured_evidence"].replace(
        "Event.create (events.py:44-68)", "Event.create (events.py:44-71)"
    ).replace("validate (73-81)", "validate (73-82)")
    set_ranges(f, "src/jarvis_foundry/events.py", [[44, 71], [73, 82]])

    # R2-F022: busy_timeout 28-31 -> 24; lock retry 72-89 -> 92-113;
    # UNIQUE 574-585 -> 552 and 572.
    f = findings["R2-F022"]
    f["measured_evidence"] = f["measured_evidence"].replace(
        "busy_timeout=10000 (database.py:28-31)", "busy_timeout=10000 (database.py:24)"
    ).replace(
        "heartbeat lock retry budget 15.5s (run.py:72-89)",
        "heartbeat lock retry budget 15.5s (_heartbeat_with_lock_retry, run.py:92-113)",
    ).replace(
        "system_actions UNIQUE(system_id,target_ref,payload_hash) (migrations.py:574-585)",
        "system_actions UNIQUE(system_id,target_ref,payload_hash) (migrations.py:552 and 572)",
    )
    set_ranges(f, "src/jarvis_foundry/database.py", [[24, 24]])
    set_ranges(f, "src/jarvis_foundry/supervisor/run.py", [[92, 113]])
    set_ranges(f, "src/jarvis_foundry/migrations.py", [[552, 552], [572, 572]])

    # R2-F023: v20 rebuild 573-585 -> 557-585; rebuild_projections span corrected.
    f = findings["R2-F023"]
    f["measured_evidence"] = f["measured_evidence"].replace(
        "system_actions v20 at 573-585", "system_actions v20 at 557-585"
    ).replace(
        "rebuild_projections (database.py:130-141)",
        "rebuild_projections (database.py:104-112)",
    )
    set_ranges(f, "src/jarvis_foundry/migrations.py", [[8, 8], [498, 520], [557, 585]])
    set_ranges(f, "src/jarvis_foundry/database.py", [[104, 112]])

    # R2-F026: detect_research_items 48-119 -> 48-117.
    f = findings["R2-F026"]
    f["measured_evidence"] = f["measured_evidence"].replace(
        "detect_research_items (research_queue.py:48-119)",
        "detect_research_items (research_queue.py:48-117)",
    )
    set_ranges(f, "src/jarvis_foundry/research_queue.py", [[48, 117]])

    with FINDINGS.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")

    with METRICS.open(encoding="utf-8") as fh:
        metrics = json.load(fh)
    metrics["event_coverage"]["exact_handled"] = 100
    with METRICS.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(metrics, fh, indent=2)
        fh.write("\n")

    print("R2 evidence repaired:")
    print(f"  {FINDINGS.name}: 100 handled types, corrected spans applied")
    print(f"  {METRICS.name}: exact_handled=100")


if __name__ == "__main__":
    main()
