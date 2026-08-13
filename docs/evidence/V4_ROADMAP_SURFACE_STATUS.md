# V4 Roadmap Surface Status — Additive Clarification

Status: **IN PROGRESS — NOT A PROMOTION VERDICT**  
Branch: `codex/v4-command-center`  
Base commit: `d07327eab983ae6d86db9e11c60733ab6320e124`

This document is an additive clarification. It does not rewrite the append-only
ledger, historical verifier verdicts, or the existing continuous-autonomy gate.

## Two meanings of “V4” in the repository

| Track | Current truth | Evidence boundary |
|---|---|---|
| Directive-defined V4 continuous-autonomy/reliability package | Historical/live package activated and its first continuous-autonomy gate is recorded as PASS | `docs/evidence/V4_ACCEPTANCE_GATE.md`, the ledger, and the frozen verifier artifacts remain authoritative |
| Original roadmap V4 Command Center, Voice, and Briefings | Incomplete at branch start; this isolated branch is implementing and testing the missing product surface | Separate roadmap-V4 acceptance gate; no PASS is implied by the reliability package |

The reliability package delivered durable observe/select/execute/verify/learn
loops, model-routing enforcement, verifier truth, lease monotonicity,
exactly-once resume, support semantics, monitors, and scoring context. It did
not by itself prove a live owner-facing Command Center, evidence-linked
decision timeline, measured cost/resource view, authenticated UI controls,
briefing outbox, or real English voice adapters.

## Scope of this isolated track

This branch adds, without mutating live runtime state:

- a durable-state Command Center read model and local UI/API;
- an evidence-linked decision timeline;
- measured/explicitly-unknown cost and resource records;
- owner controls that use the existing authenticated directive and STOP paths;
- recovery/degraded-mode projection;
- an idempotent local briefing planner/outbox;
- a provider-neutral English STT/TTS boundary with authentication, fixtures,
  audit linkage, and negative tests.

The branch does not start a sender, voice listener, scheduler, external
connector, live migration, or live database write. Real provider-backed voice
remains `BLOCKED` or `NOT_TESTED` unless independently proven by a later
isolated candidate run. Unknown measurements remain explicitly `UNKNOWN` or
`NOT_AVAILABLE`; no values are fabricated.

## Separate roadmap-V4 acceptance gate

Roadmap V4 is eligible for PASS only after an isolated frozen-artifact review
proves ledger/UI reconciliation after restart, traceable decision evidence,
measured-or-unknown costs, STOP under dependency loss, authenticated controls,
duplicate-free recoverable briefings, real tested English adapters, voice
anti-impersonation, report-over-approval behavior, green prior suites, and no
live runtime/database mutation. Until then the roadmap surface is **IN
PROGRESS**, with each unavailable dependency reported as `BLOCKED` or
`NOT_TESTED`.

