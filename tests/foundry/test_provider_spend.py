from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
import uuid
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event
from jarvis_foundry.runtime import provider_spend as spend
from jarvis_foundry.runtime.provider_config import ProviderSettings
from jarvis_foundry.runtime._stack import _prepare_hermes_home
from jarvis_foundry.workers.adapters.hermes import _pin_hermes_gateway_profile


@pytest.fixture()
def db(tmp_path: Path) -> FoundryDatabase:
    database = FoundryDatabase(tmp_path / "foundry.db")
    database.initialize()
    return database


def authorized_directives() -> list[SimpleNamespace]:
    def directive(key: str, mode: str, **metadata: object) -> SimpleNamespace:
        return SimpleNamespace(
            command_type="ADD_CONSTRAINT",
            metadata={"constraint_key": key, "mode": mode, **metadata},
            authenticated=True,
            unknown_fields=(),
            target="GLOBAL",
            exact_text="",
        )

    return [
        directive(
            "provider_model_compute",
            "ALLOW",
            provider=spend.AUTHORIZED_PROVIDER,
            model=spend.AUTHORIZED_MODEL,
            spend_scope_id=spend.SPEND_SCOPE_ID,
            usage_mode=spend.INCLUDED_SUBSCRIPTION_USAGE_MODE,
            included_subscription_usage_allowed=True,
            additional_billable_charges_allowed=False,
            fallbacks_allowed=False,
            use_credit_balance_fallback_allowed=False,
            fail_closed=True,
        ),
        directive(
            "provider_training_data_use",
            "ALLOW",
            provider=spend.AUTHORIZED_PROVIDER,
            model=spend.AUTHORIZED_MODEL,
            prompts_and_completions_may_train=True,
        ),
        directive("external_financial_effects", "DENY"),
        directive("destructive_effects", "DENY"),
    ]


def settings() -> ProviderSettings:
    return ProviderSettings(
        provider=spend.AUTHORIZED_PROVIDER,
        model=spend.AUTHORIZED_MODEL,
        base_url=spend.AUTHORIZED_BASE_URL,
        api_key="test-provider-secret",
        source="test",
    )


def gateway(
    database: FoundryDatabase,
    *,
    transport=None,
) -> spend.OpenCodeSpendGateway:
    return spend.OpenCodeSpendGateway(
        database,
        settings(),
        stop_path=None,
        mission_id="mission-test",
        timeout_seconds=2,
        transport=transport,
    )


def install_policy(monkeypatch) -> None:
    monkeypatch.setattr(
        spend, "active_owner_directives", lambda _database: authorized_directives()
    )


