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

_RESULT_INSTRUCTION = (
    "\n\nWhen you finish, write a machine-readable result file "
    f"named {RESULT_FILENAME} at the working root with "
    '{"files_changed": ["relative/path", ...]} listing exactly the files '
    "you changed. If you cannot determine the files you changed, write it "
    "with an empty list and JARVIS will record NOT_TESTED rather than "
    "fabricate a claim. Never list the result file itself."
)


def result_instruction(instructions: str) -> str:
    """Append the machine-result contract when it is not already present."""
    if RESULT_FILENAME in instructions:
        return instructions
    return f"{instructions}{_RESULT_INSTRUCTION}"


def read_result_file(working_root: Path) -> dict[str, Any] | None:
    """Parse the worker's machine-ingestable claim file.

    Returns None (NOT_TESTED) when missing or malformed. The claim only
    accepts a list of strings for ``files_changed``.
    """
    path = working_root / RESULT_FILENAME
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
