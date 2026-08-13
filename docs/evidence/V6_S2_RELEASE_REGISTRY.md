# V6-S2 — IMMUTABLE RELEASE REGISTRY AND SLOTS

```text
STATUS: PASS
PHASE: V6_CONTINUOUS_SELF_EVOLUTION
SLICE: S2 — immutable release registry and slots
OBJECTIVE: immutable release manifests (source commit, artifact hashes,
  dependencies, runtime identity, parent release, improvement id,
  deterministic risk class, migrations, verdicts, rollback target) with
  external live/candidate/previous/quarantine slot control; idempotent
  transitions; recovery from duplicate events; hash-mismatch refusal;
  unknown-parent refusal; failed history preserved.
BASE COMMIT: a28e730 (S1 close)
CANDIDATE RELEASE: none (machinery slice)
RISK CLASS: R3-class machinery (release controller) built and verified
  inside the isolated V6 construction; live surfaces untouched
HYPOTHESIS: n/a (machinery; exercised by the S11 ladder)
BASELINE METRIC: full suite 737 EXIT=0 at S1 close
SUCCESS THRESHOLD: manifests bind every required identity; slot
  transitions idempotent + atomic (append_events batch); promotion
  requires PASS verdicts + eligible improvement + candidate slot;
  rollback restores the exact previous release and quarantines the
  failed one; refusals deterministic; verify_manifest detects tampering
  per artifact; all five projection touch points wired; suite green
GUARDRAILS: isolated runtime DB only (var/v6-runtime/v6.db, now schema
  v22); live DB stays v20 (verified read-only: no release tables);
  no live writes; manifests immutable (no update path; same-id changed
  payload -> EventConflict)
ACTIONS TAKEN: TDD red (18 tests) -> implemented evolution/registry.py
  (register_release, set_slot, slot_pointers, promote_candidate,
  rollback_live, quarantine_release, verify_manifest, ReleaseError) ->
  migration v22 + PROJECTION_TABLES + reconcile key map + database
  row/all/count allowlists -> focused green -> runtime DB migrated to
  v22 (reconcile consistent) -> full suite
FILES CHANGED: src/jarvis_foundry/evolution/registry.py (new);
  tests/foundry/test_evolution_registry.py (new, 18 tests);
  src/jarvis_foundry/migrations.py (SCHEMA_VERSION 22 + migration 22);
  src/jarvis_foundry/projections.py (release.manifest_recorded +
  release.slot_changed handlers, PROJECTION_TABLES); src/jarvis_foundry/
  reconcile.py (key map); src/jarvis_foundry/database.py (row/all/count
  allowlists)
LIVE SYSTEMS CHANGED: NONE (verified read-only at close time)
TESTS: 18 new S2 tests + S1 regression (18) + FULL SUITE 755 passed
  EXIT=0 (316.07s; var/evidence/v6-s2/suite-v6-s2-full.txt,
  sha256 1f863b31bd196e10934882bd956ee2bc5164b2a892c0bb213d02ecef585eaf90)
VERIFIER: round-1 deleg_a18e9d68 — PASS (R1-R7, 53/53 adversarial
  probes; verdict JSON var/evidence/v6-s2/verdict-deleg_a18e9d68.json,
  sha256 c3f0e9369c7fb17668fa722b91a7244abbc337d1b6115c3c4d8958434bbb0012;
  2 LOW + 3 INFO, all repaired/resolved in §8) → round-2 fresh verifier
  dispatched after the repair commit (A/B discrimination)
EVIDENCE: var/evidence/v6-s2/ (suite artifacts, verdict JSON);
  var/v6-runtime/v6.db (isolated runtime, schema v22, consistent)
CANARY: none (machinery slice)
PROMOTION OR ROLLBACK: none
COST AND RESOURCE USE: local only (pytest runs)
UNRESOLVED RISKS: same-provider verifier limitation (D-031); slot
  transitions are per-event atomic batches — crash-window recovery of a
  promotion mid-batch is S8's promotion record + recovery orchestrator
  (pointers can only be partially advanced by a crash between batches,
  never corrupted: every transition is a full event batch)
NEXT AUTONOMOUS ACTION: S3 — isolated candidate builder
OWNER INPUT REQUIRED: NONE
```

## Design

`src/jarvis_foundry/evolution/registry.py`:

