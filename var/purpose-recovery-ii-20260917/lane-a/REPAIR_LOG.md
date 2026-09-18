# Lane A Repair Log

Base: `ddec34efc96b9a0154eb9821d6cc496ef76906a3`

1. **Standing intent vs recipe.** `sync_owner_goals` no longer materializes `owner-goal:<id>` objectives for discovery mandates such as "Keep improving my active projects while I'm away." It records `executive.standing_intent_recorded` and leaves mission creation to observation → candidate → Executive selection.
2. **Alignment.** `goal_alignment_score` uses a discovery-mandate floor so project-improvement work aligns without sharing tokens with the standing goal. Style-only TODOs stay weakly aligned.
3. **Registered projects are the default observation surface.** Empty A3 YAML sources now resolve from the `resources` table. `assemble_project_inventory` gives the Executive purpose/branch/HEAD/health.
4. **Orchestration seam, not a second Executive.** `run_executive_intelligence_cycle` reuses A2 observation and `ExecutiveDecisionService.evaluate_a2`.
5. **Research findings change decisions.** `_research_factors` consumes CONTRADICTING/SUPPORTING/conflicting stances. `apply_research_findings` enqueue → start → complete on the canonical ResearchQueue. `a2_precondition` rejects a contradicted premise.
6. **Learning from low-value PASS.** Calibration applies `LOW_VALUE_PENALTY` when realized value is materially below predicted even if verification PASSed.
7. **PASS is not silence.** `schedule_next_action_after_outcome` creates an existing Continuous Executive `WAIT_UNTIL_TIME` wake. A3 worker_loop schedules it after a finalized initiative outcome.
8. **Decision truth.** `executive.decision_recorded` carries additive `decision_truth`. Command Center `_executive_projection` exposes why/rejected/learned/next/wait/owner-attention.
9. **WAIT vs stall.** `executive.wait_diagnosis_recorded` is now recorded on every `ExecutiveDecisionService` decision, not only the Lane A cycle helper.
10. **Predicted vs actual on the product reconcilers.** `merge_predicted_vs_actual` attaches expected value/effort/risk to `reconcile_outcome_learning` calibration so low-value learning is not confined to the isolated `measure_outcome` helper.
11. **Empty A3 sources.** Worker-loop A3 with no YAML sources binds registered git resources before observation.
12. **Product-path context.** `prepare_executive_observation` runs at A3 worker_loop start: standing intents + project inventory + `executive.context_assembled`.
13. **Product-path WAIT vs STALL.** `record_product_wait_diagnosis` on A3 WAIT and on SELECT-without-mission. Empty sources are JUSTIFIED_WAIT. A3 admission refusal after SELECT is STALL.
14. **Discovery of real project health.** Source-dir walk prefers `src/packages/backend`. Discovery-mandate alignment includes `project_staleness` / `project_drift`.
15. **Adversarial proofs** use clones of real owner repos (JARVIS worktree + OperatorOS) and `run_cycle` only — not `run_executive_intelligence_cycle`.
16. **Circular import.** `intelligence.py` lazy-imports `SourceSpec` so `discovery_sources` can load.
