"""Local HTTP Command Center over the isolated proving stores."""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from .briefings import generate_briefing
from .directives import (
    DirectiveConflictError,
    DirectiveError,
    DirectiveService,
)
from .languages import is_supported_lang, localize
from .proving import ProvingLayout
from .seeding import seed_proving_stores
from .state import build_r10_snapshot, evidence_for_record, record_lookup

STATIC_DIR = Path(__file__).parent / "static"


def _safe(value: Any, fallback: str = "UNKNOWN") -> str:
    return escape(str(value if value not in (None, "") else fallback))


def _section(
    lang: str,
    title_key: str,
    entries: list[dict[str, Any]],
    *,
    render_entry: Any,
    empty_hint: str,
) -> str:
    title = localize(lang, title_key)
    if not entries:
        body = f'<p class="empty-hint" data-empty="{escape(empty_hint)}">{escape(empty_hint)}</p>'
    else:
        body = "\n".join(render_entry(lang, entry) for entry in entries)
    return (
        f'<section class="panel" id="{title_key}" aria-labelledby="{title_key}-title">'
        f'<header class="panel-head"><h2 id="{title_key}-title">{title}</h2>'
        f'<span class="count">{len(entries):02d}</span></header>'
        f'<div class="record-list">{body}</div></section>'
    )


def _record_card(lang: str, entry: dict[str, Any], title: str, detail: str) -> str:
    record_id = escape(entry.get("record_id", ""))
    hash_value = escape(entry.get("json_hash", ""))
    return (
        f'<article class="record">'
        f'<div class="record-copy"><h3>{title}</h3><p>{detail}</p>'
        f'<code class="hash">{hash_value[:16]}...</code></div>'
        f'<button type="button" class="icon-button record-open" '
        f'data-record="{record_id}" aria-label="{escape(localize(lang, "evidence_drilldown"))}: {escape(title)}">'
        f'<svg aria-hidden="true"><use href="#i-eye"/></svg></button>'
        f"</article>"
    )


def _render_projects(lang: str, entry: dict[str, Any]) -> str:
    return _record_card(
        lang,
        entry,
        escape(str(entry["payload"].get("title", entry["record_id"]))),
        escape(
            f"{entry['payload'].get('status', '')} / {entry['payload'].get('canonical_target', '')}"
        ),
    )


def _render_missions(lang: str, entry: dict[str, Any]) -> str:
    return _record_card(
        lang,
        entry,
        escape(str(entry["payload"].get("mission_id", entry["record_id"]))),
        escape(
            f"{entry['payload'].get('kind', '')} / {entry['payload'].get('status', '')} / "
            f"{entry['payload'].get('worker_id', '')}"
        ),
    )


def _render_decisions(lang: str, entry: dict[str, Any]) -> str:
    payload = entry["payload"]
    selected = payload.get("selected_candidate_id") or "none"
    return _record_card(
        lang,
        entry,
        escape(str(payload.get("decision_id", entry["record_id"]))),
        escape(f"selected {selected} / {payload.get('status', '')} / {payload.get('reason', '')}"),
    )


def _render_simple(lang: str, entry: dict[str, Any]) -> str:
    payload = entry.get("payload", entry)
    title = str(payload.get("title", entry["record_id"]))
    detail = str(
        payload.get("detail")
        or payload.get("status")
        or payload.get("finding")
        or payload.get("reason")
        or payload.get("objective")
        or ""
    )
    return _record_card(lang, entry, escape(title), escape(detail))


def _render_timeline(lang: str, events: list[dict[str, Any]]) -> str:
    del lang
    rows: list[str] = []
    for event in reversed(events[-24:]):
        rows.append(
            "<li>"
            f"<time>{escape(str(event.get('recorded_at', '')))}</time>"
            f"<strong>{escape(str(event.get('event_type', '')))}</strong>"
            f"<p>{escape(str(event.get('summary', '')))}</p>"
            f'<code class="hash">{escape(str(event.get("json_hash", "")))[:16]}...</code>'
            "</li>"
        )
    return '<ol class="timeline">' + "".join(rows) + "</ol>"


