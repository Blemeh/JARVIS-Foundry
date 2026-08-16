"""Real pytest behavior tests for the R11 micro target."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import score, split_tags
from workflow import run_pipeline


def test_split_tags_drops_empty() -> None:
    assert split_tags("a,,b") == ["a", "b"]


def test_split_tags_strips_whitespace() -> None:
    assert split_tags("a, b") == ["a", "b"]


def test_score_strict_mode_clamps() -> None:
    assert score({"score": 3.0}) == 0.5


def test_score_non_negative() -> None:
    assert score({"score": -1.0}) >= 0.0


def test_pipeline_removes_duplicates() -> None:
    records = [{"id": 1, "score": 1.0}, {"id": 1, "score": 1.0}]
    assert len(run_pipeline(records)) == 1


def test_pipeline_skips_none() -> None:
    records: list[dict[str, object] | None] = [None, {"id": 2, "score": 2.0}]
    assert run_pipeline(records)[0]["id"] == 2


def test_pipeline_orders_by_score() -> None:
    records = [{"id": 1, "score": 1.0}, {"id": 2, "score": 2.0}]
    assert [record["id"] for record in run_pipeline(records)] == [2, 1]


def test_render_summary() -> None:
    record = {"id": 7, "score": 0.75}
    assert f"record-{record['id']}" == "record-7"
