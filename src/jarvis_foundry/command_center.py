"""Owner-facing Command Center read model.

The command center is deliberately read-only: every field is reconstructed
from the event ledger and its deterministic projections.  It never stores a
second operational truth and it never starts a worker, sender, or connector.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime, timezone
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote

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
        memories = _safe_table(
            connection, "tier_c_memories",
            "SELECT memory_id, state, mem_type, scope, mission_id, updated_at, last_event_id "
            "FROM tier_c_memories ORDER BY updated_at DESC LIMIT 100")
        procedures = _safe_table(
            connection, "tier_c_procedures",
            "SELECT procedure_id, state, scope, updated_at, last_event_id "
            "FROM tier_c_procedures ORDER BY updated_at DESC LIMIT 100")
        steps = _safe_table(
            connection, "mission_step_contracts",
            "SELECT contract_id, mission_id, step_id, version, capability, "
            "last_event_id, updated_at FROM mission_step_contracts "
            "ORDER BY updated_at DESC LIMIT 100")

    stop = EmergencyStop(stop_path).read() if stop_path is not None else None
    try:
        from .deployment.release import read_current, read_previous
        release_identity = {
            "current": read_current(),
            "previous": read_previous(),
            "source": "immutable_release_pointers",
        }
    except Exception as error:  # noqa: BLE001 - CC read model must not fail closed on layout
        release_identity = {
            "current": None, "previous": None,
            "status": "NOT_AVAILABLE", "reason": type(error).__name__,
        }
    active_vetoes = [d for d in directives if d.get("command_type") in {"VETO_ACTION", "STOP_MISSION", "STOP_EVERYTHING"}]
    goals = [d for d in directives if d.get("command_type") == "GOAL"]
    constraints = [d for d in directives if d.get("command_type") in {"CONSTRAINT", "ADD_CONSTRAINT"}]
    reprioritizations = [d for d in directives if d.get("command_type") == "REPRIORITIZE"]
    redirects = [d for d in directives if d.get("command_type") == "REDIRECT"]
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
            "reprioritizations": [with_ref(d, "directive", "directive_id") for d in reprioritizations],
            "redirects": [with_ref(d, "directive", "directive_id") for d in redirects],
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
        "mission_steps": [with_ref(s, "mission_step", "contract_id") for s in steps],
        "memory": [with_ref(m, "memory", "memory_id") for m in memories],
        "procedures": [with_ref(p, "procedure", "procedure_id") for p in procedures],
        "release_identity": release_identity,
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
        route = self.path.split("?", 1)[0]
        if route == "/assets/Tektur.ttf":
            body = (Path(__file__).with_name("assets") / "Tektur.ttf").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "font/ttf")
            self.send_header("Cache-Control", "public, max-age=86400")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if route.startswith("/api/record/"):
            parts = route.split("/")
            if len(parts) != 5 or parts[2] != "record" or parts[3] not in {"mission", "event"} or not parts[4]:
                self.send_error(404)
                return
            snapshot = build_snapshot(self.database, stop_path=self.stop_path, timeline_limit=self.timeline_limit)
            record_kind, record_id = parts[3], unquote(parts[4])
            if record_kind == "mission":
                record = next((item for item in snapshot["missions"] if str(item.get("mission_id")) == record_id), None)
            else:
                record = next((item for item in snapshot["timeline"] if str(item.get("event_id")) == record_id), None)
            if record is None:
                self._json_response({"status": "NOT_FOUND", "kind": record_kind, "id": record_id}, status=404)
                return
            self._json_response({"status": "FOUND", "kind": record_kind, "record": record}, status=200)
            return
        if route not in {"/", "/api/command-center", "/api/timeline"}:
            self.send_error(404)
            return
        snapshot = build_snapshot(self.database, stop_path=self.stop_path, timeline_limit=self.timeline_limit)
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
        """Dependency-free telemetry observatory; all values come from durable state."""
        stop = snapshot["stop"]["active"]
        health = snapshot["health"]
        cost = snapshot["costs"]
        controls = snapshot["controls"]
        missions = snapshot["missions"][:20]
        timeline_events = snapshot["timeline"][-16:][::-1]
        unknown = "UNKNOWN"

        def safe(value: Any, fallback: str = unknown) -> str:
            return escape(str(value if value not in (None, "") else fallback))

        def tone(value: Any) -> str:
            normalized = str(value or unknown).upper()
            if normalized in {"PASS", "PASSED", "RUNNING", "READY", "CLEAR", "COMPLETED", "MEASURED", "EXECUTING"}:
                return "nominal"
            if normalized in {"BLOCKED", "FAILED", "FAIL", "ACTIVE", "STOPPED", "FROZEN"}:
                return "critical"
            if normalized in {"VERIFYING", "UNKNOWN", "NOT_TESTED", "NOT_AVAILABLE", "DEGRADED"}:
                return "caution"
            return "neutral"

        mission_cards = "".join(
            f"""<article class='mission-thread {tone(m.get('state'))}'>
              <div class='mission-thread-head'><a class='record-link' href='/api/record/mission/{quote(str(m.get("mission_id", "")), safe="")}' aria-label='Open durable mission record for {safe(m.get("mission_id"))}'>{safe(m.get("mission_id"))}</a><span class='status {tone(m.get("state"))}'><i></i>{safe(m.get("state"))}</span></div>
              <h3>{safe(m.get("objective"))}</h3>
              <div class='mission-thread-foot'><span>next vector</span><strong>{safe(m.get("next_action"))}</strong></div>
              <div class='mission-progress' aria-label='Mission progress measurement unavailable'><span>progress UNKNOWN / state-derived signal only</span></div>
            </article>"""
            for m in missions
        ) or "<article class='mission-thread empty'>No durable missions recorded. JARVIS is honestly waiting.</article>"

        timeline = "".join(
            f"""<li class='timeline-event {tone(e.get("summary"))}'>
              <div class='timeline-node' aria-hidden='true'></div>
              <div class='event-head'><a href='/api/record/event/{quote(str(e.get("event_id", "")), safe="")}' aria-label='Open durable event record {safe(e.get("event_id"))}'>EVT-{int(e["sequence"]):04d}</a><time>{safe(str(e.get("recorded_at", ""))[11:19], "--:--:--")}</time></div>
              <strong>{safe(e.get("event_type"))}</strong>
              <p>{safe(e.get("summary"), "No summary recorded")}</p>
              <span class='evidence-count'>{len(e.get("evidence_refs", []))} linked record{'s' if len(e.get("evidence_refs", [])) != 1 else ''}</span>
            </li>"""
            for e in timeline_events
        ) or "<li class='empty'>No ledger events recorded.</li>"

        goal = controls["goals"][0].get("exact_text") if controls["goals"] else "No active owner goal"
        constraint_count = len(controls["constraints"])
        active_workers = sum(1 for worker in snapshot["workers"] if worker.get("status") == "RUNNING")
        verified = sum(1 for verdict in snapshot["verification"] if verdict.get("status") == "PASS")
        blocked = len(snapshot["recovery"]["blocked_missions"])
        briefing_status = snapshot["briefings"][0].get("status") if snapshot["briefings"] else "NOT_AVAILABLE"
        voice_status = snapshot["voice"][0].get("status") if snapshot["voice"] else "NOT_TESTED"
        cost_value = cost.get("measured_actual_total_usd", unknown)
        cost_display = f"${float(cost_value):.2f}" if isinstance(cost_value, (float, int)) else safe(cost_value)
        stop_label = "ACTIVE" if stop else "CLEAR"
        degraded_label = "DEGRADED" if health.get("degraded") else "NOMINAL"
        generated = safe(snapshot["generated_at"])

        return f"""<!doctype html>