def _render_briefing(lang: str, briefing: dict[str, Any]) -> str:
    rows: list[str] = []
    for category in briefing.get("categories", []):
        rows.append(
            '<div class="briefing-item">'
            f'<span class="briefing-category">{escape(str(category.get("category", "")))}</span>'
            f"<p>{escape(str(category.get('text', '')))}</p>"
            "</div>"
        )
    return (
        f'<div class="briefing-content"><p class="briefing-title">{escape(briefing.get("title", ""))}</p>'
        f'<code class="hash">content {escape(str(briefing.get("content_hash", "")))[:16]}...</code>'
        + "".join(rows)
        + "</div>"
    )


def _banners(lang: str, snapshot: dict[str, Any]) -> str:
    quality = snapshot.get("data_quality", {})
    banners: list[str] = []
    if quality.get("empty"):
        banners.append(
            f'<div class="banner banner-empty" role="status">{escape(localize(lang, "empty_state"))}</div>'
        )
    if quality.get("stale"):
        banners.append(
            f'<div class="banner banner-stale" role="alert">{escape(localize(lang, "stale_state"))}</div>'
        )
    if quality.get("degraded"):
        banners.append(
            f'<div class="banner banner-degraded" role="alert">{escape(localize(lang, "degraded_state"))}</div>'
        )
    if quality.get("provider_failure"):
        banners.append(
            '<div class="banner banner-provider" role="alert">'
            f"{escape(localize(lang, 'provider_failure'))}</div>"
        )
    return '<div class="banners">' + "".join(banners) + "</div>"


def _quality_pill(quality: dict[str, Any]) -> str:
    detail = quality.get("detail", {})
    if not isinstance(detail, dict):
        return "quality unknown"
    return (
        f"provider={detail.get('unavailable_capabilities', 0)} "
        f"incidents={detail.get('incidents', 0)} "
        f"waiting={detail.get('blocked_or_waiting', 0)}"
    )


def _directive_control(lang: str, snapshot: dict[str, Any]) -> str:
    control = snapshot.get("control", {})
    commands = (
        ("STOP", "directive_stop", "stop"),
        ("VETO", "directive_veto", "veto"),
        ("PAUSE", "directive_pause", "pause"),
        ("RESUME", "directive_resume", "resume"),
        ("REPRIORITIZE", "directive_reprioritize", "reprioritize"),
        ("REDIRECT", "directive_redirect", "redirect"),
    )
    buttons = "".join(
        f'<button type="button" class="cmd {css}" data-cmd="{command}">'
        f"{escape(localize(lang, label))}</button>"
        for command, label, css in commands
    )
    state_rows = (
        f"<li><span>{escape(localize(lang, 'status'))}</span>"
        f"<strong>stop={bool(control.get('stop_active'))} "
        f"paused={','.join(control.get('paused_projects', []) or ['-'])} "
        f"vetoed={len(control.get('vetoed_candidates', []))}</strong></li>"
        f'<li><span>control hash</span><code class="hash">'
        f"{escape(str(control.get('control_hash', '')))[:24]}...</code></li>"
    )
    return (
        '<section class="panel control-panel" id="directives" aria-labelledby="directive-title">'
        f'<header class="panel-head"><h2 id="directive-title">'
        f"{escape(localize(lang, 'control_title'))}</h2></header>"
        f'<p class="control-note">{escape(localize(lang, "control_note"))}</p>'
        f'<div class="directive-grid" aria-label="directive commands">{buttons}</div>'
        '<form id="directive-form" class="directive-form">'
        f'<label>{escape(localize(lang, "target"))} <input name="target" autocomplete="off"></label>'
        f'<label>{escape(localize(lang, "reason"))} <input name="reason" required autocomplete="off"></label>'
        f'<label>{escape(localize(lang, "value"))} <input name="value" autocomplete="off"></label>'
        '<input type="hidden" name="command_type" value="PAUSE">'
        '<input type="hidden" name="nonce">'
        f'<button type="submit" class="primary">{escape(localize(lang, "submit_directive"))}</button>'
        "</form>"
        '<output id="directive-result" aria-live="polite"></output>'
        f'<ul class="control-state">{state_rows}</ul>'
        "</section>"
    )


