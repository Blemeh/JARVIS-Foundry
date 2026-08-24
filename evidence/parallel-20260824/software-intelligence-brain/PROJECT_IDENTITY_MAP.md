# PROJECT_IDENTITY_MAP (Phase B)

Spine: existing `resources` table fed by append-only `project.registered`.
Resolver: `src/jarvis_foundry/software_intelligence/project_identity.py`.

| Alias world | Example | Canonical mapping |
|---|---|---|
| local path | C:/x/repo | path:<lowercase abs> alias |
| git root/worktree | clone at C:/y/wt | same resource after explicit bind_worktree; own worktree identity (HEAD/branch/dirty) |
| hosted remote | https://github.com/o/r.git, git@host:o/r.git, ssh://..., o/r | remote:o/r + slug:o/r aliases (first/origin remote only) |
| nested groups | https://gitlab.example/g/s/r.git | group/s/r slug |
| resource_id | resource-<sha24(kind\0endpoint)> | canonical key |
| fork/upstream | fork remote + upstream remote | upstream NOT auto-merged; explicit bind_alias('remote:upstream/x') governance decision |

Fail-closed behaviors (all proven in tests/foundry/test_software_identity.py):
unregistered path/remote raises ProjectNotBoundError; alias hijack raises;
a directory whose history lacks the registered baseline HEAD is refused even
with no remotes; projection rebuild reproduces identical aliases.

Determinism note discovered during proof: two fixture repos with identical
trees and timestamps hash to IDENTICAL commit SHAs — real-world distinctness
requires distinct content or history. The baseline-ancestry check relies on
this property of git itself.
