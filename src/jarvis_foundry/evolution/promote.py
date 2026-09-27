"""V6-S8: atomic promotion and automatic rollback.

PRODUCTION-TRUTH NOTE (V6 convergence): this gate chain evaluates
ledger-internal slot promotions ONLY.  Production activation and rollback
use exclusively ``deployment/release.py`` (``set_current`` /
``rollback_current``) through the CURRENT/PREVIOUS pointers plus the
managed Supervisor restart; see ``evolution/registry.py`` for the full
note.  ``rollback_release`` here quarantines a ledger slot, it never
restores a production runtime.

Promotion requires the full gate chain — candidate slot, trusted
independent PASS verdict (is_trusted_verdict), measured improvement
(lab comparison with improvement_met=True), and a successful canary
for R2/R3 — and applies the transition atomically: promotion_intent +
the three slot_changed events + promoted in ONE append_events
transaction (crash between intent and completion is recovered by
recover_promotions, which re-applies the same idempotent batch).
Rollback is freeze-first: release.frozen + quarantine(live) +
live(previous) + previous(clear) + rolled_back in one atomic batch,
using the trigger vocabulary. Candidates can never promote or roll
back their own release.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any, Callable

from ..clock import isoformat
from ..database import FoundryDatabase
from ..events import Event
from ..reconcile import reconcile
from .canary import canary_completed_successfully
from .lab import compare_evaluations
from .registry import SLOTS, slot_pointers
from ._authority import SUPERVISOR_CONTROLLER
from .verify import is_trusted_verdict

TRIGGERS = ("stop", "veto", "lost_heartbeat", "health_failure",
            "invariant_violation", "outcome_regression",
            "evidence_mismatch", "schema_incompatibility",
            "absent_effect", "verifier_contradiction", "manual")

PROMOTION_STATUSES = ("INTENT", "PROMOTED", "ROLLED_BACK")


class PromoteError(ValueError):
    """A promotion or rollback is refused."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise PromoteError(message)


def _controller_ok(controller: str) -> bool:
    return isinstance(controller, str) and \
        controller == SUPERVISOR_CONTROLLER


def _stop_active(database: FoundryDatabase) -> bool:
    return (Path(database.path).parent / "EMERGENCY_STOP").exists()


def _slot_key(slot: str, release_id: str | None,
              previous: str | None,
              namespace: str | None = None) -> str:
    """Content-derived key; the namespace discriminates promotion/
    rollback ROUNDS (a new round is a genuinely new transition even
    for the same release triple — the S2 triple-collision escape
    hatch, applied automatically by the controllers)."""
    if namespace:
        return (f"release.slot_changed:{namespace}:{slot}:"
                f"{release_id or 'clear'}:{previous or 'none'}")
    return (f"release.slot_changed:{slot}:"
            f"{release_id or 'clear'}:{previous or 'none'}")


def _slot_event(slot: str, release_id: str | None,
                previous: str | None, reason: str,
                controller: str,
                namespace: str | None = None) -> Event:
    return Event.create(
        "release.slot_changed",
        {"slot": slot, "release_id": release_id,
         "previous_release_id": previous, "reason": reason,
         "controller": controller},
        source_type="supervisor", source_id="v6",
        idempotency_key=_slot_key(slot, release_id, previous,
                                  namespace))


def is_frozen(database: FoundryDatabase) -> bool:
    row = database.row("release_control", "id", 1)
    return bool(row and row["frozen"])


def freeze_active(database: FoundryDatabase, *, reason: str,
                  controller: str) -> None:
    _require(_controller_ok(controller),
             "only the Supervisor (controller) can freeze releases")
    _require(isinstance(reason, str) and reason.strip(),
             "reason must be a non-empty string")
    freeze_id = "freeze-" + uuid.uuid4().hex[:12]
    database.append(Event.create(
        "release.frozen", {"reason": reason, "controller": controller},
        source_type="supervisor", source_id="v6",
        idempotency_key=f"release.frozen:{freeze_id}"))


def unfreeze_active(database: FoundryDatabase, *, controller: str) -> None:
    _require(_controller_ok(controller),
             "only the Supervisor (controller) can unfreeze releases")
    unfreeze_id = "unfreeze-" + uuid.uuid4().hex[:12]
    database.append(Event.create(
        "release.unfrozen", {"controller": controller},
        source_type="supervisor", source_id="v6",
        idempotency_key=f"release.unfrozen:{unfreeze_id}"))


