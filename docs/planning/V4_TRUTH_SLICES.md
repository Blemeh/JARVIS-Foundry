# V4 Truth-Critical Slices — Design (DIR-2026-08-12 items 1–8)

Audit performed 2026-08-12T23:20–23:40+02:00 (read-only; live DB at
C:\Users\b\AppData\Local\hermes\foundry read-only). Each slice follows:
TDD red → smallest sound repair → focused suite → full suite → freeze →
independent adversarial verifier → commit. Order follows the directive's
priority. Verifier independence: separate session, read-only, frozen
artifacts, deterministic probes (provider diversity unavailable —
DeepSeek-only routing; limitation recorded).

## V4-S2 — Verifier truth (item 1)

**R1a** `HermesOneshotAdapter.dispatch` (roles/adapter.py:284-291):
verdict must come from an EXPLICIT structured verdict in the session
output, never from exit code. Parse rules (deterministic, applied to
stdout AFTER the failure-report classification):
1. a line matching `VERDICT:\s*(PASS|FAIL|BLOCKED|NOT_TESTED)`
   (case-insensitive, bounded scan of the first 20 lines) → that verdict;
2. OR a JSON object whose `verdict` field is one of the four → that
   verdict;
3. otherwise → `NOT_TESTED` (never PASS) with the reason recorded in the
   result evidence.
