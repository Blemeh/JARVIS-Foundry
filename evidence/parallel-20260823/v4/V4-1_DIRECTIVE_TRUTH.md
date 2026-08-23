# V4-1 Directive Truth Evidence

Status: PASS (source/lane proof only)

Commit: `e3ee481`

## Behavior improved

- Authenticated owner admissions now carry stable owner identity, admission
  version, nonce/hash provenance, and optional supersession/expiry metadata.
- A new read-only event-derived projection reconstructs active, revoked,
  superseded, stale, and UNKNOWN records without making the UI or a mutable
  table an authority.
- Duplicate directive IDs fail closed before a second ledger admission.
- Supersession is an append-only lifecycle event and disables the old legacy
  enforcement row while retaining its history.
- Malformed optional target fields remain in the ledger and surface as
  `UNKNOWN`; they do not crash the relational projection or become active
  authority.
- Command Center snapshot now exposes owner-directive history, active/unknown
  counts, and provenance records.

## Verification

Command run from the candidate worktree:

```text
python -m pytest tests/foundry/test_v4_owner_directives.py tests/foundry/test_v4_command_center.py tests/foundry/test_v7_briefing.py tests/foundry/test_event_store.py tests/foundry/test_action_broker.py tests/foundry/test_action_policy.py tests/foundry/test_executive_intelligence.py tests/convergence/test_r10_command_center_gate.py tests/convergence/test_r10_live_directive_consumer.py -q
```

Result: PASS (all collected tests passed; no live runtime/provider/DB state
was used or mutated).

Adversarial cases covered by `test_v4_owner_directives.py`: duplicate ID,
supersession, revocation + projection rebuild, stale expiry, unknown target,
malformed target/nonce/hash, and provenance reconstruction.