def _gate_promotion(
    database: FoundryDatabase, *, candidate_release_id: str,
    verification_id: str, canary_id: str | None,
    baseline_run_id: str, candidate_run_id: str, controller: str,
    veto_check: Callable[[], bool] | None,
) -> str:
    """All promotion gates; returns the release's risk class."""
    _require(_controller_ok(controller),
             "only the Supervisor (controller) can promote a release — "
             "candidate identities are refused")
    _require(not _stop_active(database),
             "stop active — promotion refused")
    if veto_check is not None and veto_check():
        raise PromoteError("veto active — promotion refused")
    _require(not is_frozen(database),
             "releases are frozen — promotion refused (freeze "
             "precedes any transition)")
    manifest = database.row("release_manifests", "release_id",
                            candidate_release_id)
    _require(manifest is not None,
             f"release {candidate_release_id} has no manifest — refused")
    _require(slot_pointers(database)["candidate"] == candidate_release_id,
             f"release {candidate_release_id} is not in the candidate "
             "slot — refused")
    request = database.row("verifications", "verification_id",
                           verification_id)
    _require(request is not None,
             f"verification {verification_id} does not exist — refused")
    import json as _json
    builder_identity = _json.loads(request["builder_identity_json"])
    _require(is_trusted_verdict(database, verification_id,
                                builder_identity),
             f"verification {verification_id} is not a trusted PASS "
             "verdict — refused (self-certification and FAIL verdicts "
             "are never promotable)")
    risk_class = str(manifest["risk_class"])
    if risk_class in ("R2", "R3"):
        _require(canary_id is not None,
                 "R2/R3 promotions require a completed canary — "
                 "refused")
        _require(canary_completed_successfully(database, canary_id),
                 f"canary {canary_id} did not complete successfully — "
                 "refused")
    comparison = compare_evaluations(
        database, baseline_run_id=baseline_run_id,
        candidate_run_id=candidate_run_id,
        improvement_id=str(manifest["improvement_id"]))
    _require(comparison["improvement_met"] is True,
             "measured improvement absent (improvement_met False) — "
             "refused (absent effect)")
    _require(comparison["guardrails_violated"] == [],
             f"guardrails violated: {comparison['guardrails_violated']} "
             "— refused")
    return risk_class


def promote_release(
    database: FoundryDatabase, *, candidate_release_id: str,
    verification_id: str, canary_id: str | None = None,
    baseline_run_id: str, candidate_run_id: str, controller: str,
    veto_check: Callable[[], bool] | None = None,
) -> str:
    """Atomically promote the candidate into the live slot."""
    risk_class = _gate_promotion(
        database, candidate_release_id=candidate_release_id,
        verification_id=verification_id, canary_id=canary_id,
        baseline_run_id=baseline_run_id,
        candidate_run_id=candidate_run_id, controller=controller,
        veto_check=veto_check)
    pointers = slot_pointers(database)
    old_live = pointers["live"]
    old_previous = pointers["previous"]
    _require(old_live != candidate_release_id,
             f"release {candidate_release_id} is already live — "
             "promotion refused (already-live guard)")
    promotion_id = "promotion-" + uuid.uuid4().hex[:12]
    batch = [
        Event.create(
            "release.promotion_intent",
            {"promotion_id": promotion_id,
             "release_id": candidate_release_id,
             "verification_id": verification_id,
             "canary_id": canary_id,
             "baseline_run_id": baseline_run_id,
             "candidate_run_id": candidate_run_id,
             "risk_class": risk_class,
             "planned": {"previous": old_live,
                         "live": candidate_release_id,
                         "candidate": None},
             "controller": controller},
            source_type="supervisor", source_id="v6",
            idempotency_key=f"release.promotion_intent:{promotion_id}"),
        _slot_event("previous", old_live, old_previous,
                    "promotion retains the exact previous release",
                    controller, namespace=promotion_id),
        _slot_event("live", candidate_release_id, old_live,
                    "atomic promotion", controller,
                    namespace=promotion_id),
        _slot_event("candidate", None, candidate_release_id,
                    "candidate slot cleared by promotion", controller,
                    namespace=promotion_id),
        Event.create(
            "release.promoted",
            {"promotion_id": promotion_id,
             "release_id": candidate_release_id,
             "controller": controller},
            source_type="supervisor", source_id="v6",
            idempotency_key=f"release.promoted:{promotion_id}"),
    ]
    database.append_events(batch)
    return promotion_id


