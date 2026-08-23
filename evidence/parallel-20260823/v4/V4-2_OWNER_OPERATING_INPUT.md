# V4-2 Owner Operating Input Evidence

Status: PASS (source/lane proof only)

Commit: `e57811e`

## Behavior improved

- Executive goal projection now consumes the shared authenticated directive
  projection, preserving directive/event/correlation/causation/nonce/source
  provenance instead of maintaining a second GOAL parser.
- Authenticated `REPRIORITIZE` directives apply to candidate ranking as a
  signed, target-aware, auditable factor; the raw owner priority and directive
  IDs remain in `CandidateScore`.
- Owner constraint records are projected with explicit `CLEAR`, `UNKNOWN`, or
  `CONFLICTING` status. Free text is carried verbatim and is never guessed
  into a machine policy; structured ALLOW/DENY conflicts remain visible.
- Resource-targeted vetoes now block executive eligibility, selection
  materialization, and verification acceptance before action selection.
- Executive decisions persist the owner-control context (constraints,
  applicable veto records, and veto reason) in their audit payload.
- Mission proposals and provider-facing instructions carry durable owner
  constraints verbatim, while policy gates remain authoritative.
- HMAC canonicalization covers supersession, expiry, and structured metadata;
  the localhost Command Center signer uses the same envelope.

## Verification

Focused source checks from the candidate worktree:

```text
python -m pytest tests/foundry/test_v4_owner_management.py -q
python -m pytest tests/foundry/test_v4_owner_directives.py tests/foundry/test_executive_intelligence.py tests/foundry/test_v4_command_center.py tests/foundry/test_v7_briefing.py tests/convergence/test_r10_command_center_gate.py tests/convergence/test_r10_live_directive_consumer.py tests/foundry/test_mission_loop.py tests/foundry/test_verifier_binding_hardening.py -q
```

Result: PASS (all collected tests passed; no live runtime/provider/DB state
was used or mutated).

Adversarial cases covered by `test_v4_owner_management.py`: target-specific
reprioritization changes ranking, vague constraints remain UNKNOWN, structured
ALLOW/DENY conflict is explicit, mission proposals preserve owner text, and a
resource veto blocks a candidate before action selection.

