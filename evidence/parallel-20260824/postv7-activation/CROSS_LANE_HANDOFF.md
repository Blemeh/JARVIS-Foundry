# Cross-Lane Handoff

This lane supplies source activation contracts for a later controlled owner
activation stage. It does not activate any real account.

The handoff is:

1. Reconcile this candidate onto the final canonical V7/V8 head after the
   separate V7 production lane closes.
2. Re-run the activation factory and the affected foundry/convergence gates on
   the reconciled head.
3. Bind a real provider credential only through the existing credential-broker
   seam. Persist an opaque reference, never the token or cookie.
4. Run identity, observed-scope, bounded-read, write-eligibility, and
   reconciliation probes as separate evidence steps.
5. Enable effects only after the explicit activation handoff and an independent
   verifier confirms the exact account, target, scope, and rollback path.

Open cross-lane facts:

- `CURRENT_STATE.md` was stale relative to the frozen source head at lane
  start. Do not use its older hash as final candidate provenance.
- The canonical main worktree advanced independently after lane creation and
  has an untracked V7-stage proof artifact. Preserve that ownership boundary;
  reconcile this candidate onto the final canonical V7/V8 head only in the
  controlled integration stage.
- Live provider readiness, live database state, scheduler state, Supervisor
  restart, and real external effects remain outside this lane.
- Drive/files remains deferred because no canonical Drive seam was present.

The candidate's final report must distinguish source PASS from live/runtime
readiness and must state that no real account, credential, message, invite,
push, or production mutation occurred.
