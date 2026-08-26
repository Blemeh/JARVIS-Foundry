"""Documentation staleness check (anti-stale contract, Phase 10).

Wraps scripts/doc_staleness_check.py: CURRENT_STATE.md must pin the source
state immediately before the final docs-only reconciliation commit and name
the schema; current-status surfaces must not carry forbidden stale phrases;
referenced evidence paths must exist.
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parents[2]


def test_doc_staleness_check_passes_on_current_tree():
    run = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "doc_staleness_check.py")],
        cwd=REPO, capture_output=True, text=True, timeout=120)
    assert run.returncode == 0, run.stdout + run.stderr
    assert "DOCUMENTATION STALENESS CHECK: PASS" in run.stdout


def test_current_state_names_real_head_and_schema():
    import subprocess as sp

    staleness = _load_staleness_module()
    head = sp.run(["git", "-C", str(REPO), "rev-parse", "HEAD"],
                  capture_output=True, text=True, check=True).stdout.strip()
    state_sha = staleness.documentation_state_sha(head)
    text = (REPO / "docs" / "CURRENT_STATE.md").read_text(encoding="utf-8")
    assert staleness.validate_documentation_pin(text, state_sha) == []
    sys.path.insert(0, str(REPO / "src"))
    from jarvis_foundry.migrations import SCHEMA_VERSION
    assert staleness.validate_schema_documentation(text, SCHEMA_VERSION) == []


def _load_staleness_module():
    import importlib.util

    path = REPO / "scripts" / "doc_staleness_check.py"
    spec = importlib.util.spec_from_file_location("doc_staleness_check", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_schema_check_rejects_incidental_36_without_semantic_marker():
    staleness = _load_staleness_module()
    text = """
    CURRENT SCHEMA: v34 live deployed database baseline (production unchanged)
    V2 KNOWLEDGE SCHEMA: v35 (24h-integration knowledge brain)
    V3 DISCOVERY/BUILDING SCHEMA: v36 (integrated on main)
    | Workflow Discovery Phase 3 | PASS — 36/36 focused |
    """
    failures = staleness.validate_schema_documentation(text, 36)
    assert any("CURRENT/CANDIDATE SCHEMA" in failure for failure in failures)


def test_schema_check_accepts_correct_v36_documentation():
    staleness = _load_staleness_module()
    text = """
    CURRENT SCHEMA: v34 live deployed database baseline (production unchanged)
    V2 KNOWLEDGE SCHEMA: v35 (24h-integration knowledge brain)
    V3 DISCOVERY/BUILDING SCHEMA: v36 (integrated on main)
    CURRENT/CANDIDATE SCHEMA: v36 (migrations.SCHEMA_VERSION)
    | Workflow Discovery Phase 3 | PASS — 36/36 focused |
    """
    assert staleness.validate_schema_documentation(text, 36) == []


def test_schema_check_rejects_contradictory_current_v35_claim():
    staleness = _load_staleness_module()
    text = """
    CURRENT SCHEMA: v34 live deployed database baseline (production unchanged)
    V2 KNOWLEDGE SCHEMA: v35 (24h-integration knowledge brain)
    V3 DISCOVERY/BUILDING SCHEMA: v36 (integrated on main)
    CURRENT/CANDIDATE SCHEMA: v35 (wrong)
    """
    failures = staleness.validate_schema_documentation(text, 36)
    assert any("CURRENT/CANDIDATE SCHEMA is v35" in failure for failure in failures)


def test_forbidden_stale_phrases_absent_from_current_surfaces():
    surfaces = ("docs/CURRENT_STATE.md",
                "docs/architecture/00_READ_FIRST.md",
                "docs/architecture/05_POST_V1_ROADMAP.md",
                "tests/acceptance/README.md")
    forbidden = ("BUILD NOT STARTED", "IMPLEMENTATION: NOT STARTED",
                 "START BUILD RECEIVED: NO", "Designed, not built",
                 "has not started implementation", "execution remains not started")
    for surface in surfaces:
        body = (REPO / surface).read_text(encoding="utf-8")
        for phrase in forbidden:
            assert phrase not in body, f"{surface} contains '{phrase}'"