def post_to_gateway(base_url: str, api_key: str, body: bytes):
    request = urllib.request.Request(
        f"{base_url}/responses",
        data=body,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    return urllib.request.urlopen(request, timeout=5)


def request_body(text: str = "hello", *, max_output_tokens: int = 32) -> bytes:
    return json.dumps(
        {
            "model": spend.AUTHORIZED_MODEL,
            "input": text,
            "max_output_tokens": max_output_tokens,
        }
    ).encode("utf-8")


def test_owner_compute_policy_requires_exact_no_fallback_terms(monkeypatch, db):
    install_policy(monkeypatch)
    spend._require_owner_compute_policy(db)

    directives = authorized_directives()
    directives[0].metadata["fallbacks_allowed"] = True
    monkeypatch.setattr(spend, "active_owner_directives", lambda _db: directives)
    with pytest.raises(spend.ProviderSpendBlocked, match="exact bounded route"):
        spend._require_owner_compute_policy(db)

    directives = authorized_directives()
    directives[0].metadata["spend_ceiling_usd"] = "1.00"
    monkeypatch.setattr(spend, "active_owner_directives", lambda _db: directives)
    with pytest.raises(spend.ProviderSpendBlocked, match="exact bounded route"):
        spend._require_owner_compute_policy(db)


def test_request_bounds_exact_route_text_and_output(monkeypatch):
    document, normalized, max_output = spend._request_token_bound(
        json.dumps(
            {
                "model": spend.AUTHORIZED_MODEL,
                "input": "ok",
                "max_output_tokens": 99_999,
            }
        ).encode()
    )
    assert max_output == spend.MAX_OUTPUT_TOKENS
    assert document["store"] is False
    assert json.loads(normalized)["max_output_tokens"] == spend.MAX_OUTPUT_TOKENS

    # Tool parameter schemas can declare names such as image_url. They are
    # counted in the serialized request reservation but are not model input.
    schema_request = {
        "model": spend.AUTHORIZED_MODEL,
        "input": "Inspect the repository and propose a small fix.",
        "tools": [
            {
                "type": "function",
                "name": "vision_analyze",
                "parameters": {
                    "type": "object",
                    "properties": {"image_url": {"type": "string"}},
                },
            }
        ],
    }
    schema_document, schema_normalized, _ = spend._request_token_bound(
        json.dumps(schema_request).encode()
    )
    assert schema_document["tools"] == schema_request["tools"]
    assert len(schema_normalized) >= len(json.dumps(schema_request).encode())

    with pytest.raises(spend.ProviderSpendBlocked, match="exact owner-authorized"):
        spend._request_token_bound(b'{"model":"muse-spark-1.2-contributor","input":"x"}')
    with pytest.raises(spend.ProviderSpendBlocked, match="stateful"):
        spend._request_token_bound(
            json.dumps(
                {
                    "model": spend.AUTHORIZED_MODEL,
                    "input": "x",
                    "previous_response_id": "resp_previous",
                }
            ).encode()
        )
    with pytest.raises(spend.ProviderSpendBlocked, match="non-text"):
        spend._request_token_bound(
            json.dumps(
                {
                    "model": spend.AUTHORIZED_MODEL,
                    "input": {"image_url": "https://example.invalid/image"},
                }
            ).encode()
        )
    with pytest.raises(spend.ProviderSpendBlocked, match="non-text"):
        spend._request_token_bound(
            json.dumps(
                {
                    "model": spend.AUTHORIZED_MODEL,
                    "input": [
                        {
                            "role": "user",
                            "content": [{"type": "input_image", "image_url": "https://example.invalid/image"}],
                        }
                    ],
                }
            ).encode()
        )


def test_usage_parser_requires_completed_response_and_sse_usage():
    usage = {"input_tokens": 12, "output_tokens": 8, "total_tokens": 20}
    completed = json.dumps({"status": "completed", "usage": usage}).encode()
    parsed = spend.parse_provider_usage(completed, "application/json")
    assert parsed is not None
    assert parsed.total_tokens == 20
    assert parsed.conservative_cost_usd == Decimal("0.000005")

    zero_usage = json.dumps(
        {"status": "completed", "usage": {"total_tokens": 0}}
    ).encode()
    assert spend.parse_provider_usage(zero_usage, "application/json") is None

    sse = (
        b"event: response.completed\n"
        + b"data: "
        + json.dumps(
            {"type": "response.completed", "response": {"status": "completed", "usage": usage}}
        ).encode()
        + b"\n\n"
    )
    assert spend.parse_provider_usage(sse, "text/event-stream") == parsed
    zero_sse = (
        b"data: "
        + json.dumps(
            {
                "type": "response.completed",
                "response": {
                    "status": "completed",
                    "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0},
                },
            }
        ).encode()
        + b"\n\n"
    )
    assert spend.parse_provider_usage(zero_sse, "text/event-stream") is None
    partial = sse.replace(b"response.completed", b"response.in_progress")
    assert spend.parse_provider_usage(partial, "text/event-stream") is None
    assert spend.parse_provider_usage(b'{"status":"incomplete","usage":{}}', "application/json") is None


def test_reservation_settlement_records_request_usage(monkeypatch, db):
    install_policy(monkeypatch)
    gate = gateway(db)
    first_id = str(uuid.uuid4())
    first_reserved = gate._reserve(first_id, "a" * 64, 128, 64)
    assert first_reserved == spend._worst_case_cost(128, 64)

    cost = spend._money(
        Decimal(120)
        * spend.USD_PER_MILLION_TOKENS_UPPER_BOUND
        / Decimal(1_000_000)
        * spend.METERING_MARGIN
    )
    gate._settle(
        first_id,
        first_reserved,
        spend.SpendUsage(100, 20, 120, cost),
        overrun=False,
    )
    second_reserved = gate._reserve(str(uuid.uuid4()), "b" * 64, 128, 64)
    assert second_reserved == spend._worst_case_cost(128, 64)
    reservations = [
        event for event in db.events()
        if event.event_type == "provider.compute_reserved"
    ]
    assert all(
        event.payload["reservation_policy"] == spend.REQUEST_BOUND_RESERVATION_POLICY
        for event in reservations
    )
    assert all(
        event.payload["usage_mode"] == spend.INCLUDED_SUBSCRIPTION_USAGE_MODE
        and "ceiling_usd" not in event.payload
        for event in reservations
    )


def test_corrupt_negative_settlement_blocks_following_provider_dispatch(monkeypatch, db):
    install_policy(monkeypatch)
    gate = gateway(db)
    request_id = str(uuid.uuid4())
    reserved = gate._reserve(request_id, "e" * 64, 128, 64)
    db.append(
        Event.create(
            "provider.compute_settled",
            {
                "spend_scope_id": spend.SPEND_SCOPE_ID,
                "request_id": request_id,
                "provider": spend.AUTHORIZED_PROVIDER,
                "model": spend.AUTHORIZED_MODEL,
                "cost_usd": "-0.50",
                "input_tokens": 1,
                "output_tokens": 1,
                "total_tokens": 2,
                "reserved_usd": format(reserved, "f"),
                "price_basis_usd_per_million_tokens": "0.20",
                "conservative_margin": "1.10",
                "overrun": False,
            },
            source_type="test",
            source_id="provider-spend-test",
            idempotency_key=f"provider.compute_settled:{request_id}",
        )
    )

    with pytest.raises(spend.ProviderSpendBlocked, match="settlement"):
        gate._reserve(str(uuid.uuid4()), "f" * 64, 128, 64)


@pytest.mark.parametrize("first_status", [200, 429])
def test_unknown_usage_holds_request_bound_and_allows_next_dispatch(monkeypatch, db, first_status):
    install_policy(monkeypatch)
    calls: list[bytes] = []

    def transport(body, _headers):
        calls.append(body)
        if len(calls) == 2:
            return spend.UpstreamResponse(
                200,
                {"Content-Type": "application/json"},
                b'{"status":"completed","usage":{"input_tokens":12,"output_tokens":8,"total_tokens":20}}',
            )
        return spend.UpstreamResponse(
            first_status,
            {"Content-Type": "application/json"},
            b'{"status":"completed","usage":null}' if first_status == 200 else b'{"error":"rate_limited"}',
        )

    gate = gateway(db, transport=transport)
    base_url = gate.start()
    try:
        if first_status == 200:
            with post_to_gateway(base_url, gate.local_api_key, request_body()) as response:
                assert response.status == 200
        else:
            with pytest.raises(urllib.error.HTTPError) as failed:
                post_to_gateway(base_url, gate.local_api_key, request_body())
            assert failed.value.code == 429
        assert len(calls) == 1

        with post_to_gateway(base_url, gate.local_api_key, request_body("next request")) as response:
            assert response.status == 200
        assert len(calls) == 2
    finally:
        gate.close()

    events = db.events()
    assert [event.event_type for event in events].count("provider.compute_held") == 1
    first_reservation = next(event for event in events if event.event_type == "provider.compute_reserved")
    assert first_reservation.payload["reserved_usd"] == first_reservation.payload["request_worst_case_usd"]
    with db.transaction() as connection:
        states = spend._event_usage(connection, spend.SPEND_SCOPE_ID)
    assert sorted(state["status"] for state in states.values()) == ["held", "settled"]
    held = next(state for state in states.values() if state["status"] == "held")
    assert spend._accounted_exposure(held) == Decimal(first_reservation.payload["request_worst_case_usd"])


@pytest.mark.parametrize("held", [False, True])
def test_legacy_full_remaining_unsettled_request_uses_recorded_bound(monkeypatch, db, held):
    install_policy(monkeypatch)
    gate = gateway(db)
    legacy_id = str(uuid.uuid4())
    bound = spend._worst_case_cost(64_560, 4_096)
    db.append(Event.create(
        "provider.compute_reserved",
        {
            "spend_scope_id": spend.SPEND_SCOPE_ID,
            "request_id": legacy_id,
            "request_sha256": "a" * 64,
            "provider": spend.AUTHORIZED_PROVIDER,
            "model": spend.AUTHORIZED_MODEL,
            "reserved_usd": "1.000000",
            "request_bytes": 64_560,
            "max_output_tokens": 4_096,
            "request_worst_case_usd": format(bound, "f"),
            "ceiling_usd": "1.00",
        },
        source_type="provider_spend_gate", source_id=spend.SPEND_SCOPE_ID,
    ))
    if held:
        gate._hold(legacy_id, Decimal("1.000000"), "upstream response lacked authoritative usage")

    with db.transaction() as connection:
        states = spend._event_usage(connection, spend.SPEND_SCOPE_ID)
    assert states[legacy_id]["reserved_usd"] == Decimal("1.000000")
    assert states[legacy_id]["status"] == ("held" if held else "reserved")
    assert spend._accounted_exposure(states[legacy_id]) == bound
    next_bound = spend._worst_case_cost(128, 64)
    assert gate._reserve(str(uuid.uuid4()), "b" * 64, 128, 64) == next_bound


def test_unknown_holds_preserve_evidence_without_capping_included_quota(monkeypatch, db):
    install_policy(monkeypatch)
    gate = gateway(db)
    bound = spend._worst_case_cost(600_000, 4_096)
    for _ in range(4):
        request_id = str(uuid.uuid4())
        reserved = gate._reserve(request_id, "c" * 64, 600_000, 4_096)
        assert reserved == bound
        gate._hold(request_id, reserved, "usage unavailable")
    assert bound * 4 > spend.LEGACY_SPEND_CEILING_USD
    assert gate._reserve(str(uuid.uuid4()), "d" * 64, 600_000, 4_096) == bound
    assert [event.event_type for event in db.events()].count("provider.compute_reserved") == 5


def test_oversized_request_is_rejected_by_request_bound():
    too_large = request_body("x" * spend.MAX_REQUEST_BYTES)
    with pytest.raises(spend.ProviderSpendBlocked, match="bounded 2 MiB"):
        spend._request_token_bound(too_large)


def test_concurrent_included_reservations_are_not_limited_by_equivalent_dollars(monkeypatch, db):
    install_policy(monkeypatch)
    gate = gateway(db)
    barrier = threading.Barrier(5)
    outcomes: list[str] = []
    bound = spend._worst_case_cost(600_000, 4_096)
    assert bound * 4 > spend.LEGACY_SPEND_CEILING_USD

    def reserve() -> None:
        barrier.wait()
        try:
            gate._reserve(str(uuid.uuid4()), "d" * 64, 600_000, 4_096)
            outcomes.append("reserved")
        except spend.ProviderSpendBlocked:
            outcomes.append("blocked")

    threads = [threading.Thread(target=reserve) for _ in range(4)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()

    assert sorted(outcomes) == ["reserved"] * 4
    assert [event.event_type for event in db.events()].count("provider.compute_reserved") == 4
    with db.transaction() as connection:
        states = spend._event_usage(connection, spend.SPEND_SCOPE_ID)
    assert sum(spend._accounted_exposure(state) for state in states.values()) == bound * 4


def test_open_request_bound_allows_later_dispatch_and_late_settlement(monkeypatch, db):
    install_policy(monkeypatch)
    first = gateway(db)
    first_id = str(uuid.uuid4())
    first_bound = first._reserve(first_id, "a" * 64, 64_560, 4_096)

    # A response can outlive its worker session. Replay keeps the open bound
    # charged, while a new gateway may reserve only from the safe remainder.
    later = gateway(db)
    later_id = str(uuid.uuid4())
    later_bound = later._reserve(later_id, "b" * 64, 128, 64)
    with db.transaction() as connection:
        states = spend._event_usage(connection, spend.SPEND_SCOPE_ID)
    assert states[first_id]["status"] == states[later_id]["status"] == "reserved"
    assert sum(spend._accounted_exposure(state) for state in states.values()) == first_bound + later_bound

    usage = spend.SpendUsage(None, None, 100, spend._money(
        Decimal(100) * spend.USD_PER_MILLION_TOKENS_UPPER_BOUND
        / Decimal(1_000_000) * spend.METERING_MARGIN
    ))
    first._settle(first_id, first_bound, usage, overrun=False)
    with db.transaction() as connection:
        states = spend._event_usage(connection, spend.SPEND_SCOPE_ID)
    assert states[first_id]["status"] == "settled"
    assert states[later_id]["status"] == "reserved"
    assert sum(spend._accounted_exposure(state) for state in states.values()) == usage.conservative_cost_usd + later_bound


def test_gateway_dispatches_while_another_response_is_in_flight(monkeypatch, db):
    install_policy(monkeypatch)
    first_upstream = threading.Event()
    release_first = threading.Event()
    first_results: list[int | Exception] = []

    def transport(body, _headers):
        if b'"input":"first"' in body:
            first_upstream.set()
            assert release_first.wait(timeout=5)
        return spend.UpstreamResponse(
            200,
            {"Content-Type": "application/json"},
            b'{"status":"completed","usage":{"input_tokens":12,"output_tokens":8,"total_tokens":20}}',
        )

    gate = gateway(db, transport=transport)
    base_url = gate.start()

    def first_request() -> None:
        try:
            with post_to_gateway(base_url, gate.local_api_key, request_body("first")) as response:
                first_results.append(response.status)
        except Exception as error:
            first_results.append(error)

    thread = threading.Thread(target=first_request)
    try:
        thread.start()
        assert first_upstream.wait(timeout=5)
        with post_to_gateway(base_url, gate.local_api_key, request_body("second")) as response:
            assert response.status == 200
    finally:
        release_first.set()
        thread.join(timeout=10)
        gate.close()
    assert not thread.is_alive()
    assert first_results == [200]
    assert [event.event_type for event in db.events()].count("provider.compute_settled") == 2


def test_gateway_profile_pins_route_and_removes_every_fallback(tmp_path):
    home = tmp_path / "hermes"
    home.mkdir()
    (home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "fallback_providers": ["openai"],
                "fallback_model": ["gpt-5"],
                "auxiliary": {
                    "title_generation": {"fallback_chain": ["openai/gpt-5"]}
                },
                "delegation": {
                    "provider": "openai",
                    "model": "gpt-5",
                    "base_url": "https://api.openai.com/v1",
                    "fallback_providers": [{"provider": "openai"}],
                    "request_overrides": {
                        "service_tier": "priority",
                        "extra_headers": {
                            "X-OpenCode-Session": "stale-session",
                            "X-Other": "keep",
                        },
                    },
                },
            }
        ),
        encoding="utf-8",
    )

    _pin_hermes_gateway_profile(
        home,
        gateway_url="http://127.0.0.1:32123/v1",
        provider_settings=settings(),
        headers={"x-opencode-session": "test-session"},
    )
    config = yaml.safe_load((home / "config.yaml").read_text(encoding="utf-8"))
    assert config["model"] == {
        "provider": "opencode-go",
        "model": spend.AUTHORIZED_MODEL,
    }
    assert config["providers"] == {
        "opencode-go": {
            "api": "http://127.0.0.1:32123/v1",
            "extra_headers": {"x-opencode-session": "test-session"},
        }
    }
    assert config["fallback_providers"] == []
    assert config["fallback_model"] == []
    assert config["auxiliary"]["transient_retries"] == 0
    assert config["auxiliary"]["title_generation"]["fallback_chain"] == []
    assert config["auxiliary"]["title_generation"]["provider"] == "opencode-go"
    assert config["auxiliary"]["title_generation"]["model"] == spend.AUTHORIZED_MODEL
    assert config["auxiliary"]["title_generation"]["api_mode"] == "codex_responses"
    assert config["auxiliary"]["web_extract"] == {
        "provider": "opencode-go",
        "model": spend.AUTHORIZED_MODEL,
        "base_url": "http://127.0.0.1:32123/v1",
        "api_key": "",
        "api_mode": "codex_responses",
        "fallback_chain": [],
    }
    delegation = config["delegation"]
    assert delegation["provider"] == "opencode-go"
    assert delegation["model"] == spend.AUTHORIZED_MODEL
    assert delegation["base_url"] == "http://127.0.0.1:32123/v1"
    assert delegation["api_key"] == ""
    assert delegation["api_mode"] == "codex_responses"
    assert delegation["fallback_providers"] == []
    assert delegation["request_overrides"]["service_tier"] == "priority"
    assert delegation["request_overrides"]["extra_headers"] == {
        "X-Other": "keep",
        "x-opencode-session": "test-session",
    }


