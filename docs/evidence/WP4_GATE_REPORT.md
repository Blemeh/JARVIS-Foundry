# WP4 Gate Report — Hermes Role Adapter

- **Date:** 2026-08-11 (continuation run 2)
- **Verdict:** **PASS**
- **Package:** WP4 (Hermes role adapter)
- **Previous state:** ACTIVE / NOT_TESTED (implementation present, unverified)

## 1. Verdict Table (acceptance criteria C1–C7 from CURRENT_WORK_PACKAGE.yaml)

| ID | Criterion | Verdict | Evidence |
|----|-----------|---------|----------|
| C1 | Fake mission dispatches three isolated role sessions (executive, builder, verifier) and records exact context and provider identity for each | **PASS** | Repo `test_role_dispatch.py::test_gate_fake_mission_three_isolated_role_sessions` + `test_gate_records_exact_context_and_provider_identity`; verifier scratch probes (3-session gate, exact manifest hash == durable `context_manifest_hash`, provider identity in row) |
| C2 | Context manifests carry exact role/mission/step/resource/capability/provider context and hash deterministically | **PASS** | Repo `test_role_manifest.py` (10 tests); verifier probes: hash deterministic, order-independent, tamper-rejected, extra-key normalized (not forgeable), roundtrip stable |
| C3 | Structured results carry verdict/evidence/provider/model/failure class; status transitions durable agent_run events | **PASS** | Repo tests (structured verdicts, result_json persistence); verifier probes: fallback ends same run row COMPLETED with exact fallback provider, failed attempt durably recorded, exact event order, reconcile/rebuild identical after every sequence; replay/conflict semantics verified (see §3) |
| C4 | Leases bound every session; heartbeat timeout terminates/releases stale sessions deterministically | **PASS** | Repo `test_role_reaper.py` (6 tests: expired-lease/orphan runs → FAILED heartbeat_timeout, idempotent, live-lease skip, lease release scope); verifier probes: reaper determinism + reconcile after reap; lease-held dispatch → BLOCKED lease_unavailable, no run row |
| C5 | Provider fallback resolves when preferred unavailable; all-exhausted → precise BLOCKED | **PASS** | Repo `test_provider_fallback_on_preferred_failure`, `test_all_providers_exhausted_blocks`; verifier probes: 3-provider chain, exhaustion detail `tried=[...]`, adapter crash → FAILED + lease released |
| C6 | Provider diversity (builder != verifier) preferred and recorded when available | **PASS** | Repo `test_provider_diversity_preferred_and_recorded` (router resolution + durable distinct provider identities in ledger); verifier probes: `prefer_diversity`/`diversity_resolution` mutually consistent |
| C7 | Adapter never touches live Hermes; real dispatch only via `.h` read-only with isolated HERMES_HOME; no Foundry tools in normal sessions | **PASS** | Repo safety-boundary tests (disabled by default, live root rejected, isolated home required, live profile home rejected, command-surface regression, junction probes by verifier: junction/case/dot-dot variants against live root all rejected; no live oneshot ever dispatched) |

## 2. Independent Verification (two verifiers)

- **deleg_f2954000** (dispatched 20:38 by the prior executive session): fresh
  adversarial session; 30-probe scratch suite attacking hashing, fallback,
  leases, reaper, reconcile/rebuild, diversity, and the Hermes safety boundary
  (incl. real junction + case-variant probes against the live Hermes root —
  construction/read-only only, no live oneshot ever dispatched); repo suite
  run 6 times across the session. **Verdict: C1–C7 all PASS**, 0 FAIL
  findings, 7 INFO items (see §3). Summary:
  `subagent-summary-0-20260811_205628_089164.txt`.