def rollback_release(
    database: FoundryDatabase, *, trigger: str, reason: str,
    controller: str, freeze: bool = True,
) -> str:
    """Freeze-first automatic rollback: the failed live release goes to
    quarantine, the exact previous release is restored, and the reason
    is recorded. One atomic batch."""
    _require(_controller_ok(controller),
             "only the Supervisor (controller) can roll back — "
             "candidate identities are refused")
    _require(trigger in TRIGGERS,
             f"trigger must be one of {TRIGGERS} — unknown triggers "
             "are refused")
    _require(isinstance(reason, str) and reason.strip(),
             "reason must be a non-empty string")
    pointers = slot_pointers(database)
    old_live = pointers["live"]
    previous = pointers["previous"]
    _require(old_live is not None and previous is not None,
             "no previous release to restore — rollback refused")
    # the rolled_back record must reference the PROMOTION that brought
    # the failed release live (latest PROMOTED row for that release)
    with database.connect() as connection:
        promotion_row = connection.execute(
            """SELECT promotion_id FROM promotions
               WHERE release_id = ? AND status = 'PROMOTED'
               ORDER BY rowid DESC LIMIT 1""",
            (old_live,)).fetchone()
    _require(promotion_row is not None,
             f"no PROMOTED promotion record for live release "
             f"{old_live} — rollback refused (lineage required)")
    promotion_id = str(promotion_row["promotion_id"])
    batch: list[Event] = []
    if freeze:
        batch.append(Event.create(
            "release.frozen",
            {"reason": f"freeze precedes rollback: {reason}",
             "controller": controller},
            source_type="supervisor", source_id="v6",
            idempotency_key=f"release.frozen:{promotion_id}"))
    batch.extend([
        _slot_event("quarantine", old_live, pointers["quarantine"],
                    f"failed release quarantined: {reason}", controller,
                    namespace=promotion_id),
        _slot_event("live", previous, old_live,
                    "automatic rollback restores the previous release",
                    controller, namespace=promotion_id),
        _slot_event("previous", None, previous,
                    "previous slot cleared by rollback", controller,
                    namespace=promotion_id),
        Event.create(
            "release.rolled_back",
            {"promotion_id": promotion_id,
             "trigger": trigger, "reason": reason,
             "from": old_live, "to": previous,
             "controller": controller},
            source_type="supervisor", source_id="v6",
            idempotency_key=f"release.rolled_back:{promotion_id}"),
    ])
    database.append_events(batch)
    report = reconcile(database)
    if not report.consistent:
        details = report.to_json()
        raise PromoteError(
            "post-rollback reconcile INCONSISTENT — severity-one, "
            "investigate immediately: "
            f"mismatches={details['mismatches']} "
            f"unexpected={details['unexpected_rows']} "
            f"missing={details['missing_rows']}")
    return promotion_id


def recover_promotions(database: FoundryDatabase, *,
                       controller: str,
                       veto_check: Callable[[], bool] | None = None,
                       ) -> list[str]:
    """Crash recovery: INTENT promotions (intent event without a
    promoted event) are re-driven to completion by re-applying the
    same namespaced batch with the SAME reason strings promote_release
    used (identical payloads under identical keys dedupe; a crash
    AFTER the slot batch but BEFORE promoted completes cleanly).

    Recovery re-checks current state before re-driving (the gates
    were passed at intent time, but the world may have moved):
    - frozen releases, an active STOP sentinel, or a vetoed state
      SKIP recovery (the INTENT stays pending, audit-visible);
    - a release that is no longer in the candidate slot AND not
      already live (e.g. moved to quarantine post-crash) is SKIPPED —
      the supervisor decides its fate."""
    _require(_controller_ok(controller),
             "only the Supervisor (controller) can recover promotions")
    recovered: list[str] = []
    for row in database.all("promotions"):
        if row["status"] != "INTENT":
            continue
        promotion_id = str(row["promotion_id"])
        release_id = str(row["release_id"])
        if is_frozen(database) or _stop_active(database):
            continue  # world is stopped — leave INTENT pending
        if veto_check is not None and veto_check():
            continue
        pointers = slot_pointers(database)
        if release_id not in (pointers["candidate"], pointers["live"]):
            continue  # slot state moved post-crash — supervisor decides
        import json as _json
        planned = _json.loads(str(row["planned_json"]))
        planned_previous = planned.get("previous")
        current_previous = pointers["previous"]
        batch = [
            _slot_event("previous", planned_previous,
                        current_previous,
                        "promotion retains the exact previous release",
                        controller, namespace=promotion_id),
            _slot_event("live", release_id, pointers["live"],
                        "atomic promotion", controller,
                        namespace=promotion_id),
            _slot_event("candidate", None, release_id,
                        "candidate slot cleared by promotion",
                        controller, namespace=promotion_id),
            Event.create(
                "release.promoted",
                {"promotion_id": promotion_id, "release_id": release_id,
                 "controller": controller},
                source_type="supervisor", source_id="v6",
                idempotency_key=f"release.promoted:{promotion_id}"),
        ]
        database.append_events(batch)
        recovered.append(promotion_id)
    return recovered


def promotion_status(database: FoundryDatabase,
                     promotion_id: str) -> str:
    row = database.row("promotions", "promotion_id", promotion_id)
    if row is None:
        raise PromoteError(f"promotion {promotion_id} does not exist")
    return str(row["status"])