def render_index(
    app: CommandCenterApp,
    snapshot: dict[str, Any],
    lang: str,
    *,
    audit: bool = False,
) -> str:
    briefing = generate_briefing(snapshot, lang)
    control = snapshot.get("control", {})
    health = snapshot.get("health", {})
    voice = snapshot.get("voice", {})
    quality = snapshot.get("data_quality", {})
    state_json = json.dumps(snapshot, ensure_ascii=False, sort_keys=True)
    sections = "".join(
        [
            _section(
                lang,
                "section_goals",
                snapshot.get("goals", []),
                render_entry=_render_simple,
                empty_hint="No goals",
            ),
            _section(
                lang,
                "section_projects",
                snapshot.get("projects", []),
                render_entry=_render_projects,
                empty_hint="No projects",
            ),
            _section(
                lang,
                "section_missions",
                snapshot.get("missions", []),
                render_entry=_render_missions,
                empty_hint="No missions",
            ),
            _section(
                lang,
                "section_decisions",
                snapshot.get("candidate_decisions", []),
                render_entry=_render_decisions,
                empty_hint="No decisions",
            ),
            _section(
                lang,
                "section_rejected",
                snapshot.get("rejected_alternatives", []),
                render_entry=_render_simple,
                empty_hint="No rejected alternatives",
            ),
            _section(
                lang,
                "section_workers",
                snapshot.get("workers", []),
                render_entry=_render_simple,
                empty_hint="No worker records",
            ),
            _section(
                lang,
                "section_verification",
                snapshot.get("verification", []),
                render_entry=_render_simple,
                empty_hint="No verification records",
            ),
            _section(
                lang,
                "section_findings",
                snapshot.get("findings", []),
                render_entry=_render_simple,
                empty_hint="No findings",
            ),
            _section(
                lang,
                "section_releases",
                snapshot.get("releases", []),
                render_entry=_render_simple,
                empty_hint="No releases",
            ),
            _section(
                lang,
                "section_capabilities",
                snapshot.get("capabilities", []),
                render_entry=_render_simple,
                empty_hint="No capabilities",
            ),
            _section(
                lang,
                "section_costs",
                snapshot.get("costs", {}).get("measurements", []),
                render_entry=_render_simple,
                empty_hint="No cost measurements",
            ),
            _section(
                lang,
                "section_resources",
                snapshot.get("resources", []),
                render_entry=_render_simple,
                empty_hint="No resources",
            ),
            _section(
                lang,
                "section_incidents",
                snapshot.get("incidents", []),
                render_entry=_render_simple,
                empty_hint="No incidents",
            ),
            _section(
                lang,
                "section_waiting",
                snapshot.get("waiting_blockers", []),
                render_entry=_render_simple,
                empty_hint="No waiting or blockers",
            ),
            _section(
                lang,
                "section_next",
                snapshot.get("next_actions", []),
                render_entry=_render_simple,
                empty_hint="No next actions",
            ),
        ]
    )
    timeline = _render_timeline(lang, snapshot.get("timeline", []))
    briefing_html = _render_briefing(lang, briefing)
    audit_html = (
        '<script id="r10-audit">document.addEventListener("DOMContentLoaded", function () {'
        " var r = runAccessibilityAudit();"
        ' var pre = document.createElement("pre"); pre.id = "r10-a11y-audit";'
        " pre.textContent = JSON.stringify(r, null, 2);"
        " document.body.appendChild(pre);"
        " });</script>"
        if audit
        else ""
    )
    html = f"""<!doctype html>
<html lang="{escape(lang)}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{escape(localize(lang, "app_title"))}</title>
<link rel="stylesheet" href="/assets/styles.css">
</head>
<body>
<a class="skip-link" href="#main">{escape(localize(lang, "nav_overview"))}</a>
<svg class="icon-sprite" aria-hidden="true" xmlns="http://www.w3.org/2000/svg">
<symbol id="i-eye" viewBox="0 0 24 24"><path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7-10-7-10-7Z"/><circle cx="12" cy="12" r="3"/></symbol>
<symbol id="i-refresh" viewBox="0 0 24 24"><path d="M21 12a9 9 0 1 1-2.6-6.3M21 3v6h-6"/></symbol>
<symbol id="i-close" viewBox="0 0 24 24"><path d="M6 6l12 12M18 6L6 18"/></symbol>
<symbol id="i-check" viewBox="0 0 24 24"><path d="M20 6 9 17l-5-5"/></symbol>
</svg>
<header class="topbar" role="banner">
  <div class="brand">
    <div class="brand-mark" aria-hidden="true"></div>
    <div><h1>{escape(localize(lang, "app_title"))}</h1><p>{escape(localize(lang, "app_subtitle"))}</p></div>
  </div>
  <div class="top-actions">
    <div class="lang-switch" role="group" aria-label="{escape(localize(lang, "language"))}">
      <a href="/?lang=en" class="{"active" if lang == "en" else ""}">EN</a>
      <a href="/?lang=sq" class="{"active" if lang == "sq" else ""}">SQ</a>
    </div>
    <a class="icon-button" href="/" aria-label="{escape(localize(lang, "refresh"))}">
      <svg aria-hidden="true"><use href="#i-refresh"/></svg>
    </a>
  </div>
</header>
{_banners(lang, snapshot)}
<div class="health-strip" aria-label="{escape(localize(lang, "section_health"))}">
  <span class="pill {"pill-stop" if control.get("stop_active") else "pill-ok"}">
    {escape(localize(lang, "directive_stop"))}: {"ACTIVE" if control.get("stop_active") else "CLEAR"}</span>
  <span class="pill {"pill-bad" if health.get("degraded") else "pill-ok"}">
    {escape(localize(lang, "degraded_state"))}: {bool(health.get("degraded"))}</span>
  <span class="pill">{escape(localize(lang, "live_workers"))}: {int(health.get("live_workers", 0))}</span>
  <span class="pill">{escape(localize(lang, "voice_status"))}: {escape(str(voice.get("classification", "NOT_TESTED")))}</span>
  <span class="pill">{escape(localize(lang, "data_quality"))}: {escape(_quality_pill(quality))}</span>
</div>
<div class="layout">
  <aside class="rail" aria-label="command center navigation">
    <nav aria-label="sections">
      <a href="#section_goals">{escape(localize(lang, "section_goals"))}</a>
      <a href="#section_missions">{escape(localize(lang, "section_missions"))}</a>
      <a href="#section_decisions">{escape(localize(lang, "section_decisions"))}</a>
      <a href="#section_evidence">{escape(localize(lang, "section_evidence"))}</a>
      <a href="#section_timeline">{escape(localize(lang, "section_timeline"))}</a>
    </nav>
    {_directive_control(lang, snapshot)}
    <section class="panel briefing-panel" aria-labelledby="briefing-title">
      <header class="panel-head"><h2 id="briefing-title">{escape(localize(lang, "briefing_title"))}</h2></header>
      <div class="briefing-toggle" role="group" aria-label="briefing language">
        <button type="button" data-lang="en">EN</button><button type="button" data-lang="sq">SQ</button>
      </div>
      <div id="briefing-output">{briefing_html}</div>
    </section>
    <p class="authority-note">{escape(localize(lang, "authority_note"))}</p>
  </aside>
  <main id="main" class="content" role="main">
    <section class="panel evidence-panel" id="section_evidence" aria-labelledby="section_evidence-title">
      <header class="panel-head"><h2 id="section_evidence-title">{escape(localize(lang, "section_evidence"))}</h2>
      <span class="count">{int(snapshot.get("evidence", {}).get("count", 0)):02d}</span></header>
      <p class="evidence-note">Click any record in the sections below to open its evidence JSON, hash, and provenance.</p>
      <div class="record-list">{"".join(_render_simple(lang, entry) for entry in snapshot.get("evidence", {}).get("records", []))}</div>
    </section>
    {sections}
    <section class="panel timeline-panel" id="section_timeline" aria-labelledby="section_timeline-title">
      <header class="panel-head"><h2 id="section_timeline-title">{escape(localize(lang, "section_timeline"))}</h2></header>
      {timeline}
    </section>
  </main>
</div>
<footer class="footer">
  <span>{escape(localize(lang, "generated_at"))}: {escape(str(snapshot.get("generated_at", "")))}</span>
  <span>{escape(localize(lang, "authority_note"))}: {escape(str(snapshot.get("source_of_truth", {}).get("kind", "")))}</span>
</footer>
<div id="evidence-modal" class="modal" hidden>
  <div class="modal-box" role="dialog" aria-modal="true" aria-labelledby="modal-title">
    <header><h2 id="modal-title">{escape(localize(lang, "evidence_drilldown"))}</h2>
      <button type="button" class="icon-button modal-close" aria-label="{escape(localize(lang, "close"))}">
        <svg aria-hidden="true"><use href="#i-close"/></svg></button></header>
    <pre id="modal-json"></pre>
  </div>
</div>
<script id="r10-state" type="application/json">{state_json}</script>
<script src="/assets/app.js"></script>
{audit_html}
</body>
</html>"""
    return html


