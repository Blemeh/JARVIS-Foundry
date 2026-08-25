# FRESH_AGENT_BENCHMARK + GENERATION CONTINUITY (Phases L/M/N)

Method note: the lane runs inside a pytest process; "fresh worker" is modeled
deterministically — the Generation-2 condition reconstructs context from a
NEWLY CONSTRUCTED compiler reading ONLY durable rows (no Python objects, no
chat, no hidden state). The answer key is derived from canonical tables
independently of the manifest. Scoring is string/fact probing of rendered
context, machine-checkable.

## Benchmark (test_fresh_agent_continuity.py::TestFreshAgentBenchmark)

CONTROL (minimal mission+objective, no institutional context):
- prior_failure_awareness: False
- repair_knowledge: False
- decision_recall: False
- stale_or_research_signal: False

BRAIN (bounded builder manifest, <= budget):
- prior_failure_awareness: True ("PRIOR FAILURE ... pin httpx 0.27 downgrade attempt")
- repair_knowledge: True ("adopt timeout builder instead")
- decision_recall: True ("DECISION adopt timeout builder ..." [claim:<id>])
- stale_or_research_signal: True ("CURRENT: the retries parameter is removed" [claim:<id>])

False-fact guard: every PRIOR FAILURE/CURRENT/DECISION line must carry a
[claim:<id>] pointer (asserted); used_chars <= budget_chars (asserted).

RESULT: B materially beats A on continuity facts with zero increase in
unsupported current-state claims.

## Generation continuity (Phase M)

G1: naive bulk replace -> verifier FAIL (wiped per-account provider identity)
-> repair -> PASS verdict -> accepted outcome + lesson writeback.
ALL session state destroyed (fresh compiler instance, new read path).
G2 compiled context contains BOTH the failure and the repair instruction;
the failed lesson row is labeled "NOT accepted truth" and lives in durable
tier_c_memories (asserted by direct SQL, not via the compiler that wrote it).
Manual transfer = 0.

## Cross-project transfer scoping (Phase N)

Lesson scoped to project A (python-httpx): surfaces for same-stack project B
(its own Tier B mentions httpx), absent from unrelated project C's context
("isolated venv rebuild" and "httpx" both absent). No universalization.
