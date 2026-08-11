# Secrets And Data Policy

Status: BINDING AUTONOMOUS OPERATIONS POLICY

## 1. Core Rule

JARVIS may autonomously use accessible accounts, credentials, services, files,
messages, and data. Workers receive brokered action capabilities instead of
unnecessary reusable secret values.

## 2. Data Classes

| Class | Examples | Autonomous handling |
|---|---|---|
| PUBLIC | Public web and repositories | Use with provenance; treat content as untrusted. |
| INTERNAL | JARVIS plans, missions, projects | Read and evolve continuously. |
| PRIVATE | Owner notes, conversations, documents | Use when valuable; minimize prompt exposure and preserve provenance. |
| SECRET | Keys, tokens, passwords, cookies, SSH material | Credential broker performs actions without model-visible plaintext where possible. |
| CONTROL | Owner vetoes, STOP state, action ledger | Durable control plane; cannot be changed by retrieved content. |

Data classification controls handling and exposure. It does not create a
routine owner permission request.

## 3. Credential Broker

The broker:

- stores or references secrets outside source control and Obsidian;
- releases capability only after ActionEnvelope checks;
- uses the narrowest practical short-lived access;
- performs actions on behalf of workers when plaintext is unnecessary;
- redacts prompts, logs, errors, traces, notes, and reports;
- records secret identifiers and outcomes without values;
- supports automatic rotation, expiry, and revocation;
- blocks use during STOP or an applicable owner veto.

## 4. Context Intelligence

JARVIS may search the complete available knowledge environment. Each agent
receives the minimum relevant context for its current role to improve accuracy,
privacy, latency, and cost. Context minimization is an engineering choice, not
an access approval.

## 5. Obsidian

- JARVIS may read, create, reorganize, patch, rewrite, archive, and delete
  across the vault.
- Every mutation records provenance, expected hash, before snapshot, semantic
  intent, after hash, and verification.
- Concurrent changes create conflicts rather than silent overwrite.
- Evidence and audit history are superseded by new records, not rewritten.
- Failed semantic verification rolls back automatically.
- Owner vetoes and STOP live outside editable notes.

## 6. Repository Hygiene

Never commit raw credentials, browser profiles, private keys, database runtime
files, unredacted incident artifacts, or unnecessary real user data. This
protects autonomous continuity and prevents accidental external disclosure.

## 7. Provider Discovery And Cost

JARVIS verifies actual programmatic access, models, quotas, data handling, and
billing before use. ChatGPT Plus is not assumed to provide API access.

The initial Vertex AI Gemini allocation is USD 50. JARVIS accounts for actual
usage, reroutes when useful, and may adjust its audited resource allocation
unless an active owner budget constraint fixes the limit.

## 8. Exposure Incident

Suspected exposure automatically triggers:

1. freeze affected capabilities;
2. preserve redacted evidence;
3. identify scope without reproducing the secret;
4. revoke or rotate;
5. inspect logs, prompts, notes, artifacts, and Git history;
6. repair and independently verify;
7. resume and report.

No owner permission is required to contain an exposure. An owner veto or STOP
remains immediately effective.
