"""V6-S2: immutable release registry and slots.

Release manifests are immutable records binding source commit, artifact
hashes, dependencies, runtime identity, parent release, improvement
(hypothesis) id, deterministic risk class, migrations, verification
verdicts and the rollback target. An external controller (never a
candidate) owns the live/candidate/previous/quarantine slot pointers.

Integrity rules implemented here:

- identical re-registration dedupes (content-derived release id);
- unknown parent, unknown rollback target, unknown improvement,
  ineligible improvement, risk-class mismatch with the improvement,
  malformed commit/artifact hashes -> deterministic refusal;
- slot transitions are idempotent: the idempotency key binds
  (slot, release_id, previous_release_id), so re-delivery of the SAME
  transition dedupes while a genuinely new transition (even to the same
  release from a different previous state) records new history;
- promotion requires the candidate slot, at least one PASS verdict and
  NO FAIL verdict, plus an eligible improvement;
- rollback restores the exact previous release, quarantines the failed
  one, and never rewrites history (everything stays auditable);
- verify_manifest re-hashes artifact files against the immutable
  declarations and reports tampering per artifact.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from ..database import FoundryDatabase
from ..domain import ARTIFACT_NAME_PATTERN
from ..events import Event, canonical_json, payload_hash
from ..supervisor.authority import SUPERVISOR_CONTROLLER
from .records import RISK_ORDER, improvement_eligibility

SLOTS = ("live", "candidate", "previous", "quarantine")

_HEX40 = re.compile(r"^[0-9a-f]{40}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")
#: Artifact names are plain file names inside the artifact root: no
#: path separators, no dot-dot, no drive letters, no backslashes.
#: Anything else is refused at registration (verify_manifest also
#: re-checks containment; fail closed both ways).
_ARTIFACT_NAME = ARTIFACT_NAME_PATTERN


class ReleaseError(ValueError):
    """A release manifest or slot transition is invalid or refused."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ReleaseError(message)


def _require_supervisor(controller: str, action: str) -> None:
    _require(
        isinstance(controller, str) and controller == SUPERVISOR_CONTROLLER,
        f"only the exact Supervisor controller identity 'supervisor' may "
        f"{action} - refused",
    )


def _slot_key(slot: str, release_id: str | None,
              previous_release_id: str | None) -> str:
    return (f"release.slot_changed:{slot}:"
            f"{release_id or 'clear'}:{previous_release_id or 'none'}")


def register_release(
    database: FoundryDatabase, *,
    source_commit: str,
    artifact_hashes: dict[str, str],
    dependencies: list[str],
    runtime_identity: dict[str, Any],
    parent_release_id: str | None,
    improvement_id: str,
    risk_class: str,
    migrations: list[int],
    verdicts: list[dict[str, Any]],
    rollback_target: str | None = None,
    controller: str,
    source_type: str = "supervisor",
    source_id: str = "v6",
) -> str:
    """Register an immutable release manifest. Returns the content-
    derived release id; identical re-registration dedupes."""
    _require_supervisor(controller, "register a release")
    _require(_HEX40.match(source_commit),
             "source_commit must be a 40-char sha1 hex string")
    _require(isinstance(artifact_hashes, dict) and artifact_hashes,
             "artifact_hashes must be a non-empty mapping")
    for name, digest in artifact_hashes.items():
        _require(isinstance(name, str) and _ARTIFACT_NAME.match(name),
                 f"artifact name {name!r} must be a plain file name "
                 "(no path separators, no '..', no drive letters) — "
                 "refused")
        _require(isinstance(digest, str) and _HEX64.match(digest),
                 f"artifact hash for {name!r} must be a 64-char sha256 "
                 "hex string")
    _require(isinstance(dependencies, list),
             "dependencies must be a list")
    _require(isinstance(runtime_identity, dict) and runtime_identity,
             "runtime_identity must be a non-empty mapping")
    _require(risk_class in RISK_ORDER,
             f"risk_class must be one of {RISK_ORDER}")
    _require(isinstance(migrations, list),
             "migrations must be a list")
    _require(isinstance(verdicts, list),
             "verdicts must be a list")
    for verdict in verdicts:
        _require(isinstance(verdict, dict)
                 and isinstance(verdict.get("verifier_run_id"), str)
                 and verdict["verifier_run_id"]
                 and verdict.get("verdict") in
                 ("PASS", "FAIL", "BLOCKED", "NOT_TESTED"),
                 "each verdict needs a verifier_run_id and a verdict in "
                 "PASS/FAIL/BLOCKED/NOT_TESTED")

    improvement = database.row("improvement_records", "improvement_id",
                               improvement_id)
    _require(improvement is not None,
             f"improvement {improvement_id} does not exist — refused")
    eligible, reason = improvement_eligibility(database, improvement_id)
    _require(eligible,
             f"improvement {improvement_id} is not promotion-eligible "
             f"({reason}) — refused")
    _require(improvement["risk_class"] == risk_class,
             f"risk_class {risk_class} does not match the improvement's "
             f"frozen {improvement['risk_class']} — refused")

    if parent_release_id is not None:
        _require(database.row("release_manifests", "release_id",
                              parent_release_id) is not None,
                 f"parent release {parent_release_id} does not exist — "
                 "refused")
    if rollback_target is not None:
        _require(database.row("release_manifests", "release_id",
                              rollback_target) is not None,
                 f"rollback target {rollback_target} does not exist — "
                 "refused")

    identity = {
        "source_commit": source_commit,
        "artifact_hashes": dict(artifact_hashes),
        "dependencies": list(dependencies),
        "runtime_identity": dict(runtime_identity),
        "parent_release_id": parent_release_id,
        "improvement_id": improvement_id,
        "risk_class": risk_class,
        "migrations": list(migrations),
        "verdicts": list(verdicts),
        "rollback_target": rollback_target,
    }
    release_id = "release-" + payload_hash(identity)[:12]
    identity["release_id"] = release_id
    identity["controller"] = controller
    event = Event.create(
        "release.manifest_recorded", identity,
        source_type=source_type, source_id=source_id,
        idempotency_key=f"release.manifest_recorded:{release_id}")
    database.append(event)
    return release_id


