# Promotion workflow

Use only when the active authenticated owner goal explicitly authorizes release
integration, promotion, deployment, or activation.

Before mutation, verify:

- exact candidate, canonical, origin, install, database-authority, and runtime
  identities;
- clean ancestry and worktrees, remote drift, and intended diff;
- schema compatibility, migration/restore path, release packaging, rollback,
  STOP, Supervisor singleton, and Scheduler boundaries;
- required focused/broad tests, independent verification, and rehearsal
  evidence for the change risk.

Promote the exact product SHA through the repository's release authority. Keep
tooling/evidence SHAs distinct. After activation, prove source = install =
database release authority = running runtime, then health, reconciliation,
critical flows, and rollback readiness.

Never manually set `TRUSTED`, hide provider/reboot/endurance gaps, force-push,
or mutate a dirty ambiguous checkout. On remote drift or identity mismatch,
freeze and reconcile before continuing.
