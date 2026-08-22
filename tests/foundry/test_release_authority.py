from __future__ import annotations

import json

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.deployment.layout import current_pointer, install_root
from jarvis_foundry.deployment.release import release_authority_report
from jarvis_foundry.supervisor.service import Supervisor


def _configure_install(tmp_path) -> tuple[object, str]:
    root = install_root()
    root.mkdir(parents=True, exist_ok=True)
    sha = "a" * 40
    current_pointer(root).write_text(f"{sha}\n", encoding="utf-8")
    return root, sha


def test_release_report_flags_empty_database_authority(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_INSTALL_ROOT", str(tmp_path / "install"))
    _configure_install(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    FoundryDatabase(home / "foundry.db").initialize()

    report = release_authority_report(foundry_home=home)

    assert report["status"] == "FILESYSTEM_ONLY_AUTHORITY"
    assert report["consistent"] is False
    assert report["database_release_records"] == 0


def test_release_report_accepts_current_recorded_in_database(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_INSTALL_ROOT", str(tmp_path / "install"))
    _root, sha = _configure_install(tmp_path)
    home = tmp_path / "home"
    database = FoundryDatabase(home / "foundry.db")
    database.initialize()
    with database.transaction() as connection:
        connection.execute(
            "INSERT INTO release_manifests("
            "release_id, source_commit, artifact_hashes_json, dependencies_json,"
            " runtime_identity_json, improvement_id, risk_class, migrations_json,"
            " verdicts_json, created_at, last_event_id, updated_at) "
            "VALUES ('rel-current', ?, '{}', '[]', '{}', 'improvement', 'LOW',"
            " '[]', '[]', '2026-08-21T00:00:00Z', 'event', '2026-08-21T00:00:00Z')",
            (sha,),
        )

    report = release_authority_report(foundry_home=home)

    assert report["status"] == "CONSISTENT"
    assert report["consistent"] is True
    assert report["database_current_recorded"] is True


def test_doctor_reports_filesystem_only_release_authority(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVIS_INSTALL_ROOT", str(tmp_path / "install"))
    _configure_install(tmp_path)
    home = tmp_path / "home"
    home.mkdir()
    database = FoundryDatabase(home / "foundry.db")
    database.initialize()
    supervisor = Supervisor(database, home / "STOP", instance_id="authority-doctor")

    report = supervisor.doctor()

    assert json.dumps(report)  # doctor output stays JSON serializable
    assert report["release_authority"]["status"] == "FILESYSTEM_ONLY_AUTHORITY"
