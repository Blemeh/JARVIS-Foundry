from __future__ import annotations

import json
from pathlib import Path

import architecture_rules
import no_silent_failure
import secret_scan

REPO_ROOT = Path(__file__).resolve().parents[3]


def test_architecture_rules_reject_kernel_to_adapter(tmp_path: Path) -> None:
    kernel = tmp_path / "src" / "jarvis_foundry" / "events.py"
    kernel.parent.mkdir(parents=True)
    kernel.write_text(
        "from jarvis_foundry.connectors.fakes import FakeConnector\n",
        encoding="utf-8",
    )
    adapter = tmp_path / "src" / "jarvis_foundry" / "connectors" / "fakes.py"
    adapter.parent.mkdir(parents=True)
    adapter.write_text(
        "from jarvis_foundry.database import FoundryDatabase\n",
        encoding="utf-8",
    )
    violations = architecture_rules.collect_violations(tmp_path)
    kinds = [violation["kind"] for violation in violations]
    assert "kernel_to_adapter_import" in kinds
    assert "adapter_to_kernel_state_import" in kinds
    assert all(violation["path"] for violation in violations)


def test_secret_scan_detects_generic_assignment() -> None:
    value = "abcdefghijklmnopqrstuvwxyz"
    matches = secret_scan.scan_bytes(f"api_key = '{value}'\n".encode(), "example.txt")
    assert any(match["pattern"] == "generic_secret_assignment" for match in matches)


def test_secret_scan_is_non_emitting() -> None:
    value = "abcdefghijklmnopqrstuvwxyz"
    matches = secret_scan.scan_bytes(f"token: {value}\n".encode(), "example.txt")
    rendered = json.dumps(matches)
    assert value not in rendered
    assert all(match["value_sha256"] for match in matches)


def test_no_silent_failure_flags_broad_handler(tmp_path: Path) -> None:
    kernel = tmp_path / "src" / "jarvis_foundry" / "events.py"
    kernel.parent.mkdir(parents=True)
    kernel.write_text(
        "def handle() -> None:\n"
        "    try:\n"
        "        value = 1\n"
        "    except BaseException:\n"
        "        return\n",
        encoding="utf-8",
    )
    findings = no_silent_failure.scan_kernel(tmp_path)
    assert len(findings) == 1
    assert findings[0]["path"] == "src/jarvis_foundry/events.py"
    assert findings[0]["handler"] == "BaseException"


def test_mutation_manifest_is_frozen_and_anchors_present() -> None:
    manifest_path = REPO_ROOT / "scripts" / "remaster" / "r3" / "frozen_mutants.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema"] == "jarvis-r3-frozen-mutant-manifest-v1"
    assert manifest["frozen"] is True
    mutants = manifest["mutants"]
    assert len(mutants) == 12
    for mutant in mutants:
        target = REPO_ROOT / mutant["target"]
        assert target.is_file(), mutant["target"]
        assert mutant["anchor"] in target.read_text(encoding="utf-8"), mutant["id"]
        assert mutant["expected_test"]
        assert mutant["pytest_args"]