- `register_release(...)` — validates source_commit (40-hex),
  artifact_hashes (name -> 64-hex), dependencies, runtime_identity,
  risk_class in R0-R3, migrations, verdict shapes; the referenced
  improvement must exist AND be promotion-eligible AND its frozen risk
  class must equal the manifest's (risk mismatch refused); parent and
  rollback_target must exist when given. Content-derived release id
  (identical re-registration dedupes; same-id changed payload ->
  EventConflict, so manifests are immutable by construction).
- Slots `live | candidate | previous | quarantine` in a singleton
  pointer table. `set_slot` is a semantic setter: pointing a slot at
  its current value is a no-op; otherwise it appends
  `release.slot_changed` keyed by (slot, release_id, previous) so
  re-delivery of the SAME transition dedupes while a genuinely new
  transition records new history. The projection handler re-checks
  slot validity, release existence, reason/controller, and upserts the
  pointer inside the append transaction.
- `promote_candidate` — requires the candidate slot, at least one
  verdict and ALL verdicts PASS, eligible improvement; applies
  previous:=old live, live:=candidate, candidate:=clear as ONE atomic
  append_events batch.
- `rollback_live` — requires live AND previous; applies
  quarantine:=failed live, live:=previous, previous:=clear as one
  atomic batch. The exact previous release is restored; the failed
  release stays fully auditable (manifest + events).
- `quarantine_release` — moves a release to quarantine (candidate
  cleared if pointed); history preserved.
- `verify_manifest(db, release_id, root)` — re-hashes every declared
  artifact file against the immutable manifest; missing or tampered
  files report False per artifact (never a silent PASS).

Projection `release_manifests` + `release_slots` (migration v22, slot
CHECK constraint), wired into PROJECTION_TABLES, the reconcile key map,
and database row/all/count allowlists (five touch points). Handler-level
parent/rollback-target/slot/release-existence checks run inside the
append transaction (defense in depth, atomic rollback).

## Tests (18)

genesis registration + dedup; unknown parent refused; child lineage;
bad commit/hash refused; unknown/ineligible improvement refused; risk
mismatch refused; immutability (same-key changed payload conflicts);
promote pointer moves + controller recorded; promote requires PASS
verdicts (empty and FAIL refused); promote requires the candidate slot;
slot transition idempotent (repeat call = no-op, one event); unknown
release/slot refused; rollback restores previous + quarantines failed +
history preserved; rollback without previous refused; quarantine
preserves history; verify_manifest detects tampering + missing files;
verify_manifest unknown release refused; projection/schema integrity
(pins to the imported SCHEMA_VERSION constant >= 22).

## 8. Round-1 verifier findings — repair record (deleg_a18e9d68, PASS)

| Severity | Finding | Repair (this commit) |
|---|---|---|
| LOW | verify_manifest performs no path containment on declared artifact names: a manifest declaring `../outside_secret.txt` reads a file OUTSIDE artifact_root (boolean oracle only, no exfiltration). | Artifact names are now validated at registration (`ARTIFACT_NAME_PATTERN` in domain.py: plain file names only — no separators, no `..`, no drive letters) AND re-checked in verify_manifest (containment defense, False without reading); the projection handler refuses bad names from raw writers (transaction rollback). Regressions: test_artifact_name_path_escape_refused, test_verify_manifest_containment_defense (legacy-row + raw-writer paths). |
| LOW | Slot idempotency key (slot, release, previous) cannot distinguish a genuine re-delivery from a LATER distinct transition repeating the same triple (live c→g→c→g conflicts). Fail-closed (no corruption), workaround existed. | `set_slot(..., transition_id=None)` escape hatch: an explicit transition_id gives a distinct key per transition while same-id re-delivery still dedupes and same-id different-payload still conflicts. Regression: test_slot_oscillation_with_transition_ids. |
| INFO | promote_candidate lacked the already-live guard: promoting a live release recorded 3 redundant events and previous==live. | promote_candidate now refuses when the candidate is already live. Regression: test_promote_already_live_refused. |
| INFO | A raw `release.manifest_recorded` with a NEW key but the same release_id and different content was accepted into the ledger (projection INSERT OR IGNORE kept the row immutable, but two conflicting events existed). | The projection handler now compares the identity fields (artifact_hashes/source_commit/improvement_id/risk_class) against the existing immutable row and raises (rollback) on conflict. Regression: test_manifest_same_id_different_content_rolled_back. |
| INFO | Positive evidence: mid-batch EventConflict injection proved promote/append_events atomicity (0 leaked events); full re-delivery of a promote batch deduped (created=False x3). | Recorded; crash-window recovery semantics hold. |

Round-2: fresh independent verifier with A/B discrimination (round-1
commit 269bdef vs the repaired tree) dispatched after this commit.
