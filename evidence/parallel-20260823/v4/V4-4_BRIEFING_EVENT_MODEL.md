# V4-4 — Briefing event model

Status: PASS — commit `c8b10ce`

## Behavior delivered

`src/jarvis_foundry/briefing_items.py` projects authoritative append-only
events into deterministic `BriefingItem` records. Each item carries:

- stable `item_id`, `dedupe_key`, and `item_version`;
- category, bounded importance, freshness (`FRESH`, `LATE`, `FUTURE`, or
  `UNKNOWN`), and bounded hostile-text-safe summary;
- source sequence/event/type, correlation/causation metadata, evidence refs,
  and entity/mission links;
- explicit `owner_action_required` classification rather than implicit
  approval semantics.

Briefing and heartbeat/outbox noise is excluded. Unknown or malformed payloads
remain visible as an `EVENT`/`UNKNOWN` item instead of being treated as a
successful operational result.

## Proof

`tests/foundry/test_v4_briefing_items.py` covers the item contract, bounded
summary/freshness, entity links, deterministic dedupe, and authoritative event
refs. `tests/foundry/test_v4_hardening.py` covers malformed legacy payloads,
invalid clocks, future timestamps, and hostile text. The focused V4 and
Command Center regression suite passed with exit code 0.

This is isolated source/ledger proof only; no live Supervisor, provider, or
production database was used.

