from __future__ import annotations

import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_ROOT = REPO_ROOT / "scripts" / "remaster" / "r1"
PURPOSE_ROOT = REPO_ROOT / "docs" / "remaster" / "r1"
PYTHON = Path(r"C:\Python313\python.exe")

if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def script_root() -> Path:
    return SCRIPT_ROOT


@pytest.fixture(scope="session")
def purpose_root() -> Path:
    return PURPOSE_ROOT


@pytest.fixture(scope="session")
def python_executable() -> Path:
    assert PYTHON.is_file(), f"required isolated host Python is missing: {PYTHON}"
    return PYTHON


@pytest.fixture(scope="session")
def reference_command(python_executable: Path, script_root: Path) -> list[str]:
    return [str(python_executable), "-B", str(script_root / "reference_adapter.py")]


@pytest.fixture(scope="session")
def mutant_command(python_executable: Path, script_root: Path):
    def command(mutant: str) -> list[str]:
        return [str(python_executable), "-B", str(script_root / "mutant_adapter.py"), "--mutant", mutant]

    return command
