# JARVIS FOUNDRY — CURRENT CAPABILITY MATRIX

Single authoritative capability status. Classifications:
`IMPLEMENTED_AND_PROVEN` · `IMPLEMENTED_UNPROVEN` · `PARTIAL` · `MISSING` · `SUPERSEDED`.

Evidence roots: `docs/evidence/`, `evidence/`, git history, live runtime state.
Current HEAD reference: `657519c` (full hash and promotion evidence in `docs/convergence/`; documentation
closure is a descendant).

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
| Obsidian integration | V1 | vault write-back, indexing, snapshots | WP6, WP11; live connector-root | IMPLEMENTED_AND_PROVEN | vault is external (read-only canonical boundary) |
| Autonomous continuation | V7 | standing mandate, auto-continuation, continuity slices | V7_S12, V7_S13 FINAL GATE | IMPLEMENTED_AND_PROVEN | owner START/STOP authority preserved |
| Provider routing | V4 | model routing + provider identity, family rules | V4_MR1, V7_S11 | IMPLEMENTED_AND_PROVEN | single-family verification pool |
| Resource registration | V1 | discovery + registration, resource scoring | WP8_RESOURCE_SCORING, V7_S4 | IMPLEMENTED_AND_PROVEN | none |
| External actions | V5 | connectors + effects + broker | V5_S4/S6 | IMPLEMENTED_AND_PROVEN | platform-specific connectors |
| Command Center | V4 | monitors, scoring, capability states, support semantics | V4 gate + V7_S8 | IMPLEMENTED_AND_PROVEN | UI surface limited |
| Voice | — | voice_interactions table exists; no full voice pipeline | migrations | PARTIAL | end-to-end voice UX |
| Multi-system operation | V5 | systems registry + broker | V5 slices | IMPLEMENTED_AND_PROVEN | single host |
| Self-evolution | V6 | builder/lab/verifier, shadow, canary, promote, restore, discovery, cycles | V6_S0–S13 | IMPLEMENTED_AND_PROVEN | none |
| Candidate/live promotion | V6 + V8 | release registry, promotion machinery; V8 canonical promotion executed | V6_S8, V8_CANONICAL_PROMOTION_REPORT | IMPLEMENTED_AND_PROVEN | none |
| Rollback / recovery | V6 + V8 | restore points, restore-from-backup, rollback drills | V6_S9, rollback drill (pre+post promotion) | IMPLEMENTED_AND_PROVEN | reverse migration not provided (by design) |
| Distributed/multi-machine operation | North star | not present | — | MISSING | genuine next-stage candidate |
| Letta runtime integration | rejected (NATIVE_JARVIS) | not present; concepts embodied natively | V8 final verdict | SUPERSEDED (decision: native) | none |
| Governed perception input (JARVIS Eyes) | Phase 2 | Screenpipe external sidecar -> privacy firewall -> normalize -> dedup -> aggregate -> significance -> `perception.observed` (read-only connector `screenpipe-local`) | PHASE2_JARVIS_EYES gate docs + fault matrix 20/20 + full suite EXIT=0 | IMPLEMENTED_AND_PROVEN | raw frame PROBING; audio disabled; continuous perception not yet activated (Phase 6) |
| Deterministic Workflow Discovery | Phase 3 | Frozen Phase-2 `PerceptionObservation` -> deterministic sessions -> safe structural tokens -> versioned fingerprint/similarity -> repeated evidence -> existing candidate generator/scorer bridge | `docs/evidence/PHASE3_WORKFLOW_DISCOVERY/` (36/36 focused; 1,202 full suite; F1-F25; independent review; bounded 10k probe) | IMPLEMENTED_AND_PROVEN | disabled by default; no continuous mining, computer control, audio, candidate approval, or mission creation |
| Governed Procedural Learning | Phase 4 | Qualified OpenAdapt 1.12.1 / Flow 1.31.0 bundle -> complete JARVIS artifact-byte and version guards -> reference-only Tier-C procedure -> existing ActionEnvelope/capability/connector/effect reconciliation -> independent local browser oracle and V6 zero-effect shadow bridge | `docs/evidence/PHASE4_PROCEDURAL_LEARNING/` (23/23 focused; 1,225 full suite; F1-F24; fresh real local replay; rollback; fresh independent review) | IMPLEMENTED_AND_PROVEN | browser substrate disabled by default; native Windows desktop remains PROBING; no continuous learning or Phase 5 promotion loop |
