# Convergence workflow

Use when combining accepted work from multiple branches or reconciling a
candidate with an advanced canonical branch.

1. Create a clean integration worktree from the exact canonical base.
2. Record refs, merge bases, ancestry, range diffs, dirty states, and protected
   worktrees before mutation.
3. Classify each input and concern as unique, overlapping, already present,
   superseded, conflicting, or not applicable.
4. Choose one canonical authority per concern; compare real semantics rather
   than commit messages.
5. Cherry-pick or patch only the accepted behavior needed. Do not blindly merge
   candidate histories or import unrelated product ancestry.
6. Resolve conflicts against current code, architecture invariants, and
   `docs/CURRENT_STATE.md`; never select a side mechanically.
7. Run focused cross-lane scenarios, relevant regression, documentation
   freshness, and diff/ancestry checks.
8. Freeze exact final SHA, limitations, omitted inputs, and canonical/live
   boundaries in the handoff.

Convergence does not authorize live promotion. A clean integration candidate
may still be `NOT_TESTED` or `SUSPENDED` in production.
