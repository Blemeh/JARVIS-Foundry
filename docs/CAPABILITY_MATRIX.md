# JARVIS FOUNDRY — CURRENT CAPABILITY MATRIX

Single authoritative capability status. Classifications:
`IMPLEMENTED_AND_PROVEN` · `IMPLEMENTED_UNPROVEN` · `PARTIAL` · `MISSING` · `SUPERSEDED`.

Evidence roots: `docs/evidence/`, `evidence/`, git history, live runtime state.
Current work reference, reconciled 2026-08-28:
the isolated convergence branch is `candidate/canonical-convergence-20260828`,
starting from recovery base `58baca5e7feccf2507614cde75d641bddc15ed98`.
Canonical `main` is `8dfb981876985525621cf10b8b9b67e34468b450`; fetched
`origin/main` is `fe0592991e98de3f5de27c53d40339f4281156f1`. The canonical
checkout is dirty and untouched by this lane. This candidate contains the
reconciled backend projection and the cinematic Command Center/NavRail surface;
its source, UI, test, and lane-classification evidence is recorded in
`docs/evidence/integration/canonical-convergence-20260828/RECONCILIATION.md`.
This lane makes no live or production claim and did not restart, migrate,
deploy, or otherwise mutate a runtime, database, Scheduler task, or remote.

Historical V456 stage (2026-08-24), retained for lineage:
canonical `main`/`origin/main` at `08dad95` (V4/V5/V6 plus cinematic Command
Center, migration-script repair, fresh operational evidence, and source
promotion). The deployed runtime remains immutable release
`769715a7f8aee22bf4ab2db8870c37e6e9c9ef1b`, schema v34; this stage did not
restart, migrate, deploy, or otherwise mutate production. Fresh source evidence:
repository suite 1,981 tests / 0 failures / 0 errors / 1 explicit skip;
V3 hard focused/supporting/migration/independent checks PASS; bounded soak PASS
with the four-hour proving window NOT_TESTED; Command Center 115 tests,
product 212/212, build, and security PASS. Live promotion remains blocked by
the legacy runtime boundary. Disposable release-factory run
`C:\temp\JARVIS-Overnight-Release-Candidate\evidence\release\overnight\20260824-v456-final\RUN.json`
is PASS with provider mode DISABLED and no scheduler installation.

## Audit-Truth Remediation (2026)

| Capability family | Current implementation | Current proof | State | Boundary |
|---|---|---|---|---|
| Duplicate launch authority | Supervisor lease ownership plus `LeaseUnavailable` fail-closed launcher exit | focused duplicate-launch regression; live singleton lease observed | IMPLEMENTED_UNPROVEN_AT_LIVE_HEAD | restart/promotion proof pending |
| Release identity/provenance | Explicit synthetic release ID plus validated actual Git commit and tree in `RELEASE.json` | focused malformed/missing provenance regressions | IMPLEMENTED_UNPROVEN_AT_LIVE_HEAD | currently deployed legacy manifest lacks new fields |
| Release authority | Filesystem CURRENT/PREVIOUS canonical; DB is projection/audit cache; doctor reports mismatch status | `FILESYSTEM_ONLY_AUTHORITY` doctor snapshot and focused tests | IMPLEMENTED_AND_PROVEN | no manual DB reconciliation is permitted |
| PID ledger reconciliation | Startup/heartbeat liveness + process creation identity + JARVIS ownership checks; unknown PIDs are never killed | stale-entry and PID-reuse regressions | IMPLEMENTED_UNPROVEN_AT_LIVE_HEAD | live restart proof pending |
| Scheduler routing | One production Supervisor task; Worker-Loop classified `LEGACY_DISABLED` and fail-closed against re-enable | task snapshot plus installer/fail-closed regressions | IMPLEMENTED_UNPROVEN_AT_LIVE_HEAD | post-promotion scheduler smoke pending |
| Headless console behavior | Reviewed preserved Windows subprocess/installer path without dead wrapper API | focused lifecycle/installer regressions | IMPLEMENTED_UNPROVEN_AT_LIVE_HEAD | visible-window/live restart proof pending |
| Test fixture host safety | Session-diff guard permits cleanup only for JF-WP7Gate tasks created by current test session | scheduler-guard regression suite | IMPLEMENTED_AND_PROVEN | integration staging remains opt-in by design |
| Owner-goal deletion governance | Authenticated GOAL directives in current executive/goal system supersede deleted admission path | current executive implementation/tests and historical audit classification | SUPERSEDED | do not resurrect old code wholesale |
| Release-handoff deletion governance | Immutable CURRENT/PREVIOUS release authority supersedes deleted handoff mechanism | current deployment/release tests and live pointers | SUPERSEDED | old capability must not be claimed as active |

