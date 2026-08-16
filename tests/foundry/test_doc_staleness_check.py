"""Documentation staleness check (anti-stale contract, Phase 10).

Wraps scripts/doc_staleness_check.py: CURRENT_STATE.md must name the real
git HEAD and schema; current-status surfaces must not carry forbidden
stale phrases; referenced evidence paths must exist.
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
    head = sp.run(["git", "-C", str(REPO), "rev-parse", "HEAD"],
                  capture_output=True, text=True, check=True).stdout.strip()
    text = (REPO / "docs" / "CURRENT_STATE.md").read_text(encoding="utf-8")
    assert head[:7] in text or head in text
    sys.path.insert(0, str(REPO / "src"))
    from jarvis_foundry.migrations import SCHEMA_VERSION
    assert f"v{SCHEMA_VERSION}" in text or str(SCHEMA_VERSION) in text


def test_forbidden_stale_phrases_absent_from_current_surfaces():
    surfaces = ("docs/CURRENT_STATE.md",
                "docs/architecture/00_READ_FIRST.md",
                "docs/architecture/05_POST_V1_ROADMAP.md")
    forbidden = ("BUILD NOT STARTED", "IMPLEMENTATION: NOT STARTED",
                 "START BUILD RECEIVED: NO", "Designed, not built",
                 "has not started implementation", "execution remains not started")
    for surface in surfaces:
        body = (REPO / surface).read_text(encoding="utf-8")
        for phrase in forbidden:
            assert phrase not in body, f"{surface} contains '{phrase}'"
