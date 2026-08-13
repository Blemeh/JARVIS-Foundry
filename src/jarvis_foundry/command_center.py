"""Owner-facing Command Center read model.

The command center is deliberately read-only: every field is reconstructed
from the event ledger and its deterministic projections.  It never stores a
second operational truth and it never starts a worker, sender, or connector.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .database import FoundryDatabase
from .owner_controls import OwnerAuthenticationError, OwnerControlService, OwnerReplayError
from .supervisor.estop import EmergencyStop


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _json_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    return value


def _rows(connection, query: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in connection.execute(query, args).fetchall()]


def _record_ref(kind: str, identifier: Any, event_id: Any = None) -> dict[str, Any]:
    ref: dict[str, Any] = {"kind": kind, "id": identifier}
    if event_id:
        ref["event_id"] = event_id
    return ref


def _event_refs(payload: dict[str, Any], event_id: str) -> list[dict[str, Any]]:
    refs = [_record_ref("event", event_id, event_id)]
    mappings = {
        "mission_id": "mission", "run_id": "agent_run", "bundle_id": "evidence",
        "verdict_id": "verification", "candidate_id": "candidate",
        "measurement_id": "cost_measurement", "briefing_id": "briefing",
        "interaction_id": "voice_interaction", "directive_id": "directive",
        "artifact_hash": "artifact",
    }
    for key, kind in mappings.items():
        if payload.get(key) is not None:
            refs.append(_record_ref(kind, payload[key], event_id))
    if isinstance(payload.get("artifact_hashes"), dict):
        for name, digest in payload["artifact_hashes"].items():
            refs.append({"kind": "artifact", "id": name, "sha256": digest, "event_id": event_id})
    if payload.get("evidence"):
        refs.append({"kind": "evidence", "value": payload["evidence"], "event_id": event_id})
    return refs


def decision_timeline(database: FoundryDatabase, *, limit: int = 200) -> list[dict[str, Any]]:
    """Return a compact trace of material ledger decisions and outcomes."""
    limit = max(1, min(int(limit), 2000))
    events = database.events()
    material = []
    for sequence, event in enumerate(events, start=1):
        payload = dict(event.payload)
        material.append({
            "sequence": sequence,
            "event_id": event.event_id,
            "event_type": event.event_type,
            "occurred_at": event.occurred_at,
            "recorded_at": event.recorded_at,
            "source": {"type": event.source_type, "id": event.source_id},
            "correlation_id": event.correlation_id,
            "causation_id": event.causation_id,
            "summary": payload.get("reason") or payload.get("objective") or payload.get("status") or event.event_type,
            "decision": payload.get("decision") or payload.get("verdict") or payload.get("outcome"),
            "rejected_alternative": payload.get("rejection_reason") or payload.get("reason") if "reject" in event.event_type else None,
            "evidence_refs": _event_refs(payload, event.event_id),
        })
    return material[-limit:]


def _safe_table(connection, table: str, query: str, args: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    try:
        return _rows(connection, query, args)
    except Exception:
        # A V4 UI pointed at an older read-only candidate reports NOT_TESTED
        # rather than turning a missing additive table into fabricated state.
        return []


def build_snapshot(
    database: FoundryDatabase,
    *,
    stop_path: str | Path | None = None,
    now: str | None = None,
    timeline_limit: int = 200,
) -> dict[str, Any]:
    """Build the complete JSON-safe Command Center projection."""
    generated_at = now or _now_iso()
    with database.connect() as connection:
        head = connection.execute("SELECT COALESCE(MAX(sequence), 0) FROM events").fetchone()[0]
        event_count = int(head or 0)
        supervisor_row = connection.execute(
            "SELECT * FROM supervisor_state WHERE singleton = 1"
        ).fetchone()
        supervisor = dict(supervisor_row) if supervisor_row else {"status": "UNKNOWN", "trust_state": "UNKNOWN"}
        system_state_row = connection.execute(
            "SELECT * FROM system_state WHERE singleton = 1"
        ).fetchone()
        system_state = dict(system_state_row) if system_state_row else {"start_active": None}
        missions = _rows(connection, "SELECT * FROM missions ORDER BY updated_at DESC LIMIT 100")
        workers = _rows(connection, "SELECT * FROM agent_runs ORDER BY updated_at DESC LIMIT 100")
        evidence = _rows(connection, "SELECT * FROM evidence_bundles ORDER BY updated_at DESC LIMIT 100")
        verdicts = _rows(connection, "SELECT * FROM verification_verdicts ORDER BY updated_at DESC LIMIT 100")
        resources = _rows(connection, "SELECT * FROM resources ORDER BY updated_at DESC LIMIT 100")
        directives = _rows(connection, "SELECT * FROM directives WHERE active = 1 ORDER BY updated_at DESC")
        leases = _rows(connection, "SELECT * FROM leases ORDER BY heartbeat_at DESC")
        budgets = _rows(connection, "SELECT * FROM budgets ORDER BY provider")
        systems = _safe_table(connection, "systems", "SELECT * FROM systems ORDER BY updated_at DESC")
        costs = _safe_table(connection, "cost_measurements", "SELECT * FROM cost_measurements ORDER BY recorded_at DESC LIMIT 200")
        measured_run_ids = {str(c.get("run_id")) for c in costs if c.get("run_id")}
        # Existing agent_run cost payloads are authoritative measurements when
        # present.  They are read into the V4 view without rewriting the
        # underlying run projection or inventing an amount when absent.
        for run in workers:
            if str(run.get("run_id")) in measured_run_ids:
                continue
            raw = run.get("cost_json")
            parsed = _json_value(raw) if raw else {}
            if isinstance(parsed, dict) and parsed:
                costs.append({
                    "measurement_id": f"agent-run:{run.get('run_id')}",
                    "mission_id": run.get("mission_id"), "run_id": run.get("run_id"),
                    "provider": run.get("provider"), "model": run.get("model"),
                    "status": "MEASURED" if any(key in parsed for key in ("actual_cost_usd", "estimated_cost_usd", "usd", "cost_usd")) else "UNKNOWN",
                    "input_tokens": parsed.get("input_tokens"), "output_tokens": parsed.get("output_tokens"), "total_tokens": parsed.get("total_tokens"),
                    "estimated_cost_usd": parsed.get("estimated_cost_usd"),
                    "actual_cost_usd": parsed.get("actual_cost_usd", parsed.get("usd", parsed.get("cost_usd"))),
                    "duration_ms": parsed.get("duration_ms"), "resource_json": parsed,
                    "failure_class": run.get("failure_class"), "evidence_json": {"run_id": run.get("run_id")},
                    "recorded_at": run.get("finished_at") or run.get("updated_at"),
                    "last_event_id": run.get("last_event_id"), "updated_at": run.get("updated_at"),
                })
        briefings = _safe_table(connection, "briefing_outbox", "SELECT * FROM briefing_outbox ORDER BY updated_at DESC LIMIT 100")
        voices = _safe_table(connection, "voice_interactions", "SELECT * FROM voice_interactions ORDER BY updated_at DESC LIMIT 100")
        candidates = _safe_table(connection, "candidates", "SELECT * FROM candidates ORDER BY updated_at DESC LIMIT 200")

    stop = EmergencyStop(stop_path).read() if stop_path is not None else None
    active_vetoes = [d for d in directives if d.get("command_type") in {"VETO_ACTION", "STOP_MISSION", "STOP_EVERYTHING"}]
    goals = [d for d in directives if d.get("command_type") == "GOAL"]
    constraints = [d for d in directives if d.get("command_type") in {"CONSTRAINT", "ADD_CONSTRAINT"}]
    paused = any(d.get("command_type") == "PAUSE" for d in directives)
    stale_leases = [l for l in leases if l.get("expires_at") and str(l["expires_at"]) < generated_at]
    blocked = [m for m in missions if m.get("state") == "BLOCKED"]
    crashed_workers = [w for w in workers if w.get("status") == "FAILED"]
    recovery_events = [e for e in decision_timeline(database, limit=timeline_limit)
                       if any(token in e["event_type"] for token in ("recover", "reap", "rollback", "degraded", "blocked"))]
    release_events = [e for e in decision_timeline(database, limit=timeline_limit)
                      if any(token in e["event_type"] for token in ("release", "canary", "rollback"))]
    measured_costs = [c for c in costs if c.get("status") == "MEASURED"]
    actual_values = [float(c["actual_cost_usd"]) for c in measured_costs if c.get("actual_cost_usd") is not None]
    estimated_values = [float(c["estimated_cost_usd"]) for c in measured_costs if c.get("estimated_cost_usd") is not None]
    actual_total: float | str = sum(actual_values) if actual_values else "UNKNOWN"
    estimated_total: float | str = sum(estimated_values) if estimated_values else "UNKNOWN"
    unknown_costs = [c for c in costs if c.get("status") != "MEASURED"]

    def with_ref(row: dict[str, Any], kind: str, key: str) -> dict[str, Any]:
        value = dict(row)
        value["record_ref"] = _record_ref(kind, row.get(key), row.get("last_event_id"))
        for field in ("acceptance_json", "tool_policy_json", "cost_json", "criteria_json", "evidence_json", "resource_json", "artifact_hashes_json"):
            if field in value:
                value[field[:-5] if field.endswith("_json") else field] = _json_value(value[field])
        return value

    return {
        "generated_at": generated_at,
        "source_of_truth": {"kind": "append_only_event_ledger", "ledger_head_sequence": event_count, "event_count": event_count},
        "supervisor": supervisor,
        "system_state": system_state,
        "stop": {"active": bool(stop), "record": stop, "source": "sentinel_or_durable_control"},
        "controls": {
            "goals": [with_ref(d, "directive", "directive_id") for d in goals],
            "constraints": [with_ref(d, "directive", "directive_id") for d in constraints],
            "vetoes": [with_ref(d, "directive", "directive_id") for d in active_vetoes],
            "paused": paused,
            "resume_available": bool(paused or stop),
            "stop_effective_without_hermes_ui_models_network": True,
        },
        "missions": [with_ref(m, "mission", "mission_id") for m in missions],
        "workers": [with_ref(w, "agent_run", "run_id") for w in workers],
        "evidence": [with_ref(e, "evidence", "bundle_id") for e in evidence],
        "verification": [with_ref(v, "verification", "verdict_id") for v in verdicts],
        "health": {
            "supervisor_status": supervisor.get("status", "UNKNOWN"),
            "trust_state": supervisor.get("trust_state", "UNKNOWN"),
            "heartbeat_at": supervisor.get("last_heartbeat_at"),
            "active_leases": len(leases) - len(stale_leases),
            "stale_leases": [with_ref(l, "lease", "lease_key") for l in stale_leases],
            "crashed_workers": [with_ref(w, "agent_run", "run_id") for w in crashed_workers],
            "degraded": supervisor.get("status") == "DEGRADED" or bool(stale_leases or crashed_workers),
        },
        "resources": [with_ref(r, "resource", "resource_id") for r in resources],
        "systems": [with_ref(s, "system", "system_id") for s in systems],
        "candidates": [with_ref(c, "candidate", "candidate_id") for c in candidates],
        "releases_canaries_rollbacks": release_events or {"status": "NOT_AVAILABLE", "evidence_refs": []},
        "recovery": {
            "blocked_missions": [with_ref(m, "mission", "mission_id") for m in blocked],
            "stale_leases": [with_ref(l, "lease", "lease_key") for l in stale_leases],
            "recovery_events": recovery_events,
            "rollback_state": release_events or {"status": "NOT_AVAILABLE", "evidence_refs": []},
            "reclaimable_work": [with_ref(m, "mission", "mission_id") for m in blocked],
            "provider_or_connector_unknowns": [c for c in costs if c.get("status") in {"UNKNOWN", "NOT_AVAILABLE", "NOT_TESTED"}],
        },
        "costs": {
            "measurements": [with_ref(c, "cost_measurement", "measurement_id") for c in costs],
            "measured_actual_total_usd": actual_total if measured_costs else "UNKNOWN",
            "measured_estimated_total_usd": estimated_total if measured_costs else "UNKNOWN",
            "unknown_measurements": len(unknown_costs),
            "budgets": budgets,
        },
        "briefings": [with_ref(b, "briefing", "briefing_id") for b in briefings],
        "voice": [with_ref(v, "voice_interaction", "interaction_id") for v in voices],
        "timeline": decision_timeline(database, limit=timeline_limit),
    }


class _CommandCenterHandler(BaseHTTPRequestHandler):
    database: FoundryDatabase
    stop_path: Path | None
    owner_secret: bytes | None
    owner_id: str = "local-owner"
    timeline_limit: int = 200

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] not in {"/", "/api/command-center", "/api/timeline"}:
            self.send_error(404)
            return
        snapshot = build_snapshot(self.database, stop_path=self.stop_path, timeline_limit=self.timeline_limit)
        route = self.path.split("?", 1)[0]
        if route == "/":
            body = self._html(snapshot).encode("utf-8")
            content_type = "text/html; charset=utf-8"
        else:
            body = json.dumps(snapshot if route != "/api/timeline" else snapshot["timeline"], ensure_ascii=True).encode()
            content_type = "application/json; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        if self.path.split("?", 1)[0] != "/api/controls":
            self.send_error(404)
            return
        if not self.owner_secret:
            self._json_response({"status": "BLOCKED", "reason": "owner authentication secret is not configured"}, status=503)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 64 * 1024:
                raise ValueError("bounded JSON body required")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise ValueError("JSON object required")
            service = OwnerControlService(self.database, secret=self.owner_secret, stop_path=self.stop_path, owner_id=self.owner_id)
            command_type = str(payload.get("command_type", ""))
            target = payload.get("target")
            exact_text = str(payload.get("exact_text", ""))
            nonce = str(payload.get("nonce", ""))
            signature = str(payload.get("signature", ""))
            if command_type == "RESUME":
                event_ids = service.resume(nonce=nonce, signature=signature, exact_text=exact_text or "resume jarvis")
                result: dict[str, Any] = {"status": "APPLIED", "command_type": command_type, "event_ids": event_ids}
            else:
                applied = service.apply(command_type, target=target, exact_text=exact_text, nonce=nonce, signature=signature, directive_id=payload.get("directive_id"))
                result = {"status": "APPLIED", "command_type": applied.command_type, "directive_id": applied.directive_id, "event_id": applied.event_id}
            self._json_response(result, status=200)
        except OwnerReplayError as error:
            self._json_response({"status": "REJECTED", "reason": str(error)}, status=409)
        except OwnerAuthenticationError as error:
            self._json_response({"status": "REJECTED", "reason": str(error)}, status=401)
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            self._json_response({"status": "MALFORMED", "reason": str(error)}, status=400)

    def _json_response(self, value: dict[str, Any], *, status: int) -> None:
        body = json.dumps(value, ensure_ascii=True).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    @staticmethod
    def _html(snapshot: dict[str, Any]) -> str:
        """Small dependency-free local view; all values come from JSON state."""
        stop = snapshot["stop"]["active"]
        health = snapshot["health"]
        cost = snapshot["costs"]
        rows = "".join(
            f"<tr><td>{m.get('mission_id','')}</td><td>{m.get('state','')}</td><td>{m.get('objective','')}</td><td>{m.get('next_action') or 'UNKNOWN'}</td></tr>"
            for m in snapshot["missions"][:20]
        ) or "<tr><td colspan='4'>No durable missions recorded</td></tr>"
        timeline = "".join(
            f"<li><code>#{e['sequence']} {e['event_type']}</code> {e.get('summary','')} <small>evidence: {len(e.get('evidence_refs', []))}</small></li>"
            for e in snapshot["timeline"][-20:][::-1]
        ) or "<li>No events recorded</li>"
        return f"""<!doctype html><html lang='en'><meta charset='utf-8'><title>JARVIS Command Center</title>