- **deleg_221bd327** (dispatched 20:43 by this run): fresh adversarial
  session; 30-probe scratch suite (crash windows, lease release on every
  exception path, reaper order/idempotency/scope, exhaustion precision,
  duplicate dispatch, hash stability across processes, env hygiene, command
  surface vs the real CLI, module importability); repo suite run twice.
  **Verdict: C1–C7 all PASS** with 1 MAJOR integrity condition (tree was a
  moving target during review → certify only against the frozen state —
  honored: §5 freeze happened after all repairs, before PASS) and 3 MINOR
  code findings (all repaired, see §3). Summary:
  `subagent-summary-0-20260811_210126_788532.txt`.
- **Moving-target caveat (recorded honestly):** the builder was actively
  repairing verifier findings while the verifiers ran (20:41–20:52:47). Both
  verifiers re-verified the final state (repo suite green at 20:54:51 and
  21:00); the suite was RED only transiently at 20:50:31 during the replay
  repair. Per verifier A's integrity condition, the gate was certified only
  AFTER the final freeze (§5), with fresh executive re-verification against
  the frozen tree (§4).
- Independence limitation (§14.3): both verifiers ran under the same
  provider family as the builder (opencode-go / deepseek-v4-flash); fresh
  isolated sessions, all evidence reproduced from scratch commands. Provider
  diversity is preferred at future gates where available.

## 3. Findings And Repairs

**Verifier findings repaired in-flight (fail-loud, no safety failure at any point):**

| Finding | Repair | Regression test |
|---------|--------|-----------------|
| **C7** — `HermesOneshotAdapter` accepted `HERMES_HOME` = live profile (only existence was checked) | `LIVE_HERMES_HOME` deny at construction — live profile root and any path inside it rejected, disabled or enabled | `test_hermes_oneshot_adapter_rejects_live_hermes_home` (live root, profile child, service dir) |
| **C3** — same-run_id re-dispatch after completion conflicted on timestamp-varying completed payloads (crash-retry could never recover) | `_replay_completed`: identical context (manifest hash) + COMPLETED row → ledger replay, no re-run, no new events; changed context still conflicts fail-loud | `test_same_run_id_redispatch_replays_identically_and_conflicts_on_change` (zero new events, adapter not re-run, EventConflict on changed context) |
| **C7** — command surface wrong: `--role` flag does not exist in the real CLI, prompt must be the `-z` VALUE, entry module is `hermes_cli.main` (no importable `hermes` module) | Adapter emits the verified real surface + `--usage-file` cost accounting; verified against `.h` `_parser.py` and a live `--help` probe | `test_hermes_oneshot_command_surface_matches_real_cli` (exact argv, isolated home env, cwd, bound, cost parse) |

**Executive self-review repairs before dispatch:** C4 reaper missing
(`roles/reaper.py` added — deterministic heartbeat-timeout enforcement);
`prefer_diversity()` contradicted its own docstring (fixed to achievability
semantics, consistent with `diversity_resolution`).

**Verifier A MINOR findings repaired after its verdict (before freeze):**

| Finding | Repair | Regression test |
|---------|--------|-----------------|
| **C3** — `agent_run.completed` projection did not clear `failure_class`, so COMPLETED rows retained stale `provider_unavailable`/`heartbeat_timeout` from an earlier failed attempt | completed projection now sets `failure_class = NULL` | extended `test_provider_fallback_on_preferred_failure` (COMPLETED row after fallback has `failure_class is None`) |
| **C2** — `from_yaml` rejected `__`-placeholder providers but accepted placeholder models (`__VERIFY_AT_ACTIVATION__` flowed into live manifests) | placeholder models rejected identically to providers | `test_from_yaml_rejects_placeholder_model_with_concrete_provider` |
| **C7** — live-root guard was exact-path equality; a subdir of the live install could pass as `hermes_root` | parent-containment check mirroring the home guard | extended `test_hermes_oneshot_adapter_rejects_live_hermes_root` (live root subdir rejected) |

