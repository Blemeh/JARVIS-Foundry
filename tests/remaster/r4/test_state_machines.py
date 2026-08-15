from __future__ import annotations

import pytest

from jarvis_foundry.kernel.errors import TransitionError
from jarvis_foundry.kernel.state_machines import (
    EffectMachine,
    GoalMachine,
    IncidentMachine,
    MissionMachine,
    MissionStepMachine,
    ReleaseMachine,
)


def assert_success_refusal_duplicate_crash_restart_recovery(
    machine_type: type[object],
    initial: str,
    action: str,
    target: str,
    recovery_actions: tuple[str, ...] = (),
    state_field: str = "state",
) -> None:
    machine = machine_type(initial)  # type: ignore[operator]
    assert getattr(machine, state_field) == initial
    if recovery_actions:
        with pytest.raises(TransitionError):
            machine.transition(recovery_actions[0])
    advanced = machine.transition(action)
    assert getattr(advanced, state_field) == target
    with pytest.raises(TransitionError):
        advanced.transition(action)
    with pytest.raises(TransitionError):
        advanced.transition("not-a-real-action")
    serialized = advanced.serialize()
    restored = machine_type.deserialize(serialized)  # type: ignore[attr-defined]
    assert getattr(restored, state_field) == target
    for recovery_action in recovery_actions:
        recovered = restored.transition(recovery_action)
        assert getattr(recovered, state_field) != "FAILED"


def test_goal_machine_transitions() -> None:
    assert_success_refusal_duplicate_crash_restart_recovery(
        GoalMachine, "DRAFT", "activate", "ACTIVE", state_field="status"
    )
    machine = GoalMachine("DRAFT")
    machine = machine.transition("activate").transition("complete")
    assert machine.status == "COMPLETED"
    with pytest.raises(TransitionError):
        machine.transition("activate")


def test_mission_machine_transitions() -> None:
    assert_success_refusal_duplicate_crash_restart_recovery(
        MissionMachine, "PLANNED", "start", "RUNNING", state_field="status"
    )
    machine = MissionMachine("PLANNED").transition("start").transition("wait")
    assert machine.status == "WAITING"
    recovered = machine.transition("resume")
    assert recovered.status == "RUNNING"


def test_mission_step_machine_transitions() -> None:
    assert_success_refusal_duplicate_crash_restart_recovery(
        MissionStepMachine, "PENDING", "activate", "READY", state_field="status"
    )
    machine = MissionStepMachine("PENDING").transition("activate").transition("start")
    assert machine.status == "RUNNING"
    machine = machine.transition("block")
    assert machine.status == "BLOCKED"
    assert machine.transition("unblock").status == "RUNNING"


def test_effect_machine_transitions() -> None:
    assert_success_refusal_duplicate_crash_restart_recovery(
        EffectMachine, "PENDING", "prepare", "PREPARED", state_field="status"
    )
    machine = EffectMachine("PENDING").transition("prepare").transition("execute")
    assert machine.status == "EXECUTING"
    assert machine.transition("succeed").status == "SUCCEEDED"
    failed = EffectMachine("PENDING").transition("prepare").transition("execute").transition("fail")
    assert failed.status == "FAILED"
    assert failed.transition("compensate").status == "COMPENSATED"


def test_release_machine_transitions() -> None:
    assert_success_refusal_duplicate_crash_restart_recovery(
        ReleaseMachine, "BUILD", "enter_lab", "LAB", state_field="stage"
    )
    machine = ReleaseMachine("BUILD")
    machine = machine.transition("enter_lab").transition("shadow")
    assert machine.stage == "SHADOW"
    machine = machine.transition("canary").transition("promote")
    assert machine.stage == "PROMOTED"
    machine = machine.transition("freeze")
    assert machine.stage == "FROZEN"
    with pytest.raises(TransitionError):
        machine.transition("canary")
    rolled = ReleaseMachine("PROMOTED").transition("rollback")
    assert rolled.stage == "ROLLED_BACK"
    assert rolled.transition("restore").stage == "RESTORED"
    quarantined = ReleaseMachine("CANARY").transition("quarantine")
    assert quarantined.stage == "QUARANTINED"


def test_incident_machine_transitions() -> None:
    assert_success_refusal_duplicate_crash_restart_recovery(
        IncidentMachine, "OPEN", "investigate", "INVESTIGATING", state_field="status"
    )
    machine = IncidentMachine("OPEN").transition("investigate").transition("mitigate")
    assert machine.status == "MITIGATING"
    machine = machine.transition("resolve")
    assert machine.status == "RESOLVED"
    assert machine.transition("close").status == "CLOSED"
    reopened = IncidentMachine("CLOSED").transition("reopen")
    assert reopened.status == "REOPENED"
    assert reopened.transition("investigate").status == "INVESTIGATING"


def test_crash_between_transitions_recovers_durably() -> None:
    machine = GoalMachine("DRAFT").transition("activate")
    crash_point = machine.serialize()
    restored = GoalMachine.deserialize(crash_point)
    assert restored.status == "ACTIVE"
    assert restored.transition("complete").status == "COMPLETED"