class CommandCenterApp:
    """Stateless route logic over one proving layout."""

    def __init__(
        self,
        layout: ProvingLayout,
        *,
        evidence_root: Path | None = None,
        seed: bool = True,
    ) -> None:
        self.layout = layout
        self.evidence_root = (
            evidence_root
            if evidence_root is not None
            else Path(__file__).resolve().parents[3] / "docs" / "remaster" / "evidence"
        )
        if seed:
            seed_proving_stores(layout, self.evidence_root)
        self.directives = DirectiveService(_portfolio_store(layout), _directive_ledger(layout))

    def snapshot(self, *, now: str | None = None) -> dict[str, Any]:
        return build_r10_snapshot(self.layout, now=now, directive_service=self.directives)

    def briefing(self, lang: str) -> dict[str, Any]:
        return generate_briefing(self.snapshot(), lang)

    def evidence(self, record_id: str) -> dict[str, Any] | None:
        entry = record_lookup(self.snapshot(), record_id)
        if entry is None:
            return None
        return evidence_for_record(entry)

    def apply_directive(self, payload: dict[str, Any]) -> dict[str, Any]:
        receipt = self.directives.apply(
            command_type=str(payload.get("command_type", "")),
            target=payload.get("target"),
            reason=str(payload.get("reason", "")),
            nonce=str(payload.get("nonce", "")),
            value=str(payload.get("value", "")),
        )
        return {
            "status": "APPLIED" if receipt.created else "IDEMPOTENT",
            "directive_id": receipt.directive_id,
            "event_id": receipt.event_id,
            "command_type": receipt.command_type,
            "sequence": receipt.sequence,
            "control": self.directives.state_dict(),
        }