For the builder role the same parse applies (evidence records what was
actually delivered; acceptance is gated on the verifier's verdict only).

**R1b** `MissionLoop.run_step` (mission/loop.py):
- verifier instructions (line 306) demand the structured verdict:
  "End your response with a line: VERDICT: PASS | FAIL | BLOCKED" +
  explicit NOT_TESTED meaning.
- `_record_evidence` (line 196): `artifact_hashes` bound to the frozen
  deliverable — after verifier PASS, hash each file under `target`
  (bounded: max 50 files, skip dirs, sha256, rel path keys) and carry
  the dict in evidence.created.

Tests (tests/foundry/test_adapter_verdict.py + loop tests):
- stub CLI exit 0, stdout "VERDICT: FAIL ..." → RoleResult verdict FAIL
- stub CLI exit 0, stdout no verdict line → verdict NOT_TESTED
- stub CLI exit 0, stdout "VERDICT: PASS" → verdict PASS
- stub CLI exit 0, JSON {"verdict": "BLOCKED"} → BLOCKED
- stub CLI exit 0, failure-prefix first line → ProviderUnavailable
  (unchanged F5/F6 behavior)
- stub CLI exit 1 → hermes_oneshot_failed (unchanged)
- loop: verifier returning NOT_TESTED → mission FAIL/BLOCKED, never
  PASSED, no commit, no mission.completed (regression: use FakeAdapter
  with verdict NOT_TESTED)
- loop: PASS path records artifact_hashes == sha256 of the deliverable
  written into target

## V4-S3 — Heartbeat/lease renewal + terminal monotonicity (item 2)

**R2a** `LeaseManager.renew(lease_key, owner_id, duration_seconds)`
(supervisor/leases.py): extend expires_at to now+duration for the
owner's lease; raise LeaseUnavailable when the lease is not held by the
owner (a session whose lease vanished cannot complete durably).
`RoleDispatcher._heartbeat` (roles/dispatcher.py:89-94): after appending
the heartbeat event, renew the session lease with duration_seconds.
Live session ⇒ lease always fresh ⇒ reaper never reaps it; dead
dispatcher ⇒ heartbeats stop ⇒ lease expires ⇒ reaper FAILED.

**R2b** projections.py agent_runs handlers become terminal-monotonic:
- `agent_run.completed`: `... WHERE run_id = ? AND status != 'FAILED'`
- `agent_run.failed`: `... WHERE run_id = ? AND status != 'COMPLETED'`
- `agent_run.started` upsert: `DO UPDATE SET ... status='RUNNING' ...`
  guarded by `WHERE agent_runs.status = 'RUNNING'` (a terminal row is
  never reset to RUNNING).
Race-safety note: event application is single-writer serialized by the
ledger sequence; the guards make a LATE event a no-op instead of a
regression.

Tests (tests/foundry/test_lease_renewal.py + projection tests):
- heartbeat renews the lease (expires_at extends beyond the original)
- a live heartbeating session is NOT reaped (reaper probe with renewed
  lease)
- a dead session (no renewal) IS reaped
- renew by a non-owner raises LeaseUnavailable
- projection: FAILED run + late agent_run.completed event → stays FAILED
- projection: COMPLETED run + late agent_run.failed → stays COMPLETED
- projection: COMPLETED run + late agent_run.started → stays COMPLETED
- regression guard: re-delivery of the same terminal event still
  idempotent

## V4-S4 — Exactly-once verifier resume (item 3)

Scenario: builder COMPLETED, verifier started but interrupted (no
terminal). Caller resumes with the SAME run_id. Today: verifier
re-dispatch under "{run_id}-verify" conflicts on
`agent_run.started:{run_id}-verify:{provider}` (EventConflict) → crash.
Repair in `MissionLoop.run_step`:
- if the builder run is a ledger replay (COMPLETED, identical manifest
  hash) AND the verifier run for this pass has no terminal event →
  dispatch the verifier under a FRESH verifier_run_id
  (`f"{run_id}-verify-{attempt}"` where attempt = count of existing
  verifier runs for this builder run), record verification.started with
  the fresh id, and append an additive correction for the dangling
  started verdict: `verification.blocked` (reason "interrupted;
  superseded by resume") — history preserved, projection terminal.
- the interrupted verifier run row is left for the reaper (lease expiry
  → FAILED heartbeat_timeout) — monotonic, never backdated.

Tests (tests/foundry/test_exactly_once_resume.py):
- RED: interrupted verifier + same-run_id resume crashes with
  EventConflict (prove the old behavior fails)
- builder is executed EXACTLY ONCE (count agent_run.started builder
  events == 1; adapter received exactly one builder manifest)
- verifier is dispatched fresh (new run id) and the mission reaches
  PASSED with a terminal verdict
- external mutation count == 1: the deliverable file is written once
  (bytes/mtime unchanged by the resume)
- the dangling started verdict receives the additive blocked correction
- a verifier that DID complete replays (no second verifier dispatch)

## V4-S1 — Support semantics + provenance hygiene (items 4–5; the
package's named FIRST MISSION)

Root cause: `note_support` (vault_retrieval.py:39-52) only knows vault
sources; 894/920 live claims (handoff/repo/system) are marked
UNSUPPORTED. Repair:
1. `note_support(connection, claim, *, vault_root=None, handoff_dir=None,
   research_dirs=None, repo_paths=())` → kind-aware:
   - vault (vault_files rel_path match wins): registry ACTIVE; when
     vault_root given also on-disk sha256 == registry content_sha256 and
     the referenced line hash exists in the file's extracted statements;
   - handoff/research: file exists under the dir AND the referenced line
     hash (`event_id`'s third segment) matches an extracted statement;
     dir missing → UNSUPPORTED (no source = no support);
   - repo: commit sha exists in the matching repo's git log (bounded
     `git log --format=%H -n 2000`, basename match; repo_paths);
   - system: `SELECT 1 FROM events WHERE sequence = N` (always
     checkable);
   - unknown prefix / no source → UNSUPPORTED.
   Batched variant for hot callers (retrieve_vault, detect_research_
   items) with a per-call file cache. Defaults preserve current behavior
   (registry-only) so existing callers/tests stay valid.
2. `_source_run_id` (live_ingestion.py:49-55): stop double-prefixing
   (`handoff:handoff:` → `handoff:`); regression on the exact format.
3. Projection normalization: knowledge_claims.source_run_id normalized
   deterministically at the projection boundary (derived state is
   rebuilt from events; history untouched). 766 live rows become clean
   on rebuild; document.
4. Re-scan the live brain after the fix; supersede (additively, never
   delete) open maintenance candidates whose UNSUPPORTED signal was
   false.

Tests: per-kind support matrices (SUPPORTED/UNSUPPORTED for vault
ACTIVE/missing/changed, handoff exists/changed/missing/dir-None, repo
present/absent/unknown-repo, system present/absent), run_id format
regression, projection normalization regression, retrieve_vault +
detect_research_items threading.

## V4-S5 — Real monitors (item 6)

`run_one_loop` (v3_loop.py): before ingestion, ensure the four live
sources (vault, handoffs, repo, system) are REGISTERED in the monitors
registry (dedup by source_kind+source_ref: reuse the existing row) and
each cycle POLLS through `poll_monitor` (dedup via canonical candidate
detection) — the loop report carries monitor ids + poll results; the
registry records last_poll_at. Live registration happens on the next
loop run (additive ledger events only).

Tests: registration dedups; repeated polls dedup candidates; poll on
disabled monitor raises; loop report includes monitor ids; live-register
probe on a STAGED copy (never the live DB in tests).

## V4-S6 — Candidate quality (item 7) + docs (item 8)

Bounded extension of score_objective:
- freshness decay from candidate created_at (age penalty after a
  threshold, deterministic);
- previous-attempt cooldown: candidates whose canonical duplicate
  mission is recently FAILED/BLOCKED get a bounded penalty (join
  missions by created_from/duplicate_of);
- source-trust term: monitor/source-kind weighting (bounded map).
Weights stay recorded in the scored event; calibration pin test updated.
Docs: PROJECT_STATE, CURRENT_WORK_PACKAGE, RECOVERY_NOTES, DECISION_LOG,
RISK_REGISTER, evidence manifests — reconciled to live truth at close;
historical statements stay historical.

## Residual risks (recorded, not hidden)

- Provider diversity unavailable (DeepSeek-only routing): verifier
  independence = separate sessions + frozen artifacts + deterministic
  probes; recorded in every verdict's independence metadata.
- git-log bounded depth (2000) for repo support: commits beyond the
  window are UNSUPPORTED (honest: not observable in the bounded log).
- live DB re-derivation of 766 run_ids is a REBUILD-time normalization
  (projection), no payload rewriting; evidence doc covers the rebuild
  runbook (stage on a copy first).