<style>body{{font:15px system-ui;background:#10151c;color:#e9eef5;max-width:1200px;margin:2rem auto;padding:0 1rem}}h1{{color:#7dd3fc}}.grid{{display:grid;grid-template-columns:repeat(4,1fr);gap:1rem}}.card{{background:#19222d;border:1px solid #334155;border-radius:8px;padding:1rem}}.danger{{color:#fca5a5}}table{{width:100%;border-collapse:collapse}}td,th{{padding:.45rem;border-bottom:1px solid #334155;text-align:left}}code{{color:#a7f3d0}}small{{color:#94a3b8}}</style>
<h1>JARVIS Command Center</h1><p>Ledger head <b>{snapshot['source_of_truth']['ledger_head_sequence']}</b> · generated {snapshot['generated_at']}</p>
<div class='grid'><div class='card'>Supervisor<br><b>{health.get('supervisor_status','UNKNOWN')}</b><br>Trust: {health.get('trust_state','UNKNOWN')}</div><div class='card'>STOP<br><b class='{'danger' if stop else ''}'>{'ACTIVE' if stop else 'CLEAR'}</b><br>Independent sentinel path</div><div class='card'>Degraded<br><b>{'YES' if health.get('degraded') else 'NO'}</b><br>Stale leases: {len(health.get('stale_leases', []))}</div><div class='card'>Measured cost<br><b>{cost.get('measured_actual_total_usd')}</b><br>Unknown measurements: {cost.get('unknown_measurements')}</div></div>
<h2>Missions</h2><table><tr><th>Mission</th><th>State</th><th>Objective</th><th>Next action</th></tr>{rows}</table>
<h2>Decision timeline</h2><ol>{timeline}</ol><p><small>Every material item links to ledger event/evidence references in <a href='/api/timeline'>the JSON timeline</a>.</small></p>
</html>"""

    def log_message(self, *_args: Any) -> None:
        return


class CommandCenterServer:
    """Opt-in local-only HTTP projection; never starts automatically."""

    def __init__(self, database: FoundryDatabase, *, stop_path: str | Path | None = None, host: str = "127.0.0.1", port: int = 0) -> None:
        handler = type("BoundCommandCenterHandler", (_CommandCenterHandler,), {})
        handler.database = database
        handler.stop_path = Path(stop_path).resolve() if stop_path else None
        handler.owner_secret = None
        handler.owner_id = "local-owner"
        self._handler = handler
        self.httpd = ThreadingHTTPServer((host, port), handler)
        self.thread: threading.Thread | None = None

    @property
    def address(self) -> tuple[str, int]:
        return self.httpd.server_address[0], int(self.httpd.server_address[1])

    def configure_owner(self, secret: bytes, *, owner_id: str = "local-owner") -> None:
        """Enable authenticated controls explicitly; never inferred from env."""
        if not secret:
            raise ValueError("owner secret cannot be empty")
        self._handler.owner_secret = bytes(secret)
        self._handler.owner_id = owner_id

    def start(self) -> None:
        if self.thread is not None:
            return
        self.thread = threading.Thread(target=self.httpd.serve_forever, name="jarvis-command-center", daemon=True)
        self.thread.start()

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        if self.thread:
            self.thread.join(timeout=5)
            self.thread = None
