"""Secret metadata brokerage and non-emitting redaction helpers."""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis_foundry.kernel.clock import Clock, SystemClock
from jarvis_foundry.kernel.errors import SecretError
from jarvis_foundry.kernel.ids import DeterministicIdGenerator, IdGenerator

METADATA_SCHEMA = """
CREATE TABLE IF NOT EXISTS secret_metadata (
  secret_id TEXT PRIMARY KEY,
  key_name TEXT NOT NULL,
  provider TEXT NOT NULL,
  reference TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
"""

REDACTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"sk-[A-Za-z0-9_\-./+=]{8,}"),
    re.compile(
        r"(?i)((?:api[_-]?key|access[_-]?token|password|passwd|secret|token|bearer|"
        r"authorization)\s*[:=]\s*)[A-Za-z0-9_\-./+=]{8,}"
    ),
)


@dataclass(frozen=True)
class SecretMetadata:
    secret_id: str
    key_name: str
    provider: str
    reference: str
    created_at: str
    updated_at: str

    def to_dict(self) -> dict[str, str]:
        return {
            "secret_id": self.secret_id,
            "key_name": self.key_name,
            "provider": self.provider,
            "reference": self.reference,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


class SecretsBroker:
    """Stores and serves secret metadata only; values never enter the kernel."""

    def __init__(
        self,
        path: str | Path,
        *,
        clock: Clock | None = None,
        ids: IdGenerator | None = None,
    ) -> None:
        self._path = Path(path)
        self._clock = clock if clock is not None else SystemClock()
        self._ids = ids if ids is not None else DeterministicIdGenerator(seed=1)
        self._initialize()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._path, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(METADATA_SCHEMA)

    def register(
        self,
        *,
        secret_id: str | None = None,
        key_name: str,
        provider: str,
        reference: str,
    ) -> SecretMetadata:
        now = self._clock.now_utc()
        metadata = SecretMetadata(
            secret_id=secret_id if secret_id is not None else self._ids.new_id("secret"),
            key_name=key_name,
            provider=provider,
            reference=reference,
            created_at=now,
            updated_at=now,
        )
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO secret_metadata ("
                "secret_id, key_name, provider, reference, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?)",
                (
                    metadata.secret_id,
                    key_name,
                    provider,
                    reference,
                    metadata.created_at,
                    metadata.updated_at,
                ),
            )
        return metadata

    def metadata(self, secret_id: str) -> SecretMetadata:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM secret_metadata WHERE secret_id = ?", (secret_id,)
            ).fetchone()
        if row is None:
            raise SecretError(f"unknown secret metadata: {secret_id}")
        return SecretMetadata(
            secret_id=str(row["secret_id"]),
            key_name=str(row["key_name"]),
            provider=str(row["provider"]),
            reference=str(row["reference"]),
            created_at=str(row["created_at"]),
            updated_at=str(row["updated_at"]),
        )

    def list_metadata(self) -> list[SecretMetadata]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT * FROM secret_metadata ORDER BY created_at"
            ).fetchall()
        return [
            SecretMetadata(
                secret_id=str(row["secret_id"]),
                key_name=str(row["key_name"]),
                provider=str(row["provider"]),
                reference=str(row["reference"]),
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
            )
            for row in rows
        ]


class Redaction:
    def redact(self, text: str) -> str:
        result = text
        for pattern in REDACTION_PATTERNS:
            if pattern.pattern.startswith(r"sk-"):
                result = pattern.sub("[REDACTED]", result)
            else:
                result = pattern.sub(lambda match: match.group(1) + "[REDACTED]", result)
        return result

    def redact_object(self, value: Any) -> Any:
        if isinstance(value, str):
            return self.redact(value)
        if isinstance(value, list):
            return [self.redact_object(item) for item in value]
        if isinstance(value, dict):
            return {key: self.redact_object(item) for key, item in value.items()}
        return value

    def assert_non_emitting(self, payload: Any) -> bool:
        text = str(payload)
        return not any(pattern.search(text) for pattern in REDACTION_PATTERNS)
