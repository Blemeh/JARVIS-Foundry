"""Credential brokerage: canonical identity, expiry, revocation, scoped bindings."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jarvis_foundry.capabilities_live.ledger_events import record_event
from jarvis_foundry.kernel.clock import Clock, FixedClock, SystemClock
from jarvis_foundry.kernel.errors import CapabilityError
from jarvis_foundry.kernel.ids import DeterministicIdGenerator, IdGenerator
from jarvis_foundry.kernel.ledger import Ledger
from jarvis_foundry.kernel.timeutil import iso_compare

STORE_SCHEMA = """
CREATE TABLE IF NOT EXISTS credentials (
  credential_id TEXT PRIMARY KEY,
  identity_canonical TEXT NOT NULL,
  provider TEXT NOT NULL,
  secret_value TEXT NOT NULL,
  capabilities_json TEXT NOT NULL CHECK (json_valid(capabilities_json)),
  scope_json TEXT NOT NULL CHECK (json_valid(scope_json)),
  expires_at TEXT NOT NULL,
  revoked_at TEXT,
  fingerprint TEXT NOT NULL UNIQUE,
  created_at TEXT NOT NULL
);
"""


class AuthorizationDeniedError(CapabilityError):
    """Credential resolution failed deterministically with a structured reason."""


@dataclass(frozen=True)
class CredentialRecord:
    credential_id: str
    identity_canonical: str
    provider: str
    capabilities: tuple[str, ...]
    scope: Mapping[str, Any]
    expires_at: str
    revoked_at: str | None
    fingerprint: str
    created_at: str

    def public(self) -> dict[str, Any]:
        return {
            "credential_id": self.credential_id,
            "identity_canonical": self.identity_canonical,
            "provider": self.provider,
            "capabilities": sorted(self.capabilities),
            "scope": dict(self.scope),
            "expires_at": self.expires_at,
            "revoked_at": self.revoked_at,
            "fingerprint": self.fingerprint,
            "created_at": self.created_at,
            "secret_value": "[REDACTED]",  # nosec B105 - public redaction placeholder, never a credential value
        }


@dataclass(frozen=True)
class ResolvedCredential:
    credential_id: str
    identity_canonical: str
    provider: str
    capabilities: tuple[str, ...]
    scope: Mapping[str, Any]
    expires_at: str
    fingerprint: str
    secret_value: str
    resolved_at: str

    def public(self) -> dict[str, Any]:
        return {
            "credential_id": self.credential_id,
            "identity_canonical": self.identity_canonical,
            "provider": self.provider,
            "capabilities": sorted(self.capabilities),
            "scope": dict(self.scope),
            "expires_at": self.expires_at,
            "fingerprint": self.fingerprint,
            "resolved_at": self.resolved_at,
            "secret_value": "[REDACTED]",  # nosec B105 - public redaction placeholder, never a credential value
        }

    def __repr__(self) -> str:
        return (
            f"ResolvedCredential(credential_id={self.credential_id!r}, "
            f"fingerprint={self.fingerprint[:12]}..., secret_value=[REDACTED])"
        )


class CredentialBroker:
    """Deterministic, scoped credential resolution with redacted fingerprints."""

    def __init__(
        self,
        path: str | Path,
        *,
        clock: Clock | None = None,
        ids: IdGenerator | None = None,
        ledger: Ledger | None = None,
    ) -> None:
        self._path = Path(path)
        self._clock = clock if clock is not None else SystemClock()
        self._ids = ids if ids is not None else DeterministicIdGenerator(seed=1)
        self._ledger = ledger
        self._initialize()

    @property
    def path(self) -> Path:
        return self._path

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
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection:
            connection.executescript(STORE_SCHEMA)

    @staticmethod
    def fingerprint(secret_value: str) -> str:
        return hashlib.sha256(secret_value.encode("utf-8")).hexdigest()

    @staticmethod
    def canonical_identity(identity: str) -> str:
        return identity.strip().lower()

    @staticmethod
    def canonical_target(target: str) -> str:
        normalized = target.strip().replace("\\", "/")
        parts = [part for part in normalized.split("/") if part not in ("", ".")]
        return "/".join(part.lower() for part in parts)

    def register(
        self,
        *,
        identity: str,
        provider: str,
        secret_value: str,
        capabilities: tuple[str, ...] | list[str],
        scope: Mapping[str, Any],
        expires_at: str,
        credential_id: str | None = None,
    ) -> CredentialRecord:
        now = self._clock.now_utc()
        record = CredentialRecord(
            credential_id=credential_id if credential_id is not None else self._ids.new_id("cred"),
            identity_canonical=self.canonical_identity(identity),
            provider=provider,
            capabilities=tuple(sorted(set(capabilities))),
            scope=dict(scope),
            expires_at=expires_at,
            revoked_at=None,
            fingerprint=self.fingerprint(secret_value),
            created_at=now,
        )
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO credentials ("
                "credential_id, identity_canonical, provider, secret_value, "
                "capabilities_json, scope_json, expires_at, revoked_at, fingerprint, created_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?)",
                (
                    record.credential_id,
                    record.identity_canonical,
                    record.provider,
                    secret_value,
                    json.dumps(list(record.capabilities), sort_keys=True),
                    json.dumps(dict(record.scope), sort_keys=True),
                    record.expires_at,
                    record.fingerprint,
                    record.created_at,
                ),
            )
        if self._ledger is not None:
            record_event(
                self._ledger,
                event_type="credential.registered",
                payload={
                    "credential_id": record.credential_id,
                    "identity_canonical": record.identity_canonical,
                    "provider": record.provider,
                    "capabilities": sorted(record.capabilities),
                    "expires_at": record.expires_at,
                    "fingerprint": record.fingerprint,
                    "scope_hash": hashlib.sha256(
                        json.dumps(dict(record.scope), sort_keys=True).encode("utf-8")
                    ).hexdigest(),
                },
                source_type="capability_live.broker",
                source_id=record.credential_id,
                idempotency_key=f"credential.registered:{record.credential_id}",
                event_id=f"r9:credential.registered:{record.credential_id}",
                occurred_at=self._clock.now_utc(),
            )
        return record

    def revoke(self, credential_id: str, reason: str) -> CredentialRecord:
        now = self._clock.now_utc()
        with self.connect() as connection:
            connection.execute(
                "UPDATE credentials SET revoked_at = ? WHERE credential_id = ?",
                (now, credential_id),
            )
        if self._ledger is not None:
            record_event(
                self._ledger,
                event_type="credential.revoked",
                payload={"credential_id": credential_id, "reason": reason},
                source_type="capability_live.broker",
                source_id=credential_id,
                idempotency_key=f"credential.revoked:{credential_id}",
                event_id=f"r9:credential.revoked:{credential_id}",
                occurred_at=now,
            )
        return self._load(credential_id)

    def resolve(
        self,
        credential_id: str,
        *,
        capability: str,
        requested_target: str,
        at: str,
    ) -> ResolvedCredential:
        record = self._load(credential_id)
        reason = self._denial_reason(record, capability, requested_target, at)
        if reason is not None:
            raise AuthorizationDeniedError(f"{credential_id} refused: {reason}")
        return ResolvedCredential(
            credential_id=record.credential_id,
            identity_canonical=record.identity_canonical,
            provider=record.provider,
            capabilities=record.capabilities,
            scope=record.scope,
            expires_at=record.expires_at,
            fingerprint=record.fingerprint,
            secret_value=self._secret_value(credential_id),
            resolved_at=at,
        )

    def resolve_for_capability(
        self,
        identity: str,
        *,
        capability: str,
        requested_target: str,
        at: str,
    ) -> ResolvedCredential:
        identity_canonical = self.canonical_identity(identity)
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT credential_id FROM credentials WHERE identity_canonical = ? "
                "ORDER BY credential_id",
                (identity_canonical,),
            ).fetchall()
        reasons: list[str] = []
        for row in rows:
            credential_id = str(row["credential_id"])
            try:
                return self.resolve(
                    credential_id,
                    capability=capability,
                    requested_target=requested_target,
                    at=at,
                )
            except AuthorizationDeniedError as error:
                reasons.append(str(error))
        if self._ledger is not None:
            record_event(
                self._ledger,
                event_type="authorization.denied",
                payload={
                    "credential_id": rows[0]["credential_id"] if rows else "",
                    "capability": capability,
                    "requested_target": requested_target,
                    "reason": "no authorized credential",
                },
                source_type="capability_live.broker",
                source_id=identity_canonical,
                idempotency_key=f"authorization.denied:{identity_canonical}:{capability}:{self.canonical_target(requested_target)}",
                event_id=f"r9:authorization.denied:{identity_canonical}:{capability}",
                occurred_at=self._clock.now_utc(),
            )
        raise AuthorizationDeniedError(
            f"no authorized credential for {capability} on {requested_target}: "
            + ("; ".join(reasons) if reasons else "no matching identity")
        )

    def _denial_reason(
        self,
        record: CredentialRecord,
        capability: str,
        requested_target: str,
        at: str,
    ) -> str | None:
        if record.revoked_at is not None:
            return "credential revoked"
        if iso_compare(record.expires_at, at) <= 0:
            return "credential expired"
        if capability not in record.capabilities:
            return f"capability {capability!r} not bound"
        if not self._target_allowed(requested_target, record.scope):
            return "target outside granted scope"
        return None

    def _target_allowed(self, requested_target: str, scope: Mapping[str, Any]) -> bool:
        targets = [str(item) for item in scope.get("targets", [])]
        canonical = self.canonical_target(requested_target)
        for allowed in targets:
            allowed_canonical = self.canonical_target(allowed)
            if allowed_canonical == "*" or canonical == allowed_canonical:
                return True
            if allowed_canonical.endswith("/*"):
                prefix = allowed_canonical[:-1].rstrip("/")
                if canonical == prefix or canonical.startswith(prefix + "/"):
                    return True
            elif canonical.startswith(allowed_canonical.rstrip("/") + "/"):
                return True
        return False

    def _secret_value(self, credential_id: str) -> str:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT secret_value FROM credentials WHERE credential_id = ?",
                (credential_id,),
            ).fetchone()
        if row is None:
            raise CapabilityError(f"unknown credential: {credential_id}")
        return str(row["secret_value"])

    def _load(self, credential_id: str) -> CredentialRecord:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM credentials WHERE credential_id = ?", (credential_id,)
            ).fetchone()
        if row is None:
            raise CapabilityError(f"unknown credential: {credential_id}")
        return CredentialRecord(
            credential_id=str(row["credential_id"]),
            identity_canonical=str(row["identity_canonical"]),
            provider=str(row["provider"]),
            capabilities=tuple(json.loads(str(row["capabilities_json"]))),
            scope=json.loads(str(row["scope_json"])),
            expires_at=str(row["expires_at"]),
            revoked_at=row["revoked_at"],
            fingerprint=str(row["fingerprint"]),
            created_at=str(row["created_at"]),
        )

    def records_public(self) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT credential_id FROM credentials ORDER BY credential_id"
            )
            ids = [str(row["credential_id"]) for row in rows.fetchall()]
        return [self._load(credential_id).public() for credential_id in ids]

    def secrets(self) -> list[str]:
        with self.connect() as connection:
            rows = connection.execute("SELECT secret_value FROM credentials")
            return [str(row["secret_value"]) for row in rows.fetchall()]

    def secret_leaks_in(self, text: str) -> list[str]:
        return [secret for secret in self.secrets() if secret and secret in text]

    @staticmethod
    def fixed_clock(iso: str) -> FixedClock:
        return FixedClock(iso)
