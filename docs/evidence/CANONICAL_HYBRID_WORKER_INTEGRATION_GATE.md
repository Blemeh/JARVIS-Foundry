# CANONICAL_HYBRID_WORKER_INTEGRATION_GATE

```text
STATUS: PASS (canonical gate 18/18; fresh independent adversarial review PASS)
CANONICAL START HEAD: e715b812dc49a04e1a0294b8dbd18c48abacb9f8
INTEGRATION BRANCH: integration/canonical-hybrid-worker
INTEGRATION COMMIT: 01e9cc9 (rebased onto current canonical main 2f7360e7;
                    canonical main advanced externally via v8 promotion
                    commits 4e50f49 + 2f7360e during the mission — docs/
                    scripts/evidence only, zero overlap with this branch)
PROVIDER: direct OpenCode Go (https://opencode.ai/zen/go/v1)
MODEL: deepseek-v4-flash (owner mandate; no substitutions)
```

## Objective

Integrate the proven Hybrid Worker architecture natively into CURRENT
canonical JARVIS (which gained v8 hybrid-memory after the candidate's
baseline), preserve all post-baseline canonical work, close the OpenHands
containment defect or honestly declare NOT_READY, and prove the canonical
mission path can dispatch through workers with JARVIS-owned truth.

## OpenHands containment closure (Phase 3)

Live adversarial probes (real OpenHands CLI, direct opencode-go,
deepseek-v4-flash):

```text
PROBE 1 (completed run, NO disposable home): FAIL — wrote 27+ event files
  to ~/.openhands/conversations/<id> and modified the skills cache reflog.
  The fail-closed adapter correctly returned BLOCKED with violations.
  (Probe-owned dirs removed afterward; owner state restored to the 6
  pre-existing dirs.)

PROBE 2 (completed run, disposable home): PASS — zero ~/.openhands
  violations; conversation ae5d0af7fb474470af87056b24129a00 persisted under
  the disposable home (contained-home/.openhands/conversations/...);
  public + held-out tests both exit 0.

PROBE 3 (forced termination mid-run, disposable home): PASS — process
  killed after startup window; zero ~/.openhands violations.
```

Containment strategy (implemented in `workers/adapters/openhands.py`):

- `OH_PERSISTENCE_DIR` — redirects agent-server settings/secrets/profiles
  stores (verified against the installed runtime `persistence/store.py`).
- Disposable `USERPROFILE` / `HOME` / `TMP` / `TEMP` — redirects every
  SDK `Path.home()` anchor (conversations, skills cache, plugins, auth,
  profiles, extensions).
- Before/after sha256 snapshot diff of the real `~/.openhands` — fail
  closed (BLOCKED) on any new/removed/modified owner entry, including on
  timeout/forced-termination paths.

Result: `OPENHANDS_READINESS = READY` (positively proven, evidence above).
Readiness in the router remains NOT_READY by default and flips to READY
only via explicit containment evidence (`mark_ready` + config template).

## Canonical integration (Phases 1-5)

- `workers/` package copied verbatim (no overlap with v8 changes).
- `workers/mission_bridge.py` — new `HybridWorkerRoleAdapter` implementing
  the canonical `RoleAdapter` protocol, so the existing `MissionLoop`
  dispatches through hybrid workers natively.
- `workers/orchestrator.py` — added worker-crash (Path D) handling:
  durable `worker.crashed` event + partial-state capture + Hermes repair.
- Paths A-F: Hermes primary, bounded fast path (OpenHands READY with
  containment proof), false-success recovery, worker crash, STOP, scope
  escape — all demonstrated by tests.

## Gate matrix (18 items, directive Phase 7)

