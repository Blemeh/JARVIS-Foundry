from __future__ import annotations

from datetime import timedelta

from ..clock import isoformat, parse, utc_now
from ..database import FoundryDatabase


class LeaseUnavailable(RuntimeError):
    pass


class LeaseManager:
    def __init__(self, database: FoundryDatabase) -> None:
        self.database = database

    def acquire(self, lease_key: str, owner_id: str, duration_seconds: int) -> None:
        now = utc_now()
        expires = isoformat(now + timedelta(seconds=duration_seconds))
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT owner_id, expires_at FROM leases WHERE lease_key = ?", (lease_key,)
            ).fetchone()
            if row and row["owner_id"] != owner_id and parse(row["expires_at"]) > now:
                raise LeaseUnavailable(f"lease {lease_key} is held by another live owner")
            connection.execute(
                """INSERT INTO leases(lease_key, owner_id, expires_at, heartbeat_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(lease_key) DO UPDATE SET owner_id=excluded.owner_id,
                   expires_at=excluded.expires_at, heartbeat_at=excluded.heartbeat_at""",
                (lease_key, owner_id, expires, isoformat(now)),
            )

    def release(self, lease_key: str, owner_id: str) -> bool:
        with self.database.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM leases WHERE lease_key = ? AND owner_id = ?", (lease_key, owner_id)
            )
            return cursor.rowcount == 1

