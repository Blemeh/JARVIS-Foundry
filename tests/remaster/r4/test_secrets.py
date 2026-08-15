from __future__ import annotations

from pathlib import Path

import pytest

from jarvis_foundry.kernel.errors import SecretError
from jarvis_foundry.kernel.secrets import Redaction, SecretsBroker


def broker(tmp_path: Path) -> SecretsBroker:
    return SecretsBroker(tmp_path / "secrets.db", clock=None, ids=None)


def test_broker_never_accepts_or_stores_secret_values(tmp_path: Path) -> None:
    service = broker(tmp_path)
    assert not hasattr(service, "store_value")
    assert not hasattr(service, "set_value")
    assert not hasattr(service, "put_secret")
    metadata = service.register(
        secret_id="cred-1",
        key_name="OPENAI_API_KEY",
        provider="env",
        reference="environment/OPENAI_API_KEY",
    )
    assert metadata.secret_id == "cred-1"
    assert "value" not in metadata.to_dict()
    with service.connect() as connection:
        columns = [row["name"] for row in connection.execute("PRAGMA table_info(secret_metadata)")]
    assert "value" not in columns


def test_lookup_and_list_metadata(tmp_path: Path) -> None:
    service = broker(tmp_path)
    service.register(
        secret_id="cred-1",
        key_name="OPENAI_API_KEY",
        provider="env",
        reference="environment/OPENAI_API_KEY",
    )
    assert service.metadata("cred-1").key_name == "OPENAI_API_KEY"
    with pytest.raises(SecretError):
        service.metadata("missing")
    assert len(service.list_metadata()) == 1


def test_redaction_helper_is_deterministic_and_non_emitting() -> None:
    redaction = Redaction()
    text = "token=sk-abc123def456 token=sk-abc123def456 password=not-a-real-password"
    first = redaction.redact(text)
    assert first == redaction.redact(text)
    assert "sk-abc123def456" not in first
    assert "not-a-real-password" not in first
    assert "password=" in first
    object_payload = {"key": "OPENAI_API_KEY", "value": "sk-abc123def456", "nested": [text]}
    redacted = redaction.redact_object(object_payload)
    serialized = str(redacted)
    assert "sk-abc123def456" not in serialized
    assert redaction.assert_non_emitting(redacted) is True
