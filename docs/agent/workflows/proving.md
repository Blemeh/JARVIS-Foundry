# Proving workflow

Use for runtime, provider, recovery, endurance, reboot, Scheduler, performance,
or trust evidence collection.

Default mode is observation-only. Establish explicit allowed mutations before
any bounded hard-kill, reboot, recovery, provider call, or fault injection.

1. Capture source, install, release, database, runtime, process, Scheduler,
   provider, STOP, and trust identities independently.
2. Use read-only database and filesystem paths; instrument reads before
   claiming side-effect freedom.
3. Preserve timestamps, hashes, commands, raw results, sampling windows, and
   process lineage.
4. Distinguish short health checks, elapsed endurance, real provider effects,
   restart recovery, physical reboot proof, rollback, and restored trust.
5. Classify evidence truthfully as `PASS`, `FAIL`, `BLOCKED`, `NOT_TESTED`,
   `PENDING`, `UNKNOWN`, or `SUSPENDED` as applicable.
6. If evidence reveals a source defect, report and route it to a separate build
   task; do not patch during an observation-only proving session.

HTTP success, fixtures, configured credentials, candidate tests, or simulated
UI do not prove production effects or trust.
