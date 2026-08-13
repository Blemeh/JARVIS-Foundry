"""V5-S2: ActionEnvelope + deterministic policy broker (architecture §7).

The broker implements the exact 15-step deterministic processing order
from the architecture contract and returns exactly one of the seven
contract codes. An LLM may explain a decision but may never override it:
``decide`` is a pure function of durable state + the envelope, and every
call re-reads that state (a STOP raised between deliveries changes the
decision — decisions are never cached).

Order implemented here (contract §7):
1. emergency STOP (marker, STOP_EVERYTHING, FROZEN/SUSPENDED);
2. target resolution + action-kind normalization (canonical form — a
   veto cannot be bypassed by case/whitespace/separator variants);
3. registration/reconciliation (system targets must be in the S1
   registry; file targets must sit inside a registered resource root);
4. goals, vetoes, lawfulness, risk, and capability readiness;
5. secret/credential requirements (honest NOT_READY when the required
   credential capability is not registered READY — no fabricated
   credentials);
6. resource and monetary budgets (fail closed when an estimate has no
   budget policy);
7. executable recovery strategy (irreversible/C2/C3 need
   rollback_plan.method);
8. before-state capture contract (mutating actions must declare one);
9. capability issuance (S3 wires the real broker; the planned issuance
   is recorded with the decision);
10. one execution under an idempotency key;
11. after-state capture (executor's step — S6);
12. deterministic verification (verification_plan required);
13. independent verification where required (risk C3);
14. commit/retain/freeze/rollback (recovery validated in step 7);
15. terminal event — ``action.decided`` via ``record_decision``.

``require_allowance`` is the executor-side ticket check: no broker ALLOW
decision, no execution — a worker can never bypass the broker.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..database import FoundryDatabase
from ..domain import ActionEnvelope, BrokerDecision
from ..events import Event, payload_hash

_VALID_RISK_CLASSES = frozenset({"C0", "C1", "C2", "C3"})
_READY_STATES = frozenset({"READY", "ACTIVE"})
_ENV_VAR = re.compile(
    r"\$[A-Za-z_][A-Za-z0-9_]*|%[A-Za-z_][A-Za-z0-9_]*%|\{[A-Za-z_][A-Za-z0-9_]*\}")
_WILDCARD = re.compile(r"[*?\[\]]")
_SEPARATOR = re.compile(r"[/:]+")
_WS = re.compile(r"\s+")


def normalize_action_kind(kind: str) -> str:
    """Canonical action-kind form: lowercase, no whitespace, '/' and ':'
    separators collapsed to '.', repeated dots removed. Vetoes match the
    canonical form, so case/whitespace/separator variants can never
    bypass a VETO_ACTION (S1 verifier deleg_d7639766 P9(b))."""
    if not isinstance(kind, str) or not kind.strip():
        raise ValueError("action kind must be a non-empty string")
    canonical = _WS.sub("", kind.strip().lower())
    canonical = _SEPARATOR.sub(".", canonical)
    canonical = re.sub(r"\.{2,}", ".", canonical).strip(".")
    return canonical


def resolve_canonical_target(target: str | Path) -> Path:
    """Canonical absolute target; rejects fuzzy or ambiguous forms:
    empty targets, environment-variable references, wildcards, drive or
    filesystem roots, and relative paths (no registered root context).
    Symlinks are followed deterministically — the resolved path is the
    identity that gets recorded, so no ambiguous target is ever acted on.
    """
    raw = str(target).strip()
    if not raw:
        raise ValueError("empty target")
    if _ENV_VAR.search(raw) or _WILDCARD.search(raw):
        raise ValueError(f"fuzzy target (environment variable or wildcard): {raw!r}")
    expanded = Path(raw).expanduser()
    if not expanded.is_absolute():
        raise ValueError(f"relative target without a registered root: {raw!r}")
    resolved = expanded.resolve(strict=False)
    if resolved == resolved.parent:
        raise ValueError(f"drive or filesystem root is not a target: {raw!r}")
    return resolved


@dataclass(frozen=True, slots=True)
class BrokerResult:
    decision: BrokerDecision
    reason: str
    canonical_kind: str | None = None
    resolved_targets: tuple[str, ...] = ()


class PolicyBroker:
    """Deterministic policy broker — architecture contract §7."""

    def __init__(self, database: FoundryDatabase,
                 stop_path: str | Path | None = None) -> None:
        self.database = database
        self.stop_path = Path(stop_path) if stop_path is not None else None

    # ------------------------------------------------------------------
    # the 15-step decision pipeline
    # ------------------------------------------------------------------
    def decide(self, envelope: ActionEnvelope, *,
               budget: dict[str, float] | None = None) -> BrokerResult:
        # 0 — envelope contract validation (a malformed envelope can never
        # reach state evaluation)
        error = self._validate_envelope(envelope)
        if error:
            return BrokerResult(BrokerDecision.NOT_READY, error)
        try:
            canonical_kind = normalize_action_kind(envelope.action_kind)
        except ValueError as exc:
            return BrokerResult(BrokerDecision.NOT_READY, str(exc))

        # 1 — emergency STOP (fresh durable state every call)
        stop = self._emergency_stop_active()
        if stop:
            return BrokerResult(BrokerDecision.EMERGENCY_STOPPED, stop,
                                canonical_kind)

        # 2 — target resolution and normalization
        try:
            targets = self._resolve_targets(envelope)
        except ValueError as exc:
            return BrokerResult(BrokerDecision.INVALID_TARGET, str(exc),
                                canonical_kind)

        # 3 — registration/reconciliation
        unregistered = self._registration_gap(envelope, targets)
        if unregistered:
            return BrokerResult(BrokerDecision.INVALID_TARGET, unregistered,
                                canonical_kind, targets)

        # 4 — goals, vetoes, lawfulness, risk, capability readiness
        veto = self._matching_veto(envelope, canonical_kind, targets)
        if veto:
            return BrokerResult(BrokerDecision.VETOED, veto, canonical_kind,
                                targets)
        readiness = self._capability_readiness(envelope, targets)
        if readiness:
            return BrokerResult(BrokerDecision.NOT_READY, readiness,
                                canonical_kind, targets)
        if not envelope.goal_alignment:
            return BrokerResult(
                BrokerDecision.NOT_READY,
                "goal alignment is required (every action serves a goal)",
                canonical_kind, targets)

        # 5 — secret/credential requirements
        credentials = self._credential_gap(envelope)
        if credentials:
            return BrokerResult(BrokerDecision.NOT_READY, credentials,
                                canonical_kind, targets)

        # 6 — resource and monetary budgets
        budget_issue = self._budget_gap(envelope, budget)
        if budget_issue:
            decision, reason = budget_issue
            return BrokerResult(decision, reason, canonical_kind, targets)

        # 7 — executable recovery strategy
        recovery = self._recovery_gap(envelope)
        if recovery:
            return BrokerResult(BrokerDecision.MISSING_RECOVERY_PATH, recovery,
                                canonical_kind, targets)

        # 8 — before-state capture
        before = self._before_state_gap(envelope)
        if before:
            return BrokerResult(BrokerDecision.NOT_READY, before,
                                canonical_kind, targets)

        # 9 — capability issuance: S3 wires the real short-lived issuance;
        # the planned issuance (capabilities_required) is recorded with
        # the decision so the executor's ticket is scoped to it.

        # 10 — one execution under an idempotency key
        if not envelope.idempotency_key:
            return BrokerResult(
                BrokerDecision.NOT_READY,
                "idempotency key is required (one execution per effect)",
                canonical_kind, targets)

        # 11-13 — after-state capture (executor), deterministic
        # verification, independent verification where required
        verification = self._verification_gap(envelope)
        if verification:
            return BrokerResult(BrokerDecision.NOT_READY, verification,
                                canonical_kind, targets)

        # 14 — commit/retain/freeze/rollback: recovery validated in step 7;
        # the terminal event (step 15) is recorded by record_decision().
        return BrokerResult(
            BrokerDecision.ALLOW,
            "all deterministic controls passed (steps 1-14)",
            canonical_kind, targets)

    # ------------------------------------------------------------------
    # deterministic gates (each returns None when the gate passes)
    # ------------------------------------------------------------------
    def _validate_envelope(self, envelope: ActionEnvelope) -> str | None:
        if not envelope.action_id or not envelope.mission_id:
            return "action and mission identity are required"
        if not envelope.action_kind:
            return "action kind is required"
        if envelope.risk_class not in _VALID_RISK_CLASSES:
            return (f"risk class must be one of {sorted(_VALID_RISK_CLASSES)}")
        if envelope.reversibility not in ("reversible", "irreversible"):
            return "reversibility must be 'reversible' or 'irreversible'"
        return None

    def _emergency_stop_active(self) -> str | None:
        if self.stop_path is not None and Path(self.stop_path).exists():
            return "emergency stop marker is present"
        with self.database.connect() as connection:
            stopped = connection.execute(
                "SELECT directive_id FROM directives "
                "WHERE active = 1 AND command_type = 'STOP_EVERYTHING'"
            ).fetchone()
            if stopped:
                return f"STOP_EVERYTHING directive {stopped[0]} is active"
            state = connection.execute(
                "SELECT status FROM supervisor_state WHERE singleton = 1"
            ).fetchone()
        if state is not None and state[0] in ("FROZEN", "SUSPENDED"):
            return f"supervisor is {state[0]}"
        return None

    def _resolve_targets(self, envelope: ActionEnvelope) -> tuple[str, ...]:
        refs: tuple[str, ...] = envelope.target_resources
        if not refs and envelope.target is not None:
            refs = (str(envelope.target),)
        if not refs:
            raise ValueError("no target resources declared")
        resolved: list[str] = []
        for ref in refs:
            if self._system_registered(ref):
                resolved.append(f"system:{ref}")
                continue
            path = resolve_canonical_target(ref)  # raises ValueError
            resolved.append(f"file:{path}")
        return tuple(resolved)

    def _system_registered(self, system_id: str) -> bool:
        with self.database.connect() as connection:
            return connection.execute(
                "SELECT 1 FROM systems WHERE system_id = ?",
                (system_id,)).fetchone() is not None

    def _registration_gap(self, envelope: ActionEnvelope,
                          targets: tuple[str, ...]) -> str | None:
        for target in targets:
            if target.startswith("system:"):
                continue  # existence proven during resolution
            # file targets must sit inside a REGISTERED resource root
            if envelope.resource_id:
                with self.database.connect() as connection:
                    resource = connection.execute(
                        "SELECT resolved_root FROM resources "
                        "WHERE resource_id = ?", (envelope.resource_id,)
                    ).fetchone()
                if resource is None:
                    return (f"resource {envelope.resource_id} is not "
                            "registered — file targets require a "
                            "registered root")
                root = Path(str(resource[0])).resolve(strict=False)
                file_path = Path(target.split(":", 1)[1])
                try:
                    file_path.relative_to(root)
                except ValueError:
                    return (f"resolved target {file_path} escapes the "
                            f"registered root {root}")
            else:
                return "file targets require a registered resource root"
        return None

    def _matching_veto(self, envelope: ActionEnvelope, canonical_kind: str,
                       targets: tuple[str, ...]) -> str | None:
        identities = {"GLOBAL", envelope.action_id, envelope.mission_id,
                      canonical_kind}
        if envelope.capability:
            identities.add(envelope.capability)
        if envelope.resource_id:
            identities.add(envelope.resource_id)
        identities.update(targets)
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT directive_id, command_type, target FROM directives "
                "WHERE active = 1"
            ).fetchall()
        for row in rows:
            if row["command_type"] in ("VETO_ACTION", "STOP_MISSION") and \
                    row["target"] in identities:
                return (f"active {row['command_type']} directive "
                        f"{row['directive_id']} matches")
        return None

    def _capability_readiness(self, envelope: ActionEnvelope,
                              targets: tuple[str, ...]) -> str | None:
        for target in targets:
            if not target.startswith("system:"):
                continue
            system_id = target.split(":", 1)[1]
            with self.database.connect() as connection:
                row = connection.execute(
                    "SELECT capabilities_json, readiness FROM systems "
                    "WHERE system_id = ?", (system_id,)).fetchone()
            capabilities = json.loads(str(row["capabilities_json"]))
            if envelope.capabilities_required:
                for capability in envelope.capabilities_required:
                    state = capabilities.get(capability)
                    if state not in _READY_STATES:
                        return (f"capability {capability} on {system_id} is "
                                f"{state or 'UNREGISTERED'}")
            elif row["readiness"] not in _READY_STATES:
                return (f"system {system_id} readiness is "
                        f"{row['readiness']}")
        if envelope.resource_id and envelope.capability:
            with self.database.connect() as connection:
                resource = connection.execute(
                    "SELECT capabilities_json FROM resources "
                    "WHERE resource_id = ?", (envelope.resource_id,)
                ).fetchone()
            if resource is not None:
                state = json.loads(str(resource["capabilities_json"])).get(
                    envelope.capability)
                if state not in _READY_STATES:
                    return (f"capability {envelope.capability} on resource "
                            f"{envelope.resource_id} is "
                            f"{state or 'UNREGISTERED'}")
        return None

    def _credential_gap(self, envelope: ActionEnvelope) -> str | None:
        if not envelope.credential_requirements:
            return None
        with self.database.connect() as connection:
            systems = connection.execute(
                "SELECT capabilities_json FROM systems WHERE kind = "
                "'credentials'").fetchall()
        for required in envelope.credential_requirements:
            for row in systems:
                state = json.loads(str(row["capabilities_json"])).get(required)
                if state in _READY_STATES:
                    break
            else:
                return (f"credential capability {required} is not READY "
                        "(no credentials system with that capability)")
        return None

    def _budget_gap(self, envelope: ActionEnvelope,
                    budget: dict[str, float] | None
                    ) -> tuple[BrokerDecision, str] | None:
        if not envelope.budget_estimate:
            return None
        if budget is None:
            return (BrokerDecision.NOT_READY,
                    "budget policy is not configured but a budget estimate "
                    "is declared — fail closed")
        for key, value in envelope.budget_estimate.items():
            limit = budget.get(key)
            if limit is None:
                return (BrokerDecision.NOT_READY,
                        f"no budget policy for {key} — fail closed")
            if value > limit:
                return (BrokerDecision.BUDGET_EXCEEDED,
                        f"budget {key} {value} exceeds the limit {limit}")
        return None

    def _recovery_gap(self, envelope: ActionEnvelope) -> str | None:
        if envelope.reversibility == "irreversible" or \
                envelope.risk_class in ("C2", "C3"):
            method = envelope.rollback_plan.get("method")
            if not method:
                return ("no executable recovery strategy — rollback_plan "
                        "must declare a method for irreversible/C2/C3 "
                        "actions")
        return None

    def _before_state_gap(self, envelope: ActionEnvelope) -> str | None:
        if envelope.expected_effects or envelope.expected_effect:
            capture = envelope.before_state_capture
            if not capture or not (capture.get("ref") or capture.get("hash")):
                return ("before-state capture is required for a mutating "
                        "action (ref or hash)")
        return None

    def _verification_gap(self, envelope: ActionEnvelope) -> str | None:
        if envelope.verification_required and not envelope.verification_plan:
            return "verification plan is required"
        if envelope.risk_class == "C3" and not \
                envelope.verification_plan.get("independent"):
            return ("independent verification is required for risk class "
                    "C3")
        return None


def record_decision(database: FoundryDatabase, envelope: ActionEnvelope,
                    result: BrokerResult) -> None:
    """Step 15 — the terminal decision event.

    The idempotency key covers the full decision payload: an identical
    re-delivery dedupes (one event), while a changed envelope records a
    NEW decision event (append-only audit trail — the decision history
    is never rewritten).
    """
    payload = {
        "action_id": envelope.action_id,
        "mission_id": envelope.mission_id,
        "step_id": envelope.step_id,
        "requested_by_agent_run_id": envelope.requested_by_agent_run_id,
        "decision": result.decision.value,
        "reason": result.reason,
        "action_kind": result.canonical_kind,
        "target_resources": list(result.resolved_targets),
        "capabilities_required": list(envelope.capabilities_required),
        "capability_states_required": list(envelope.capability_states_required),
        "goal_alignment": envelope.goal_alignment,
        "risk_class": envelope.risk_class,
        "reversibility": envelope.reversibility,
        "idempotency_key": envelope.idempotency_key,
        "budget_estimate": envelope.budget_estimate,
        "credential_requirements": list(envelope.credential_requirements),
        "created_at": envelope.created_at,
    }
    event = Event.create(
        "action.decided",
        payload,
        source_type="policy",
        source_id="v5-broker",
        correlation_id=envelope.mission_id,
        idempotency_key=(f"action.decided:{envelope.action_id}:"
                         f"{result.decision.value}:{payload_hash(payload)}"),
    )
    database.append(event)


def require_allowance(database: FoundryDatabase, action_id: str) -> None:
    """Executor-side ticket check (worker-bypass proof).

    Execution of an action is allowed ONLY while the LATEST broker
    decision for that action is ALLOW. No decision -> refused; a later
    non-allow decision (e.g. STOP raised while the action was queued)
    revokes the ticket immediately.
    """
    with database.connect() as connection:
        row = connection.execute(
            "SELECT payload_json FROM events WHERE event_type = "
            "'action.decided' AND json_extract(payload_json, '$.action_id') "
            "= ? ORDER BY sequence DESC LIMIT 1", (action_id,)).fetchone()
    if row is None:
        raise PermissionError(
            f"action {action_id} has no broker decision — refusing "
            "execution (broker bypass is not possible)")
    decision = json.loads(row["payload_json"])["decision"]
    if decision != BrokerDecision.ALLOW.value:
        raise PermissionError(
            f"action {action_id} was not allowed ({decision}) — refusing "
            "execution")
