"""Unit tests for ``scripts/doctor.py`` pure logic (PATH hygiene)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def doctor():
    spec = importlib.util.spec_from_file_location("jarvis_doctor_script", ROOT / "scripts" / "doctor.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    assert spec.loader is not None
    spec.loader.exec_module(module)
    yield module
    sys.modules.pop(spec.name, None)


LEAKED = [
    r"C:\Users\b\AppData\Local\Temp\pytest-of-b\pytest-12\test_cli_setup_show_and_test0\JARVIS\bin",
    r"C:\temp\jarvis-final\test_cli_setup_show_and_test0\JARVIS\bin",
    r"C:\work\repo\.pytest-tmp-x\case\JARVIS\bin",
]
REAL = [r"C:\Users\b\AppData\Local\JARVIS\bin", r"C:\Tools\bin"]


def test_leaked_test_temp_entries_are_identified(doctor) -> None:
    buckets = doctor.classify_path_entries(REAL + LEAKED, exists=lambda _p: True)
    assert buckets["test_temp"] == LEAKED
    assert buckets["healthy"] == REAL


def test_dead_entries_and_duplicates_are_separated(doctor) -> None:
    entries = [REAL[0], r"C:\Gone\bin", REAL[0].lower(), REAL[1]]
    buckets = doctor.classify_path_entries(entries, exists=lambda p: "Gone" not in p)
    assert buckets["missing"] == [r"C:\Gone\bin"]
    assert buckets["duplicate"] == [REAL[0].lower()]
    assert buckets["healthy"] == [REAL[0], REAL[1]]


def test_cleaned_path_keeps_order_and_only_good_entries(doctor) -> None:
    entries = LEAKED[:1] + [REAL[0]] + [r"C:\Gone\bin"] + [REAL[1], REAL[0]]
    buckets = doctor.classify_path_entries(entries, exists=lambda p: "Gone" not in p)
    assert doctor.cleaned_path(entries, buckets) == ";".join(REAL)


def test_split_path_ignores_empty_segments(doctor) -> None:
    assert doctor.split_path(";C:\\a;;C:\\b;") == ["C:\\a", "C:\\b"]
    assert doctor.split_path("") == []
    assert doctor.split_path(None) == []


def test_user_path_writes_are_refused_inside_the_test_suite(doctor) -> None:
    """The suite-wide guard (conftest) must also stop the doctor's PATH fixer."""
    winreg = pytest.importorskip("winreg")
    with pytest.raises(AssertionError, match="registry"):
        winreg.SetValueEx(None, "Path", 0, winreg.REG_EXPAND_SZ, "x")
