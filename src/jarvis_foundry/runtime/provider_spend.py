"""Authenticated, run-scoped spend gate for the OpenCode Go Muse Spark route.

Every Hermes model request must pass through this loopback gateway.  It reserves
that request's conservative upper bound in the append-only Foundry event ledger
before opening an upstream connection.  Successful responses settle against
provider token usage; ambiguous responses keep the request bound charged.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import re
import secrets
import threading
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

from ..clock import isoformat, utc_now
from ..database import FoundryDatabase
from ..events import Event
from ..owner_directives import active_owner_directives
from .provider_config import ProviderSettings


AUTHORIZED_PROVIDER = "opencode_go"
AUTHORIZED_MODEL = "muse-spark-1.3-contributor"
AUTHORIZED_API_MODE = "codex_responses"
AUTHORIZED_BASE_URL = "https://opencode.ai/zen/go/v1"
SPEND_SCOPE_ID = "jarvis-sustained-autonomy-2026-09-24-opencode-go-muse-spark-1.3-contributor"
SPEND_CEILING_USD = Decimal("1.00")
REQUEST_BOUND_RESERVATION_POLICY = "request_bound_v1"
MAX_OUTPUT_TOKENS = 4096
MAX_REQUEST_BYTES = 2 * 1024 * 1024
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
MAX_TOKEN_BOUND_MULTIPLIER = Decimal("2")
USD_PER_MILLION_TOKENS_UPPER_BOUND = Decimal("0.20")
METERING_MARGIN = Decimal("1.10")
_MICRO_USD = Decimal("0.000001")
_LOG = logging.getLogger(__name__)


class ProviderSpendBlocked(RuntimeError):
    """The owner policy or cumulative model-compute ceiling blocks a request."""


@dataclass(frozen=True, slots=True)
class SpendUsage:
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int
    conservative_cost_usd: Decimal


@dataclass(frozen=True, slots=True)
class UpstreamResponse:
    status: int
    headers: dict[str, str]
    body: bytes


def _money(value: Decimal) -> Decimal:
    return value.quantize(_MICRO_USD, rounding=ROUND_CEILING)


def _constraint_records(database: FoundryDatabase, key: str) -> list[Any]:
    return [
        record
        for record in active_owner_directives(database)
        if record.command_type in {"CONSTRAINT", "ADD_CONSTRAINT"}
        and record.metadata.get("constraint_key") == key
    ]


def _require_owner_compute_policy(database: FoundryDatabase) -> None:
    """Check the live authenticated owner authority on every request."""
    compute = _constraint_records(database, "provider_model_compute")
    compute_allow = [r for r in compute if str(r.metadata.get("mode", "")).upper() == "ALLOW"]
    if len(compute_allow) != 1 or any(
        str(r.metadata.get("mode", "")).upper() == "DENY" for r in compute
    ):
        raise ProviderSpendBlocked("authenticated owner model-compute authorization is absent or conflicting")
    policy = compute_allow[0]
    metadata = policy.metadata
    if (
        policy.authenticated is not True
        or policy.unknown_fields
        or policy.target not in {None, "", "GLOBAL"}
        or metadata.get("provider") != AUTHORIZED_PROVIDER
        or metadata.get("model") != AUTHORIZED_MODEL
        or metadata.get("spend_scope_id") != SPEND_SCOPE_ID
        or str(metadata.get("spend_ceiling_usd")) != "1.00"
        or metadata.get("fallbacks_allowed") is not False
        or metadata.get("use_credit_balance_fallback_allowed") is not False
        or metadata.get("fail_closed") is not True
    ):
        raise ProviderSpendBlocked("authenticated owner model-compute authorization does not match the exact bounded route")

    training = _constraint_records(database, "provider_training_data_use")
    training_allow = [r for r in training if str(r.metadata.get("mode", "")).upper() == "ALLOW"]
    if len(training_allow) != 1 or any(
        str(r.metadata.get("mode", "")).upper() == "DENY" for r in training
    ):
        raise ProviderSpendBlocked("authenticated owner Contributor training-data consent is absent or conflicting")
    consent = training_allow[0]
    if (
        consent.authenticated is not True
        or consent.unknown_fields
        or consent.target not in {None, "", "GLOBAL"}
        or consent.metadata.get("provider") != AUTHORIZED_PROVIDER
        or consent.metadata.get("model") != AUTHORIZED_MODEL
        or consent.metadata.get("prompts_and_completions_may_train") is not True
    ):
        raise ProviderSpendBlocked("authenticated owner training-data consent does not match the exact Contributor route")

    for key in ("external_financial_effects", "destructive_effects"):
        records = _constraint_records(database, key)
        deny = [r for r in records if str(r.metadata.get("mode", "")).upper() == "DENY"]
        global_deny = [
            r for r in deny if r.target in {None, "", "GLOBAL"}
        ]
        if (
            not global_deny
            or any(str(r.metadata.get("mode", "")).upper() == "ALLOW" for r in records)
            or any(
                r.authenticated is not True or r.unknown_fields
                for r in global_deny
            )
        ):
            raise ProviderSpendBlocked(f"authenticated owner DENY policy is absent or conflicting for {key}")

    # A surviving vague directive about paid/provider/computing use cannot be
    # safely merged with the precise policy above.
    for record in active_owner_directives(database):
        if record.command_type not in {"CONSTRAINT", "ADD_CONSTRAINT"}:
            continue
        if record.metadata.get("constraint_key"):
            continue
        text = (record.exact_text or "").casefold()
        if re.search(r"financial|destructive|provider.{0,20}compute|model.{0,20}spend|paid.{0,20}(?:model|api)", text):
            raise ProviderSpendBlocked("an active unclassified owner constraint may restrict model spending")


def _accounted_exposure(state: dict[str, Any]) -> Decimal:
    status = state["status"]
    if status == "settled":
        return state["cost_usd"]
    if status in {"held", "reserved"}:
        # Legacy reservations may name the entire remaining ceiling. Their
        # validated request bound is the maximum unsettled exposure, whether
        # the response is still in flight or its result is permanently unknown.
        # Original reservation and hold events remain untouched.
        return state["request_worst_case_usd"]
    raise ProviderSpendBlocked("provider-compute ledger has an invalid request state")


def _event_usage(connection, scope_id: str) -> dict[str, dict[str, Any]]:
    states: dict[str, dict[str, Any]] = {}
    rows = connection.execute(
        "SELECT event_type, payload_json FROM events "
        "WHERE event_type IN ('provider.compute_reserved', 'provider.compute_settled', 'provider.compute_held') "
        "ORDER BY sequence"
    ).fetchall()
    for row in rows:
        try:
            payload = json.loads(row["payload_json"])
        except (TypeError, ValueError) as error:
            raise ProviderSpendBlocked(
                "provider-compute ledger contains malformed event JSON"
            ) from error
        if not isinstance(payload, dict):
            raise ProviderSpendBlocked(
                "provider-compute ledger event payload is not an object"
            )
        event_scope = payload.get("spend_scope_id")
        if not isinstance(event_scope, str) or not event_scope:
            raise ProviderSpendBlocked(
                "provider-compute ledger event has no valid spend scope"
            )
        if event_scope != scope_id:
            continue
        if (
            payload.get("provider") != AUTHORIZED_PROVIDER
            or payload.get("model") != AUTHORIZED_MODEL
        ):
            raise ProviderSpendBlocked(
                "provider-compute ledger event does not match the authorized route"
            )
        request_id = payload.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            raise ProviderSpendBlocked(
                "provider-compute ledger event has no valid request identity"
            )
        try:
            uuid.UUID(request_id)
        except (ValueError, AttributeError) as error:
            raise ProviderSpendBlocked(
                "provider-compute ledger request identity is malformed"
            ) from error
        event_type = row["event_type"]
        if event_type == "provider.compute_reserved":
            if request_id in states:
                raise ProviderSpendBlocked(
                    "provider-compute ledger has a duplicate reservation"
                )
            try:
                reserved = Decimal(str(payload["reserved_usd"]))
                worst_case = Decimal(str(payload["request_worst_case_usd"]))
                ceiling = Decimal(str(payload["ceiling_usd"]))
            except (KeyError, ArithmeticError, ValueError) as error:
                raise ProviderSpendBlocked(
                    "provider-compute reservation has malformed accounting values"
                ) from error
            request_bytes = payload.get("request_bytes")
            max_output = payload.get("max_output_tokens")
            remaining = _money(
                SPEND_CEILING_USD
                - sum(_accounted_exposure(state) for state in states.values())
            )
            reservation_policy = payload.get("reservation_policy")
            if reservation_policy is None:
                # Preserve the original full-remaining reservation contract.
                reservation_matches = reserved == remaining
            elif reservation_policy == REQUEST_BOUND_RESERVATION_POLICY:
                reservation_matches = reserved == worst_case and worst_case <= remaining
            else:
                reservation_matches = False
            if (
                not re.fullmatch(r"[0-9a-f]{64}", str(payload.get("request_sha256", "")))
                or not isinstance(request_bytes, int)
                or isinstance(request_bytes, bool)
                or not 1 <= request_bytes <= MAX_REQUEST_BYTES
                or not isinstance(max_output, int)
                or isinstance(max_output, bool)
                or not 1 <= max_output <= MAX_OUTPUT_TOKENS
                or not reserved.is_finite()
                or not worst_case.is_finite()
                or not ceiling.is_finite()
                or not Decimal("0") < reserved <= SPEND_CEILING_USD
                or not Decimal("0") < worst_case <= reserved
                or worst_case != _worst_case_cost(request_bytes, max_output)
                or ceiling != SPEND_CEILING_USD
                or not reservation_matches
            ):
                raise ProviderSpendBlocked(
                    "provider-compute reservation does not match the bounded route"
                )
            states[request_id] = {
                "status": "reserved",
                "reserved_usd": reserved,
                "request_worst_case_usd": worst_case,
            }
        elif event_type == "provider.compute_held":
            state = states.get(request_id)
            if state is None or state.get("status") != "reserved":
                raise ProviderSpendBlocked(
                    "provider-compute hold has no active reservation"
                )
            try:
                held = Decimal(str(payload["reserved_usd"]))
            except (KeyError, ArithmeticError, ValueError) as error:
                raise ProviderSpendBlocked(
                    "provider-compute hold has malformed accounting values"
                ) from error
            if (
                not held.is_finite()
                or held != state["reserved_usd"]
                or not isinstance(payload.get("reason"), str)
                or not payload["reason"]
            ):
                raise ProviderSpendBlocked(
                    "provider-compute hold does not match its reservation"
                )
            state["status"] = "held"
        elif event_type == "provider.compute_settled":
            state = states.get(request_id)
            if state is None or state.get("status") != "reserved":
                raise ProviderSpendBlocked(
                    "provider-compute settlement has no active reservation"
                )
            try:
                cost = Decimal(str(payload["cost_usd"]))
                reserved = Decimal(str(payload["reserved_usd"]))
                total_tokens = payload["total_tokens"]
            except (KeyError, ArithmeticError, ValueError) as error:
                raise ProviderSpendBlocked(
                    "provider-compute settlement has malformed accounting values"
                ) from error
            input_tokens = payload.get("input_tokens")
            output_tokens = payload.get("output_tokens")
            if any(
                value is not None
                and (
                    not isinstance(value, int)
                    or isinstance(value, bool)
                    or value < 0
                )
                for value in (input_tokens, output_tokens)
            ) or (
                not isinstance(total_tokens, int)
                or isinstance(total_tokens, bool)
                or total_tokens < 1
            ):
                raise ProviderSpendBlocked(
                    "provider-compute settlement has invalid token usage"
                )
            minimum_total = (input_tokens or 0) + (output_tokens or 0)
            expected_cost = _money(
                Decimal(total_tokens)
                * USD_PER_MILLION_TOKENS_UPPER_BOUND
                / Decimal(1_000_000)
                * METERING_MARGIN
            )
            if (
                not cost.is_finite()
                or cost < 0
                or not reserved.is_finite()
                or reserved != state["reserved_usd"]
                or total_tokens < minimum_total
                or cost != expected_cost
                or not isinstance(payload.get("overrun"), bool)
                or payload.get("overrun") != (cost > reserved)
                or payload.get("price_basis_usd_per_million_tokens") != format(
                    USD_PER_MILLION_TOKENS_UPPER_BOUND, "f"
                )
                or payload.get("conservative_margin") != format(METERING_MARGIN, "f")
            ):
                raise ProviderSpendBlocked(
                    "provider-compute settlement does not match the provider usage"
                )
            state["cost_usd"] = cost
            state["status"] = "settled"
    return states


def _append_event(database: FoundryDatabase, connection, event_type: str, payload: dict[str, Any], key: str) -> None:
    event = Event.create(
        event_type,
        payload,
        source_type="provider_spend_gate",
        source_id=SPEND_SCOPE_ID,
        correlation_id=str(payload.get("request_id") or SPEND_SCOPE_ID),
        idempotency_key=key,
    )
    database.append_events_on_connection(connection, [event])


def _worst_case_cost(request_bytes: int, max_output_tokens: int) -> Decimal:
    # JSON bytes overstate escaped text and include request/tool framing. The
    # 2x multiplier and 10% margin cover tokenization and billing-rounding
    # uncertainty while remaining below the $1 ceiling at the 2 MiB body cap.
    token_bound = (
        Decimal(request_bytes) * MAX_TOKEN_BOUND_MULTIPLIER
        + Decimal(max_output_tokens)
    )
    return _money(
        token_bound
        * USD_PER_MILLION_TOKENS_UPPER_BOUND
        / Decimal(1_000_000)
        * METERING_MARGIN
    )


def _request_token_bound(body: bytes) -> tuple[dict[str, Any], bytes, int]:
    if len(body) > MAX_REQUEST_BYTES:
        raise ProviderSpendBlocked("request exceeds the bounded 2 MiB payload size")
    try:
        document = json.loads(body)
    except (UnicodeDecodeError, ValueError, TypeError) as error:
        raise ProviderSpendBlocked("request is not valid UTF-8 JSON") from error
    if not isinstance(document, dict):
        raise ProviderSpendBlocked("Responses API request must be a JSON object")
    if document.get("model") != AUTHORIZED_MODEL:
        raise ProviderSpendBlocked("request model does not match the exact owner-authorized model")
    if document.get("previous_response_id") or document.get("conversation") or document.get("input_file"):
        raise ProviderSpendBlocked("stateful or externally stored provider context cannot be bounded")
    if any(key in document for key in ("max_tokens", "max_completion_tokens")):
        raise ProviderSpendBlocked("only the bounded Responses API output-token field is allowed")

    max_output = document.get("max_output_tokens", MAX_OUTPUT_TOKENS)
    if isinstance(max_output, bool) or not isinstance(max_output, int) or max_output < 1:
        raise ProviderSpendBlocked("max_output_tokens must be a positive integer")
    max_output = min(max_output, MAX_OUTPUT_TOKENS)
    document["max_output_tokens"] = max_output
    document["store"] = False
    document.pop("previous_response_id", None)
    document.pop("conversation", None)

    forbidden_keys = {
        "image", "image_url", "input_image", "input_audio", "audio", "video",
        "file", "file_id", "input_file", "attachments", "computer_screenshot",
    }

    def reject_unbounded_modalities(value: Any) -> None:
        if isinstance(value, dict):
            if any(str(key).casefold() in forbidden_keys for key in value):
                raise ProviderSpendBlocked("non-text provider input is outside the bounded spend policy")
            for child in value.values():
                reject_unbounded_modalities(child)
        elif isinstance(value, list):
            for child in value:
                reject_unbounded_modalities(child)

    # Tool declarations contain JSON Schemas. A schema may legitimately name
    # an argument ``image_url`` (for example, a vision tool) without sending
    # image input to the provider. Keep rejecting modality fields at the
    # request top level and inside actual model input, while leaving tool
    # schemas to the existing whole-body byte reservation below.
    if any(str(key).casefold() in forbidden_keys for key in document):
        raise ProviderSpendBlocked("non-text provider input is outside the bounded spend policy")
    reject_unbounded_modalities(document.get("input"))
    normalized = json.dumps(document, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    if len(normalized) > MAX_REQUEST_BYTES:
        raise ProviderSpendBlocked("normalized request exceeds the bounded 2 MiB payload size")
    return document, normalized, max_output


def _usage_objects(value: Any):
    if isinstance(value, dict):
        usage = value.get("usage")
        if isinstance(usage, dict):
            yield usage
        for child in value.values():
            yield from _usage_objects(child)
    elif isinstance(value, list):
        for child in value:
            yield from _usage_objects(child)


def parse_provider_usage(body: bytes, content_type: str) -> SpendUsage | None:
    """Read the provider's final JSON/SSE usage; absence remains unknown."""
    objects: list[Any] = []
    if "text/event-stream" in (content_type or "").casefold():
        completed: list[Any] = []
        for line in body.splitlines():
            if not line.startswith(b"data:"):
                continue
            raw = line[5:].strip()
            if not raw or raw == b"[DONE]":
                continue
            try:
                document = json.loads(raw)
            except (UnicodeDecodeError, ValueError):
                continue
            if (
                isinstance(document, dict)
                and document.get("type") == "response.completed"
                and isinstance(document.get("response"), dict)
                and document["response"].get("status") == "completed"
            ):
                completed.append(document)
        if len(completed) != 1:
            return None
        objects = completed
    else:
        try:
            document = json.loads(body)
        except (UnicodeDecodeError, ValueError, TypeError):
            return None
        if not isinstance(document, dict) or document.get("status") != "completed":
            return None
        objects.append(document)
    found: list[SpendUsage] = []
    for document in objects:
        for usage in _usage_objects(document):
            input_tokens = usage.get("input_tokens", usage.get("prompt_tokens"))
            output_tokens = usage.get("output_tokens", usage.get("completion_tokens"))
            total_tokens = usage.get("total_tokens")
            if any(isinstance(v, bool) for v in (input_tokens, output_tokens, total_tokens) if v is not None):
                continue
            if input_tokens is not None and (not isinstance(input_tokens, int) or input_tokens < 0):
                continue
            if output_tokens is not None and (not isinstance(output_tokens, int) or output_tokens < 0):
                continue
            if total_tokens is not None and (not isinstance(total_tokens, int) or total_tokens < 0):
                continue
            if total_tokens is None:
                if input_tokens is None or output_tokens is None:
                    continue
                total_tokens = input_tokens + output_tokens
            total_tokens = max(total_tokens, (input_tokens or 0) + (output_tokens or 0))
            if total_tokens < 1:
                continue
            cost = _money(
                Decimal(total_tokens)
                * USD_PER_MILLION_TOKENS_UPPER_BOUND
                / Decimal(1_000_000)
                * METERING_MARGIN
            )
            found.append(SpendUsage(input_tokens, output_tokens, total_tokens, cost))
    if not found:
        return None
    return max(found, key=lambda item: item.total_tokens)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class OpenCodeSpendGateway:
    """Ephemeral, loopback-only single-route provider spend gateway."""

    def __init__(
        self,
        database: FoundryDatabase,
        settings: ProviderSettings,
        *,
        stop_path: Path | str | None,
        mission_id: str,
        timeout_seconds: int,
        transport: Callable[..., UpstreamResponse] | None = None,
    ) -> None:
        if (
            settings.provider != AUTHORIZED_PROVIDER
            or settings.model != AUTHORIZED_MODEL
            or settings.base_url.rstrip("/") != AUTHORIZED_BASE_URL
            or not settings.api_key
        ):
            raise ProviderSpendBlocked("configured provider identity is not the exact authorized OpenCode Go route")
        self.database = database
        self.settings = settings
        self.stop_path = Path(stop_path) if stop_path is not None else None
        self.mission_id = mission_id
        self.timeout_seconds = max(1, int(timeout_seconds))
        self._upstream_transport = transport or self._https_transport
        self.local_api_key = secrets.token_urlsafe(32)
        self.server: ThreadingHTTPServer | None = None
        self.thread: threading.Thread | None = None
        self.base_url: str | None = None

    def start(self) -> str:
        if self.server is not None:
            if self.base_url is None:
                raise RuntimeError("gateway startup is incomplete")
            return self.base_url
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: Any) -> None:
                # Requests can contain sensitive prompt and provider details.
                return

            def do_POST(self) -> None:
                owner._handle_post(self)

            def do_GET(self) -> None:
                owner._respond(self, 404, {"error": "route unavailable"})

            def do_PUT(self) -> None:
                owner._respond(self, 404, {"error": "route unavailable"})

            def do_DELETE(self) -> None:
                owner._respond(self, 404, {"error": "route unavailable"})

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        host, port = self.server.server_address[:2]
        self.base_url = f"http://{host}:{port}/v1"
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            name="jarvis-opencode-spend-gateway",
            daemon=True,
        )
        self.thread.start()
        return self.base_url

    def close(self) -> None:
        server = self.server
        self.server = None
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if self.thread is not None:
            self.thread.join(timeout=2)
        self.thread = None

    def _respond(self, handler: BaseHTTPRequestHandler, status: int, value: dict[str, Any]) -> None:
        body = json.dumps(value, separators=(",", ":")).encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        handler.send_header("Content-Length", str(len(body)))
        handler.send_header("Connection", "close")
        handler.end_headers()
        try:
            handler.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass

    def _stopped(self) -> bool:
        if self.stop_path is not None and self.stop_path.exists():
            return True
        from ..owner_directives import active_directives_for

        records = active_directives_for(
            self.database,
            command_types={"STOP_EVERYTHING", "STOP_MISSION", "PAUSE"},
            targets={"GLOBAL", self.mission_id},
        )
        for record in records:
            if record.command_type in {"STOP_EVERYTHING", "PAUSE"}:
                return True
            if record.command_type == "STOP_MISSION" and record.target in {"GLOBAL", self.mission_id}:
                return True
        return False

    def _handle_post(self, handler: BaseHTTPRequestHandler) -> None:
        if handler.path != "/v1/responses":
            self._respond(handler, 404, {"error": "only the authorized Responses route is available"})
            return
        if not hmac.compare_digest(handler.headers.get("Authorization", ""), f"Bearer {self.local_api_key}"):
            self._respond(handler, 401, {"error": "gateway authentication failed"})
            return
        try:
            length = int(handler.headers.get("Content-Length", "-1"))
        except ValueError:
            length = -1
        if length < 0 or length > MAX_REQUEST_BYTES:
            self._respond(handler, 413, {"error": "bounded request size required"})
            return
        body = handler.rfile.read(length)
        if len(body) != length:
            self._respond(handler, 400, {"error": "incomplete request body"})
            return
        try:
            _document, normalized, max_output = _request_token_bound(body)
            if self._stopped():
                raise ProviderSpendBlocked("Supervisor or owner STOP/pause is active")
            request_id = str(uuid.uuid4())
            digest = hashlib.sha256(normalized).hexdigest()
            reserved = self._reserve(request_id, digest, len(normalized), max_output)
        except ProviderSpendBlocked as error:
            self._respond(handler, 429, {"error": str(error), "type": "provider_spend_blocked"})
            return
        except Exception:
            _LOG.exception("provider spend reservation failed; blocking before upstream dispatch")
            self._respond(handler, 503, {"error": "spend authorization unavailable; request blocked"})
            return

        try:
            upstream = self._upstream_transport(normalized, handler.headers)
        except Exception:
            self._hold(request_id, reserved, "upstream transport result is ambiguous")
            self._respond(handler, 502, {"error": "upstream result is ambiguous; request spend bound is held"})
            return

        if 300 <= upstream.status < 400:
            self._hold(request_id, reserved, "upstream redirect rejected")
            self._respond(handler, 502, {"error": "upstream redirect rejected; request spend bound is held"})
            return
        usage = parse_provider_usage(upstream.body, upstream.headers.get("Content-Type", ""))
        if not 200 <= upstream.status < 300 or usage is None:
            self._hold(request_id, reserved, "upstream response lacked authoritative usage")
        else:
            try:
                if usage.conservative_cost_usd > reserved:
                    self._settle(request_id, reserved, usage, overrun=True)
                else:
                    self._settle(request_id, reserved, usage, overrun=False)
            except Exception:
                _LOG.exception("provider usage settlement failed; full request reservation remains charged")
                self._hold(request_id, reserved, "provider usage settlement could not be recorded")
                self._respond(handler, 503, {"error": "provider usage could not be recorded; request spend bound is held"})
                return

        safe_body = _redact_provider_secret(upstream.body, self.settings.api_key or "")
        headers = {key.casefold(): value for key, value in upstream.headers.items()}
        handler.send_response(upstream.status)
        handler.send_header("Content-Type", headers.get("content-type", "application/json"))
        for name in ("x-request-id", "x-opencode-session"):
            if name in headers:
                handler.send_header(name, headers[name])
        handler.send_header("Content-Length", str(len(safe_body)))
        handler.send_header("Connection", "close")
        handler.end_headers()
        try:
            handler.wfile.write(safe_body)
        except (BrokenPipeError, ConnectionResetError, OSError):
            # Provider spend is already settled or held before bytes reach Hermes.
            pass

    def _reserve(self, request_id: str, request_hash: str, request_bytes: int, max_output: int) -> Decimal:
        upper_bound = _worst_case_cost(request_bytes, max_output)
        with self.database.transaction() as connection:
            _require_owner_compute_policy(self.database)
            states = _event_usage(connection, SPEND_SCOPE_ID)
            total = sum(_accounted_exposure(state) for state in states.values())
            remaining = SPEND_CEILING_USD - total
            if remaining <= 0:
                raise ProviderSpendBlocked("the authorized $1.00 provider-compute ceiling has been reached")
            # Requests from the same worker can overlap while a prior response
            # is still being read. Keep each open request charged at its bound;
            # a late or lost settlement must not freeze unrelated safe work.
            if upper_bound > remaining:
                raise ProviderSpendBlocked("request worst-case cost exceeds the remaining authorized spend")
            payload = {
                "spend_scope_id": SPEND_SCOPE_ID,
                "request_id": request_id,
                "request_sha256": request_hash,
                "provider": AUTHORIZED_PROVIDER,
                "model": AUTHORIZED_MODEL,
                "reserved_usd": format(upper_bound, "f"),
                "reservation_policy": REQUEST_BOUND_RESERVATION_POLICY,
                "request_bytes": request_bytes,
                "max_output_tokens": max_output,
                "request_worst_case_usd": format(upper_bound, "f"),
                "ceiling_usd": format(SPEND_CEILING_USD, "f"),
            }
            _append_event(
                self.database,
                connection,
                "provider.compute_reserved",
                payload,
                f"provider.compute_reserved:{SPEND_SCOPE_ID}:{request_id}",
            )
            return upper_bound

    def _hold(self, request_id: str, reserved: Decimal, reason: str) -> None:
        try:
            with self.database.transaction() as connection:
                _append_event(
                    self.database,
                    connection,
                    "provider.compute_held",
                {
                    "spend_scope_id": SPEND_SCOPE_ID,
                    "request_id": request_id,
                    "provider": AUTHORIZED_PROVIDER,
                    "model": AUTHORIZED_MODEL,
                    "reserved_usd": format(reserved, "f"),
                    "reason": reason,
                },
                    f"provider.compute_held:{SPEND_SCOPE_ID}:{request_id}",
                )
        except Exception:
            _LOG.exception("could not append provider spend hold; original reservation remains active")

    def _settle(self, request_id: str, reserved: Decimal, usage: SpendUsage, *, overrun: bool) -> None:
        with self.database.transaction() as connection:
            _append_event(
                self.database,
                connection,
                "provider.compute_settled",
                {
                    "spend_scope_id": SPEND_SCOPE_ID,
                    "request_id": request_id,
                    "provider": AUTHORIZED_PROVIDER,
                    "model": AUTHORIZED_MODEL,
                    "cost_usd": format(usage.conservative_cost_usd, "f"),
                    "input_tokens": usage.input_tokens,
                    "output_tokens": usage.output_tokens,
                    "total_tokens": usage.total_tokens,
                    "reserved_usd": format(reserved, "f"),
                    "price_basis_usd_per_million_tokens": format(USD_PER_MILLION_TOKENS_UPPER_BOUND, "f"),
                    "conservative_margin": format(METERING_MARGIN, "f"),
                    "overrun": bool(overrun),
                },
                f"provider.compute_settled:{SPEND_SCOPE_ID}:{request_id}",
            )

    def _https_transport(self, body: bytes, request_headers) -> UpstreamResponse:
        url = f"{AUTHORIZED_BASE_URL}/responses"
        permitted = {"content-type", "accept", "user-agent", "x-opencode-session"}
        headers = {
            key: value
            for key, value in request_headers.items()
            if key.casefold() in permitted
        }
        headers["Authorization"] = f"Bearer {self.settings.api_key}"
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        opener = urllib.request.build_opener(_NoRedirect())
        try:
            response = opener.open(request, timeout=self.timeout_seconds)
        except urllib.error.HTTPError as error:
            response = error
        try:
            response_body = response.read(MAX_RESPONSE_BYTES + 1)
            if len(response_body) > MAX_RESPONSE_BYTES:
                raise ProviderSpendBlocked("upstream response exceeds the safe buffered-response bound")
            return UpstreamResponse(
                int(response.status),
                {str(k): str(v) for k, v in response.headers.items()},
                response_body,
            )
        finally:
            response.close()


def _redact_provider_secret(body: bytes, secret: str) -> bytes:
    if secret:
        body = body.replace(secret.encode("utf-8"), b"[REDACTED]")
    return re.sub(rb"sk-[A-Za-z0-9]{16,}", b"[REDACTED]", body)
