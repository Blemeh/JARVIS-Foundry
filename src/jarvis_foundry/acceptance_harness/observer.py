"""Read-only observation of the canonical JARVIS ledger for acceptance runs."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..command_center import build_snapshot
from ..connectors.account_activation import activation_records
from ..database import FoundryDatabase
from ..events import Event
from ..reporting.briefing import briefing_count, latest_briefing
from ..resilience.core import rebuild_incidents


def _payload(event: Event) -> dict[str, Any]:
    return dict(event.payload)


def _rows(database: FoundryDatabase, event_types: set[str] | None = None,
          prefix: str | None = None) -> list[dict[str, Any]]:
    rows = []
    for sequence, event in enumerate(database.events(
        event_types=tuple(sorted(event_types)) if event_types else None,
        event_type_prefixes=(prefix,) if prefix else None,
    ), start=1):
        rows.append({"sequence": sequence, "event_type": event.event_type,
                     "event_id": event.event_id, "payload": _payload(event)})
    return rows


def _counts(database: FoundryDatabase, types: tuple[str, ...]) -> dict[str, int]:
    return {event_type: len(database.events(event_types=(event_type,))) for event_type in types}


@dataclass(frozen=True, slots=True)
class CanonicalObservation:
    """JSON-safe snapshot containing no fixture state or credential material."""

    observed_at: str
    source: str
    fixture_leakage: bool
    supervisor: dict[str, Any]
    stop: dict[str, Any]
    release: dict[str, Any]
    portfolio: dict[str, Any]
    procedures: dict[str, Any]
    accounts: list[dict[str, Any]]
    incidents: list[dict[str, Any]]
    effects: dict[str, Any]
    v6: dict[str, Any]
    briefings: dict[str, Any]
    command_center: dict[str, Any]
    manual_continuation_required: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "observed_at": self.observed_at,
            "source": self.source,
            "fixture_leakage": self.fixture_leakage,
            "supervisor": self.supervisor,
            "stop": self.stop,
            "release": self.release,
            "portfolio": self.portfolio,
            "procedures": self.procedures,
            "accounts": self.accounts,
            "incidents": self.incidents,
            "effects": self.effects,
            "v6": self.v6,
            "briefings": self.briefings,
            "command_center": self.command_center,
            "manual_continuation_required": self.manual_continuation_required,
        }


def _read_pointer(root: Path, name: str) -> str | None:
    path = root / name
    if not path.exists():
        return None
    value = path.read_text(encoding="utf-8").strip()
    return value or None


def _release_observation(release_root: str | Path | None) -> dict[str, Any]:
    if release_root is None:
        return {"source": "not_supplied", "current": None, "previous": None}
    root = Path(release_root).expanduser().resolve()
    result: dict[str, Any] = {
        "source": "immutable_release_pointers",
        "root": str(root),
        "current": _read_pointer(root, "CURRENT"),
        "previous": _read_pointer(root, "PREVIOUS"),
    }
    manifests: dict[str, Any] = {}
    for label in ("current", "previous"):
        sha = result.get(label)
        if not sha:
            continue
        path = root / "releases" / str(sha) / "RELEASE.json"
        if not path.exists():
            path = root / str(sha) / "RELEASE.json"
        if path.exists():
            try:
                manifests[label] = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                manifests[label] = {"status": "UNREADABLE"}
    result["manifests"] = manifests
    return result


def observe_canonical(
    database: FoundryDatabase,
    *,
    stop_path: str | Path | None = None,
    release_root: str | Path | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    """Observe canonical state using read-only projections only.

    The disposable simulator is intentionally not imported.  This function
    therefore cannot turn fixture counters into live proof.
    """
    events = list(database.events())
    with database.connect(readonly=True) as connection:
        row = connection.execute("SELECT * FROM supervisor_state WHERE singleton = 1").fetchone()
        supervisor = dict(row) if row else {"status": "UNKNOWN", "singleton": 1}
    stop_file = Path(stop_path) if stop_path else None
    stop = {
        "active": bool(stop_file and stop_file.exists()),
        "path": str(stop_file) if stop_file else None,
        "source": "sentinel" if stop_file else "not_supplied",
    }
    portfolio_events = [
        {"sequence": sequence, "event_type": e.event_type, "payload": _payload(e)}
        for sequence, e in enumerate(events, start=1) if e.event_type.startswith("portfolio.")
    ]
    procedure_events = [
        {"sequence": sequence, "event_type": e.event_type, "payload": _payload(e)}
        for sequence, e in enumerate(events, start=1)
        if e.event_type.startswith(("operation.", "procedure.library."))
    ]
    waits = [row for row in portfolio_events
             if row["event_type"] == "portfolio.selection_recorded"
             and row["payload"].get("decision") == "WAIT"]
    selections = [row for row in portfolio_events
                  if row["event_type"] == "portfolio.selection_recorded"
                  and row["payload"].get("decision") == "SELECT"]
    outcomes = [row for row in portfolio_events if row["event_type"] == "portfolio.outcome_recorded"]
    terminal = [row for row in procedure_events if row["event_type"] == "operation.procedure.terminal"]
    effects = {
        "started": len([e for e in events if e.event_type == "system.action_started"]),
        "completed": len([e for e in events if e.event_type == "system.action_completed"]),
        "failed": len([e for e in events if e.event_type == "system.action_failed"]),
        "frozen": len([e for e in events if e.event_type == "effect.frozen"]),
        "unknown": len([e for e in events if e.event_type in {"effect.reconcile_pending", "effect.unknown"}]),
    }
    incident_records = [item.to_payload() for item in rebuild_incidents(database)]
    v6_events = [
        {"sequence": sequence, "event_type": e.event_type, "payload": _payload(e)}
        for sequence, e in enumerate(events, start=1)
        if e.event_type.startswith(("evolution.", "discovery."))
    ]
    continuation_rows = [e for e in events if e.event_type == "manual_continuation_required" or
                         bool(_payload(e).get("manual_continuation_required"))]
    try:
        cc = build_snapshot(database, stop_path=stop_path, now=now)
    except Exception as error:  # read model must remain inspectable if optional tables drift
        cc = {"status": "NOT_TESTED", "error": type(error).__name__}
    try:
        latest = latest_briefing(database)
    except Exception:
        latest = {"status": "NOT_AVAILABLE", "source": "ledger_projection"}
    observation = CanonicalObservation(
        observed_at=now or "ledger-observed",
        source="canonical_append_only_ledger",
        fixture_leakage=False,
        supervisor=supervisor,
        stop=stop,
        release=_release_observation(release_root),
        portfolio={
            "selections": selections,
            "waits": waits,
            "outcomes": outcomes,
            "selection_count": len(selections),
            "wait_count": len(waits),
            "outcome_count": len(outcomes),
        },
        procedures={
            "events": procedure_events,
            "terminal": terminal,
            "event_count": len(procedure_events),
            "terminal_count": len(terminal),
        },
        accounts=[
            {
                key: value for key, value in record.to_dict().items()
                if key not in {"credential_reference", "last_probe", "last_reconcile", "last_write_eligibility"}
            }
            for record in activation_records(database)
        ],
        incidents=incident_records,
        effects=effects,
        v6={"events": v6_events, "event_count": len(v6_events), "source": "ledger_only"},
        briefings={
            "count": briefing_count(database),
            "latest": latest,
            "source": "ledger_projection",
        },
        command_center=cc,
        manual_continuation_required=len(continuation_rows),
    )
    return observation.to_dict()


__all__ = ["CanonicalObservation", "observe_canonical"]