| item | test | status |
|---|---|---|
| 1 worker abstraction neutrality | test_canonical_hybrid_gate::test_1 | PASS |
| 2 canonical mission-state ownership | test_2 | PASS |
| 3 Hermes isolation | test_3 | PASS |
| 4 OpenHands readiness honesty | test_4 | PASS |
| 5 result-envelope identity accuracy | test_5 | PASS |
| 6 unsupported telemetry prevention | test_6 | PASS |
| 7 false-success rejection | test_7 | PASS |
| 8 evidence reconciliation | test_8 | PASS |
| 9 routing determinism | test_9 | PASS |
| 10 fallback correctness | test_10 | PASS |
| 11 worker-crash recovery | test_11 | PASS |
| 12 STOP behavior | test_12 | PASS |
| 13 scope containment | test_13 | PASS |
| 14 held-out verifier isolation | test_14 | PASS |
| 15 preservation of unrelated owner state | test_15 | PASS |
| 16 no live Hermes mutation | test_16 | PASS |
| 17 no secret leakage | test_17 | PASS |
| 18 no regression of post-baseline canonical functionality | test_18 + full suite | PASS |

Focused worker suite: 97 worker tests (incl. 18-item canonical gate) all
green on current canonical. Full suite: `tests/foundry` EXIT=0, 1124
collected (1020 canonical incl. v8 + 104 worker-layer tests).

## Fresh independent adversarial review (Phase 8)

Fresh isolated reviewer (did not build the integration; read-only posture):

```text
OVERALL: PASS — no in-scope claim disproven; no reproducible defect.
1 additive diff to current canonical        CONFIRMED (only workers/ + tests
                                             + 2 docs + 1 config template)
2 native MissionLoop bridge                 CONFIRMED (HybridWorkerRoleAdapter
                                             drives the real MissionLoop;
                                             false success never reaches PASS)
3 worker-crash recovery (Path D)            CONFIRMED (durable worker.crashed
                                             + partial-state capture + Hermes
                                             repair)
4 OpenHands containment                     CONFIRMED (disposable home +
                                             fail-closed owner diff; zero
                                             violations completed + killed)
5 gate + worker subset                      CONFIRMED (97/97)
5 full suite                                NOT_TESTED by reviewer
                                             (time budget) — re-run to
                                             completion by builder: EXIT=0,
                                             1124 collected
6 no secret leakage                         CONFIRMED
7 no live-state mutation                    CONFIRMED (canonical main only
                                             6 untracked docs; .h clean;
                                             ~/.openhands exactly 6 owner
                                             dirs; transient external noise
                                             documented)
8 STOP semantics                            CONFIRMED (dispatch, queued
                                             fallback, verification)
```

Precision notes recorded by the reviewer (not blockers):

- One optional leg (full suite) was time-interrupted during review; the
  builder re-ran it to completion (EXIT=0, 1124 collected).
- Residual non-`~/.openhands` write path: `tools/browser_use/impl.py` reads
  `%LOCALAPPDATA%` (ms-playwright browser installs). The adapter now
  redirects `LOCALAPPDATA`/`APPDATA` to the disposable home too; a
  browser-using session was out of scope for this step's worker contract,
  so this remains documented residual, not an open containment leak.
- Probe 1 (the UNCONTAINED experiment run) appended a git reflog entry in
  the owner `~/.openhands/cache/skills/public-skills/.git/logs/HEAD` before
  the containment strategy existed. It is a cache-repo reflog side effect;
  the pre-probe reflog content was not backed up, so it is recorded as a
  residual owner-cache metadata note rather than claimed restored. No
  conversation/runtime state from any probe survives in owner state.

## Known defects / residual uncertainty

- `OH_PERSISTENCE_DIR` alone is insufficient (probe 1); containment depends
  on the disposable-home strategy + fail-closed diff. If a future OpenHands
  version stops honoring `USERPROFILE`, the diff backstop still fails
  closed (BLOCKED), never silent leakage.
- OpenHands emits no structured VERDICT line; its verdict stays
  NOT_TESTED and success is judged by JARVIS evidence reconciliation +
  held-out tests (by design).
- Verifier provider independence remains LIMITED (owner DeepSeek-only
  mandate); independent verification uses fresh isolated sessions +
  adversarial artifact inspection.
- The skills-cache reflog modified by probe 1 is a cache side effect of
  the UNCONTAINED run; probe 2/3 (contained) show zero such effects.
