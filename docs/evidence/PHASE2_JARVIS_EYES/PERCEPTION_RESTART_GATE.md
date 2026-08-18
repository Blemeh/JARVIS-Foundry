# PERCEPTION RESTART GATE — JARVIS EYES PHASE 2

## Durable cursor

`perception/cursor.py` reuses the existing `ingestion_cursors` projection
and `ingestion.cursor_updated` events. The cursor is the monotonic
external source id; a restart never re-scans the full source and a
crash-before-advance replays idempotently (content-derived observation
ids + ledger idempotency keys dedupe).

## Evidence

- P2-F13 cursor/restart PASS: after first cycle cursor=3; a fresh driver
  instance reads `after_id=3` only, zero re-reads.
- P2-F19 supervisor/process restart PASS: fresh driver on the same
  durable home resumes from cursor, `appended=0` on replay.
- P2-F11 dedup PASS: replay of identical source records yields exactly
  one observation event.
- Unit tests: cursor read/advance idempotence; replay dedup; restart
  from durable truth (no hidden chat dependency).
