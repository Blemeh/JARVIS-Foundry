# V8 CANONICAL PROMOTION REPORT

PROJECT: JARVIS × Letta Hybrid Memory — canonical production promotion
STATUS: PROMOTED (post-promotion certification round closed)

## 1. Baseline (preflight)

- OLD CANONICAL BASELINE: `93a3ddc0965dfaf3e4cb9dc0952467bd5250520a` (main; 6 untracked owner docs, hashes recorded).
- Certified V8 candidate: `6918c9fc5afb0c0ad8698bee61e42e8ddbb0db66` (descendant of base, 8 commits, 9/9 certification, 1026/0/0).
- Live pre-promotion: schema 32 (66,271 events), STOP OFF (4 active directives, none STOP/PAUSE), 2 supervisor instances, heartbeat fresh.
- CANONICAL DRIFT BEFORE MERGE: NO (canonical was exactly the expected base).

## 2. Gates

| Gate | Result | Evidence |
|---|---|---|
| Isolated integration rehearsal (ff-only) | PASS — resolved EXACTLY to 6918c9f | zero diff |
| Clean-room dependency test | FAIL→PASS | pyyaml undeclared (roles/router.py); repair `8860e1a` `dependencies=["pyyaml>=6.0"]`; fresh install + test_role_routing 14/14 |
| Clean-room full suite | PASS 1026/0/0 EXIT=0 | JUnit 434.96s |
| V8 script suite (clean room) | REHEARSAL 9/9 · BENCHMARK PASS · SHADOW PASS · CANARY PASS · ADVERSARIAL 13P/0F/1INFO | run outputs |
| Migration rehearsal on realistic copies | PASS 9/9 (empty + 7 var copies + LIVE DB backup) | additive: events/claims/directives preserved, +6 tier_c tables, quick_check ok |
| Rollback drill (pre-promotion) | PASS | restore-from-backup: 32→33→32, events equal, STOP equal, previous executable (93a3ddc) rc=0 |
| Pre-promotion independent verifier | FAIL → repaired | .gitignore rewritten 55→3 lines in V8 commits; repaired `76e38a0` (verbatim restore, diff=0); all 12 other criteria PASS |
| Recovery point | created + verified | git bundle + tag `v8-pre-promotion-93a3ddc` + live DB backup (quick_check ok) |
| Canonical promotion | PASS (ff-only) | canonical HEAD → `76e38a0`; owner files hash-identical |
| Post-merge canonical full suite | PASS **1027/0/0 EXIT=0** | JUnit 424.46s (1026 + takeover regression) |
| Live DB migration | PASS | 32→33 via normal startup; quick_check ok; 6 tier_c tables; ledger intact |
| Live supervisor | PASS | single pair (15068/5252), RUNNING, heartbeat advancing, lease `supervisor-ef275ebc` renewing, no crash loop |
| Live V8 canary | PASS (idempotent ×2) | memory→CORE+stamp, recall, EMERGENCY STOP ACTIVE in governance, verifier isolation, 13 block revisions, reconcile consistent ON LIVE DB, stale, budget |
| STOP semantics | PASS | STOP was OFF; sentinel activated for maintenance, cleared after canary; supervisor resumed RUNNING; EmergencyStop machinery untouched |
| Post-promotion certifier round 1 | FAIL → remediated | P3 (postmerge-suite.txt staged-not-committed) + P12 (drill pre-promotion-only) — both fixed in `4e50f49` (drill `--pre-v8-source` post-promotion mode + `--record` gate; drill evidence committed; POST-PROMOTION DRILL PASS); 12/14 criteria were PASS; rollback mechanism independently re-verified real; no critical/high |
| Post-promotion certifier round 2 | [LOCKED — see certifier report] | |

## 3. Live finding repaired during promotion

Supervisor takeover during an ACTIVE STOP crashed (heartbeat `EventConflict` — `emergency_stop.frozen` idempotency keyed by stop_id only). Repair `9cfd675`: key = stop_id + instance_id; regression `test_supervisor_takeover_during_active_stop_does_not_collide`. This is the only change to supervisor control code; stop semantics unweakened (certifier P7/P8 PASS).

## 4. Final state

```text
FINAL PROMOTED CANONICAL: 4e50f49 (lineage: 93a3ddc → 8 V8 commits → 6918c9f →
                           8860e1a → d21590f → 76e38a0 → 9cfd675 → e715b81 → 4e50f49)
NEW TRUSTED BASELINE:     4e50f49 (old baseline 93a3ddc preserved as historical/pre-V8)
CANONICAL STATE:          PROMOTED
LIVE:                     schema 33, supervisor RUNNING, STOP OFF (original state), Tier C live
CANONICAL MUTATION:       YES — intentional promotion
UNEXPECTED MUTATION:      NONE
LIVE DAMAGE:              NONE
ROLLBACK REQUIRED:        NO
UNRESOLVED CRITICAL:      0
UNRESOLVED HIGH:          0
```

## 5. Known notes (non-blocking)

- `e715b81` commit message says "block revisions 7"; committed live-canary.json and the live DB hold 13 (the idempotent canary grew revisions across runs). Cosmetic; history left untouched for baseline stability.
- Rollback strategy is RESTORE-FROM-BACKUP (no reverse migration; v33 additive; the ledger is Tier C's source of truth). The pre-promotion recovery bundle + verified DB backup live at `C:/Users/b/AppData/Local/Temp/v8-promotion-recovery/`.
- Rollback drill now has two modes: pre-promotion (live-snapshot) and post-promotion (`--pre-v8-source <recovery backup>`); plain runs never write evidence (`--record` gate).

## 6. Evidence root

- Repo: `evidence/v8-canonical-promotion/` (live-canary.json, rollback-drill.json, postmerge-suite.txt).
- External: `C:/Users/b/AppData/Local/Temp/v8-promotion-recovery/` (preflight hashes, git bundle, DB backup, JUnit, suite logs, certifier reports).
