"""V7B-5: disposable inspect/edit/test/commit/push/reconcile workflow."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from jarvis_foundry.operations.project import (
    DisposableProjectWorkflow,
    ProjectWorkflowError,
)


def test_disposable_project_workflow_reconciles_local_remote(tmp_path: Path) -> None:
    project = DisposableProjectWorkflow(
        tmp_path / "disposable-project", remote=tmp_path / "disposable-remote.git"
    )
    receipt = project.run(
        issue_spec="# Issue\nAdd the synthetic greeting.\n",
        edits={"app.py": "GREETING = 'hello synthetic office'\n"},
        test_command=(
            sys.executable, "-c",
            "from pathlib import Path; assert 'synthetic office' in Path('app.py').read_text()",
        ),
        commit_message="feat: add synthetic greeting",
    )
    assert receipt.commit == receipt.remote_commit
    observation = project.inspect()
    assert observation.clean
    assert observation.head == receipt.commit
    assert observation.issue_spec_hash == receipt.issue_spec_hash
    assert project.verify_remote() == receipt.commit
    assert project.resume().head == receipt.commit


def test_project_workflow_rejects_owner_like_root(tmp_path: Path) -> None:
    project = DisposableProjectWorkflow(
        tmp_path / "disposable-project", remote=tmp_path / "remote.git"
    )
    # An absolute edit and traversal are rejected before any source mutation.
    with pytest.raises(ProjectWorkflowError, match="relative"):
        project.write_file(str(tmp_path / "outside.py"), "bad")
    with pytest.raises(ProjectWorkflowError, match="escapes"):
        project.write_file("..\\outside.py", "bad")