def slot_pointers(database: FoundryDatabase) -> dict[str, str | None]:
    """Current slot pointers: {slot: release_id or None}."""
    pointers: dict[str, str | None] = {slot: None for slot in SLOTS}
    with database.connect() as connection:
        rows = connection.execute(
            "SELECT slot, release_id FROM release_slots").fetchall()
    for row in rows:
        pointers[row["slot"]] = row["release_id"]
    return pointers


def set_slot(
    database: FoundryDatabase, slot: str, release_id: str | None, *,
    reason: str, controller: str,
    transition_id: str | None = None,
    source_type: str = "supervisor", source_id: str = "v6",
) -> None:
    """Point a slot at a release (or clear it).

    Default idempotency: the key binds (slot, release_id, previous),
    so re-delivery of the SAME transition dedupes and a genuinely new
    transition records new history. For oscillating histories where a
    later distinct transition would repeat an earlier triple (e.g.
    live c -> g -> c -> g), pass an explicit ``transition_id`` (a
    uuid) so the new transition gets its own key; re-delivery with the
    SAME transition_id dedupes, a changed payload under the same
    transition_id conflicts (correct).
    """
    _require(slot in SLOTS, f"unknown slot {slot!r} — refused")
    _require(isinstance(reason, str) and reason.strip(),
             "reason must be a non-empty string")
    _require_supervisor(controller, "change release slots")
    if release_id is not None:
        _require(database.row("release_manifests", "release_id",
                              release_id) is not None,
                 f"release {release_id} does not exist — refused")
    previous = slot_pointers(database)[slot]
    if previous == release_id:
        return  # semantic no-op: the slot already points at the target
    if transition_id is not None:
        idempotency_key = f"release.slot_changed:{slot}:{transition_id}"
    else:
        idempotency_key = _slot_key(slot, release_id, previous)
    event = Event.create(
        "release.slot_changed",
        {"slot": slot, "release_id": release_id,
         "previous_release_id": previous, "reason": reason,
         "controller": controller},
        source_type=source_type, source_id=source_id,
        idempotency_key=idempotency_key)
    database.append(event)


def promote_candidate(
    database: FoundryDatabase, candidate_release_id: str, *,
    verdicts: list[dict[str, Any]], controller: str, reason: str,
    source_type: str = "supervisor", source_id: str = "v6",
) -> dict[str, str | None]:
    """Promote the candidate to live (atomic batch). The old live
    becomes previous; the candidate slot is cleared. Requires PASS
    verdicts, no FAIL verdicts, and an eligible improvement."""
    _require_supervisor(controller, "promote a release")
    _require(database.row("release_manifests", "release_id",
                          candidate_release_id) is not None,
             f"candidate release {candidate_release_id} does not exist — "
             "refused")
    pointers = slot_pointers(database)
    _require(pointers["candidate"] == candidate_release_id,
             f"release {candidate_release_id} is not in the candidate "
             "slot — refused")
    _require(pointers["live"] != candidate_release_id,
             f"release {candidate_release_id} is already live — "
             "promotion refused")
    _require(isinstance(verdicts, list) and verdicts,
             "promotion requires at least one verification verdict — "
             "refused")
    for verdict in verdicts:
        _require(verdict.get("verdict") == "PASS",
                 f"promotion refused: verdict {verdict.get('verdict')} "
                 "is not PASS")
    row = database.row("release_manifests", "release_id",
                       candidate_release_id)
    eligible, reason_eligibility = improvement_eligibility(
        database, row["improvement_id"])
    _require(eligible,
             f"candidate improvement {row['improvement_id']} is not "
             f"promotion-eligible ({reason_eligibility}) — refused")

    old_live = pointers["live"]
    old_previous = pointers["previous"]
    events = [
        Event.create(
            "release.slot_changed",
            {"slot": "previous", "release_id": old_live,
             "previous_release_id": old_previous,
             "reason": reason, "controller": controller},
            source_type=source_type, source_id=source_id,
            idempotency_key=_slot_key("previous", old_live,
                                      old_previous)),
        Event.create(
            "release.slot_changed",
            {"slot": "live", "release_id": candidate_release_id,
             "previous_release_id": old_live,
             "reason": reason, "controller": controller},
            source_type=source_type, source_id=source_id,
            idempotency_key=_slot_key("live", candidate_release_id,
                                      old_live)),
        Event.create(
            "release.slot_changed",
            {"slot": "candidate", "release_id": None,
             "previous_release_id": candidate_release_id,
             "reason": reason, "controller": controller},
            source_type=source_type, source_id=source_id,
            idempotency_key=_slot_key("candidate", None,
                                      candidate_release_id)),
    ]
    database.append_events(events)
    return slot_pointers(database)


