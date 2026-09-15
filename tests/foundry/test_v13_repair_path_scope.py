"""Repair-path scope regression: quoted JSON in prose is not an envelope.

Live finding 2026-09-15 (mission-699bf14dd056): a builder FAIL moved the
mission to REPAIRING, and the repair role could not dispatch at all:
invalid_workforce_scope / 'fabric action file.write is missing scoped field
target'.

Mechanism: MissionLoop prepends the repair context, which embeds the
verifier's findings verbatim. Those findings legitimately quote JSON -- the
builder's own .jarvis-result.json that the verifier is reporting on. The
envelope scanner took the FIRST brace anywhere in the text, so an incidental
quoted fragment was treated as the fabric scope payload, and the scope check
then demanded a scoped field the step never carried.

The repair: only a JSON object that terminates the instructions is an
envelope. A real envelope still drives the structured fast path; prose
declines it and the real worker runs inside the registered root, where
JARVIS-owned evidence reconciles the work against that root. Scope is not
weakened: out-of-scope, missing-field, and forbidden-path cases still fail
closed, and a targeted edit still resolves its target from the envelope.
"""
from __future__ import annotations

import json
from pathlib import Path

from jarvis_foundry.fabric.adapter import payload_from_manifest
from jarvis_foundry.roles.manifest import ContextManifest

NEWLINE = chr(10)

# Exactly the shape the live verifier produced: prose that quotes the
# builder's own result contract inside a findings sentence.
QUOTED_JSON_FINDING = (
    'The builder wrote .jarvis-result.json containing '
    '{"files_changed": ["scripts/example.py"]} and re-ran the pinned check. '
    'Criterion 1 passes; criterion 2 was not independently reproduced.'
)


def _manifest(instructions: str, *, role: str = 'repair') -> ContextManifest:
    return ContextManifest(
        run_id="repair-scope-run",
        role=role,
        mission_id="mission-1",
        step_id="02-execute",
        resource_id="resource-1",
        capability="write_registered_worktree_files",
        provider="opencode_go",
        model="deepseek-v4.1-flash",
        isolation_group="repair-scope",
        instructions=instructions,
        expected_artifacts=(".jarvis/outcome.md",),
    )


def _prose_repair_instructions() -> str:
    """Repair instructions as MissionLoop builds them: findings + step text."""
    context = [
        "This is an automatic repair pass after an independent FAIL.",
        "Inspect the current partial state before changing it; preserve valid work.",
        "Verifier findings:",
        "- " + QUOTED_JSON_FINDING,
        "Read the plan, then perform the owner goal in the registered resource.",
    ]
    return NEWLINE.join(context)


def test_quoted_json_in_prose_is_not_treated_as_an_envelope(
    tmp_path: Path,
) -> None:
    """The live failure: prose must not yield a scope payload."""
    payload = payload_from_manifest(
        _manifest(_prose_repair_instructions()),
        "file.write",
        tmp_path,
        structured_only=True,
    )
    assert payload is None, "quoted JSON must not become the fabric envelope"


def test_prose_repair_declines_the_structured_path_without_a_scope_error(
    tmp_path: Path,
) -> None:
    """The scope check must not invent a violation for prose instructions."""
    from jarvis_foundry.workers.mission_bridge import HybridWorkerRoleAdapter

    adapter = HybridWorkerRoleAdapter.__new__(HybridWorkerRoleAdapter)
    # No database access is needed once the payload resolves to None.
    adapter.database = None
    allowed, scope_error = adapter._fabric_scope_check(
        _manifest(_prose_repair_instructions()),
        tmp_path,
        (tmp_path,),
        (),
    )
    assert scope_error is None, f"repair blocked by scope: {scope_error}"
    assert allowed is True, "prose falls through to the real worker"


def test_trailing_envelope_is_still_a_scope_authority(tmp_path: Path) -> None:
    """A real trailing envelope still drives the structured fast path."""
    instructions = (
        'Repair context: the previous attempt failed on verification.'
        + NEWLINE + NEWLINE
        + json.dumps({'target': 'delivery/landed.txt', 'content': 'ok'})
    )
    payload = payload_from_manifest(
        _manifest(instructions, role="builder"),
        "file.write",
        tmp_path,
        structured_only=True,
    )
    assert payload is not None
    assert payload['target'] == 'delivery/landed.txt'


def test_plain_prose_without_json_declines_the_structured_path(
    tmp_path: Path,
) -> None:
    payload = payload_from_manifest(
        _manifest("Fix the reported defect in the registered worktree."),
        "file.write",
        tmp_path,
        structured_only=True,
    )
    assert payload is None


def test_prose_followed_by_quoted_json_does_not_win(
    tmp_path: Path,
) -> None:
    """A quoted object before the real envelope must not be chosen."""
    instructions = (
        'Finding quoted {"files_changed": []} for context.'
        + NEWLINE + NEWLINE
        + json.dumps({'target': 'final/target.txt', 'content': 'real'})
    )
    payload = payload_from_manifest(
        _manifest(instructions, role="builder"),
        "file.write",
        tmp_path,
        structured_only=True,
    )
    assert payload is not None
    assert payload["target"] == "final/target.txt"


def test_malformed_envelope_still_declines(tmp_path: Path) -> None:
    payload = payload_from_manifest(
        _manifest('{"target": "unterminated'),
        "file.write",
        tmp_path,
        structured_only=True,
    )
    assert payload is None
