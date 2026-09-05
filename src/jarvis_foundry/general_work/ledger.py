"""Event-sourced plan records and global output claims for General Work."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..database import FoundryDatabase
from ..events import Event
from .model import GeneralWorkPlan


PLAN_RECORDED = "general_work.plan_recorded"
TARGET_CLAIMED = "general_work.target_claimed"
LEDGER_SCHEMA = "v13.general-work-ledger.v1"


class GeneralWorkLedgerError(ValueError):
    """An immutable plan or target identity conflicts with ledger truth."""


@dataclass(frozen=True, slots=True)
class PlanClaimReceipt:
    plan_id: str
    plan_hash: str
    target_keys: tuple[str, ...]
    event_sequences: tuple[int, ...]
    inserted: tuple[bool, ...]

    @property
    def replayed(self) -> bool:
        return not any(self.inserted)

    def to_payload(self) -> dict[str, Any]:
        return {
            "plan_id": self.plan_id,
            "plan_hash": self.plan_hash,
            "target_keys": list(self.target_keys),
            "event_sequences": list(self.event_sequences),
            "inserted": list(self.inserted),
            "replayed": self.replayed,
        }


def _target_key(path: Path) -> str:
    return os.path.normcase(str(path.resolve(strict=False))).casefold()


def _claim_id(target_key: str) -> str:
    return hashlib.sha256(target_key.encode("utf-8")).hexdigest()


def resolve_delivery_target(root: str | Path, relative_path: str) -> Path:
    owned_root = Path(root).expanduser().resolve()
    if not owned_root.is_dir():
        raise GeneralWorkLedgerError(
            f"resource root is not a directory: {owned_root}"
        )
    target = (owned_root / Path(*relative_path.split("/"))).resolve(strict=False)
    try:
        target.relative_to(owned_root)
    except ValueError as error:
        raise GeneralWorkLedgerError(
            f"delivery target escapes the resource root: {relative_path}"
        ) from error
    current = owned_root
    for part in Path(*relative_path.split("/")).parts:
        current = current / part
        if current.exists() and current.is_symlink():
            raise GeneralWorkLedgerError(
                f"delivery target traverses a symbolic link: {relative_path}"
            )
    return target


def _event_payloads(
    connection: Any,
    event_type: str,
) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT payload_json FROM events WHERE event_type = ? ORDER BY sequence",
        (event_type,),
    ).fetchall()
    result: list[dict[str, Any]] = []
    for row in rows:
        try:
            payload = json.loads(str(row["payload_json"]))
        except (KeyError, TypeError, json.JSONDecodeError):
            continue
        if isinstance(payload, dict):
            result.append(payload)
    return result


def record_plan_and_claim_targets(
    database: FoundryDatabase,
    plan: GeneralWorkPlan,
    *,
    resource_root: str | Path,
) -> PlanClaimReceipt:
    """Record one immutable plan and all output claims atomically."""

    root = Path(resource_root).expanduser().resolve()
    target_rows: list[tuple[str, Path, str]] = []
    for deliverable in plan.deliverables:
        target = resolve_delivery_target(root, deliverable.path)
        target_rows.append(
            (deliverable.deliverable_id, target, _target_key(target))
        )
    if len({row[2] for row in target_rows}) != len(target_rows):
        raise GeneralWorkLedgerError(
            "deliverable targets collide after filesystem normalization"
        )

    plan_hash = plan.plan_hash
    events = [
        Event.create(
            PLAN_RECORDED,
            {
                "schema": LEDGER_SCHEMA,
                "plan_id": plan.plan_id,
                "plan_hash": plan_hash,
                "objective_id": plan.objective_id,
                "campaign_id": plan.campaign_id,
                "resource_id": plan.resource_id,
                "plan": plan.to_payload(),
            },
            source_type="supervisor",
            source_id="v13-general-work",
            correlation_id=plan.plan_id,
            idempotency_key=f"{PLAN_RECORDED}:{plan.plan_id}:{plan_hash}",
        )
    ]
    for deliverable_id, target, target_key in target_rows:
        events.append(
            Event.create(
                TARGET_CLAIMED,
                {
                    "schema": LEDGER_SCHEMA,
                    "plan_id": plan.plan_id,
                    "plan_hash": plan_hash,
                    "deliverable_id": deliverable_id,
                    "resource_id": plan.resource_id,
                    "target_ref": next(
                        item.path
                        for item in plan.deliverables
                        if item.deliverable_id == deliverable_id
                    ),
                    "target_path": str(target),
                    "target_key": target_key,
                },
                source_type="supervisor",
                source_id="v13-general-work",
                correlation_id=plan.plan_id,
                idempotency_key=f"{TARGET_CLAIMED}:{_claim_id(target_key)}",
            )
        )

    with database.transaction() as connection:
        existing_plans = [
            payload
            for payload in _event_payloads(connection, PLAN_RECORDED)
            if payload.get("plan_id") == plan.plan_id
        ]
        if any(payload.get("plan_hash") != plan_hash for payload in existing_plans):
            raise GeneralWorkLedgerError(
                f"plan id already names different immutable work: {plan.plan_id}"
            )
        claims = _event_payloads(connection, TARGET_CLAIMED)
        claims_by_key = {
            str(payload.get("target_key") or ""): payload
            for payload in claims
            if payload.get("target_key")
        }
        for deliverable_id, target, target_key in target_rows:
            prior = claims_by_key.get(target_key)
            if prior is not None and (
                prior.get("plan_id") != plan.plan_id
                or prior.get("plan_hash") != plan_hash
                or prior.get("deliverable_id") != deliverable_id
            ):
                raise GeneralWorkLedgerError(
                    f"delivery target is already claimed: {target}"
                )
            if prior is None and target.exists():
                raise GeneralWorkLedgerError(
                    f"unclaimed delivery target already exists: {target}"
                )
        results = database.append_events_on_connection(connection, events)

    return PlanClaimReceipt(
        plan_id=plan.plan_id,
        plan_hash=plan_hash,
        target_keys=tuple(row[2] for row in target_rows),
        event_sequences=tuple(sequence for sequence, _ in results),
        inserted=tuple(inserted for _, inserted in results),
    )


def load_recorded_plan(
    database: FoundryDatabase,
    plan_id: str,
    *,
    allowed_action_kinds: tuple[str, ...] | None = None,
) -> GeneralWorkPlan:
    matches = [
        event.payload
        for event in database.events(event_types=(PLAN_RECORDED,))
        if event.payload.get("plan_id") == str(plan_id)
    ]
    if not matches:
        raise GeneralWorkLedgerError(f"General Work plan is not recorded: {plan_id}")
    latest = matches[-1]
    raw = latest.get("plan")
    if not isinstance(raw, dict):
        raise GeneralWorkLedgerError("recorded General Work plan payload is malformed")
    plan = GeneralWorkPlan.from_payload(
        raw,
        allowed_action_kinds=allowed_action_kinds,
    )
    if plan.plan_hash != latest.get("plan_hash"):
        raise GeneralWorkLedgerError("recorded General Work plan hash is invalid")
    return plan


def target_claims(
    database: FoundryDatabase,
    *,
    plan_id: str | None = None,
) -> tuple[dict[str, Any], ...]:
    claims = [
        dict(event.payload)
        for event in database.events(event_types=(TARGET_CLAIMED,))
    ]
    if plan_id is not None:
        claims = [
            claim for claim in claims if claim.get("plan_id") == str(plan_id)
        ]
    return tuple(claims)


__all__ = [
    "LEDGER_SCHEMA",
    "PLAN_RECORDED",
    "TARGET_CLAIMED",
    "GeneralWorkLedgerError",
    "PlanClaimReceipt",
    "load_recorded_plan",
    "record_plan_and_claim_targets",
    "resolve_delivery_target",
    "target_claims",
]
