"""Machine-ingestable worker result contract (live-provider claim path).

Hermes and OpenHands sessions cannot report a durable, machine-readable
``files_changed`` claim through their CLI output, so the bounded task
contract requires the worker to write ``.jarvis-result.json`` inside its
working root. The adapters translate that file into the normalized
envelope's claim fields. The file is JARVIS-observed truth (it lives in
the worktree JARVIS inspects) but it is a CONTROL file, not a deliverable:
it never belongs in the worker's own claim list and it is excluded from
the reconciliation diff comparison and from step commits.

Missing/malformed result files stay NOT_TESTED; JARVIS-observed diffs are
never copied into the worker claim.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

RESULT_FILENAME = ".jarvis-result.json"
RESULT_PATH_ENV = "JARVIS_RESULT_PATH"

_RESULT_INSTRUCTION = (
    "\n\nWhen you finish, write a machine-readable result file "
    f"named {RESULT_FILENAME} at the working root with "
    '{"files_changed": ["relative/path", ...]} listing exactly the files '
    "you changed. If you cannot determine the files you changed, write it "
    "with an empty list and JARVIS will record NOT_TESTED rather than "
    "fabricate a claim. Never list the result file itself."
)

MISSING_RESULT_NOTE = (
    "[adapter] worker did not write .jarvis-result.json — "
    "recorded NOT_TESTED, never PASS"
)


def result_path(working_root: Path) -> Path:
    """Absolute control-file path EvidenceReconciler / adapters inspect."""
    return working_root.resolve() / RESULT_FILENAME


def result_env(working_root: Path) -> dict[str, str]:
    """Env the live worker process can use to locate the control file."""
    return {RESULT_PATH_ENV: str(result_path(working_root))}


def result_instruction(
    instructions: str,
    working_root: Path | None = None,
) -> str:
    """Prepend the machine-result contract when it is not already present.

    The live provider path appends this too late and names only the
    relative filename, so sessions time out before writing and the
    marker is ABSENT from the worktree JARVIS inspects. The contract
    is prepended (survives Windows argv truncation) and, when a
    working root is known, names the exact absolute path.
    """
    if RESULT_FILENAME in instructions:
        return instructions
    if working_root is None:
        return f"{_RESULT_INSTRUCTION.lstrip()}\n\n{instructions}" if instructions else _RESULT_INSTRUCTION.strip()
    path = result_path(working_root)
    block = (
        "JARVIS RESULT CONTRACT (required): write UTF-8 JSON to the exact "
        f"file {path} with "
        '{"files_changed": ["relative/path", ...]} listing exactly the files '
        "you changed. Write this file immediately after your first real "
        "change and update it before you exit — do not wait until the "
        "session times out. If you cannot determine the files, write an "
        "empty list; JARVIS will record NOT_TESTED rather than fabricate "
        f"a claim. Never list {RESULT_FILENAME} itself and never git-add it."
    )
    if instructions:
        return f"{block}\n\n{instructions}"
    return block


def ingest_result_claim(working_root: Path) -> tuple[tuple[str, ...], bool]:
    """Read the worker claim without inventing files from the git diff.

    Returns ``(files_changed, valid)``. Missing/malformed → empty claim
    and ``valid=False`` (NOT_TESTED). Timeout paths must still call this
    so a marker written before the bound expires is not ignored.
    """
    result = read_result_file(working_root)
    if result is None:
        return (), False
    return tuple(result["files_changed"]), True


def read_result_file(working_root: Path) -> dict[str, Any] | None:
    """Parse the worker's machine-ingestable claim file.

    Returns None (NOT_TESTED) when missing or malformed. The claim only
    accepts a list of strings for ``files_changed``.
    """
    path = result_path(working_root)
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    changed = payload.get("files_changed", [])
    if not isinstance(changed, list) or not all(
            isinstance(item, str) for item in changed):
        return None
    return {"files_changed": tuple(changed)}
