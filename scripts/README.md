# Deterministic Tooling

This directory contains autonomous deterministic tooling such as read-only
preflight checks, schema validation, STOP controls, backup checks, and
acceptance fault injection.

The unattended acceptance command is:

```text
python scripts/unattended_acceptance.py --profile ACCELERATED_12H_EQUIVALENT
```

Use `--profile SHORT_REAL_TIME` for the bounded wall-clock disposable proof.
The `FULL_12H`, `FULL_24H`, and `REBOOT_ASSISTED_24H` profiles are declarations
only until an owner explicitly supplies `--allow-long-profile`.

After `START BUILD`, a script may become a startup task, scheduled task, or
background process when its evidence gate, STOP behavior, recovery, and
independent verification pass. No per-script owner permission is required.
