# Inactive Configuration Templates

Every file in this directory is an example. Nothing here starts the runtime or
contains a credential.

Rules:

- After `START BUILD`, JARVIS materializes active configuration automatically
  from inspected reality.
- Replace every `__REQUIRED__` value from inspected local reality.
- Evidence and owner vetoes determine action; no approval field is introduced.
- Never place secrets in these files.
- Validate configuration against the corresponding schema before use.
- Unknown or missing security-sensitive values fail closed.
- Active configuration must be versioned separately from secret material.