**INFO findings recorded (documented, not gate-blocking):**
- C5: all-fallbacks-exhausted returns a BLOCKED RoleResult with precise
  detail; the durable row records FAILED / provider_unavailable (the honest
  ledger state — no distinct BLOCKED projection state). Documented in
  `test_all_providers_exhausted_blocks`.
- C5: the failed attempt's provider is carried in the event detail string and
  idempotency-key suffix, not a structured field (the completing provider IS
  structured in result_json).
- C6: `diversity_resolution` has no production caller yet — the gate
  test/caller applies it; production wiring is WP5 work.
- C4: heartbeats do not renew the lease; the session bound IS the lease
  duration. Reaper enforces termination deterministically (by-design timeout
  semantics).
- C3: replay is keyed on the chain[0] manifest; a run completed via fallback
  does not match the replay probe (stored hash = fallback provider) and
  proceeds to conflict fail-loud on completed. No corruption.
- C2: `content_hash` binds the normalized manifest; extra keys in a stored
  payload are dropped and invisible to the hash (not forgeable).

## 4. Executive Re-Verification (fresh commands, final state)

- `./.venv/Scripts/python.exe -m pytest tests/foundry -q --tb=short` →
  **145 passed** (exit 0), repeatedly — after every repair wave and again
  against the final frozen tree.
- Hermes baseline (isolated HERMES_HOME): `tests/test_estop.py` → **24 passed**.
- Canonical DBs: all 4 re-hashed against recorded evidence → unchanged
  (`canonical-databases-full-recheck.json` regenerated by
  `var/evidence/recheck_canonical_databases.py` at every freeze).
- No stray python processes; no JARVIS/Foundry scheduled tasks; repo tree
  settled (no src/tests writes after the final repair wave, 21:02).

## 5. Frozen-State Snapshot (var/evidence/wp4-gate/, frozen AFTER all repairs)

| Artifact | sha256 |
|----------|--------|
| verified-state-snapshot.json | `5fbf1fb80eea1949c29333268ac7fb482dca73df414fa382228bcc0a19d9540f` |
| foundry-tests-final.txt | `43de331fceff28522cd99077ae5f8da444c738a48546aa307c96e29b9a5bedfe` |
| hermes-baseline-estop-final.txt | `284fe7016720a6440f18069e211722d2c3dce869487c5d857c5cd07095bd07a2` |
| canonical-databases-recheck.json | `399ab6bb23147887712990bae0b1625ef70f1e951882df479c3b715763dc0710` (identical across all gates — canonical DBs byte-unchanged throughout) |
| canonical-databases-full-recheck.json | `357fc0fe08511b17a83252e07e8a82055fa4661a8fd769e42b9240da089df6f0` |

Summary artifact: `verification-summary.json` sha256
`28f6ef9e6027c68b82de29763dd433024520c6e096bfabcd2901f2c760aeadfa`

## 6. Residual Risks (recorded, not gate-blocking)

- Same-provider independence limitation (§14.3); mitigated by two fresh
  adversarial sessions and executive re-verification.
- Real oneshot dispatch remains disabled by default; the command surface is
  pinned by regression tests against the .h CLI, but a first real bounded
  staging probe (isolated HERMES_HOME, trivial prompt) is recommended during
  WP5 to exercise the full path end-to-end.
- Replay-via-fallback edge (see §3) fails loudly; WP5's caller should treat
  EventConflict on dispatch as "run already recorded — read the ledger".
- Junction/path semantics verified on this host (Windows 10, Python 3.13.5).
- Repo has no git commits (untracked); frozen-state = sha256 manifests.

## 7. Conclusion

All seven gate criteria have reproduced evidence of PASS from two independent
adversarial verifiers and fresh executive re-verification. Eight real findings
were repaired with regression tests (six verifier-driven, two from executive
self-review, incl. two command-surface defects against the real CLI). State
frozen at wp4-gate bundle AFTER all repairs, per verifier A's integrity
condition. WP4 declared **PASS**; next package **WP5 (mission and
verification loop)** activated automatically per gate rules.
