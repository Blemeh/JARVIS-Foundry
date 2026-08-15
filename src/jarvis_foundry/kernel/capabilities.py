"""Scoped expiring capability grants with canonical target identity."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from jarvis_foundry.kernel.clock import Clock, SystemClock
from jarvis_foundry.kernel.errors import CapabilityError
from jarvis_foundry.kernel.ids import DeterministicIdGenerator, IdGenerator
from jarvis_foundry.kernel.records import CapabilityGrant
from jarvis_foundry.kernel.timeutil import add_seconds_to_iso, iso_compare

GRANT_SCHEMA = """
CREATE TABLE IF NOT EXISTS capability_grants (
  grant_id TEXT PRIMARY KEY,
  capability TEXT NOT NULL,
  target_canonical TEXT NOT NULL,
  grantee TEXT NOT NULL,
  scope TEXT NOT NULL CHECK (json_valid(scope)),
  issued_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  revoked_at TEXT,
  status TEXT NOT NULL
);
"""


class CapabilityBroker:
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
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self.connect() as connection:
            connection.executescript(GRANT_SCHEMA)

    def canonical_target(self, target: str) -> str:
        normalized = target.strip().replace("\\", "/")
        parts = [part for part in normalized.split("/") if part not in ("", ".")]
        return "/".join(part.lower() for part in parts)

    def issue(
        self,
        *,
        grantee: str,
        capability: str,
        target: str,
        scope: Mapping[str, Any],
        ttl_seconds: int,
    ) -> CapabilityGrant:
        issued_at = self._clock.now_utc()
        grant = CapabilityGrant(
            grant_id=self._ids.new_id("grant"),
            capability=capability,
            target_canonical=self.canonical_target(target),
            grantee=grantee,
            scope=dict(scope),
            issued_at=issued_at,
            expires_at=add_seconds_to_iso(issued_at, ttl_seconds),
            revoked_at=None,
            status="ACTIVE",
        )
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO capability_grants ("
                "grant_id, capability, target_canonical, grantee, scope, issued_at, "
                "expires_at, revoked_at, status"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, NULL, 'ACTIVE')",
                (
                    grant.grant_id,
                    capability,
                    grant.target_canonical,
                    grantee,
                    json.dumps(dict(scope), sort_keys=True),
                    issued_at,
                    grant.expires_at,
                ),
            )
        return grant

    def verify(self, grant_id: str, *, requested_target: str, at: str) -> CapabilityGrant:
        grant = self._load(grant_id)
        if grant.status != "ACTIVE":
            raise CapabilityError(f"capability grant {grant_id} is not active")
        if iso_compare(grant.expires_at, at) <= 0:
            raise CapabilityError(f"capability grant {grant_id} is expired")
        if self.canonical_target(requested_target) != grant.target_canonical:
            raise CapabilityError(f"capability grant {grant_id} target substitution refused")
        return grant

    def revoke(self, grant_id: str, reason: str) -> CapabilityGrant:
        with self.connect() as connection:
            connection.execute(
                "UPDATE capability_grants SET status = 'REVOKED', revoked_at = ? "
                "WHERE grant_id = ?",
                (self._clock.now_utc(), grant_id),
            )
        return self._load(grant_id)

    def list_grants(self, grantee: str | None = None) -> list[CapabilityGrant]:
        sql = "SELECT * FROM capability_grants"
        params: tuple[str, ...] = ()
        if grantee is not None:
            sql += " WHERE grantee = ?"
            params = (grantee,)
        with self.connect() as connection:
            rows = connection.execute(sql + " ORDER BY issued_at", params).fetchall()
        return [self._grant_from_row(row) for row in rows]

    def _load(self, grant_id: str) -> CapabilityGrant:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT * FROM capability_grants WHERE grant_id = ?", (grant_id,)
            ).fetchone()
        if row is None:
            raise CapabilityError(f"unknown capability grant: {grant_id}")
        return self._grant_from_row(row)

    def _grant_from_row(self, row: sqlite3.Row) -> CapabilityGrant:
        return CapabilityGrant(
            grant_id=str(row["grant_id"]),
            capability=str(row["capability"]),
            target_canonical=str(row["target_canonical"]),
            grantee=str(row["grantee"]),
            scope=json.loads(str(row["scope"])),
            issued_at=str(row["issued_at"]),
            expires_at=str(row["expires_at"]),
            revoked_at=row["revoked_at"],
            status=str(row["status"]),
        )
