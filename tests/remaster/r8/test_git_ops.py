from __future__ import annotations

from pathlib import Path

from r8_helpers import commit_all, write_files

from jarvis_foundry.orchestration.git_ops import (
    clone_project,
    diff_evidence,
    inspect_project,
    tree_state_hash,
)


def test_inspect_reports_git_and_dirty_state(tmp_path: Path) -> None:
    root = tmp_path / "source"
    write_files(root, {"README.md": "# source\n", "app.py": "print('ok')\n"})
    commit_all(root)
    (root / "untracked.txt").write_text("dirty", encoding="utf-8")
    report = inspect_project(root)
    assert report["is_git_repo"] is True
    assert report["head"]
    assert report["dirty_report"]["untracked"] == ["untracked.txt"]
    assert report["tree_hash"]


def test_clone_is_isolated_and_source_untouched(tmp_path: Path) -> None:
    source = tmp_path / "source"
    write_files(source, {"README.md": "# source\n"})
    commit_all(source)
    before = inspect_project(source)
    clone = tmp_path / "clones" / "mission-a"
    info = clone_project(source, clone, branch="r8/test", proving_root=tmp_path)
    assert Path(info["clone"]).is_dir()
    assert info["branch"] == "r8/test"
    after = inspect_project(source)
    assert before["tree_hash"] == after["tree_hash"]
    assert before["porcelain"] == after["porcelain"]


def test_diff_evidence_captures_new_files(tmp_path: Path) -> None:
    source = tmp_path / "source"
    write_files(source, {"README.md": "# source\n"})
    commit_all(source)
    clone = tmp_path / "clones" / "mission-b"
    clone_project(source, clone, branch="r8/test", proving_root=tmp_path)
    before_hash = tree_state_hash(clone, proving_root=tmp_path)
    (clone / "tests").mkdir(parents=True, exist_ok=True)
    (clone / "tests" / "new_test.py").write_text("def test_ok():\n    pass\n", encoding="utf-8")
    evidence = diff_evidence(clone, proving_root=tmp_path)
    after_hash = tree_state_hash(clone, proving_root=tmp_path)
    assert before_hash != after_hash
    assert "new_test.py" in evidence["diff_full"]
    assert evidence["tree_hash"] == after_hash