def rollback_live(
    database: FoundryDatabase, *, reason: str, controller: str,
    source_type: str = "supervisor", source_id: str = "v6",
) -> dict[str, str | None]:
    """Roll the live release back to the exact previous release. The
    failed live release moves to quarantine; previous is consumed.
    History is never rewritten (all transitions are new events)."""
    _require_supervisor(controller, "roll back a release")
    pointers = slot_pointers(database)
    old_live = pointers["live"]
    previous = pointers["previous"]
    _require(old_live is not None and previous is not None,
             "rollback refused: live or previous slot is empty")
    _require(isinstance(reason, str) and reason.strip(),
             "reason must be a non-empty string")
    old_quarantine = pointers["quarantine"]
    events = [
        Event.create(
            "release.slot_changed",
            {"slot": "quarantine", "release_id": old_live,
             "previous_release_id": old_quarantine,
             "reason": reason, "controller": controller},
            source_type=source_type, source_id=source_id,
            idempotency_key=_slot_key("quarantine", old_live,
                                      old_quarantine)),
        Event.create(
            "release.slot_changed",
            {"slot": "live", "release_id": previous,
             "previous_release_id": old_live,
             "reason": reason, "controller": controller},
            source_type=source_type, source_id=source_id,
            idempotency_key=_slot_key("live", previous, old_live)),
        Event.create(
            "release.slot_changed",
            {"slot": "previous", "release_id": None,
             "previous_release_id": previous,
             "reason": reason, "controller": controller},
            source_type=source_type, source_id=source_id,
            idempotency_key=_slot_key("previous", None, previous)),
    ]
    database.append_events(events)
    return slot_pointers(database)


def quarantine_release(
    database: FoundryDatabase, release_id: str, *, reason: str,
    controller: str,
    source_type: str = "supervisor", source_id: str = "v6",
) -> dict[str, str | None]:
    """Move a release into quarantine (candidate slot cleared if it
    pointed there). History preserved."""
    _require_supervisor(controller, "quarantine a release")
    _require(database.row("release_manifests", "release_id",
                          release_id) is not None,
             f"release {release_id} does not exist — refused")
    _require(isinstance(reason, str) and reason.strip(),
             "reason must be a non-empty string")
    pointers = slot_pointers(database)
    old_quarantine = pointers["quarantine"]
    events = [
        Event.create(
            "release.slot_changed",
            {"slot": "quarantine", "release_id": release_id,
             "previous_release_id": old_quarantine,
             "reason": reason, "controller": controller},
            source_type=source_type, source_id=source_id,
            idempotency_key=_slot_key("quarantine", release_id,
                                      old_quarantine)),
    ]
    if pointers["candidate"] == release_id:
        events.append(Event.create(
            "release.slot_changed",
            {"slot": "candidate", "release_id": None,
             "previous_release_id": release_id,
             "reason": reason, "controller": controller},
            source_type=source_type, source_id=source_id,
            idempotency_key=_slot_key("candidate", None, release_id)))
    database.append_events(events)
    return slot_pointers(database)


def verify_manifest(database: FoundryDatabase, release_id: str,
                    artifact_root: str | Path) -> dict[str, bool]:
    """Re-hash every declared artifact under ``artifact_root`` against
    the immutable manifest. Per-artifact True/False; a missing or
    tampered file is False (never a silent PASS)."""
    row = database.row("release_manifests", "release_id", release_id)
    if row is None:
        raise ReleaseError(
            f"release {release_id} does not exist — refused")
    declared = json.loads(row["artifact_hashes_json"])
    root = Path(artifact_root)
    report: dict[str, bool] = {}
    for name, digest in declared.items():
        # defense in depth: containment re-check at verification time
        if not _ARTIFACT_NAME.match(name):
            report[name] = False
            continue
        path = (root / name).resolve()
        if root.resolve() not in path.parents:
            report[name] = False
            continue
        if not path.is_file():
            report[name] = False
            continue
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        report[name] = actual == digest
    return report
