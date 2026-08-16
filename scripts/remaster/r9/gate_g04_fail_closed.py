"""R9-G04: fail-closed contract paths with no partial effects."""

from __future__ import annotations

import sys
import time

from common import (
    EVIDENCE_DIR,
    EVIDENCE_UTC,
    TEMP_BASE,
    read_json,
    safe_clear,
    write_json,
)

from jarvis_foundry.capabilities_live.adapters.fakes import FailClosedFakeAdapter
from jarvis_foundry.capabilities_live.broker import CredentialBroker
from jarvis_foundry.capabilities_live.policy import FinancialPolicy
from jarvis_foundry.kernel.clock import FixedClock
from jarvis_foundry.kernel.errors import FinancialActionDeniedError

EXPECTED_OUTCOMES: dict[str, str] = {
    "missing_credential": "REFUSED",
    "expired": "REFUSED",
    "revoked": "REFUSED",
    "quota": "DEFERRED",
    "rate": "DEFERRED",
    "malformed_receipt": "NOT_ACCEPTED",
    "unknown_target": "REFUSED",
    "financial": "DENIED",
}


def main() -> int:
    started = time.monotonic()
    fail_closed = read_json(EVIDENCE_DIR / "R9_FAIL_CLOSED.json")
    financial = read_json(EVIDENCE_DIR / "R9_FINANCIAL_DENIAL.json")
    credentials = read_json(EVIDENCE_DIR / "R9_CREDENTIALS.json")
    mode_checks: dict[str, bool] = {}
    for probe in fail_closed["probes"]:
        mode = probe["mode"]
        mode_checks[mode] = bool(
            probe["outcome"] == EXPECTED_OUTCOMES[mode]
            and probe["no_partial_effect"] is True
            and probe["before_state_hash"] == probe["after_state_hash"]
        )
    all_modes_ok = bool(
        mode_checks and all(mode_checks.values()) and fail_closed["all_no_partial_effect"]
    )
    financial_ok = bool(
        financial["capability_exists"] is False
        and financial["capability_ids"] == []
        and financial["financial_event_kinds"] == []
        and financial["policy_refused"] is True
    )
    broker_refusals_ok = all(item["refused"] for item in credentials["broker_probes"])

    probe_root = TEMP_BASE / "gates" / "g04"
    safe_clear(probe_root)
    probe_root.mkdir(parents=True, exist_ok=True)
    broker = CredentialBroker(probe_root / "credentials.db", clock=FixedClock(EVIDENCE_UTC))
    broker.register(
        identity="gate-owner",
        provider="gate",
        secret_value="r9-local-canary-gate-fake-token",
        capabilities=("fake.quota", "fake.malformed_receipt", "fake.financial"),
        scope={"targets": ["canary/gate/*"]},
        expires_at="2026-08-17T06:00:00Z",
        credential_id="cred-gate-fake",
    )
    fresh: dict[str, dict[str, object]] = {}
    for mode in ("quota", "malformed_receipt", "financial"):
        adapter = FailClosedFakeAdapter(probe_root / mode, mode)
        before = adapter._dir_hash(adapter.root)
        credential = broker.resolve(
            "cred-gate-fake",
            capability=adapter.capability_id,
            requested_target="canary/gate/target",
            at=EVIDENCE_UTC,
        )
        result = adapter.execute(
            "act",
            {"idempotency_key": f"gate-{mode}", "mode": mode, "request_type": "payment"},
            credential,
        )
        after = adapter._dir_hash(adapter.root)
        fresh[mode] = {
            "outcome": result.outcome.value,
            "expected": EXPECTED_OUTCOMES[mode],
            "no_partial_effect": result.no_partial_effect and before == after,
        }
    policy_denied = False
    try:
        FinancialPolicy.assert_denied("transfer")
    except FinancialActionDeniedError:
        policy_denied = True
    fresh_financial = policy_denied

    passed = bool(all_modes_ok and financial_ok and broker_refusals_ok and fresh_financial)
    payload = {
        "schema": "jarvis-r9-g04-fail-closed-v1",
        "exit_code": 0 if passed else 1,
        "wall_seconds": round(time.monotonic() - started, 3),
        "passed": passed,
        "mode_checks": mode_checks,
        "all_modes_ok": all_modes_ok,
        "financial_policy": financial_ok,
        "broker_refusals_ok": broker_refusals_ok,
        "fresh_reproduction": {
            "modes": fresh,
            "financial_policy_denied": fresh_financial,
        },
    }
    write_json(EVIDENCE_DIR / "R9_G04_FAIL_CLOSED.json", payload)
    print(f"g04 fail closed: passed={passed} wall_seconds={payload['wall_seconds']}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
