"""Typed deterministic state machines for goals, missions, effects, releases, incidents."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from jarvis_foundry.kernel.errors import TransitionError


def transition_status[Status: StrEnum](
    current: Status, action: str, transitions: Mapping[Status, Mapping[str, Status]]
) -> Status:
    allowed = transitions.get(current)
    if allowed is None or action not in allowed:
        raise TransitionError(f"transition {action!r} is not allowed from {current.value}")
    return allowed[action]


class GoalStatus(StrEnum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"
    COMPLETED = "COMPLETED"
    ARCHIVED = "ARCHIVED"
    CANCELLED = "CANCELLED"


GOAL_TRANSITIONS: dict[GoalStatus, dict[str, GoalStatus]] = {
    GoalStatus.DRAFT: {"activate": GoalStatus.ACTIVE, "cancel": GoalStatus.CANCELLED},
    GoalStatus.ACTIVE: {
        "pause": GoalStatus.PAUSED,
        "complete": GoalStatus.COMPLETED,
        "cancel": GoalStatus.CANCELLED,
    },
    GoalStatus.PAUSED: {"resume": GoalStatus.ACTIVE, "cancel": GoalStatus.CANCELLED},
    GoalStatus.COMPLETED: {"archive": GoalStatus.ARCHIVED},
    GoalStatus.ARCHIVED: {},
    GoalStatus.CANCELLED: {},
}


class MissionStatus(StrEnum):
    PLANNED = "PLANNED"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


MISSION_TRANSITIONS: dict[MissionStatus, dict[str, MissionStatus]] = {
    MissionStatus.PLANNED: {
        "start": MissionStatus.RUNNING,
        "cancel": MissionStatus.CANCELLED,
    },
    MissionStatus.RUNNING: {
        "wait": MissionStatus.WAITING,
        "block": MissionStatus.BLOCKED,
        "complete": MissionStatus.COMPLETED,
        "fail": MissionStatus.FAILED,
        "cancel": MissionStatus.CANCELLED,
    },
    MissionStatus.WAITING: {"resume": MissionStatus.RUNNING, "cancel": MissionStatus.CANCELLED},
    MissionStatus.BLOCKED: {"recover": MissionStatus.RUNNING, "cancel": MissionStatus.CANCELLED},
    MissionStatus.FAILED: {"retry": MissionStatus.PLANNED, "cancel": MissionStatus.CANCELLED},
    MissionStatus.COMPLETED: {},
    MissionStatus.CANCELLED: {},
}


class StepStatus(StrEnum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


STEP_TRANSITIONS: dict[StepStatus, dict[str, StepStatus]] = {
    StepStatus.PENDING: {"activate": StepStatus.READY, "cancel": StepStatus.CANCELLED},
    StepStatus.READY: {"start": StepStatus.RUNNING, "cancel": StepStatus.CANCELLED},
    StepStatus.RUNNING: {
        "complete": StepStatus.COMPLETED,
        "fail": StepStatus.FAILED,
        "block": StepStatus.BLOCKED,
        "cancel": StepStatus.CANCELLED,
    },
    StepStatus.BLOCKED: {"unblock": StepStatus.RUNNING, "cancel": StepStatus.CANCELLED},
    StepStatus.FAILED: {"retry": StepStatus.READY, "cancel": StepStatus.CANCELLED},
    StepStatus.COMPLETED: {},
    StepStatus.CANCELLED: {},
}


class EffectStatus(StrEnum):
    PENDING = "PENDING"
    PREPARED = "PREPARED"
    EXECUTING = "EXECUTING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    COMPENSATED = "COMPENSATED"
    CANCELLED = "CANCELLED"


EFFECT_TRANSITIONS: dict[EffectStatus, dict[str, EffectStatus]] = {
    EffectStatus.PENDING: {"prepare": EffectStatus.PREPARED, "cancel": EffectStatus.CANCELLED},
    EffectStatus.PREPARED: {
        "execute": EffectStatus.EXECUTING,
        "compensate": EffectStatus.COMPENSATED,
        "cancel": EffectStatus.CANCELLED,
    },
    EffectStatus.EXECUTING: {
        "succeed": EffectStatus.SUCCEEDED,
        "fail": EffectStatus.FAILED,
    },
    EffectStatus.FAILED: {
        "compensate": EffectStatus.COMPENSATED,
        "retry": EffectStatus.PREPARED,
    },
    EffectStatus.SUCCEEDED: {},
    EffectStatus.COMPENSATED: {},
    EffectStatus.CANCELLED: {},
}


class ReleaseStage(StrEnum):
    BUILD = "BUILD"
    LAB = "LAB"
    SHADOW = "SHADOW"
    CANARY = "CANARY"
    PROMOTED = "PROMOTED"
    FROZEN = "FROZEN"
    ROLLED_BACK = "ROLLED_BACK"
    RESTORED = "RESTORED"
    QUARANTINED = "QUARANTINED"


RELEASE_TRANSITIONS: dict[ReleaseStage, dict[str, ReleaseStage]] = {
    ReleaseStage.BUILD: {
        "enter_lab": ReleaseStage.LAB,
        "promote": ReleaseStage.PROMOTED,
        "quarantine": ReleaseStage.QUARANTINED,
    },
    ReleaseStage.LAB: {
        "shadow": ReleaseStage.SHADOW,
        "promote": ReleaseStage.PROMOTED,
        "quarantine": ReleaseStage.QUARANTINED,
    },
    ReleaseStage.SHADOW: {
        "canary": ReleaseStage.CANARY,
        "promote": ReleaseStage.PROMOTED,
        "quarantine": ReleaseStage.QUARANTINED,
    },
    ReleaseStage.CANARY: {
        "promote": ReleaseStage.PROMOTED,
        "quarantine": ReleaseStage.QUARANTINED,
    },
    ReleaseStage.PROMOTED: {
        "freeze": ReleaseStage.FROZEN,
        "rollback": ReleaseStage.ROLLED_BACK,
    },
    ReleaseStage.FROZEN: {"rollback": ReleaseStage.ROLLED_BACK},
    ReleaseStage.ROLLED_BACK: {"restore": ReleaseStage.RESTORED},
    ReleaseStage.RESTORED: {
        "promote": ReleaseStage.PROMOTED,
        "quarantine": ReleaseStage.QUARANTINED,
    },
    ReleaseStage.QUARANTINED: {},
}


class IncidentStatus(StrEnum):
    OPEN = "OPEN"
    INVESTIGATING = "INVESTIGATING"
    MITIGATING = "MITIGATING"
    RESOLVED = "RESOLVED"
    CLOSED = "CLOSED"
    REOPENED = "REOPENED"


INCIDENT_TRANSITIONS: dict[IncidentStatus, dict[str, IncidentStatus]] = {
    IncidentStatus.OPEN: {"investigate": IncidentStatus.INVESTIGATING},
    IncidentStatus.INVESTIGATING: {
        "mitigate": IncidentStatus.MITIGATING,
        "resolve": IncidentStatus.RESOLVED,
    },
    IncidentStatus.MITIGATING: {"resolve": IncidentStatus.RESOLVED},
    IncidentStatus.RESOLVED: {"close": IncidentStatus.CLOSED},
    IncidentStatus.CLOSED: {"reopen": IncidentStatus.REOPENED},
    IncidentStatus.REOPENED: {
        "investigate": IncidentStatus.INVESTIGATING,
        "mitigate": IncidentStatus.MITIGATING,
    },
}


def _serialize(status: StrEnum) -> dict[str, str]:
    return {"status": status.value}


@dataclass(frozen=True, init=False)
class GoalMachine:
    status: GoalStatus = field(default=GoalStatus.DRAFT)

    def __init__(self, status: str | GoalStatus = "DRAFT") -> None:
        object.__setattr__(
            self,
            "status",
            status if isinstance(status, GoalStatus) else GoalStatus(status),
        )

    def transition(self, action: str) -> GoalMachine:
        return GoalMachine(transition_status(self.status, action, GOAL_TRANSITIONS))

    def serialize(self) -> dict[str, str]:
        return _serialize(self.status)

    @classmethod
    def deserialize(cls, payload: Mapping[str, str]) -> GoalMachine:
        return cls(payload["status"])


@dataclass(frozen=True, init=False)
class MissionMachine:
    status: MissionStatus = field(default=MissionStatus.PLANNED)

    def __init__(self, status: str | MissionStatus = "PLANNED") -> None:
        object.__setattr__(
            self,
            "status",
            status if isinstance(status, MissionStatus) else MissionStatus(status),
        )

    def transition(self, action: str) -> MissionMachine:
        return MissionMachine(transition_status(self.status, action, MISSION_TRANSITIONS))

    def serialize(self) -> dict[str, str]:
        return _serialize(self.status)

    @classmethod
    def deserialize(cls, payload: Mapping[str, str]) -> MissionMachine:
        return cls(payload["status"])


@dataclass(frozen=True, init=False)
class MissionStepMachine:
    status: StepStatus = field(default=StepStatus.PENDING)

    def __init__(self, status: str | StepStatus = "PENDING") -> None:
        object.__setattr__(
            self,
            "status",
            status if isinstance(status, StepStatus) else StepStatus(status),
        )

    def transition(self, action: str) -> MissionStepMachine:
        return MissionStepMachine(transition_status(self.status, action, STEP_TRANSITIONS))

    def serialize(self) -> dict[str, str]:
        return _serialize(self.status)

    @classmethod
    def deserialize(cls, payload: Mapping[str, str]) -> MissionStepMachine:
        return cls(payload["status"])


@dataclass(frozen=True, init=False)
class EffectMachine:
    status: EffectStatus = field(default=EffectStatus.PENDING)

    def __init__(self, status: str | EffectStatus = "PENDING") -> None:
        object.__setattr__(
            self,
            "status",
            status if isinstance(status, EffectStatus) else EffectStatus(status),
        )

    def transition(self, action: str) -> EffectMachine:
        return EffectMachine(transition_status(self.status, action, EFFECT_TRANSITIONS))

    def serialize(self) -> dict[str, str]:
        return _serialize(self.status)

    @classmethod
    def deserialize(cls, payload: Mapping[str, str]) -> EffectMachine:
        return cls(payload["status"])


@dataclass(frozen=True, init=False)
class ReleaseMachine:
    stage: ReleaseStage = field(default=ReleaseStage.BUILD)

    def __init__(self, stage: str | ReleaseStage = "BUILD") -> None:
        object.__setattr__(
            self,
            "stage",
            stage if isinstance(stage, ReleaseStage) else ReleaseStage(stage),
        )

    def transition(self, action: str) -> ReleaseMachine:
        return ReleaseMachine(transition_status(self.stage, action, RELEASE_TRANSITIONS))

    def serialize(self) -> dict[str, str]:
        return _serialize(self.stage)

    @classmethod
    def deserialize(cls, payload: Mapping[str, str]) -> ReleaseMachine:
        return cls(payload["status"])


@dataclass(frozen=True, init=False)
class IncidentMachine:
    status: IncidentStatus = field(default=IncidentStatus.OPEN)

    def __init__(self, status: str | IncidentStatus = "OPEN") -> None:
        object.__setattr__(
            self,
            "status",
            status if isinstance(status, IncidentStatus) else IncidentStatus(status),
        )

    def transition(self, action: str) -> IncidentMachine:
        return IncidentMachine(transition_status(self.status, action, INCIDENT_TRANSITIONS))

    def serialize(self) -> dict[str, str]:
        return _serialize(self.status)

    @classmethod
    def deserialize(cls, payload: Mapping[str, str]) -> IncidentMachine:
        return cls(payload["status"])
