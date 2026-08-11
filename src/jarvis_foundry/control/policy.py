from __future__ import annotations

import json
from pathlib import Path

from ..database import FoundryDatabase
from ..domain import ActionDecision, ActionEnvelope, PolicyVerdict

_VALID_RISK_CLASSES = frozenset({"C0", "C1", "C2", "C3"})


def _validate_envelope(envelope: ActionEnvelope) -> str | None:
    """Deterministic envelope contract validation.

    Returns a human-readable reason when the envelope does not satisfy the
    standing action contract, else None. Runs before any state lookup so a
    malformed envelope can never reach capability or resource evaluation.
    """
    if not envelope.action_id or not envelope.mission_id or not envelope.resource_id:
        return "action, mission, and resource identity are required"
    if not envelope.action_kind or not envelope.capability:
        return "action kind and capability are required"
    if not envelope.expected_effects:
        return "expected effects are required"
    if not envelope.rollback:
        return "rollback contract is required"
    if envelope.risk_class not in _VALID_RISK_CLASSES:
        return f"risk class must be one of {sorted(_VALID_RISK_CLASSES)}"
    return None


class ActionPolicy:
    def __init__(self, database: FoundryDatabase, stop_path: Path) -> None:
        self.database = database
        self.stop_path = stop_path.resolve()

    def evaluate(self, envelope: ActionEnvelope) -> PolicyVerdict:
        if self.stop_path.exists():
            return PolicyVerdict(ActionDecision.STOPPED, "emergency stop is active")
        validation_error = _validate_envelope(envelope)
        if validation_error:
            return PolicyVerdict(ActionDecision.NOT_READY, validation_error)
        with self.database.connect() as connection:
            start_active = connection.execute(
                "SELECT start_active FROM system_state WHERE singleton = 1"
            ).fetchone()[0]
        if not start_active:
            return PolicyVerdict(ActionDecision.NOT_READY, "standing START BUILD mandate is not active")

        resource = self.database.row("resources", "resource_id", envelope.resource_id)
        if resource is None:
            return PolicyVerdict(ActionDecision.NOT_READY, "resource is not registered")
        mission = self.database.row("missions", "mission_id", envelope.mission_id)
        if mission is None:
            return PolicyVerdict(ActionDecision.NOT_READY, "mission is not registered")
        if mission["resource_id"] != envelope.resource_id:
            return PolicyVerdict(ActionDecision.NOT_READY, "mission does not own the target resource")

        root = Path(str(resource["resolved_root"])).resolve()
        target = envelope.target.expanduser()
        if not target.is_absolute():
            target = root / target
        resolved = target.resolve(strict=False)
        try:
            resolved.relative_to(root)
        except ValueError:
            return PolicyVerdict(ActionDecision.INVALID_TARGET, "resolved target escapes registered root", resolved)

        capabilities = json.loads(str(resource["capabilities_json"]))
        state = capabilities.get(envelope.capability)
        if state not in ("READY", "ACTIVE"):
            return PolicyVerdict(
                ActionDecision.NOT_READY,
                f"capability {envelope.capability} is {state or 'UNREGISTERED'}",
                resolved,
            )

        vetoes = self._matching_vetoes(envelope, resolved)
        if vetoes:
            return PolicyVerdict(ActionDecision.VETOED, "active owner veto matches action", resolved, tuple(vetoes))
        if not envelope.expected_effects or not envelope.rollback:
            return PolicyVerdict(
                ActionDecision.NOT_READY,
                "expected effects and rollback contract are required",
                resolved,
            )
        return PolicyVerdict(ActionDecision.ALLOWED, "standing mandate and deterministic controls passed", resolved)

    def _matching_vetoes(self, envelope: ActionEnvelope, target: Path) -> list[str]:
        with self.database.connect() as connection:
            rows = connection.execute(
                "SELECT directive_id, command_type, target FROM directives WHERE active = 1"
            ).fetchall()
        matched: list[str] = []
        identities = {
            "GLOBAL", envelope.action_id, envelope.mission_id, envelope.resource_id,
            envelope.capability, str(target),
        }
        for row in rows:
            if row["command_type"] == "STOP_EVERYTHING":
                matched.append(row["directive_id"])
            elif row["command_type"] in {"VETO_ACTION", "STOP_MISSION"} and row["target"] in identities:
                matched.append(row["directive_id"])
        return matched
