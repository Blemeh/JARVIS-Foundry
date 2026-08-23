# V4-3 Control State Evidence

Status: PASS (source/lane proof only)

Commit: `TBD`

## Behavior improved

- A single read-only owner-control state projection now applies explicit
  precedence: STOPPED, PAUSED, BLOCKED, DEGRADED, RUNNING/WAITING, then
  UNKNOWN when trustworthy runtime evidence is absent.
- STOP sentinel and durable STOP_EVERYTHING/PAUSE records are surfaced with
  reason, active directive IDs, resume availability, and evidence counts.
- Projection-only legacy hold rows remain visible to enforcement/status reads
  but are marked `legacy_projection_only`; they are not silently promoted to
  authenticated owner history.
- Command Center controls now expose runtime state, state reason/source,
  pause/stop/veto IDs, and the complete state evidence without adding a second
  control authority.
- Resume only revokes active STOP/PAUSE controls. Constraints and unrelated
  vetoes remain active after projection rebuild and authenticated resume.
- Discovery hold behavior includes authenticated PAUSE while deterministic
  broker STOP/veto enforcement remains unchanged.

## Verification

Focused source checks from the candidate worktree:

```text
python -m pytest tests/foundry/test_v4_control_state.py tests/foundry/test_v4_owner_management.py tests/foundry/test_v4_owner_directives.py tests/foundry/test_v4_command_center.py tests/foundry/test_command_center_discovery.py tests/foundry/test_command_center_health.py tests/foundry/test_executive_intelligence.py tests/foundry/test_mission_loop.py tests/foundry/test_verifier_binding_hardening.py tests/foundry/test_v7_briefing.py tests/convergence/test_r10_command_center_gate.py tests/convergence/test_r10_live_directive_consumer.py -q
```

Result: PASS (all collected tests passed; no live runtime/provider/DB state
was used or mutated).

Adversarial cases covered by `test_v4_control_state.py`: missing runtime
evidence is UNKNOWN, pause survives projection rebuild, resume preserves
constraints/vetoes, STOP outranks PAUSE, and STOP clears through the durable
resume path.

