# Backup And Restore Contract

Status: DESIGN ONLY - DESTINATIONS NOT YET REGISTERED

## 1. Protected State

V1 backup scope eventually includes:

- Foundry SQLite database, WAL-safe snapshot, and schema version;
- append-only event and evidence artifacts;
- configuration with secrets excluded;
- start event, standing mandate, resource records, capability states, and
  owner vetoes;
- governed JARVIS/V1 Obsidian notes and hashes;
- registered worktree commit and dirty-state metadata;
- release identity and executable hash.

## 2. Backup Rules

- Snapshot only through a database-consistent method.
- Encrypt private backup content using a verified mechanism selected during
  autonomous capability discovery.
- Keep credentials outside the backup or under a separate credential policy.
- Record content hashes and a manifest.
- Use bounded retention with at least one restore point independent from the
  active machine state before consequential acceptance testing.
- A backup is not accepted until restored into an isolated location.

## 3. Restore Order

1. Freeze dispatch and capabilities.
2. Preserve current failed state for analysis.
3. Select the newest verified compatible restore point.
4. Verify manifest, hashes, schema, and release compatibility.
5. Restore into isolation.
6. Run integrity and projection-rebuild checks.
7. Reconcile repository and Obsidian hashes.
8. Independently verify expected mission, capability, veto, and STOP state.
9. Switch active state only after PASS.
10. Keep execution frozen until owner/policy resume conditions are satisfied.

## 4. Semantic Verification

Restore success is not `files exist`. It must prove:

- accepted evidence and decisions remain linked;
- beliefs retain confidence and provenance;
- mission and step states are legal;
- no completed action becomes pending again;
- no vetoed or stopped capability becomes active;
- Obsidian projections match the database state;
- duplicate briefings and mutations will not occur after resume.

## 5. Required Drills

- normal database restore;
- restore after incomplete WAL/event write;
- restore with old executable and newer schema candidate;
- Obsidian conflict and snapshot restore;
- worktree rollback with uncommitted user changes protected;
- missing or corrupt latest backup with fallback to prior point;
- low-disk recovery.
