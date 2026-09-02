# Project Context Profile Contract

Status: V11 candidate contract. This package is read-only intelligence and is
not an admission, scoring, planning, execution, or effect authority.

## Purpose

`jarvis_foundry.project_context` converts one explicitly configured, bounded
Git repository into `jarvis.project-profile.v1`. Discovery, research, and the
A3 bridge can consume the profile through `ProjectProfileService` and
`ProjectContextResolver` without reading arbitrary repository content or
executing a discovered command.

The supported product scope is deliberately small: Python repositories,
Node/Next.js repositories, and repositories containing both. It is not a
universal parser, language server, dependency solver, or code-intelligence
platform.

## Profile fields

Every `ProjectProfile` carries:

- stable `profile_id`, configured `project_id`, canonical repository identity,
  and the current checkout root;
- language/runtime, recognized frameworks, package/dependency managers, and
  component roots;
- important manifests and configuration with content hashes;
- inert build, test, lint, and typecheck command hints containing `argv`,
  working directory, evidence, confidence, and `execute=false`;
- source and documentation roots;
- current branch, HEAD, dirty status, bounded dirty paths, and registered
  Git-worktree information;
- bounded architecture hints and project constraints extracted only from
  configured/allowlisted project documents, always labelled
  `REPOSITORY_DATA_ONLY`;
- CI and deployment/release configuration hints;
- recent commits that touched profile inputs;
- capability requirements with `admission_effect=NONE`;
- explicit `UNKNOWN` entries for information that evidence does not support;
- input evidence, provenance, source fingerprint, evidence hash, and stable
  profile hash.

Raw remote URLs are never persisted. Hosted remotes retain only the existing
project-identity normalizer's owner/repository slug; opaque/local remotes are
represented by a hash. Repository documents are redacted before bounded hints
are exposed.

## Lifecycle

```text
configured ProjectTarget
  -> bounded relevant-input snapshot
  -> stable repository identity
  -> MISSING: compile and atomically persist (FIRST_USE)
  -> CURRENT: overlay live Git truth, do not rewrite (UNCHANGED_REPLAY)
  -> STALE: compile changed inputs and atomically replace (REFRESHED)
  -> IDENTITY_CONFLICT: fail closed
```

The source fingerprint contains only bounded, relevant manifests, build/tool
configuration, CI/release configuration, and authoritative project documents,
plus the inspection bounds themselves. Ordinary source edits and HEAD/branch/
dirty changes do not rewrite the stable profile. Those volatile Git facts are
observed and overlaid on every request.

The profile store must be an explicit absolute directory outside the target
repository. It writes atomically and performs no replacement for byte-identical
content. A durable path binding prevents a configured path from silently
changing to a different repository identity.

## Supported evidence

Recognized Python evidence includes `pyproject.toml`, `requirements*.txt`,
standard lockfiles, Python-version files, and explicit pytest/ruff/mypy/build
configuration. For `uv` projects with declared optional dependencies, derived
tool commands include `--all-extras` so a fresh worktree receives the tools the
manifest makes optional. Recognized Node evidence includes `package.json`, declared
scripts, `packageManager`, Node engines, TypeScript configuration, and npm,
pnpm, yarn, or Bun lockfiles. Nested manifests are supported within strict
depth/file/byte limits for mixed repositories.

Package managers and runtime versions are never inferred when no manifest or
lockfile states them. A known package script without a known package manager
does not become an executable command hint; the command category remains
`UNKNOWN`.

## A3 interface and authority boundary

The resolver implements:

```text
candidate + configured target
  -> ProjectProfile
  -> actionable_context for discovery / research / A3
```

Every resolver result contains:

```json
{
  "authority": "ADVISORY_READ_ONLY",
  "execution": {
    "allowed": false,
    "commands_executed": false,
    "repository_mutation": false,
    "external_writes": false
  },
  "admission": {
    "evaluated": false,
    "authority": "EXISTING_EXECUTIVE_OUTSIDE_THIS_CONTRACT"
  }
}
```

Candidate/target mismatches fail closed. The resolver has no Foundry database,
MissionLoop, scoring, objective, worker, effect, provider-write, release, or
Command Center dependency. The existing Executive and A3 admission path remain
the only authorities for deciding whether work proceeds.

## Proof boundary

`scripts/v11_project_context_proof.py` profiles only repositories explicitly
passed as `PROJECT_ID=ABSOLUTE_PATH`. Cache and evidence paths must be outside
every target. It hashes Git status before and after each profile, proves an
unchanged replay does not rewrite the profile, and runs changed-manifest
lifecycle proof only in a disposable repository below the evidence root.

The proof script does not run any discovered project command, create an
objective or mission, modify an owner repository, contact a provider, or write
to an external system. Its only writes are the explicitly configured local
profile-cache and evidence paths plus its disposable lifecycle repository.
