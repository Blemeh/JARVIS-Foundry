# V4-9 — Structured briefing packet and export contract

Status: PASS — commit `6280ae6`

## Behavior delivered

`src/jarvis_foundry/briefing_packet.py` defines the stable
`v4.briefing-packet.v1` packet. It includes the window, selected briefing
items, decision-required subset, source refs, next-action summary, explicit
uncertainty, selection/cursor metadata, content digest, and delivery
eligibility.

`serialize_briefing_packet()` emits canonical sorted JSON. The explicit
`export_briefing_packet()` helper writes only a caller-selected local path.
The Command Center snapshot exposes an additive `briefing_packet` projection.
External connectors, voice, localization, and scheduler changes remain out of
scope; external sinks are reported `NOT_IMPLEMENTED`, never as delivered.

## Proof

`tests/foundry/test_v4_briefing_packet.py` proves deterministic repeated
serialization, local export bytes, no outbox mutation, stable digest, and
rejection of unknown packet kinds/schemas. The focused V4 and UI build gates
passed.