def test_worker_home_pins_auxiliary_and_delegation_api_mode(tmp_path):
    _prepare_hermes_home(
        tmp_path,
        {"allow_data_training_tiers_noninteractive": True},
        provider_settings=settings(),
    )
    config = yaml.safe_load((tmp_path / "config.yaml").read_text(encoding="utf-8"))
    for task_name in (
        "vision", "compression", "skills_hub", "approval", "review", "mcp",
        "title_generation", "memory_query_rewrite", "tts_audio_tags",
        "triage_specifier", "kanban_decomposer", "profile_describer", "goal_judge",
        "curator", "monitor", "background_review", "moa_reference", "moa_aggregator",
        "web_extract",
    ):
        route = config["auxiliary"][task_name]
        assert route["provider"] == "opencode-go"
        assert route["model"] == spend.AUTHORIZED_MODEL
        assert route["base_url"] == settings().base_url
        assert route["api_mode"] == "codex_responses"
        assert route["fallback_chain"] == []
    assert config["delegation"] == {
        "provider": "opencode-go",
        "model": spend.AUTHORIZED_MODEL,
        "base_url": settings().base_url,
        "api_key": "",
        "api_mode": "codex_responses",
        "fallback_providers": [],
        "request_overrides": {
            "extra_headers": {
                "x-opencode-session": config["providers"]["opencode-go"][
                    "extra_headers"]["x-opencode-session"]
            },
        },
    }
