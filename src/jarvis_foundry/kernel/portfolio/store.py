"""Durable, replayable portfolio store with hash-bound append-only events."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

from jarvis_foundry.kernel.canonical import canonical_bytes, canonical_hash
from jarvis_foundry.kernel.portfolio.errors import ConflictError, IntegrityError, PortfolioError
from jarvis_foundry.kernel.portfolio.records import (
    BudgetState,
    CandidateAction,
    ControlDirective,
    DiscoveredCandidate,
    MissionLink,
    MonitorChange,
    MonitorCursor,
    Observation,
    PortfolioProject,
    Record,
    ResearchSynthesis,
    SelectionDecision,
)

RECORD_TYPES: dict[str, type[Record]] = {
    "portfolio_project": PortfolioProject,
    "candidate_action": CandidateAction,
    "observation": Observation,
    "discovered_candidate": DiscoveredCandidate,
    "selection_decision": SelectionDecision,
    "research_synthesis": ResearchSynthesis,
    "monitor_cursor": MonitorCursor,
    "monitor_change": MonitorChange,
    "mission_link": MissionLink,
    "budget_state": BudgetState,
    "control_directive": ControlDirective,
}

_ID_FIELDS: dict[str, str] = {
    "portfolio_project": "project_id",
    "candidate_action": "candidate_id",
    "observation": "observation_id",
    "discovered_candidate": "candidate_id",
    "selection_decision": "decision_id",
    "research_synthesis": "research_id",
    "monitor_cursor": "source_id",
    "monitor_change": "change_id",
    "mission_link": "mission_id",
    "budget_state": "budget_key",
    "control_directive": "directive_id",
}


@dataclass(frozen=True)
class AppendResult:
    kind: str
    key: str
    record: Record
    sequence: int
    created: bool


class PortfolioStore:
    """Append-only event store; state is rebuilt deterministically on open."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self._events_path = self.root / "events.jsonl"
        self._state_path = self.root / "state.json"
        self._latest: dict[tuple[str, str], tuple[int, str, Record]] = {}
        self._history: dict[tuple[str, str], list[tuple[int, str, Record]]] = {}
        self._sequence = 0
        self._rebuild()

    def _rebuild(self) -> None:
        self._latest = {}
        self._history = {}
        self._sequence = 0
        if not self._events_path.is_file():
            self._write_state()
            return
        for line_no, raw in enumerate(
            self._events_path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not raw.strip():
                continue
            marker = raw.rfind("|")
            if marker < 0:
                raise IntegrityError(f"event line {line_no} has no hash marker")
            body, digest = raw[:marker], raw[marker + 1 :]
            if canonical_hash(json.loads(body)) != digest:
                raise IntegrityError(f"event line {line_no} failed hash verification")
            payload = json.loads(body)
            kind = str(payload["kind"])
            key = str(payload["key"])
            record_cls = RECORD_TYPES.get(kind)
            if record_cls is None:
                raise PortfolioError(f"unknown record kind: {kind}")
            record = cast(Any, record_cls).from_dict(payload["record"])
            self._apply(kind, key, record)
        self._write_state()

    def _apply(self, kind: str, key: str, record: Record) -> None:
        self._sequence += 1
        id_field = _ID_FIELDS[kind]
        logical_id = str(getattr(record, id_field))
        entry = (self._sequence, key, record)
        history_key = (kind, logical_id)
        self._history.setdefault(history_key, []).append(entry)
        self._latest[history_key] = entry

    def _write_state(self) -> None:
        payload = self._state_payload()
        self._state_path.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

    def _state_payload(self) -> dict[str, Any]:
        return {
            "sequence": self._sequence,
            "records": {
                f"{kind}:{logical_id}": {
                    "key": key,
                    "record": record.to_dict(),
                }
                for (kind, logical_id), (_, key, record) in sorted(self._latest.items())
            },
            "state_hash": self.state_hash(),
        }

    def state_hash(self) -> str:
        entries = sorted(
            (kind, logical_id, key, record.to_dict())
            for (kind, logical_id), (_, key, record) in self._latest.items()
        )
        return canonical_hash(entries)

    def append(
        self,
        kind: str,
        idempotency_key: str,
        record: Record,
    ) -> AppendResult:
        if kind not in RECORD_TYPES:
            raise PortfolioError(f"unknown record kind: {kind}")
        existing = self._find_by_key(kind, idempotency_key)
        if existing is not None:
            return AppendResult(
                kind=kind,
                key=idempotency_key,
                record=existing,
                sequence=self._sequence_for_key(kind, idempotency_key),
                created=False,
            )
        body = {
            "kind": kind,
            "key": idempotency_key,
            "record": record.to_dict(),
        }
        digest = canonical_hash(body)
        line = json.dumps(body, sort_keys=True, separators=(",", ":")) + f"|{digest}\n"
        with self._events_path.open("a", encoding="utf-8") as handle:
            handle.write(line)
        self._apply(kind, idempotency_key, record)
        self._write_state()
        return AppendResult(
            kind=kind,
            key=idempotency_key,
            record=record,
            sequence=self._sequence,
            created=True,
        )

    def append_with_conflict_check(
        self,
        kind: str,
        idempotency_key: str,
        record: Record,
    ) -> AppendResult:
        existing = self._find_by_key(kind, idempotency_key)
        if existing is not None and existing.to_dict() != record.to_dict():
            raise ConflictError(
                f"idempotency key {idempotency_key!r} reused with different content"
            )
        return self.append(kind, idempotency_key, record)

    def _find_by_key(self, kind: str, key: str) -> Record | None:
        for (record_kind, _), (_, stored_key, record) in self._latest.items():
            if record_kind == kind and stored_key == key:
                return record
        return None

    def _sequence_for_key(self, kind: str, key: str) -> int:
        for (record_kind, _), (sequence, stored_key, _) in self._latest.items():
            if record_kind == kind and stored_key == key:
                return sequence
        return self._sequence

    def latest(self, kind: str, logical_id: str) -> Record | None:
        entry = self._latest.get((kind, logical_id))
        return entry[2] if entry is not None else None

    def history(self, kind: str, logical_id: str) -> list[Record]:
        return [entry[2] for entry in self._history.get((kind, logical_id), [])]

    def _all(self, kind: str) -> list[Record]:
        return [
            record
            for (record_kind, _), (_, _, record) in sorted(self._latest.items())
            if record_kind == kind
        ]

    def projects(self) -> list[PortfolioProject]:
        return [
            record
            for record in self._all("portfolio_project")
            if isinstance(record, PortfolioProject)
        ]

    def project(self, project_id: str) -> PortfolioProject | None:
        record = self.latest("portfolio_project", project_id)
        return record if isinstance(record, PortfolioProject) else None

    def candidates(self) -> list[CandidateAction]:
        return [
            record
            for record in self._all("candidate_action")
            if isinstance(record, CandidateAction)
        ]

    def candidate(self, candidate_id: str) -> CandidateAction | None:
        record = self.latest("candidate_action", candidate_id)
        return record if isinstance(record, CandidateAction) else None

    def observations(self) -> list[Observation]:
        return [record for record in self._all("observation") if isinstance(record, Observation)]

    def discoveries(self) -> list[DiscoveredCandidate]:
        return [
            record
            for record in self._all("discovered_candidate")
            if isinstance(record, DiscoveredCandidate)
        ]

    def active_discoveries(self) -> list[DiscoveredCandidate]:
        return [record for record in self.discoveries() if record.status == "CANDIDATE"]

    def decisions(self) -> list[SelectionDecision]:
        return [
            record
            for record in self._all("selection_decision")
            if isinstance(record, SelectionDecision)
        ]

    def decision(self, decision_id: str) -> SelectionDecision | None:
        record = self.latest("selection_decision", decision_id)
        return record if isinstance(record, SelectionDecision) else None

    def research(self) -> list[ResearchSynthesis]:
        return [
            record
            for record in self._all("research_synthesis")
            if isinstance(record, ResearchSynthesis)
        ]

    def research_by_key(self, key: str) -> ResearchSynthesis | None:
        record = self._find_by_key("research_synthesis", key)
        return record if isinstance(record, ResearchSynthesis) else None

    def cursors(self) -> list[MonitorCursor]:
        return [
            record for record in self._all("monitor_cursor") if isinstance(record, MonitorCursor)
        ]

    def cursor(self, source_id: str) -> MonitorCursor | None:
        record = self.latest("monitor_cursor", source_id)
        return record if isinstance(record, MonitorCursor) else None

    def cursor_history(self, source_id: str) -> list[MonitorCursor]:
        records = self.history("monitor_cursor", source_id)
        return [record for record in records if isinstance(record, MonitorCursor)]

    def changes(self) -> list[MonitorChange]:
        return [
            record for record in self._all("monitor_change") if isinstance(record, MonitorChange)
        ]

    def mission_links(self) -> list[MissionLink]:
        return [record for record in self._all("mission_link") if isinstance(record, MissionLink)]

    def budget_states(self) -> list[BudgetState]:
        return [record for record in self._all("budget_state") if isinstance(record, BudgetState)]

    def control_directives(self) -> list[ControlDirective]:
        return [
            record
            for record in self._all("control_directive")
            if isinstance(record, ControlDirective)
        ]

    def append_project(
        self, project: PortfolioProject, *, idempotency_key: str | None = None
    ) -> AppendResult:
        return self.append_with_conflict_check(
            "portfolio_project",
            idempotency_key or f"portfolio_project:{project.project_id}",
            project,
        )

    def append_candidate(
        self, candidate: CandidateAction, *, idempotency_key: str | None = None
    ) -> AppendResult:
        return self.append_with_conflict_check(
            "candidate_action",
            idempotency_key or f"candidate_action:{candidate.candidate_id}",
            candidate,
        )

    def append_observation(
        self, observation: Observation, *, idempotency_key: str | None = None
    ) -> AppendResult:
        return self.append_with_conflict_check(
            "observation",
            idempotency_key or f"observation:{observation.observation_id}",
            observation,
        )

    def append_discovery(
        self, candidate: DiscoveredCandidate, *, idempotency_key: str | None = None
    ) -> AppendResult:
        return self.append_with_conflict_check(
            "discovered_candidate",
            idempotency_key or f"discovered_candidate:{candidate.candidate_id}",
            candidate,
        )

    def append_decision(
        self, decision: SelectionDecision, *, idempotency_key: str | None = None
    ) -> AppendResult:
        return self.append_with_conflict_check(
            "selection_decision",
            idempotency_key or decision.idempotency_key,
            decision,
        )

    def append_research(
        self, synthesis: ResearchSynthesis, *, idempotency_key: str | None = None
    ) -> AppendResult:
        return self.append_with_conflict_check(
            "research_synthesis",
            idempotency_key or f"research_synthesis:{synthesis.research_id}",
            synthesis,
        )

    def append_cursor(
        self, cursor: MonitorCursor, *, idempotency_key: str | None = None
    ) -> AppendResult:
        return self.append_with_conflict_check(
            "monitor_cursor",
            idempotency_key or f"monitor_cursor:{cursor.source_id}",
            cursor,
        )

    def append_change(
        self, change: MonitorChange, *, idempotency_key: str | None = None
    ) -> AppendResult:
        return self.append_with_conflict_check(
            "monitor_change",
            idempotency_key or change.idempotency_key,
            change,
        )

    def append_mission_link(
        self, link: MissionLink, *, idempotency_key: str | None = None
    ) -> AppendResult:
        return self.append_with_conflict_check(
            "mission_link",
            idempotency_key or f"mission_link:{link.mission_id}",
            link,
        )

    def append_budget_state(
        self, state: BudgetState, *, idempotency_key: str | None = None
    ) -> AppendResult:
        return self.append_with_conflict_check(
            "budget_state",
            idempotency_key or f"budget_state:{state.budget_key}",
            state,
        )

    def append_directive(
        self, directive: ControlDirective, *, idempotency_key: str | None = None
    ) -> AppendResult:
        return self.append_with_conflict_check(
            "control_directive",
            idempotency_key or f"control_directive:{directive.directive_id}",
            directive,
        )

    def snapshot_bytes(self) -> bytes:
        return self._events_path.read_bytes() if self._events_path.is_file() else b""

    def restore_bytes(self, data: bytes) -> None:
        if data:
            self._events_path.write_bytes(data)
        else:
            self._events_path.unlink(missing_ok=True)
        self._rebuild()

    def verify(self) -> None:
        self._rebuild()

    def state_bytes(self) -> bytes:
        return canonical_bytes(self._state_payload())

    def event_count(self) -> int:
        return self._sequence
