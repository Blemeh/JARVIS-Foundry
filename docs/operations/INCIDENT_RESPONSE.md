# Incident Response

Status: DESIGN ONLY

## 1. Incident Classes

| Severity | Meaning | Default response |
|---|---|---|
| S1 | Owner-veto breach, secret exposure, STOP failure, unlawful action, hidden external effect, or unrecoverable data risk | Freeze immediately; owner notification; preserve evidence. |
| S2 | Incorrect consequential result, repeated state corruption, failed rollback, uncontrolled cost | Freeze affected capabilities; stop affected mission. |
| S3 | Recoverable worker/provider failure, duplicate prevention event, isolated test failure | Degrade or repair within policy; record evidence. |
| S4 | Non-consequential defect or observability issue | Add bounded repair candidate. |

## 2. Response Sequence

```text
DETECT
  -> FREEZE AFFECTED CAPABILITIES
  -> PRESERVE REDACTED EVIDENCE
  -> CLASSIFY CONSEQUENCE
  -> CONTAIN
  -> RECONCILE ACTUAL STATE
  -> REPAIR OR RESTORE
  -> INDEPENDENTLY VERIFY
  -> OWNER DECISION WHEN REQUIRED
  -> RESUME BOUNDED CAPABILITY
  -> RECORD LESSON AND PREVENTION
```

## 3. Incident Record

```yaml
incident_id: null
severity: null
detected_at: null
detected_by: null
mission_ids: []
affected_resources: []
capabilities_frozen: []
observations: []
evidence: []
claims: []
known_effects: []
uncertain_effects: []
secrets_involved: false
containment_actions: []
restore_actions: []
verification: null
owner_decisions: []
root_cause: null
preventive_actions: []
closed_at: null
```

## 4. Evidence Rules

- Preserve original events and immutable evidence.
- Redact secrets without destroying the ability to trace the exposure.
- Separate observed effects from inferred root cause.
- Record clock uncertainty and missing evidence.
- Never edit history to make recovery appear cleaner.
- Record unsuccessful repair and rollback attempts.

## 5. Resume Rule

An S1 or S2 incident cannot be closed by the builder that caused or repaired
it. Independent verification is required. Capabilities stay frozen until
repair, reconciliation, and verification pass. An active owner veto or STOP
must also be cleared by the owner before matching work resumes.
