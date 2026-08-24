# Job Queue

| Job | Scope | State | Evidence |
|---|---|---|---|
| A1 | Current account/auth truth map | PASS | `ACCOUNT_MATRIX.md`; source inventory captured in `INITIAL_STATE.md` |
| A2 | Durable activation record and replay | PASS | `connectors/account_activation.py`; replay regression |
| A3 | Credential-reference and redaction boundary | PASS | opaque reference validation and ledger scan tests |
| A4 | Auth/token lifecycle | PASS | configured, authenticating, expired, refreshing, invalid-grant, revoked, outage, restart tests |
| A5 | Observed scope/capability discovery | PASS | scope observation, scope drift, and capability mapping tests |
| A6 | Bounded read-only probe | PASS | identity plus hash-only probe receipts for Gmail, Calendar, GitHub, browser |
| A7 | Dry-run write/effect eligibility | PASS | exact target, idempotency key, handoff gate, and reconciliation tests |
| A8 | Gmail source pack | PASS | fake Gmail read, draft/send eligibility, duplicate/reconciliation coverage |
| A9 | Calendar source pack | PASS | fake Calendar identity, read, scope degradation, write eligibility |
| A10 | GitHub source pack | PASS | fake GitHub identity, repository read, branch/PR scope eligibility |
| A11 | Authenticated browser session pack | PASS | synthetic profile/domain/session identity and structural read/effect eligibility |
| A12 | Drive/files pack | DEFERRED | no existing canonical Drive connector seam; explicit machine-readable deferral |
| A13 | Multi-account separation | PASS | two-account isolation and mismatch tests |
| A14 | Activation/deactivation | PASS | explicit enable handoff, disable, revoke, history preservation |
| A15 | Unified diagnostics | PASS | structured readiness diagnostics and next-action contract |
| A16 | Malicious provider content | PASS | hash-only untrusted content proof; owner-like directives cannot change readiness authority |
| A17 | Activation factory | PASS | `scripts/postv7_activation_factory.py`; 17 fake scenarios |
| A18+ | Remaining gaps and repository gate | IN_PROGRESS | 1,985-test foundry suite exited 0; secret scan, hygiene, independent review, commit, and candidate push remain |

The queue closes only after each applicable final-gate predicate is evidenced.

## Checkpoint

Implementation checkpoint: the source layer, provider packs, factory, and
focused regression tests are staged in this isolated worktree. The factory
reports 17 PASS scenarios with no live accounts, real credentials, or real
effects; the 13 focused activation tests, 19 convergence tests, and 1,985-test
foundry suite have exited 0. Final closure is intentionally held until the
secret scan, clean-tree check, normal commit, and candidate push have all been
recorded.
