# V3 Scoring and Portfolio

Version: 1.0 (design)
Base SHA: `a743fdeb535f78c5ad455e8b09944f721f2515a7`

## 1. Scoring policy registry

**Version:** `v3-policy-2026-08-21` (initial default).

**Registration event:** `scoring.policy_registered` with full weights and thresholds stored in `scoring_policy_registry` projection.

**Rule:** Every `discovery.candidate_scored` and `executive.decision_recorded` MUST record `policy_version`. Selection replays from registry, not live config.

## 2. Hard eligibility filters

Applied before scoring. Failure → `discovery.candidate_rejected` with `reason_code`, not negative weight.

| Filter | Condition | reason_code |
|--------|-----------|-------------|
| STOP active | `emergency_stop_reason()` non-null | `HARD_STOP` |
| Veto active | `matching_veto_reason()` non-null | `HARD_VETO` |
| Illegal action | action policy denies | `HARD_ILLEGAL` |
| Missing capability | any `required_capabilities` not READY | `HARD_CAPABILITY` |
| Blast radius | `blast_radius > max_blast_radius` (0.85) | `HARD_BLAST` |
| Severity-one defect | open defect registry match | `HARD_DEFECT` |
| Expired | `expires_at < now` | `HARD_EXPIRED` |
| Duplicate | `fingerprint` matches unresolved | `HARD_DUPLICATE` |
| Unmeasurable | empty `expected_outcome` | `HARD_UNMEASURABLE` |
| Performative | objective matches circular pattern registry | `HARD_CIRCULAR` |
| Evidence insufficient | `len(evidence_refs) < min_evidence` (1) | `HARD_NO_EVIDENCE` |

## 3. Normalized dimensions

All inputs clamped to [0, 1] before weighting.

| Dimension | Source | Notes |
|-----------|--------|-------|
| strategic_fit | goal alignment score from `executive/goals.py` | 0 if no goals |
| expected_value | term heuristic + outcome class prior | extends `score_objective` value |
| information_gain | uncertainty reduction estimate | research terms |
| urgency | time sensitivity | urgency terms + deadline proximity |
| confidence | evidence strength × source_trust | cannot exceed evidence cap |
| effort | inverted penalty | higher effort → lower contribution |
| cost | inverted penalty | normalized to budget |
| risk | inverted penalty | includes blast_radius component |
| reversibility | direct | higher is better |
| opportunity_cost | inverted | active work contention |

**Evidence cap:** `confidence = min(confidence_raw, evidence_coverage)` where `evidence_coverage = min(1.0, len(evidence_refs) / 3)`.

**Anti-gaming:** Split detection — candidates with same `provenance_hash` prefix and >0.9 objective similarity → merge or reject `HARD_SPLIT`.

## 4. Initial weights and thresholds

```json
{
  "policy_version": "v3-policy-2026-08-21",
  "weights": {
    "strategic_fit": 0.18,
    "expected_value": 0.18,
    "information_gain": 0.12,
    "urgency": 0.10,
    "confidence": 0.10,
    "effort": 0.08,
    "cost": 0.06,
    "risk": 0.06,
    "reversibility": 0.06,
    "opportunity_cost": 0.06
  },
  "thresholds": {
    "min_total_score": 0.40,
    "min_strategic_fit": 0.20,
    "min_confidence": 0.25,
    "min_information_gain_for_research_override": 0.55,
    "comparative_wait_margin": 0.05
  }
}
```

**Rationale:** Aligns executive `min_action_score=0.40` (existing `executive/decisions.py`). Unifies with legacy loop `0.35` by raising loop to policy registry. Research override allows high information_gain to beat marginal build when `confidence < 0.40`.

**Formula:**

```text
positive = w_fit×fit + w_value×value + w_info×info + w_urgency×urgency
         + w_conf×confidence + w_rev×reversibility
negative = w_effort×effort + w_cost×cost + w_risk×risk + w_opp×opportunity_cost
total = clamp(positive - negative + calibration_adjust, 0, 1)
```

