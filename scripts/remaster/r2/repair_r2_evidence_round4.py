"""Round-4 deterministic repair of R2 forensic evidence (AST-exact spans).

R2 verifier round 3 (FAIL) found the work-package counter metadata swap plus
residual non-AST-exact ranges. This script applies the full set of corrections
verified by scripts/remaster/r2/audit_r2_spans.py so every cited function/class
range matches its AST span exactly.
"""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[3]
FINDINGS = ROOT / "docs" / "remaster" / "evidence" / "r2" / "R2_FORENSIC_FINDINGS.json"
SUMMARY = ROOT / "docs" / "remaster" / "evidence" / "r2" / "R2_SUMMARY.md"


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

    # R2-F001: reset spans 1729-1740 (AST), not 1729-1739.
    f = findings["R2-F001"]
    f["measured_evidence"] = f["measured_evidence"].replace(
        "reset spans 1729-1739", "reset spans 1729-1740"
    )
    set_ranges(f, "src/jarvis_foundry/projections.py", [[58, 1726], [1729, 1740]])

    # R2-F005: FakeConnector class 18-55.
    f = findings["R2-F005"]
    f["measured_evidence"] = f["measured_evidence"].replace(
        "FakeConnector:18-60", "FakeConnector:18-55"
    )
    set_ranges(f, "src/jarvis_foundry/connectors/fakes.py", [[18, 55]])

    # R2-F006: enable_scheduler 117-123.
    f = findings["R2-F006"]
    set_ranges(f, "src/jarvis_foundry/evolution/scheduler.py", [[117, 123], [156, 246]])

    # R2-F007: _advance 160-193; PolicyBroker 195-459; issue_capability 68-109;
    # register_capability 66-86.
    f = findings["R2-F007"]
    f["measured_evidence"] = f["measured_evidence"].replace(
        "MissionLoop._advance at mission/loop.py:160-190",
        "MissionLoop._advance at mission/loop.py:160-193",
    ).replace(
        "evolution/registry.py:214-277 promote_candidate",
        "evolution/registry.py:214-277 promote_candidate",
    )
    set_ranges(f, "src/jarvis_foundry/mission/loop.py", [[160, 193], [286, 287]])
    set_ranges(f, "src/jarvis_foundry/control/broker.py", [[195, 459]])
    set_ranges(f, "src/jarvis_foundry/control/capability_broker.py", [[68, 109]])
    set_ranges(f, "src/jarvis_foundry/capabilities/registry.py", [[66, 86]])

    # R2-F013: configure_owner 548-553.
    f = findings["R2-F013"]
    set_ranges(f, "src/jarvis_foundry/command_center.py", [[548, 553]])

    # R2-F014: service.py range must reflect start 34-65 and heartbeat 67-90.
    f = findings["R2-F014"]
    set_ranges(f, "src/jarvis_foundry/supervisor/service.py", [[34, 65], [67, 90]])

    # R2-F015: DispatchError 40-41; FakeConnector 18-55; _last_file_hash 58-69.
    f = findings["R2-F015"]
    set_ranges(f, "src/jarvis_foundry/roles/dispatcher.py", [[40, 41]])
    set_ranges(f, "src/jarvis_foundry/connectors/fakes.py", [[18, 55]])
    set_ranges(f, "src/jarvis_foundry/live_ingestion.py", [[58, 69]])

    # R2-F017: enable_scheduler 117-123.
    f = findings["R2-F017"]
    set_ranges(f, "src/jarvis_foundry/evolution/scheduler.py", [[1, 5], [117, 123], [156, 246]])

    # R2-F024: hard-kill test 306-378; gateway-loss test 269-327.
    f = findings["R2-F024"]
    f["measured_evidence"] = f["measured_evidence"].replace(
        "hard-kills spawned daemons (306-354)", "hard-kills spawned daemons (306-378)"
    ).replace(
        "drives a staged gateway via HERMES_HOME (13-22)",
        "drives a staged gateway via HERMES_HOME (13-22)",
    )
    set_ranges(f, "tests/foundry/test_installer.py", [[306, 378]])
    set_ranges(f, "tests/foundry/test_role_gateway_kill.py", [[269, 327]])

    # R2-F028: reconciliation_matrix 143-145.
    f = findings["R2-F028"]
    set_ranges(f, "src/jarvis_foundry/capabilities/registry.py", [[143, 145]])

    with FINDINGS.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=2)
        fh.write("\n")

    text = SUMMARY.read_text(encoding="utf-8")
    text = text.replace("hard-kills spawned daemons", "hard-kills spawned daemons")
    SUMMARY.write_text(text, encoding="utf-8", newline="\n")

    print("R2 evidence round-4 repaired: AST-exact spans applied")


if __name__ == "__main__":
    main()