def _portfolio_store(layout: ProvingLayout) -> Any:
    from jarvis_foundry.kernel.portfolio.store import PortfolioStore

    return PortfolioStore(layout.portfolio)


def _directive_ledger(layout: ProvingLayout) -> Any:
    from .ledger import DirectiveLedger

    return DirectiveLedger(layout.directive_ledger)


class _Handler(BaseHTTPRequestHandler):
    app: CommandCenterApp

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        route = parsed.path
        query = parse_qs(parsed.query)
        lang = query.get("lang", ["en"])[0]
        if not is_supported_lang(lang):
            self._json_response({"status": "MALFORMED", "reason": "unsupported language"}, 400)
            return
        if route == "/assets/styles.css" or route == "/assets/app.js":
            asset = STATIC_DIR / Path(route).name
            if not asset.is_file():
                self.send_error(404)
                return
            body = asset.read_bytes()
            content_type = (
                "text/css; charset=utf-8" if route.endswith(".css") else "application/javascript"
            )
            self._bytes_response(body, content_type)
            return
        if route == "/api/health":
            snapshot = self.app.snapshot()
            self._json_response(
                {
                    "status": "OK",
                    "generated_at": snapshot["generated_at"],
                    "degraded": snapshot["health"]["degraded"],
                    "stop_active": snapshot["control"]["stop_active"],
                },
                200,
            )
            return
        if route == "/api/state":
            self._json_response(self.app.snapshot(), 200)
            return
        if route == "/api/timeline":
            self._json_response(self.app.snapshot()["timeline"], 200)
            return
        if route == "/api/briefings":
            self._json_response(self.app.briefing(lang), 200)
            return
        if route.startswith("/api/evidence/"):
            record_id = unquote(route[len("/api/evidence/") :])
            result = self.app.evidence(record_id)
            if result is None:
                self._json_response({"status": "NOT_FOUND", "record_id": record_id}, 404)
                return
            self._json_response(result, 200)
            return
        if route != "/":
            self.send_error(404)
            return
        audit = query.get("audit", ["0"])[0] == "1"
        snapshot = self.app.snapshot()
        body = render_index(self.app, snapshot, lang, audit=audit).encode("utf-8")
        self._bytes_response(body, "text/html; charset=utf-8")

    def do_POST(self) -> None:
        if self.path.split("?", 1)[0] != "/api/directives":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 64 * 1024:
                raise ValueError("bounded JSON body required")
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            if not isinstance(payload, dict):
                raise TypeError("JSON object required")
            result = self.app.apply_directive(payload)
            self._json_response(result, 200)
        except DirectiveConflictError as error:
            self._json_response({"status": "REJECTED", "reason": str(error)}, 409)
        except DirectiveError as error:
            self._json_response({"status": "MALFORMED", "reason": str(error)}, 400)
        except (ValueError, TypeError, json.JSONDecodeError) as error:
            self._json_response({"status": "MALFORMED", "reason": str(error)}, 400)

    def _json_response(self, value: dict[str, Any], status: int) -> None:
        body = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self._bytes_response(body, "application/json; charset=utf-8", status=status)

    def _bytes_response(self, body: bytes, content_type: str, *, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_args: Any) -> None:
        return


@dataclass
class RunningServer:
    app: CommandCenterApp
    httpd: ThreadingHTTPServer
    thread: threading.Thread

    @property
    def address(self) -> tuple[str, int]:
        return str(self.httpd.server_address[0]), int(self.httpd.server_address[1])

    @property
    def base_url(self) -> str:
        return f"http://{self.address[0]}:{self.address[1]}"

    def close(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=10)


def serve(
    layout: ProvingLayout,
    *,
    evidence_root: Path | None = None,
    host: str = "127.0.0.1",
    port: int = 0,
    seed: bool = True,
) -> RunningServer:
    app = CommandCenterApp(layout, evidence_root=evidence_root, seed=seed)
    handler = type("BoundR10Handler", (_Handler,), {"app": app})
    httpd = ThreadingHTTPServer((host, port), handler)
    thread = threading.Thread(target=httpd.serve_forever, name="r10-command-center", daemon=True)
    thread.start()
    return RunningServer(app=app, httpd=httpd, thread=thread)
