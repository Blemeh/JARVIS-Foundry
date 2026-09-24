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
            spend_ceiling_usd="1.00",
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


def test_reservation_settlement_and_next_reservation_account_cumulatively(monkeypatch, db):
    install_policy(monkeypatch)
    gate = gateway(db)
    first_id = str(uuid.uuid4())
    first_reserved = gate._reserve(first_id, "a" * 64, 128, 64)
    assert first_reserved == Decimal("1.000000")

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
    assert second_reserved == Decimal("0.999973")


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


def test_unknown_usage_holds_remaining_budget_and_blocks_next_dispatch(monkeypatch, db):
    install_policy(monkeypatch)
    calls: list[bytes] = []

    def transport(body, _headers):
        calls.append(body)
        return spend.UpstreamResponse(
            200,
            {"Content-Type": "application/json"},
            b'{"status":"completed","usage":null}',
        )

    gate = gateway(db, transport=transport)
    base_url = gate.start()
    try:
        with post_to_gateway(base_url, gate.local_api_key, request_body()) as response:
            assert response.status == 200
        assert len(calls) == 1

        with pytest.raises(urllib.error.HTTPError) as blocked:
            post_to_gateway(base_url, gate.local_api_key, request_body("next request"))
        assert blocked.value.code == 429
        assert len(calls) == 1
    finally:
        gate.close()

    assert [event.event_type for event in db.events()].count("provider.compute_held") == 1


def test_request_that_cannot_fit_remaining_budget_never_reaches_transport(monkeypatch, db):
    install_policy(monkeypatch)
    calls: list[bytes] = []

    def transport(body, _headers):
        calls.append(body)
        raise AssertionError("unaffordable dispatch reached upstream transport")

    gate = gateway(db, transport=transport)
    prior_id = str(uuid.uuid4())
    reservation = gate._reserve(prior_id, "c" * 64, 128, 64)
    high_cost = spend._money(
        Decimal(4_000_000)
        * spend.USD_PER_MILLION_TOKENS_UPPER_BOUND
        / Decimal(1_000_000)
        * spend.METERING_MARGIN
    )
    gate._settle(
        prior_id,
        reservation,
        spend.SpendUsage(None, None, 4_000_000, high_cost),
        overrun=False,
    )

    base_url = gate.start()
    too_large = request_body("x" * 800_000)
    try:
        with pytest.raises(urllib.error.HTTPError) as blocked:
            post_to_gateway(base_url, gate.local_api_key, too_large)
        assert blocked.value.code == 429
    finally:
        gate.close()
    assert calls == []


def test_concurrent_requests_cannot_hold_two_reservations(monkeypatch, db):
    install_policy(monkeypatch)
    gate = gateway(db)
    barrier = threading.Barrier(3)
    outcomes: list[str] = []

    def reserve() -> None:
        barrier.wait()
        try:
            gate._reserve(str(uuid.uuid4()), "d" * 64, 128, 64)
            outcomes.append("reserved")
        except spend.ProviderSpendBlocked:
            outcomes.append("blocked")

    threads = [threading.Thread(target=reserve) for _ in range(2)]
    for thread in threads:
        thread.start()
    barrier.wait()
    for thread in threads:
        thread.join(timeout=10)
        assert not thread.is_alive()

    assert sorted(outcomes) == ["blocked", "reserved"]
    assert [event.event_type for event in db.events()].count("provider.compute_reserved") == 1


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