<html lang='en'>
<head>
<meta charset='utf-8'>
<meta name='viewport' content='width=device-width,initial-scale=1'>
<meta name='color-scheme' content='dark'>
<title>JARVIS Command Center</title>
<style>
@font-face{{font-family:"Tektur";src:url('/assets/Tektur.ttf') format('truetype');font-weight:400 900;font-display:swap}}
:root{{
  --void:#02040a;--panel:#07101c;--panel-raised:#0b1928;--line:#1a3751;--line-hot:#2a6382;
  --text:#effcff;--muted:#9fc5d7;--dim:#7093a7;--signal:#8affef;--cyan:#66d9ff;
  --violet:#9b8cff;--amber:#ffd071;--red:#ff7189;--focus:0 0 0 3px rgba(102,217,255,.34)
}}
*{{box-sizing:border-box}}
html{{background:var(--void);scroll-behavior:smooth}}
body{{margin:0;min-width:320px;background:var(--void);color:var(--text);font:500 14px/1.5 "Segoe UI Variable Text","Segoe UI",system-ui,sans-serif}}
body::before{{content:"";position:fixed;inset:0;pointer-events:none;background:radial-gradient(ellipse at 50% 9%,rgba(79,167,255,.20),transparent 30%),radial-gradient(ellipse at 88% 70%,rgba(115,73,255,.10),transparent 34%),linear-gradient(180deg,rgba(255,255,255,.025),transparent 22%);z-index:-1}}
::selection{{background:var(--signal);color:#03221c}}
a{{color:inherit;text-decoration-color:var(--line-hot);text-underline-offset:4px}}
a:hover{{color:var(--signal)}}
:focus-visible{{outline:none;box-shadow:var(--focus);border-radius:4px}}
.shell{{display:grid;grid-template-columns:232px minmax(0,1fr);min-height:100vh}}
.rail{{position:sticky;top:0;height:100vh;padding:24px 18px;border-right:1px solid var(--line);background:linear-gradient(180deg,#030710,#061323 58%,#050913);display:flex;flex-direction:column}}
.brand{{display:flex;align-items:center;gap:12px;padding:4px 6px 28px}}
.brand-mark{{width:34px;height:34px;position:relative;display:grid;place-items:center;border:1px solid var(--signal);clip-path:polygon(20% 0,80% 0,100% 20%,100% 80%,80% 100%,20% 100%,0 80%,0 20%);box-shadow:0 0 30px rgba(138,255,239,.18)}}
.brand-mark::before{{content:"";width:9px;height:9px;border:2px solid var(--signal);border-radius:50%;box-shadow:0 0 18px rgba(138,255,239,.65)}}
.brand strong,.identity h1,.command-copy h2,.goal-panel blockquote,.panel-head h2{{font-family:"Tektur","Segoe UI",sans-serif}}.brand strong{{display:block;font-size:15px;letter-spacing:.08em}}.brand small{{color:var(--muted);font-size:10px;letter-spacing:.16em}}
nav{{display:grid;gap:5px}}nav a{{display:flex;align-items:center;gap:11px;padding:10px 11px;color:var(--muted);text-decoration:none;border-radius:8px;border:1px solid transparent}}
nav a::before{{content:"";width:6px;height:6px;border:1px solid currentColor;transform:rotate(45deg)}}nav a.active{{color:var(--text);background:var(--panel);border-color:var(--line)}}nav a.active::before{{background:var(--signal);border-color:var(--signal)}}
.rail-foot{{margin-top:auto;padding:14px 10px 4px;border-top:1px solid var(--line);color:var(--muted);font-size:11px}}.rail-foot b{{display:block;color:var(--signal);margin-bottom:3px}}
.main{{min-width:0}}
.topbar{{min-height:72px;display:flex;align-items:center;justify-content:space-between;gap:20px;padding:14px 30px;border-bottom:1px solid var(--line);background:rgba(2,4,10,.72);position:sticky;top:0;z-index:10;backdrop-filter:blur(18px)}}
.identity h1{{font-size:17px;margin:0;letter-spacing:.06em}}.identity p{{margin:2px 0 0;color:var(--muted);font-size:11px}}
.top-signals{{display:flex;align-items:center;gap:10px;flex-wrap:wrap;justify-content:flex-end}}
.status{{display:inline-flex;align-items:center;gap:7px;white-space:nowrap;font:700 10px/1.2 ui-monospace,"Cascadia Mono",monospace;letter-spacing:.07em;text-transform:uppercase}}
.status i{{width:6px;height:6px;border-radius:50%;background:var(--dim)}}.status.nominal{{color:var(--signal)}}.status.nominal i{{background:var(--signal);box-shadow:0 0 9px rgba(100,244,210,.6)}}.status.caution{{color:var(--amber)}}.status.caution i{{background:var(--amber)}}.status.critical{{color:var(--red)}}.status.critical i{{background:var(--red)}}.status.neutral{{color:var(--muted)}}
.stop-state{{border:1px solid var(--line);padding:8px 12px;border-radius:8px;background:var(--panel)}}.stop-state.critical{{border-color:rgba(255,107,107,.55)}}
.content{{padding:28px 30px 48px;max-width:1680px;margin:0 auto}}
.downlink{{position:relative;border:1px solid var(--line-hot);background:linear-gradient(145deg,rgba(12,30,50,.95),rgba(3,9,18,.98) 75%);clip-path:polygon(0 18px,18px 0,calc(100% - 18px) 0,100% 18px,100% calc(100% - 18px),calc(100% - 18px) 100%,18px 100%,0 calc(100% - 18px));box-shadow:0 28px 80px rgba(0,0,0,.46),0 0 90px rgba(66,145,255,.08)}}
.downlink::before,.downlink::after{{content:"";position:absolute;z-index:2;pointer-events:none;background:var(--signal)}}.downlink::before{{height:1px;width:82px;top:0;left:28px}}.downlink::after{{width:1px;height:82px;top:28px;left:0}}
.calibration{{height:26px;border-bottom:1px solid var(--line);background:repeating-linear-gradient(90deg,transparent 0 23px,var(--line-hot) 23px 24px);position:relative}}.calibration::after{{content:"JARVIS / OWNER DOWNLINK / LEDGER PROJECTION";position:absolute;left:26px;top:6px;background:var(--panel);padding:0 10px;color:var(--dim);font:700 9px ui-monospace,monospace;letter-spacing:.12em}}
.mission-control{{display:grid;grid-template-columns:minmax(0,1.45fr) minmax(300px,.55fr);border-bottom:1px solid var(--line)}}
.command-field{{min-height:300px;border-right:1px solid var(--line);background:linear-gradient(145deg,rgba(12,29,48,.85),rgba(5,14,27,.5) 72%);padding:34px 34px 28px;position:relative;overflow:hidden}}
.command-field::after{{content:"";position:absolute;right:-28px;top:-112px;width:380px;height:380px;border:1px solid rgba(102,217,255,.16);border-radius:50%;box-shadow:0 0 0 18px rgba(102,217,255,.035),0 0 0 78px rgba(102,217,255,.028),0 0 0 142px rgba(155,140,255,.022)}}
.command-copy{{position:relative;z-index:1;max-width:760px}}.command-copy h2{{font-size:43px;line-height:1.02;letter-spacing:-.035em;margin:10px 0 14px;max-width:16ch;text-shadow:0 0 30px rgba(102,217,255,.16)}}.command-copy>p{{color:var(--muted);max-width:58ch;margin:0 0 24px}}
.signal-label{{font:700 10px/1.2 ui-monospace,"Cascadia Mono",monospace;letter-spacing:.14em;color:var(--signal);text-transform:uppercase}}
.directive-box{{display:grid;grid-template-columns:auto 1fr auto;align-items:center;gap:12px;max-width:760px;background:#050b10;border:1px solid var(--line);border-radius:10px;padding:11px 13px}}
.directive-box svg{{width:17px;height:17px;stroke:var(--cyan)}}.directive-box input{{min-width:0;border:0;background:transparent;color:var(--muted);font:inherit;outline:0}}.directive-box button{{border:1px solid var(--line);background:var(--panel-raised);color:var(--dim);border-radius:7px;padding:8px 12px;font:700 10px ui-monospace,monospace;letter-spacing:.07em}}.directive-box button:disabled{{cursor:not-allowed}}
.preview-note{{display:flex;align-items:center;gap:8px;margin-top:10px;color:var(--amber);font-size:11px}}
.goal-panel{{padding:28px 24px;display:flex;flex-direction:column;justify-content:space-between;background:linear-gradient(180deg,rgba(11,26,46,.62),rgba(5,12,23,.34))}}
.goal-panel blockquote{{font-size:22px;line-height:1.25;margin:14px 0 22px;color:var(--text)}}.goal-meta{{display:grid;grid-template-columns:1fr 1fr;gap:12px;padding-top:16px;border-top:1px solid var(--line)}}.datum span{{display:block;color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.1em}}.datum b{{display:block;margin-top:4px;font:700 15px ui-monospace,monospace}}
  .core-orbit{{width:172px;height:172px;margin:2px auto 12px;position:relative;display:grid;place-items:center;border:1px solid rgba(102,217,255,.32);border-radius:50%;box-shadow:0 0 42px rgba(102,217,255,.11),inset 0 0 36px rgba(102,217,255,.08);animation:core-pulse 5.6s ease-in-out 2}}.core-orbit::before,.core-orbit::after{{content:"";position:absolute;border:1px solid rgba(155,140,255,.28);border-radius:50%;transform:rotate(34deg)}}.core-orbit::before{{inset:20px -14px;animation:orbit-drift 7s ease-in-out 2}}.core-orbit::after{{inset:-14px 20px;transform:rotate(-34deg);animation:orbit-drift-reverse 7s ease-in-out 2}}.core-orbit .core{{width:66px;height:66px;border:1px solid var(--signal);border-radius:50%;display:grid;place-items:center;background:radial-gradient(circle,rgba(138,255,239,.22),rgba(9,23,39,.88) 64%);box-shadow:0 0 24px rgba(138,255,239,.32),inset 0 0 16px rgba(138,255,239,.3);font:700 9px ui-monospace,monospace;letter-spacing:.1em;color:var(--signal);z-index:1;animation:core-glow 2.8s ease-in-out 4}}.core-orbit .orbit-dot{{position:absolute;width:7px;height:7px;border-radius:50%;background:var(--cyan);box-shadow:0 0 12px var(--cyan);top:10px;left:83px}}.core-orbit .orbit-dot.two{{top:auto;left:auto;right:17px;bottom:41px;background:var(--violet);box-shadow:0 0 12px var(--violet)}}
.telemetry{{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));border-bottom:1px solid var(--line);background:rgba(5,15,28,.82)}}
.telemetry article{{padding:17px 19px;min-width:0}}.telemetry article+article{{border-left:1px solid var(--line)}}.telemetry span{{display:block;color:var(--muted);font-size:10px;text-transform:uppercase;letter-spacing:.11em}}.telemetry strong{{display:block;font:650 24px/1.1 ui-monospace,"Cascadia Mono",monospace;margin:7px 0 4px;overflow-wrap:anywhere}}.telemetry small{{color:var(--dim);font-size:10px}}
.operations{{display:grid;grid-template-columns:minmax(0,1.6fr) minmax(340px,.65fr);align-items:start}}
.panel{{background:rgba(5,14,26,.82);overflow:hidden}}.operations>.panel:first-child{{border-right:1px solid var(--line)}}
.panel-head{{display:flex;justify-content:space-between;align-items:center;gap:14px;padding:18px 20px;border-bottom:1px solid var(--line)}}.panel-head h2{{font-size:14px;letter-spacing:.06em;margin:0}}.panel-head p{{margin:2px 0 0;color:var(--muted);font-size:11px}}.panel-head a{{font:700 10px ui-monospace,monospace;color:var(--cyan)}}
 .mission-list{{display:grid;gap:1px}}.mission-thread{{position:relative;padding:18px 20px 16px;border-bottom:1px solid var(--line);background:linear-gradient(90deg,rgba(102,217,255,.025),transparent 72%);overflow:hidden}}.mission-thread::before{{content:"";position:absolute;left:0;top:0;bottom:0;width:1px;background:var(--cyan);opacity:.55}}.mission-thread.critical::before{{background:var(--red)}}.mission-thread.caution::before{{background:var(--amber)}}.mission-thread-head,.mission-thread-foot{{display:flex;align-items:center;justify-content:space-between;gap:16px}}.mission-thread h3{{font-family:"Tektur","Segoe UI",sans-serif;font-size:17px;line-height:1.18;font-weight:600;margin:10px 0 14px;max-width:62ch}}.mission-thread-foot{{justify-content:flex-start;color:var(--dim);font:700 9px ui-monospace,monospace;letter-spacing:.08em;text-transform:uppercase}}.mission-thread-foot strong{{color:var(--muted);font-size:10px;letter-spacing:0;text-transform:none;font-weight:500}}.mission-progress{{margin-top:14px;color:var(--dim);font:700 9px ui-monospace,monospace;letter-spacing:.08em;text-transform:uppercase}}.mission-progress span{{display:block}}
.timeline-panel{{max-height:720px;overflow:auto}}.timeline-list{{list-style:none;margin:0;padding:7px 20px 20px 46px}}.timeline-event{{position:relative;padding:14px 0 16px;border-bottom:1px solid var(--line)}}.timeline-event:last-child{{border-bottom:0}}.timeline-event::before{{content:"";position:absolute;left:-24px;top:0;bottom:0;width:1px;background:var(--line)}}.timeline-node{{position:absolute;left:-28px;top:20px;width:9px;height:9px;border:2px solid var(--signal);background:var(--panel);transform:rotate(45deg)}}.timeline-event:first-child .timeline-node{{animation:signal-breathe 2.8s ease-in-out 2}}.timeline-event.caution .timeline-node{{border-color:var(--amber)}}.timeline-event.critical .timeline-node{{border-color:var(--red)}}.event-head{{display:flex;justify-content:space-between;gap:8px;font:700 9px ui-monospace,monospace;color:var(--dim)}}.event-head a{{color:var(--cyan);text-decoration:none}}.timeline-event strong{{display:block;font:650 11px ui-monospace,monospace;margin:6px 0 3px;color:var(--text)}}.timeline-event p{{margin:0;color:var(--muted);font-size:11px;line-height:1.45}}.evidence-count{{display:inline-block;margin-top:8px;color:var(--signal);font:700 9px ui-monospace,monospace;text-transform:uppercase;letter-spacing:.06em}}
.footer{{display:flex;justify-content:space-between;gap:20px;padding:15px 26px;color:var(--dim);font-size:10px;border-top:1px solid var(--line)}}.footer a{{color:var(--muted)}}
 @keyframes signal-breathe{{0%,100%{{box-shadow:0 0 0 rgba(138,255,239,0)}}50%{{box-shadow:0 0 12px rgba(138,255,239,.42)}}}}@keyframes core-pulse{{0%,100%{{box-shadow:0 0 42px rgba(102,217,255,.11),inset 0 0 36px rgba(102,217,255,.08)}}50%{{box-shadow:0 0 72px rgba(102,217,255,.24),inset 0 0 52px rgba(102,217,255,.14)}}}}@keyframes core-glow{{0%,100%{{transform:scale(1)}}50%{{transform:scale(1.035)}}}}@keyframes orbit-drift{{0%,100%{{transform:rotate(34deg) scale(1)}}50%{{transform:rotate(39deg) scale(1.035)}}}}@keyframes orbit-drift-reverse{{0%,100%{{transform:rotate(-34deg) scale(1)}}50%{{transform:rotate(-39deg) scale(1.035)}}}}
@media(max-width:1120px){{.shell{{grid-template-columns:78px minmax(0,1fr)}}.rail{{padding:22px 12px}}.brand-copy,nav span,.rail-foot span{{display:none}}.brand{{justify-content:center}}nav a{{justify-content:center;padding:12px}}.mission-control,.operations{{grid-template-columns:1fr}}.command-field,.operations>.panel:first-child{{border-right:0;border-bottom:1px solid var(--line)}}.telemetry{{grid-template-columns:repeat(3,1fr)}}.telemetry article:nth-child(4){{border-left:0;border-top:1px solid var(--line)}}.telemetry article:nth-child(5){{border-top:1px solid var(--line)}}}}
@media(max-width:700px){{.shell{{display:block}}.rail{{position:static;height:auto;padding:10px 14px;display:flex;flex-direction:row;align-items:center;border-right:0;border-bottom:1px solid var(--line)}}.brand{{padding:0;margin-right:auto}}.brand-copy{{display:block}}nav{{display:flex;overflow:auto}}nav a{{padding:9px}}nav a:not(.active){{display:none}}.rail-foot{{display:none}}.topbar{{padding:11px 14px;align-items:center}}.identity p{{display:none}}.top-signals{{gap:7px}}.top-signals>.status:not(.stop-state){{display:none}}.stop-state{{padding:7px 9px}}.content{{padding:14px 10px 28px}}.downlink{{clip-path:polygon(0 12px,12px 0,calc(100% - 12px) 0,100% 12px,100% calc(100% - 12px),calc(100% - 12px) 100%,12px 100%,0 calc(100% - 12px))}}.calibration::after{{left:12px;font-size:8px}}.mission-control{{display:block}}.goal-panel{{border-top:1px solid var(--line)}}.command-field{{min-height:0;padding:26px 18px 22px}}.command-copy h2{{font-size:32px}}.directive-box{{grid-template-columns:auto minmax(0,1fr);overflow:hidden}}.directive-box input{{text-overflow:ellipsis}}.directive-box button{{display:none}}.telemetry{{grid-template-columns:1fr 1fr}}.telemetry article:nth-child(n){{border-left:0;border-top:1px solid var(--line)}}.telemetry article:nth-child(odd){{border-right:1px solid var(--line)}}.telemetry article:first-child,.telemetry article:nth-child(2){{border-top:0}}.operations{{display:block}}.timeline-panel{{max-height:none}}.panel-head{{padding:15px 16px}}.mission-thread{{padding:17px 16px}}.mission-thread h3{{font-size:16px}}.footer{{display:block}}.footer span{{display:block;margin-bottom:7px}}}}
 @media(prefers-reduced-motion:reduce){{html{{scroll-behavior:auto}}.timeline-node,.core-orbit,.core-orbit::before,.core-orbit::after,.core-orbit .core{{animation:none}}.timeline-node{{box-shadow:0 0 0 2px rgba(138,255,239,.12)}}}}
</style>
</head>
<body>
<!--
THESIS: JARVIS is a living command core for an autonomous institution, not a dashboard waiting for clicks.
OWN-WORLD: Deep space-black, electric cyan and violet orbit light, cinematic radial depth, spatial telemetry planes, and ledger-linked signals.
STORY: The owner speaks an intent, the JARVIS core reveals the active system posture, and mission vectors radiate outward into evidence.
FIRST VIEWPORT: A cinematic command core anchors an Owner Link, current goal, orbit telemetry, mission vectors, and a scrollable evidence stream.
FORM: 2100 autonomous command deck, user-pinned Iron Man JARVIS direction; seed f99526de was overruled by the explicit brief. Signature motion is a living core pulse and orbit drift.
FINISH: unreviewed and undocumented is unfinished; this build ends with the finish review, the verdict, and DESIGN.md
-->
<div class='shell'>
  <aside class='rail'>
    <div class='brand'><div class='brand-mark' aria-hidden='true'></div><div class='brand-copy'><strong>JARVIS</strong><small>COMMAND CENTER</small></div></div>
    <nav aria-label='Primary navigation'>
      <a class='active' href='#overview'><span>Command</span></a>
      <a href='#missions'><span>Missions</span></a>
      <a href='#timeline'><span>Timeline</span></a>
      <a href='/api/command-center'><span>Evidence API</span></a>
    </nav>
    <div class='rail-foot'><b>LOCAL CANDIDATE</b><span>Durable projection only<br>No live connectors</span></div>
  </aside>
  <main class='main'>
    <header class='topbar'>
      <div class='identity'><h1>Command Center</h1><p>Append-only operational projection · generated {generated}</p></div>
      <div class='top-signals'>
        <span class='status {tone(health.get("supervisor_status"))}'><i></i>Supervisor {safe(health.get("supervisor_status"))}</span>
        <span class='stop-state status {tone(stop_label)}'><i></i>STOP {stop_label}</span>
      </div>
    </header>
    <div class='content' id='overview'>
      <div class='downlink'>
      <div class='calibration' aria-hidden='true'></div>
      <section class='mission-control' aria-labelledby='owner-link-title'>
        <div class='command-field'>
          <div class='command-copy'>
            <h2 id='owner-link-title'>What should JARVIS move next?</h2>
            <p>Goals, constraints, vetoes, pause, resume, and deterministic STOP enter one audited control path. Routine operation continues without becoming an approval queue.</p>
            <div class='directive-box' aria-label='Owner communication preview'>
              <svg viewBox='0 0 24 24' fill='none' aria-hidden='true'><path d='M12 3v18M8 7.5C8 5.6 9.8 4 12 4s4 1.6 4 3.5V12c0 2.2-1.8 4-4 4s-4-1.8-4-4V7.5Z' stroke-width='1.6'/><path d='M5 12a7 7 0 0 0 14 0M9 21h6' stroke-width='1.6'/></svg>
              <input value='Owner console preview — authentication is not configured' readonly aria-label='Owner directive preview status'>
              <button type='button' disabled>CHANNEL LOCKED</button>
            </div>
            <div class='preview-note'><span class='status caution'><i></i>{safe(voice_status)}</span> Real English STT/TTS remains explicitly gated.</div>
          </div>
        </div>
        <aside class='goal-panel' aria-label='Current owner intent'>
          <div><div class='core-orbit' aria-hidden='true'><span class='orbit-dot'></span><span class='orbit-dot two'></span><span class='core'>JARVIS</span></div><blockquote>{safe(goal)}</blockquote></div>
          <div class='goal-meta'><div class='datum'><span>Constraints</span><b>{constraint_count:02d}</b></div><div class='datum'><span>Trust state</span><b>{safe(health.get("trust_state"))}</b></div></div>
        </aside>
      </section>
      <section class='telemetry' aria-label='Operational cross-check'>
        <article><span>System posture</span><strong class='status {tone(degraded_label)}'><i></i>{degraded_label}</strong><small>{len(health.get("stale_leases", []))} stale leases</small></article>
        <article><span>Active workers</span><strong>{active_workers:02d}</strong><small>{len(snapshot["workers"])} durable run records</small></article>
        <article><span>Verified</span><strong>{verified:02d}</strong><small>independent verdicts passed</small></article>
        <article><span>Blocked work</span><strong>{blocked:02d}</strong><small>reclaimable mission records</small></article>
        <article><span>Measured cost</span><strong>{cost_display}</strong><small>{cost.get("unknown_measurements", 0)} unknown measurements</small></article>
      </section>
      <div class='operations'>
        <section class='panel' id='missions'>
          <header class='panel-head'><div><h2>Mission operations</h2><p>Current intent, execution state, and next action from durable records.</p></div><span class='status {tone(briefing_status)}'><i></i>Briefing {safe(briefing_status)}</span></header>
          <div class='mission-list'>{mission_cards}</div>
        </section>
        <aside class='panel timeline-panel' id='timeline'>
          <header class='panel-head'><div><h2>Decision signal</h2><p>Newest ledger events first.</p></div><a href='/api/timeline'>OPEN JSON</a></header>
          <ol class='timeline-list'>{timeline}</ol>
        </aside>
      </div>
      <footer class='footer'><span>Ledger head {snapshot['source_of_truth']['ledger_head_sequence']} · {snapshot['source_of_truth']['event_count']} append-only events</span><a href='/api/command-center'>Inspect complete durable projection</a></footer>
      </div>
    </div>
  </main>
</div>
</body>
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
