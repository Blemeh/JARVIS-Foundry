"""A real bounded code candidate completes a full lifecycle."""

from __future__ import annotations

from r11_helpers import run_mini_cycle


def test_real_r1_cycle_promotes_with_real_measurement(tmp_path) -> None:
    store, record = run_mini_cycle(tmp_path, "c1")
    assert record["outcome"] == "promoted"
    assert record["risk_class"] == "R1"
    assert record["baseline_metrics"]["pytest_passed"] == 2
    assert record["candidate_metrics"]["pytest_passed"] == 6
    assert record["verification"]["verdict"] == "PASS"
    assert str(record["verification"]["verifier_id"]).startswith("r11-fresh-verifier-")
    promotion = record["promotion"]
    assert promotion["live_before_hash"] != promotion["live_after_hash"]
    chain = store.ledger.verify_chain()
    assert chain.valid


def test_real_broken_candidate_rejects_with_history_preserved(tmp_path) -> None:
    store, record = run_mini_cycle(tmp_path, "c3")
    assert record["outcome"] == "rejected"
    assert record["verification"]["verdict"] == "FAIL"
    assert record["rejection"]["failed_history_ref"].startswith("failed_history/")
    assert store.failed_history_exists(str(record["cycle_id"]))