`calibration_adjust` from `CalibrationState` (existing `executive/calibration.py`): PASSED +0.02, FAILED -0.05, BLOCKED 0, capped ±0.10.

## 5. Deterministic tie-breaking

1. Higher `total_score`
2. Higher `strategic_fit`
3. Higher `information_gain` (research preference on tie)
4. Lower `candidate_id` lexicographic (stable)

## 6. Research vs build comparison

When top two candidates are `research` vs `build`:

- If `confidence(build) < 0.40` AND `information_gain(research) >= min_information_gain_for_research_override` → research wins if within 0.08 total of build.
- Record `comparative_rationale` in `executive.decision_recorded`.

## 7. Portfolio capacity and aging

| Parameter | Default |
|-----------|---------|
| max_active_missions | 3 |
| max_queued_select | 1 |
| max_parallel_research | 2 |
| priority_aging_per_day | 0.02 (urgency boost) |
| defer_cooldown_hours | 24 |
| retry_budget_per_candidate | 2 |
| starvation_guard_cycles | 10 |

**Starvation prevention:** After `starvation_guard_cycles` without selection, boost lowest eligible candidate `urgency` by 0.05 (capped once per candidate).

**Cooldown:** DEFERRED candidates re-eligible after `cooldown_until`.

## 8. Deduplication

| Case | Action |
|------|--------|
| Exact fingerprint | REJECT `HARD_DUPLICATE` |
| Causal duplicate (same incident) | SUPERSEDE older if lower score |
| Recurring maintenance | New fingerprint with `window_id` suffix |
| New evidence | New fingerprint if `provenance_hash` differs |

**Monitor poll:** `covered_objective` filter (existing `discovery_candidates.py`).

## 9. Calibration

- Record `executive.prediction_recorded` before SELECT.
- On outcome: `executive.calibration_recorded` with delta.
- Weight changes require `scoring.policy_registered` new version + experiment record — no silent self-tuning.
- Failed missions reduce repeat selection via calibration penalty, not deletion.

## 10. Worked examples

### Example A: Maintenance repair (selected)

- **Objective:** "Fix support semantics: handoff claims marked UNSUPPORTED"
- **Type:** repair
- **Dimensions:** value=0.72, info=0.45, urgency=0.60, confidence=0.70, effort=0.30
- **Total:** 0.58 → SELECT
- **Evidence:** WP12 live loop finding, 894 unsupported claims

### Example B: Low-value cosmetic (rejected)

- **Objective:** "Rename variable x to y in test file"
- **Type:** optimization
- **Dimensions:** value=0.12, effort=0.15, confidence=0.50
- **Total:** 0.18 < 0.40 → REJECT `BELOW_THRESHOLD`

### Example C: Research beats premature build

- **Build:** confidence=0.32, total=0.42
- **Research:** info_gain=0.62, total=0.39
- **Override:** research wins (confidence below 0.40, info above 0.55)

### Example D: Honest idle

- All candidates below 0.40 after filters
- **Decision:** WAIT, `wait_kind=NO_ELIGIBLE_CANDIDATES`
- **Rejected alternatives:** recorded with scores in `executive.decision_recorded`

### Example E: STOP blocks selection

- Top candidate score 0.85
- STOP active → hard filter → no SELECT; cycle completes with `outcome=STOPPED`

## 11. Sensitivity tests (acceptance)

| Test | Input change | Expected |
|------|--------------|----------|
| S1 | +0.10 urgency | total increases ≤ 0.02 |
| S2 | evidence_refs 0→3 | confidence capped upward |
| S3 | duplicate fingerprint | HARD_DUPLICATE, no second mission |
| S4 | policy_version bump | scores change only with new version event |
| S5 | calibration FAILED prior | -0.05 adjust on same class |

## 12. Audit trace

Every score stores:

```json
{
  "policy_version": "v3-policy-2026-08-21",
  "dimensions": { "...": 0.0 },
  "weights": { "...": 0.0 },
  "calibration_adjust": 0.0,
  "hard_filters": [],
  "total": 0.0
}
```

Persisted in `discovery.candidate_scored` payload and `candidate_scores.weights_json`.
