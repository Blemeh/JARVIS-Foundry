# Evidence Manifest — V2 OPERATIONAL GATE (Phase 5, sanitized)

Committed 2026-08-12 · candidate HEAD `eca0124` (tree clean at freeze).
This manifest is the durable, privacy-safe index for the gate's
evidence. Private/local artifacts stay under `var/evidence/` (gitignored)
and are referenced by sha256 — their loss or modification is detectable.

## Candidate

- HEAD (round 1): `eca0124a3e2622cb0bea707a6341faffac9ff795`
- HEAD (round 2, after C8/C9 repairs): `41eb9c6` — tree clean at freeze
- HEAD (round-2 verification): `4083fd9` — tree clean start→end
- Suite: 378 tests, EXIT=0 (command:
  `./.venv/Scripts/python.exe -m pytest tests/foundry -q --tb=short`;
  collect count: `pytest --collect-only -q | grep -oP ': \d+$' |
  awk -F': ' '{s+=$2} END {print s}'` → 378)

## Commands run (exit codes)

| Command | Exit | Output |
|---|---|---|
| pytest tests/foundry (full suite) | 0 | 378 tests (dots) |
| var/evidence/gate_v2_operational.py | 0 | gate-run-20260812T195113.txt |
| reconcile() on live DB | consistent=True | 0 mismatches (5/5 runs) |
| live initialize() v10→v16 | 0 | before/after recorded in artifact |

## Artifact hashes (sha256)

- `var/evidence/v2-operational-gate/gate-run-20260812T195113.txt` →
  `c0bfe268a55751b024442e7c658ba9ce67c21d40a741260025bf122c0e5e917b`
- `var/evidence/v2-operational-gate/gate-run-20260812T202215.txt` →
  `f843c31edab3959f197d123fc1499020d0dac5201d9e826ee624546a3cd62803`
  (round-2 artifact; round-1 artifact `gate-run-20260812T195113.txt` →
  `c0bfe268a55751b024442e7c658ba9ce67c21d40a741260025bf122c0e5e917b`)
- `var/evidence/v2-operational-gate/verdict-round1-deleg_886d2cd4.json` →
  `05c49d0c90e2ac128e63a92141a0934d6fe3a55df39ffaa5fe51c056fea52f5a`
  (round-1 FAIL: C8 agent_run trace absent, C9 pristine-bytes disproven)
- `var/evidence/v2-operational-gate/verdict-round2-deleg_303dac65.json` →
  `38c64c7242197131348c5ab992e59498f2d5b1b8a4ff8e3267b1b5c7b2fa5fa0`
  (round-2 **PASS 16/16**; C8/C9 repairs A/B-discriminated; tree clean
  start→end at 4083fd9)
- `var/evidence/wp10-slice2/suite-slice2.txt` →
  `5cf4bee5ed475d137766be227fffbb42f6025a2e2b9d20823cd7d3d8ad8d8147`
- `var/evidence/wp10-slice2/verdict-round1-*.json` →
  `52297273cbcdeedcfc89bb0f097b1881c6d4525845a59a5f96491d9c34ffba0f`
- `var/evidence/wp10-slice2/verdict-round2-*.json` →
  `8db8209e86240f7ae5313b26f1354cec27a03e571d8c506b4cfb2b71ba2a67b1`
- Live-DB snapshot copies created during gate work: under
  `var/evidence/v2-operational-gate/snapshots/` (rollback demo
  snapshots; verify with verify_snapshot).

## Verifier identity

- Gate verifier: <PENDING — fresh adversarial session dispatched against
  HEAD eca0124>. Provider: same model family as the builder (recorded
  limitation; compensated by deterministic probes + A/B discrimination).

## PASS/FAIL lineage

- Slices A–G: implementation commits 80d3c38, dd28e00, cdb05e8, 4a910b9,
  3eed244, 6d464c9, 07a36b7; gate-prep fixes eca0124 (run_id scheme,
  per-source budgets, reconcile WAL snapshot).
- FAILs recorded this gate: ingestion_queued UNIQUE (fixed), shared
  budget starvation (fixed), reconcile/daemon race (fixed + regression),
  run_id scheme conflicts (fixed; live re-runs record them as errors —
  atomic rollbacks, zero corruption).
- WP10 slice-2 lineage: round-1 FAIL (5639) → repair 89b7ca4 → round-2
  PASS (5830–5831).

## Repairs (with regression coverage)

1. `mission.completed` pre-v11 guard + `mission.created` clean validation
   (test_discovery_monitors +2).
2. Per-line ingestion run_ids (stable dedup payloads) + per-source cycle
   budgets (test_live_ingestion 12).
3. Reconcile single-WAL-snapshot read (test_reconcile concurrent-writer
   regression).

## Remaining uncertainty

- Live knowledge data has no contradiction pairs yet (surfacing
  test-verified; envelope returns empty lists).
- Live re-runs of ingestion report idempotency conflicts for claims
  recorded under the pre-fix run_id scheme (documented migration
  artifact; fresh DBs are conflict-free per the suite).
- Worker support semantics: claim refs carry line hashes; file-hash
  comparisons by a worker correctly yield UNSUPPORTED; the authoritative
  support check is the vault-index cross-reference.
- Same-provider verifier limitation.

## Private evidence references (not committed)

- `var/evidence/v2-operational-gate/` — gate-run artifacts, snapshots.
- `var/evidence/wp11-slice-*/` — per-slice test artifacts.
- `var/evidence/wp10-slice2/` — slice-2 verdict JSONs + suite artifact.
- Live DB: `C:\Users\b\AppData\Local\hermes\foundry\foundry.db`
  (private; hash-change detectable via this manifest's references +
  ledger event counts recorded in the gate-run artifact).
