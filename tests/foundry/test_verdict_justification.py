"""Regression: an honest verdict WITH justification must still be read.

The live verifier instruction tells the worker to end its response with the
verdict line. Real sessions append their own justification to that line
("VERDICT: PASS (criteria confirmed by direct inspection)"). The strict
whole-line grammar read that as no verdict at all, recorded NOT_TESTED, and
blocked the mission instead of accepting independently verified work.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from jarvis_foundry.workers.adapters.hermes import HermesWorkerAdapter  # noqa: E402


def test_verdict_with_justification_is_read():
    transcript = "\n".join([
        "inspection transcript",
        "VERDICT: PASS (criteria 1, 2 and 4 confirmed by direct inspection)",
    ])
    assert HermesWorkerAdapter._structured_verdict(transcript) == "PASS"


def test_bare_verdict_line_still_read():
    assert HermesWorkerAdapter._structured_verdict("VERDICT: FAIL") == "FAIL"


def test_instruction_alternation_is_still_not_a_verdict():
    assert HermesWorkerAdapter._structured_verdict(
        "End your response with a line: VERDICT: PASS | FAIL | BLOCKED | NOT_TESTED",
    ) is None
    assert HermesWorkerAdapter._structured_verdict(
        "VERDICT: PASS | FAIL | BLOCKED | NOT_TESTED",
    ) is None


def test_word_boundary_still_rejects_passed():
    assert HermesWorkerAdapter._structured_verdict("VERDICT: PASSED") is None


def test_last_verdict_wins():
    transcript = "VERDICT: FAIL (first reading)\nVERDICT: PASS (after re-check)"
    assert HermesWorkerAdapter._structured_verdict(transcript) == "PASS"


def test_unrelated_text_is_not_a_verdict():
    assert HermesWorkerAdapter._structured_verdict(
        "[adapter] no structured verdict found in the session output",
    ) is None
