from __future__ import annotations

from pathlib import Path

from jarvis_foundry.deployment import release


def test_release_snapshot_bundles_launcher_templates_as_package_assets(
    tmp_path: Path,
) -> None:
    repo = tmp_path / "repo"
    (repo / "src" / "jarvis_foundry").mkdir(parents=True)
    (repo / "src" / "jarvis_foundry" / "__init__.py").write_text(
        "", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[project]\nname='fixture'\n", encoding="utf-8")
    templates = repo / "packaging"
    templates.mkdir()
    (templates / "jarvis-supervisor-headless.vbs").write_text(
        "Set shell = CreateObject(\"WScript.Shell\")\n", encoding="utf-8")
    (templates / "jarvis-supervisor.cmd").write_text(
        "@echo off\n", encoding="utf-8")

    snapshot = tmp_path / "release-source"
    release._copy_source_snapshot(repo, snapshot)

    packaged = snapshot / "src" / "jarvis_foundry" / "assets" / "packaging"
    assert (packaged / "jarvis-supervisor-headless.vbs").is_file()
    assert (packaged / "jarvis-supervisor.cmd").is_file()


def test_installed_release_resolves_its_packaged_launcher_template(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = (
        tmp_path / "venv" / "Lib" / "site-packages" / "jarvis_foundry"
        / "deployment" / "release.py"
    )
    packaged = module.parents[1] / "assets" / "packaging"
    packaged.mkdir(parents=True)
    template = packaged / "jarvis-supervisor-headless.vbs"
    template.write_text("packaged template\n", encoding="utf-8")
    monkeypatch.setattr(release, "__file__", str(module))

    assert release._packaging_template(template.name) == template
