"""V5-13 disposable three-connector composed mission harness."""

from __future__ import annotations

import subprocess
from pathlib import Path

from jarvis_foundry.connectors.browser import BrowserConnector
from jarvis_foundry.connectors.composed import ComposedMissionHarness
from jarvis_foundry.connectors.git import GitConnector
from jarvis_foundry.connectors.http import HttpServiceConnector
from jarvis_foundry.connectors.lineage import lineage_for_mission
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.owner_controls import OwnerControlService, sign_command

from test_browser_connector import _BrowserFixture
from v3_fixtures import make_bare_remote


def _worktree(bare: Path, tmp_path: Path) -> Path:
    work = tmp_path / "worktree"
    subprocess.run(["git", "clone", "-q", str(bare), str(work)], check=True)
    subprocess.run(["git", "-C", str(work), "config", "user.email", "compose@local"], check=True)
    subprocess.run(["git", "-C", str(work), "config", "user.name", "compose"], check=True)
    return work


def _harness(tmp_path: Path, fixture: _BrowserFixture):
    db = FoundryDatabase(tmp_path / "foundry.db")
    db.initialize()
    bare = make_bare_remote(tmp_path / "remote")
    work = _worktree(bare, tmp_path)
    http = HttpServiceConnector(fixture.url, timeout_s=0.2)
    return db, bare, work, ComposedMissionHarness(
        db, http=http, git=GitConnector(db, owned_root=tmp_path),
        browser=BrowserConnector(http), worktree=work, remote=bare,
    )


def test_composed_mission_runs_three_connectors_and_replays_without_duplicates(tmp_path: Path) -> None:
    fixture = _BrowserFixture()
    try:
        db, bare, work, harness = _harness(tmp_path, fixture)
        result = harness.run("mission-compose")
        assert result["status"] == "COMPLETED"
        assert result["connector_classes"] == ["http_service", "git", "browser"]
        assert len(result["lineage_ids"]) == 3
        assert fixture.state.count == 1
        remote_head = subprocess.run(
            ["git", "ls-remote", str(bare), "main"], capture_output=True, text=True, check=True,
        ).stdout.split()[0]
        assert remote_head == result["git_head"]
        replay = harness.run("mission-compose")
        assert replay["status"] == "REPLAYED"
        assert fixture.state.count == 1
        with db.connect() as connection:
            completed = connection.execute(
                "SELECT COUNT(*) FROM events WHERE event_type = 'composed.mission.completed'"
            ).fetchone()[0]
        assert completed == 1
        assert len(lineage_for_mission(db, "mission-compose")) == 3
        assert not subprocess.run(
            ["git", "-C", str(work), "status", "--porcelain"], capture_output=True, text=True, check=True,
        ).stdout.strip()
    finally:
        fixture.close()


def test_composed_mission_stop_and_veto_are_core_gates(tmp_path: Path) -> None:
    fixture = _BrowserFixture()
    try:
        db, _bare, _work, harness = _harness(tmp_path, fixture)
        stop_marker = tmp_path / "STOP"
        stop_marker.write_text("stop", encoding="utf-8")
        stopped = harness.run("mission-stop", stop_path=stop_marker)
        assert stopped["status"] == "BLOCKED"
        stop_marker.unlink()
        secret = b"compose-owner-secret-32-bytes-minimum!!"
        service = OwnerControlService(db, secret=secret, owner_id="compose-owner")
        nonce = "compose-veto-nonce"
        signature = sign_command(secret, "VETO_ACTION", "mission-veto", "freeze composed mission", nonce)
        service.apply(
            "VETO_ACTION", target="mission-veto", exact_text="freeze composed mission",
            nonce=nonce, signature=signature, directive_id="compose-veto",
        )
        vetoed = harness.run("mission-veto")
        assert vetoed["status"] == "BLOCKED"
        assert "VETO_ACTION" in vetoed["reason"]
    finally:
        fixture.close()


def test_http_outage_isolated_from_unrelated_git_work(tmp_path: Path) -> None:
    fixture = _BrowserFixture()
    db, bare, work, harness = _harness(tmp_path, fixture)
    fixture.close()
    blocked = harness.run("mission-http-outage")
    assert blocked["status"] == "BLOCKED"
    connector = GitConnector(db, owned_root=tmp_path)
    head = connector.create_commit(work, {"unrelated.txt": "git still works\n"}, "unrelated git work")
    pushed = connector.push(action_id="unrelated-git", worktree=work, remote=bare, expected_head=head)
    assert pushed["outcome"] == "COMPLETED"
    assert subprocess.run(
        ["git", "ls-remote", str(bare), "main"], capture_output=True, text=True, check=True,
    ).stdout.split()[0] == head

