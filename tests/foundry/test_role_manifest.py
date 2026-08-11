"""WP4 context manifest tests: exact context, deterministic hashing, roundtrip."""
from __future__ import annotations

import pytest

from jarvis_foundry.roles.manifest import ContextManifest, verify_manifest_hash


def manifest(**overrides) -> ContextManifest:
    values = {
        "run_id": "run-1",
        "role": "builder",
        "mission_id": "mission-1",
        "step_id": "step-1",
        "resource_id": "resource-1",
        "capability": "local_project_write",
        "provider": "opencode_go",
        "model": "kimi-k2.7-code",
        "isolation_group": "builder",
        "tool_policy": ("code", "tools"),
        "budget_usd": 0.0,
        "duration_seconds": 300,
        "instructions": "build the smallest correct change",
    }
    values.update(overrides)
    return ContextManifest(**values)


def test_manifest_carries_exact_context():
    m = manifest()
    payload = m.to_payload()
    assert payload["run_id"] == "run-1"
    assert payload["role"] == "builder"
    assert payload["mission_id"] == "mission-1"
    assert payload["step_id"] == "step-1"
    assert payload["resource_id"] == "resource-1"
    assert payload["capability"] == "local_project_write"
    assert payload["provider"] == "opencode_go"
    assert payload["model"] == "kimi-k2.7-code"
    assert payload["isolation_group"] == "builder"
    assert payload["tool_policy"] == ["code", "tools"]
    assert payload["duration_seconds"] == 300


def test_manifest_hash_is_deterministic_and_content_addressed():
    a = manifest()
    b = manifest()
    assert a.content_hash() == b.content_hash()
    assert verify_manifest_hash(a, a.content_hash()) is True
    assert len(a.content_hash()) == 64


def test_manifest_hash_changes_with_context():
    base = manifest()
    changed = manifest(step_id="step-2")
    assert base.content_hash() != changed.content_hash()


def test_manifest_hash_is_order_stable_across_tool_policy():
    a = manifest(tool_policy=("code", "tools"))
    b = manifest(tool_policy=("tools", "code"))
    # tool_policy is a tuple: order is part of the context, so hashes differ.
    assert a.content_hash() != b.content_hash()


def test_manifest_never_carries_credentials_fields():
    payload = manifest().to_payload()
    joined = " ".join(payload.keys()).lower()
    for forbidden in ("api_key", "token", "secret", "password", "credential"):
        assert forbidden not in joined


def test_manifest_roundtrip_from_payload():
    m = manifest()
    restored = ContextManifest.from_payload(m.to_payload())
    assert restored == m
    assert restored.content_hash() == m.content_hash()


def test_verify_manifest_hash_rejects_tampering():
    m = manifest()
    assert verify_manifest_hash(m, m.content_hash())
    forged = manifest(run_id="run-9")
    assert not verify_manifest_hash(forged, m.content_hash())


def test_manifest_hash_differs_between_roles():
    builder = manifest(role="builder")
    verifier = manifest(role="verifier")
    assert builder.content_hash() != verifier.content_hash()


def test_manifest_isolation_group_present():
    for role, group in [("executive", "executive"), ("builder", "builder"), ("verifier", "verifier")]:
        m = manifest(role=role, isolation_group=group)
        assert m.isolation_group == group
        assert m.to_payload()["isolation_group"] == group


def test_missing_required_field_rejected():
    with pytest.raises((KeyError, TypeError)):
        ContextManifest.from_payload({"run_id": "run-1"})
