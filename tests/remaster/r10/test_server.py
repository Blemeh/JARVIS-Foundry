"""Command Center HTTP surface: health, state, evidence, directive endpoint."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from r10_helpers import EVIDENCE_ROOT, make_layout, seed_layout

from jarvis_foundry.command_center.server import serve


def test_health_and_state_endpoints(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    seed_layout(layout)
    running = serve(layout, evidence_root=EVIDENCE_ROOT, seed=False)
    try:
        with urlopen(f"{running.base_url}/api/health", timeout=5) as response:
            health = json.loads(response.read())
        assert health["status"] == "OK"
        with urlopen(f"{running.base_url}/api/state", timeout=5) as response:
            state = json.loads(response.read())
        assert state["schema"] == "jarvis-r10-command-center-state-v1"
        assert state["projects"]
        with urlopen(f"{running.base_url}/api/timeline", timeout=5) as response:
            timeline = json.loads(response.read())
        assert isinstance(timeline, list) and timeline
    finally:
        running.close()


def test_evidence_drilldown_endpoint(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    seed_layout(layout)
    running = serve(layout, evidence_root=EVIDENCE_ROOT, seed=False)
    try:
        with urlopen(f"{running.base_url}/api/state", timeout=5) as response:
            state = json.loads(response.read())
        mission_id = state["missions"][0]["record_id"]
        with urlopen(f"{running.base_url}/api/evidence/{mission_id}", timeout=5) as response:
            evidence = json.loads(response.read())
        assert evidence["record_id"] == mission_id
        assert len(evidence["json_hash"]) == 64
        assert evidence["provenance"]
        try:
            urlopen(f"{running.base_url}/api/evidence/not-a-record", timeout=5)
        except HTTPError as error:
            assert error.code == 404
        else:
            raise AssertionError("unknown record returned success")
    finally:
        running.close()


def test_directive_endpoint_is_durable_end_to_end(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    seed_layout(layout)
    running = serve(layout, evidence_root=EVIDENCE_ROOT, seed=False)
    base = running.base_url
    try:
        body = {
            "command_type": "VETO",
            "target": "cand-11d1ef18f372",
            "reason": "veto from endpoint",
            "nonce": "endpoint-veto-1",
            "value": "",
        }
        with urlopen(
            Request(f"{base}/api/directives", data=json.dumps(body).encode(), method="POST"),
            timeout=5,
        ) as response:
            payload = json.loads(response.read())
        assert payload["status"] == "APPLIED"
        with urlopen(f"{base}/api/state", timeout=5) as response:
            state = json.loads(response.read())
        assert "cand-11d1ef18f372" in state["control"]["vetoed_candidates"]
    finally:
        running.close()


def test_directive_endpoint_rejects_malformed(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    seed_layout(layout)
    running = serve(layout, evidence_root=EVIDENCE_ROOT, seed=False)
    try:
        body = {
            "command_type": "STOP",
            "target": "GLOBAL",
            "reason": "",
            "nonce": "bad-1",
            "value": "",
        }
        try:
            urlopen(
                Request(
                    f"{running.base_url}/api/directives",
                    data=json.dumps(body).encode(),
                    method="POST",
                ),
                timeout=5,
            )
        except HTTPError as error:
            assert error.code == 400
        else:
            raise AssertionError("malformed directive accepted")
    finally:
        running.close()


def test_page_contains_landmarks_and_audit_hook(tmp_path: Path) -> None:
    layout = make_layout(tmp_path)
    seed_layout(layout)
    running = serve(layout, evidence_root=EVIDENCE_ROOT, seed=False)
    try:
        with urlopen(f"{running.base_url}/?audit=1", timeout=5) as response:
            body = response.read().decode("utf-8")
        assert '<header class="topbar" role="banner">' in body
        assert '<nav aria-label="sections">' in body
        assert '<main id="main" class="content" role="main">' in body
        assert "runAccessibilityAudit" in body
        assert "banner-provider" in body
    finally:
        running.close()
