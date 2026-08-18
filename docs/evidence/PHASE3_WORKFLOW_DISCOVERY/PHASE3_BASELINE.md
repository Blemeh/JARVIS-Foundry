# PHASE 3 BASELINE AND BOUNDARY

## Prior evidence

`docs/evidence/PHASE2_JARVIS_EYES/PHASE2_FINAL_VERDICT.md` remains PASS:
JARVIS Eyes READY AND PROVEN, 20/20 faults PASS, secret scan PASS, full suite
1,166 EXIT 0, independent review 25/25, live probe PASS, and rollback PASS.
The Phase-2 contract is reused directly through
`jarvis_foundry.perception.contracts.PerceptionObservation`.

## Scope implemented

Phase 3 adds only:

```text
PerceptionObservation
  -> deterministic sessionization
  -> safe structural tokens
  -> stable fingerprint + bounded similarity
  -> repeated-pattern evidence
  -> existing discovery candidate generator + scorer
```

The package is `src/jarvis_foundry/workflow_discovery/`. It is pure and
disabled by default through `config/workflow_discovery.yaml`; no continuous
loop is wired into perception or the Supervisor.

## Hard boundaries

- No OpenAdapt integration, desktop/browser control, replay, audio, or Tier-C
  executor reference.
- No workflow-pattern table, second candidate database, second scorer, second
  mission engine, or second transaction coordinator.
- The bridge never approves a candidate and never emits mission creation. A
  controlled mission provenance test calls the existing `approve_candidate`
  API only after the candidate path is proven.
- Observed text is never used as authority. Candidate objectives contain only a
  caller-supplied safe pattern id and are rejected if secret-like assignments
  are present.
- Owner time is a derived `ESTIMATE`, not observed truth; frequency is an
  observed-window rate, not a monthly promise.
