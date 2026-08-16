"""Real English and Albanian interaction through the HTTP surface."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from r10_helpers import EVIDENCE_ROOT, make_layout, seed_layout

from jarvis_foundry.command_center.server import render_index, serve


def _server(tmp_path: Path):
    layout = make_layout(tmp_path)
    seed_layout(layout)
    running = serve(layout, evidence_root=EVIDENCE_ROOT, seed=False)
    return running, layout


def test_english_page_is_real_interaction(tmp_path: Path) -> None:
    running, _ = _server(tmp_path)
    try:
        with urlopen(f"{running.base_url}/", timeout=5) as response:
            body = response.read().decode("utf-8")
        assert '<html lang="en">' in body
        assert "JARVIS Command Center" in body
        assert "Owner directive control" in body
        assert "Briefings" in body
        assert "STOP" in body
    finally:
        running.close()


def test_albanian_page_is_real_interaction(tmp_path: Path) -> None:
    running, _ = _server(tmp_path)
    try:
        with urlopen(f"{running.base_url}/?lang=sq", timeout=5) as response:
            body = response.read().decode("utf-8")
        assert '<html lang="sq">' in body
        assert "Qendra e Komandës JARVIS" in body
        assert "Kontrolli i direktivave të pronarit" in body
        assert "Përmbledhjet" in body
        assert "Misionet" in body
        assert "RIFILLO" in body
    finally:
        running.close()


def test_briefing_endpoint_returns_correct_language(tmp_path: Path) -> None:
    running, _ = _server(tmp_path)
    try:
        with urlopen(f"{running.base_url}/api/briefings?lang=sq", timeout=5) as response:
            albanian = json.loads(response.read())
        assert albanian["lang"] == "sq"
        assert albanian["categories"][0]["category"] == "Fakt"
        with urlopen(f"{running.base_url}/api/briefings?lang=en", timeout=5) as response:
            english = json.loads(response.read())
        assert english["lang"] == "en"
        assert english["categories"][0]["category"] == "Fact"
        assert albanian["content_hash"] != english["content_hash"]
        assert "Faza" in albanian["categories"][0]["text"]
        assert "Stage" not in albanian["categories"][0]["text"]
    finally:
        running.close()


def test_unsupported_language_is_rejected(tmp_path: Path) -> None:
    running, _ = _server(tmp_path)
    try:
        try:
            urlopen(f"{running.base_url}/api/briefings?lang=de", timeout=5)
        except HTTPError as error:
            assert error.code == 400
        else:
            raise AssertionError("unsupported language accepted")
        try:
            urlopen(f"{running.base_url}/?lang=de", timeout=5)
        except HTTPError as error:
            assert error.code == 400
        else:
            raise AssertionError("unsupported page language accepted")
    finally:
        running.close()


def test_render_index_marks_empty_stale_degraded_provider_states(tmp_path: Path) -> None:
    from r10_helpers import FIXED_UTC

    from jarvis_foundry.command_center.server import CommandCenterApp
    from jarvis_foundry.command_center.state import build_r10_snapshot

    layout = make_layout(tmp_path, "main")
    seed_layout(layout)
    snapshot = build_r10_snapshot(layout, now=FIXED_UTC)
    app = CommandCenterApp(layout, evidence_root=EVIDENCE_ROOT, seed=False)
    html = render_index(app, snapshot, "en")
    assert "banner-degraded" in html
    assert "banner-provider" in html

    empty_layout = make_layout(tmp_path, "empty")
    empty_snapshot = build_r10_snapshot(empty_layout, now=FIXED_UTC)
    empty_html = render_index(app, empty_snapshot, "en")
    assert "banner-empty" in empty_html
    assert "No durable records yet" in empty_html
