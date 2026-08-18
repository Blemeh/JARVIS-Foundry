# PERCEPTION PRIVACY GATE — JARVIS EYES PHASE 2

## Firewall order (contract section C)

SCREENPIPE INTERNAL PRIVACY -> EXCLUDED app/window -> SENSITIVE title /
private zone -> secret-like text via existing `control.redaction` ->
normalization -> aggregation -> significance -> `perception.observed`

`privacy.py` implements deterministic `PrivacyPolicy` with:

- excluded applications (exact, case/whitespace-insensitive)
- excluded windows
- sensitive titles (substring, case-insensitive) -> window title
  replaced with `[REDACTED-TITLE]`
- owner private zones (app, window) pairs
- secret-like fragment scan reusing `control.redaction.contains_secret_like`
  and `redact_text` (no duplicated credential-pattern implementation)
- under-capture default: exclusions/secret scan ON by default;
  `raw_frame_escalation` forbidden by config validation in Phase 2

## Evidence

- P2-F5 privacy firewall PASS (sensitive title + secret fragments not
  retained)
- P2-F6 synthetic secret scan PASS (see SECRET_SCAN.md)
- Unit tests: excluded app suppressed; sensitive title redacted;
  `api_key/password/token/authorization` fragments never survive;
  existing `redact_payload` reuse verified.
- `raw_frame_escalation` config raises `ValueError` (fail closed).
