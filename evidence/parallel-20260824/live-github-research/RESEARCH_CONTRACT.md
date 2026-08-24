# Research Contract (G3/G4/G11) — Implementation & Proof

Module: `src/jarvis_foundry/research_contract.py`
Transport: `src/jarvis_foundry/connectors/github_research.py`
Tests: `tests/foundry/test_research_contract.py`, `tests/foundry/test_github_research_connector.py`

## One research interface, existing machinery

Research items are recorded as canonical `knowledge.claim_recorded` events;
evidence links are canonical append-only `knowledge.evidence_linked` events;
attempt evidence uses `connector.attempt_recorded`. No new store/scorer/registry.

Epistemic kinds → `claim_type`: `observation` | `evidence` | `claim` |
`unknown` | `conflict`.

## Provenance preserved per evidence item

Every claim event payload carries a `provenance` block:
`provider (source_type)`, `canonical_location (URL/API path)`,
`retrieved_at`, `title`, `content_sha256_16`, `data_only: true`. The projected
statement stays a CLEAN proposition so the EXISTING deterministic contradiction
detection keeps working on content; provenance lives in immutable events.

## OBSERVATION / EVIDENCE / CLAIM / UNKNOWN / CONFLICT

- OBSERVATION: direct connector read (CI run red, head SHA).
- EVIDENCE: preserved source with provenance + linked attempt hash.
- CLAIM: proposition derived from sources (e.g. "windows-ci absent on default branch").
- UNKNOWN: honest open question with information value + recommended experiment.
- CONFLICT: both sides recorded side-by-side plus an explicit conflict claim.

## Source quality / contradiction handling (G4) — proven

1. **Negated-pair conflict** (docs vs issue): "Library X works on Python 3.13"
   vs "does not work" → canonical projection flags both claims CONTRADICTED,
   both remain visible, contradiction row created. Explicit conflict claim
   records the recommended experiment.
2. **Non-negation disagreement** (release page vs registry version): outside
   bounded negation logic → explicit `conflict` claim keeps both sides visible
   without deleting either (test proves).
3. **Oversized source**: PyPI JSON exceeds the 256 KiB bounded read cap →
   recorded honestly as truncated observation, NOT inferred from partial data;
   resolved via upstream release feed instead (`T4` in DISCOVERY_PROOF.md).
4. **Missing source**: fallback unavailable → `record_unknown` with experiment.

## Adversarial web/GitHub content (G11) — DATA_ONLY proven

Corpus: ignore-previous-owner overwrite, disable STOP + push-to-main, fake
owner directive + token reveal + veto cancel, fake maintainer instruction +
unrelated command execution, token substitution + mission redirection.

Result (live campaign, `E2E_PROOF.json` → `adversarial`):

```
control_events_before == control_events_after == 0
verdict: DATA_ONLY   (all probes flagged control-shaped lexically,
                      stored ONLY as adversarial_probe observations)
```

Structural guarantee: no retrieval path in this lane can append
owner_directive/veto/stop/control events — the only event types emitted are
knowledge and research events; scope cannot be widened by content.