## Post-V7 Remaster Convergence

| Capability family | Canonical current implementation | Current proof | State | Boundary |
|---|---|---|---|---|
| R7 goals, portfolio, research, provenance, lineage, honest waiting | mission/evolution/discovery/knowledge surfaces | `CONVERGENCE_CURRENT_HEAD_JUNIT.xml` (R7 gates) | `IMPLEMENTED_AND_PROVEN` | live provider research and elapsed proving remain `WAITING` |
| R8 inspect, dispatch, evidence, verify, repair, reconcile, continue | Hybrid Workers + mission loop + runtime worker loop | R8 convergence gate and disposable deployment/restore canary | `IMPLEMENTED_AND_PROVEN` | live worker-loop activation remains `DISABLED` |
| R9 capability lifecycle, scope, credential/financial denial, effects | capabilities, connectors, capability broker | R9 gate plus focused redaction tests | `IMPLEMENTED_AND_PROVEN` | real external effects remain `NOT_TESTED` |
| R10 command center, durable controls, EN/SQ briefing semantics | command center, broker, briefings, `convergence/briefings_l10n.py` | R10 gates, local controls, and disposable accessibility audit | `IMPLEMENTED_AND_PROVEN` | live voice/provider and production UI inspection remain `NOT_TESTED` |
| R11 governed evolution and Supervisor authority | canonical `evolution/**` and `supervisor/authority.py` | R11 authority/evolution gates | `IMPLEMENTED_AND_PROVEN` | live elapsed promotions remain `WAITING` |
| R12 fault, freeze, rollback, restore | integrated rollback and adapted fault campaign | R12 gate, restore canary, independent verifier | `IMPLEMENTED_AND_PROVEN` | physical/unavailable-provider rows remain `NOT_TESTED/BLOCKED` |

