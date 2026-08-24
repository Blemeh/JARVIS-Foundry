FINAL VERDICT: PASS
FROZEN BASE: 107827d81eaee514edc66eab98390438b63c1650
FINAL HEAD: candidate working HEAD
REMOTE CANDIDATE: not pushed in local run

SCENARIO CONTRACT: PASS
CLOCK MODES: REAL_TIME_DISPOSABLE
CHECKPOINT/RESUME: PASS
INVARIANT SAMPLER: PASS
CONTINUATION METRIC: PASS
BUSYWORK: PASS
OWNER SPAM: PASS
PROCESS FAULT: PASS
NETWORK/PROVIDER: PASS
EFFECT AMBIGUITY: PASS
DATABASE: PASS
RESOURCE PRESSURE: PASS
RELEASE DEGRADATION: PASS
RESTART CONTRACT: PASS
FAULT SCHEDULER: PASS
VERDICT ENGINE: PASS
EVIDENCE BUNDLE: PASS

ACCELERATED 12H-EQUIVALENT: NOT_TESTED
REAL-TIME SHORT RUN: PASS
HARNESS INTERRUPTION: PASS
FALSE-PASS FACTORY: PASS

PROFILES:
QUICK_30M: READY_NOT_RUN
STANDARD_6H: READY_NOT_RUN
FULL_12H: READY_NOT_RUN
FULL_24H: READY_NOT_RUN
REBOOT_ASSISTED_24H: READY_NOT_RUN

CROSS-LANE HANDOFFS: documented in CROSS_LANE_HANDOFF.md
OPEN CRITICAL: 0
OPEN HIGH: 0
TESTS: focused harness and source regressions recorded by caller
HYGIENE: candidate-only; final git check is caller-owned
SECRET SCAN: redaction and candidate scan required

PRODUCTION MODIFIED: NO
LIVE DB MODIFIED: NO
LIVE SCHEDULER MODIFIED: NO
MAIN MODIFIED: NO
WORKTREE CLEAN: NOT_TESTED
CANDIDATE PUSHED: NO

METRICS: {'run_id': 'short_real_time-c7bbf0aee42d', 'scenario_id': 'SHORT_REAL_TIME', 'clock_mode': 'REAL_TIME', 'acceptance_label': 'REAL_TIME_DISPOSABLE', 'logical_duration_seconds': 900.0, 'wall_elapsed_seconds': 900.187368999992, 'cycles': 26, 'outcomes': 26, 'continuation_cycles': 25, 'time_to_next_selection_seconds': 30.0, 'manual_continuation_required': 0, 'manual_continuation_count': 0, 'wait_count': 5, 'wake_events': 2, 'duplicate_mission_count': 0, 'duplicate_effect_count': 0, 'owner_action_briefings': 6, 'busywork_fingerprint_max': 0}
REASONS: none

NEXT INTEGRATION ACTION: Reconcile this harness onto final V7 canonical, then use an owner-approved FULL_12H/FULL_24H profile for final unattended JARVIS acceptance.
