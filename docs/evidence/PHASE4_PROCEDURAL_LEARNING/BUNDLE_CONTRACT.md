# Phase 4 Bundle and Tier-C Contract

The executable OpenAdapt artifact remains outside Tier C. JARVIS adopts it by
content hash and records a reference-only metadata body.

Required frozen identity:

- procedure ID and source Workflow Discovery/candidate/mission evidence;
- OpenAdapt `1.12.1`, OpenAdapt-Flow `1.31.0`, and JARVIS bundle contract
  `1.0.0`;
- OpenAdapt manifest schema `2`, content digest, manifest hash, and every
  listed file hash;
- input/parameter schema, preconditions, expected effects, verification plan,
  required capability, risk class, creation time, and lineage;
- executor reference `openadapt-flow` with authority `jarvis.action_envelope`.

`verify_bundle()` rejects missing files, path traversal, hash changes, unknown
manifest schema, unknown JARVIS bundle version, and unsupported OpenAdapt
versions before an ActionEnvelope is constructed.

`register_tier_c_reference()` uses the existing
`jarvis_foundry.hybrid_memory.procedures.ProcedureRegistry`. Its body contains
only the reference and provenance. It rejects secrets and executable keys such
as `steps`, `workflow_code`, and `python`. `verify_tier_c_reference()` reloads
the external artifact and compares bundle hash, content hash, and version.

No migration, new procedure table, parallel memory registry, candidate engine,
mission engine, or release controller was added.