| Capability | Original target | Current implementation | Evidence | State | Remaining gap |
|---|---|---|---|---|---|
| Durable autonomous runtime | V1 | Supervisor + heartbeat loop + lease + WAL SQLite | WP0–WP8 gate reports; live RUNNING | IMPLEMENTED_AND_PROVEN | none |
| Deterministic Supervisor | V1 | `supervisor/` (service, run, leases, estop), independent of Hermes | WP2, V7_S3; live single lease owner | IMPLEMENTED_AND_PROVEN | none |
| STOP / emergency stop | Permanent invariant | EmergencyStop sentinel + STOP_EVERYTHING/PAUSE directives; takeover-safe (9cfd675) | V8 promotion certifier P8; live OFF | IMPLEMENTED_AND_PROVEN | none |
| Mission persistence | V1 | missions table, mission loop, mission bridge | WP5, V7_S5; live 3 COMPLETE + 1 PLANNED | IMPLEMENTED_AND_PROVEN | none |
| Action selection | V1 | action broker + action policy (deterministic gates) | test_action_broker/policy | IMPLEMENTED_AND_PROVEN | none |
| Worker dispatch | V1 → V8+ | worker orchestrator/router + worker abstraction (Hermes + OpenHands adapters) | CANONICAL_HYBRID_WORKER_INTEGRATION_GATE (18/18); 97 worker tests | IMPLEMENTED_AND_PROVEN | additional adapters on demand |
| Independent verification | Permanent invariant | verifier isolation, fresh sessions, no builder self-certification | V6_S5, V8 certifier rounds, worker gate review | IMPLEMENTED_AND_PROVEN | provider diversity (REDUCED) |
| Repair loop | V1 | diagnose→repair→regression→re-verify in missions and V6 loop | V6 slices, V8 fail loops | IMPLEMENTED_AND_PROVEN | none |
| Event reconciliation | V1 | reconcile() + rebuild_projections + doctor | test_reconcile; live consistent=True | IMPLEMENTED_AND_PROVEN | none |
| Hybrid Memory (Tier C) | V8 (Letta influence, native) | lifecycle/stamps, blocks, recall, procedures, curation, defense, git versioning, compiler; schema v33 | V8_CANONICAL_PROMOTION_REPORT; live canary; 41 tests | IMPLEMENTED_AND_PROVEN | semantic recall hook reserved |
| Knowledge claims/evidence | V2 | knowledge_claims (freshness/contradictions), evidence bundles, retrieval manifests | WP9–WP12 gates, V2/V3 operational gates | IMPLEMENTED_AND_PROVEN | none |
| Obsidian integration | V1 | vault write-back, indexing, snapshots, bounded graph/note projection | WP6, WP11; candidate vault graph contract | IMPLEMENTED_AND_PROVEN | vault is external (read-only canonical boundary) |
| Autonomous continuation | V7 | standing mandate, auto-continuation, continuity slices | V7_S12, V7_S13 FINAL GATE | IMPLEMENTED_AND_PROVEN | owner START/STOP authority preserved |
| Provider routing | V4 | model routing + provider identity, family rules | V4_MR1, V7_S11 | IMPLEMENTED_AND_PROVEN | single-family verification pool |
| Resource registration | V1 | discovery + registration, resource scoring | WP8_RESOURCE_SCORING, V7_S4 | IMPLEMENTED_AND_PROVEN | none |
| External actions | V5 | connectors + effects + broker | V5_S4/S6 | IMPLEMENTED_AND_PROVEN | platform-specific connectors |
| Command Center | V4 | monitors, scoring, capability states, owner controls, briefings, cinematic core, NavRail, bounded vault graph | V4/V7 gates + candidate browser/product suites | IMPLEMENTED_AND_PROVEN | live provider/runtime inspection remains separate |
| Voice | — | voice_interactions table exists; no full voice pipeline | migrations | PARTIAL | end-to-end voice UX |
| Multi-system operation | V5 | systems registry + broker | V5 slices | IMPLEMENTED_AND_PROVEN | single host |
| Self-evolution | V6 | builder/lab/verifier, shadow, canary, promote, restore, discovery, cycles | V6_S0–S13 | IMPLEMENTED_AND_PROVEN | none |
| Candidate/live promotion | V6 + V8 | release registry, immutable release identity, disposable promotion/rollback harness | V6 focused gate, `scripts/release_identity_repro.py` PASS, source suite | IMPLEMENTED_UNPROVEN | live release is schema v34/source-only manifest; no post-stage migration/restart/provider proof |
| Rollback / recovery | V6 + V8 | restore points, restore-from-backup, rollback drills | V6_S9, rollback drill (pre+post promotion) | IMPLEMENTED_AND_PROVEN | reverse migration not provided (by design) |
| Distributed/multi-machine operation | North star | not present | — | MISSING | genuine next-stage candidate |
| Letta runtime integration | rejected (NATIVE_JARVIS) | not present; concepts embodied natively | V8 final verdict | SUPERSEDED (decision: native) | none |
| Governed perception input (JARVIS Eyes) | Phase 2 | Screenpipe external sidecar -> privacy firewall -> normalize -> dedup -> aggregate -> significance -> `perception.observed` (read-only connector `screenpipe-local`) | PHASE2_JARVIS_EYES gate docs + fault matrix 20/20 + full suite EXIT=0 | IMPLEMENTED_AND_PROVEN | raw frame PROBING; audio disabled; continuous perception not yet activated (Phase 6) |
| Deterministic Workflow Discovery | Phase 3 | Frozen Phase-2 `PerceptionObservation` -> deterministic sessions -> safe structural tokens -> versioned fingerprint/similarity -> repeated evidence -> existing candidate generator/scorer bridge | `docs/evidence/PHASE3_WORKFLOW_DISCOVERY/` (36/36 focused; 1,202 full suite; F1-F25; independent review; bounded 10k probe) | IMPLEMENTED_AND_PROVEN | disabled by default; no continuous mining, computer control, audio, candidate approval, or mission creation |
| Governed Procedural Learning | Phase 4 | Qualified OpenAdapt 1.12.1 / Flow 1.31.0 bundle -> complete JARVIS artifact-byte and version guards -> reference-only Tier-C procedure -> existing ActionEnvelope/capability/connector/effect reconciliation -> independent local browser oracle and V6 zero-effect shadow bridge | `docs/evidence/PHASE4_PROCEDURAL_LEARNING/` (23/23 focused; 1,225 full suite; F1-F24; fresh real local replay; rollback; fresh independent review) | IMPLEMENTED_AND_PROVEN | browser substrate disabled by default; native Windows desktop remains PROBING; no continuous learning or Phase 5 promotion loop |
