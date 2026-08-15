"""Restart-safe real-source monitoring with exactly-one change candidates."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from jarvis_foundry.kernel.canonical import canonical_hash
from jarvis_foundry.kernel.clock import Clock, SystemClock
from jarvis_foundry.kernel.portfolio.discovery import DiscoveryService
from jarvis_foundry.kernel.portfolio.errors import PortfolioError
from jarvis_foundry.kernel.portfolio.provenance import excerpt, is_fresh, sha256_bytes
from jarvis_foundry.kernel.portfolio.records import (
    MonitorChange,
    MonitorCursor,
    Observation,
    SourceRef,
)
from jarvis_foundry.kernel.portfolio.store import PortfolioStore


@dataclass(frozen=True)
class PollResult:
    source_id: str
    captured_at: str
    content_hash: str
    changed: bool
    meaningful: bool
    stale: bool
    change_id: str | None
    candidate_id: str | None
    duplicate_of: str | None
    after_hash: str
    reason: str


class MonitorService:
    def __init__(self, store: PortfolioStore, clock: Clock | None = None) -> None:
        self.store = store
        self._clock = clock if clock is not None else SystemClock()

    def register_source(
        self,
        source_id: str,
        *,
        locator: str,
        freshness_seconds: int,
        capture_method: str = "read-only",
    ) -> MonitorCursor:
        cursor = MonitorCursor(
            source_id=source_id,
            locator=locator,
            last_captured_at=self._clock.now_utc(),
            last_content_hash="",
            last_change_hash=None,
            candidate_id=None,
            freshness_seconds=freshness_seconds,
        )
        self.store.append_cursor(cursor, idempotency_key=f"monitor_cursor:{source_id}:init")
        return cursor

    def poll(
        self,
        source_id: str,
        capture_fn: Callable[[], tuple[bytes, str]],
    ) -> PollResult:
        cursor = self.store.cursor(source_id)
        if cursor is None:
            raise PortfolioError(f"monitor source not registered: {source_id}")
        content, captured_at = capture_fn()
        now = self._clock.now_utc()
        if not is_fresh(captured_at, now, cursor.freshness_seconds):
            return PollResult(
                source_id=source_id,
                captured_at=captured_at,
                content_hash="",
                changed=False,
                meaningful=False,
                stale=True,
                change_id=None,
                candidate_id=None,
                duplicate_of=None,
                after_hash="",
                reason=f"stale capture {captured_at} outside freshness {cursor.freshness_seconds}s",
            )
        content_hash = sha256_bytes(
            content if isinstance(content, bytes) else str(content).encode("utf-8")
        )
        observation_id = f"obs-{canonical_hash([source_id, captured_at, content_hash])[:12]}"
        if cursor.last_content_hash == "":
            observation = Observation(
                observation_id=observation_id,
                source_type="monitor",
                source_id=source_id,
                captured_at=captured_at,
                content_hash=content_hash,
                evidence_refs=(
                    SourceRef(
                        source_type="monitor",
                        source_id=source_id,
                        captured_at=captured_at,
                        content_hash=content_hash,
                        locator=cursor.locator,
                        capture_method="read-only",
                        trust_class="owner-controlled",
                        freshness_seconds=cursor.freshness_seconds,
                    ),
                ),
                observed_value="baseline capture established",
                meaningful_change=False,
                change_hash=None,
            )
            self.store.append_observation(
                observation, idempotency_key=f"observation:{observation_id}:baseline"
            )
            baseline = MonitorCursor(
                source_id=source_id,
                locator=cursor.locator,
                last_captured_at=captured_at,
                last_content_hash=content_hash,
                last_change_hash=None,
                candidate_id=None,
                freshness_seconds=cursor.freshness_seconds,
            )
            self.store.append_cursor(
                baseline, idempotency_key=f"monitor_cursor:{source_id}:{content_hash}"
            )
            return PollResult(
                source_id=source_id,
                captured_at=captured_at,
                content_hash=content_hash,
                changed=False,
                meaningful=False,
                stale=False,
                change_id=None,
                candidate_id=None,
                duplicate_of=None,
                after_hash=content_hash,
                reason="baseline capture; no candidate",
            )
        if content_hash == cursor.last_content_hash:
            observation = Observation(
                observation_id=observation_id,
                source_type="monitor",
                source_id=source_id,
                captured_at=captured_at,
                content_hash=content_hash,
                evidence_refs=(),
                observed_value="unchanged source content",
                meaningful_change=False,
                change_hash=None,
            )
            self.store.append_observation(
                observation, idempotency_key=f"observation:{observation_id}:unchanged"
            )
            return PollResult(
                source_id=source_id,
                captured_at=captured_at,
                content_hash=content_hash,
                changed=False,
                meaningful=False,
                stale=False,
                change_id=None,
                candidate_id=cursor.candidate_id,
                duplicate_of=None,
                after_hash=content_hash,
                reason="source unchanged; no candidate",
            )
        text = (
            content.decode("utf-8", errors="replace")
            if isinstance(content, bytes)
            else str(content)
        )
        observation = Observation(
            observation_id=observation_id,
            source_type="monitor",
            source_id=source_id,
            captured_at=captured_at,
            content_hash=content_hash,
            evidence_refs=(
                SourceRef(
                    source_type="monitor",
                    source_id=source_id,
                    captured_at=captured_at,
                    content_hash=content_hash,
                    locator=cursor.locator,
                    capture_method="read-only",
                    trust_class="owner-controlled",
                    freshness_seconds=cursor.freshness_seconds,
                ),
            ),
            observed_value=excerpt(text, 240),
            meaningful_change=True,
            change_hash=content_hash,
        )
        self.store.append_observation(
            observation, idempotency_key=f"observation:{observation_id}:changed"
        )
        discovery = DiscoveryService(self.store).discover(observation)
        change_id = f"change-{canonical_hash([source_id, content_hash])[:12]}"
        change = MonitorChange(
            change_id=change_id,
            source_id=source_id,
            before_hash=cursor.last_content_hash,
            after_hash=content_hash,
            captured_at=captured_at,
            meaningful=True,
            candidate_id=discovery.candidate_id,
            duplicate_of=discovery.duplicate_of,
            idempotency_key=f"monitor_change:{source_id}:{content_hash}",
        )
        self.store.append_change(
            change, idempotency_key=f"monitor_change:{source_id}:{content_hash}"
        )
        advanced = MonitorCursor(
            source_id=source_id,
            locator=cursor.locator,
            last_captured_at=captured_at,
            last_content_hash=content_hash,
            last_change_hash=content_hash,
            candidate_id=discovery.candidate_id,
            freshness_seconds=cursor.freshness_seconds,
        )
        self.store.append_cursor(
            advanced, idempotency_key=f"monitor_cursor:{source_id}:{content_hash}"
        )
        return PollResult(
            source_id=source_id,
            captured_at=captured_at,
            content_hash=content_hash,
            changed=True,
            meaningful=True,
            stale=False,
            change_id=change_id,
            candidate_id=discovery.candidate_id,
            duplicate_of=discovery.duplicate_of,
            after_hash=content_hash,
            reason="meaningful change produced exactly one candidate",
        )

    def rollback(self, source_id: str, reason: str) -> dict[str, object]:
        history = self.store.cursor_history(source_id)
        if len(history) < 2:
            raise PortfolioError(f"cannot rollback monitor {source_id}: no prior cursor")
        current = history[-1]
        previous = history[-2]
        rejected = None
        if current.candidate_id:
            rejected = DiscoveryService(self.store).reject(current.candidate_id, reason)
        restored = MonitorCursor(
            source_id=source_id,
            locator=current.locator,
            last_captured_at=previous.last_captured_at,
            last_content_hash=previous.last_content_hash,
            last_change_hash=previous.last_change_hash,
            candidate_id=None,
            freshness_seconds=current.freshness_seconds,
            status="RESTORED",
        )
        self.store.append_cursor(
            restored,
            idempotency_key=f"monitor_cursor:{source_id}:restored:{self._clock.now_utc()}",
        )
        return {
            "source_id": source_id,
            "restored_content_hash": previous.last_content_hash,
            "rejected_candidate": rejected.candidate_id if rejected else None,
            "reason": reason,
        }
