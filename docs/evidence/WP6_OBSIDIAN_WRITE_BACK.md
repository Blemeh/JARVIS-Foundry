# WP6 Evidence — Governed V1 Obsidian Write-Back

Status: **PASS** (gate closed 2026-08-11, continuation run)
Updated: 2026-08-11

## Gate Close Record

- Independent verifier deleg_d32753dd (156-check adversarial scratch suite,
  full suite reproduced): **PASS on E1–E7**, zero gate-failing findings.
  Two defense-in-depth observations hardened before the freeze (restore
  containment, atomic conflict preservation).
- Executive re-verification: full suite **180 passed** fresh; Hermes estop
  baseline 24 passed; canonical DBs byte-unchanged; schema v4 confirmed.
- Frozen snapshot: var/evidence/wp6-gate/ (snapshot sha256
  `8227654afa663eb3e9ec97be0b532b4a55fae5ad8188f0789f98ce8466b40150`).
- Full detail: `docs/evidence/WP6_GATE_REPORT.md`.

## Objective (CURRENT_WORK_PACKAGE.yaml)

JARVIS can project mission state into the V1 Obsidian area with the
documented frontmatter contract and the full write protocol — expected hash,
before snapshot, atomic replace, parse check, after hash, audit event,
rollback pointer — so a concurrent edit or a crash can never lose meaning or
bytes, and every write is recoverable.

## Implementation

| Module | Responsibility |
|--------|----------------|
| `src/jarvis_foundry/obsidian/policy.py` | `ObsidianWriter`: vault boundary (write only under `<vault>/JARVIS/V1`), governed note set (`00 - V1 State.md` … `07 - Daily State.md`), Audit/ area managed by the writer, whole-vault READ allowed (containment-checked), hand-rolled frontmatter parser enforcing the contract (origin: jarvis, current_steward: jarvis, system: jarvis-foundry, version: v1) |
| `src/jarvis_foundry/obsidian/snapshots.py` | `SnapshotStore`: byte-exact before/after snapshots under `<write_root>/.jarvis/snapshots/`, atomic writes and restores, sha256 helpers |
| `src/jarvis_foundry/obsidian/writer.py` | `ObsidianService`: transactional write (expected hash check -> before snapshot -> obsidian.write_started -> atomic replace -> parse check -> after hash -> obsidian.write_completed); concurrent-edit detection raises `ObsidianConflictError` (note untouched, incoming version preserved under `Audit/conflicts/`, obsidian.conflict event); `rollback` restores the before bytes exactly (obsidian.rolled_back with rollback pointer) and refuses to clobber a newer edit (after_hash guard) |
| `src/jarvis_foundry/migrations.py` v4 | `obsidian_writes` projection table (write_id, note, status STARTED/COMPLETED/CONFLICT/ROLLED_BACK, hashes, snapshot/conflict/rollback pointers) |
| `src/jarvis_foundry/projections.py` | obsidian.write_started / write_completed / conflict / rolled_back handlers; table in rebuild/reset list |
| `src/jarvis_foundry/database.py`, `reconcile.py` | obsidian_writes row-lookup allowlist + reconcile key map |

## Write Protocol (03 spec §13)

Each write: expected hash → before snapshot → atomic replace → parse check →
after hash → audit event → rollback pointer. A caller-supplied expected hash
that differs from the on-disk state means a concurrent edit: the note on disk
is left untouched, the incoming bytes are preserved under
`Audit/conflicts/`, and an obsidian.conflict event records both hashes —
meaning AND bytes survive. Rollback restores the before snapshot byte-exactly
and refuses when the note changed again since our write.

## Safety Invariants

- Writes confined to the V1 write root; note names from the governed set;
  traversal/absolute/audit-name writes rejected before any mutation.
- Whole-vault reads allowed; read paths containment-checked against the vault.
- The real vault (`C:\Users\b\Blendi`) is never referenced by tests or src as
  a write target — all fixtures are temp vaults.
- Snapshots live inside the JARVIS-owned `.jarvis/` area of the write root.

## Test Inventory (180-test suite; WP6 = 13 new)

- `test_obsidian_write.py` (7): note set + Audit/ creation, frontmatter
  contract enforcement (no mutation on violation), transactional protocol
  events + hashes + snapshot, conflict detection preserving both versions
  (raise + write_id + preserved_path), write boundary matrix, vault read
  allowed / write restricted, rebuild_projections + reconcile consistency.
- `test_obsidian_rollback.py` (6): byte-exact restore + rolled_back event,
  clobber guard (note changed since write → refused), unknown write refused,
  conflicted write refused, restore containment (outside write root refused),
  rollback + rebuild consistency.

## Verification

- Independent verifier deleg_d32753dd: **PASS on E1–E7** (156-check scratch
  suite; summary `subagent-summary-0-20260811_223955_463451.txt`); two
  observations hardened (restore containment, atomic conflict preservation).
- Full suite: **180 passed** (fresh reproduction, frozen state).

## Remaining Work

- WP6 gate closed (PASS). Next: WP7 (V1 integration and activation) —
  installer/uninstaller for the V1 Supervisor task, CLI, tray, config,
  Foundry profile; clean install → disabled state → activation → restart →
  STOP → resume → uninstall reproducible; real installation only after the
  gate passes.
