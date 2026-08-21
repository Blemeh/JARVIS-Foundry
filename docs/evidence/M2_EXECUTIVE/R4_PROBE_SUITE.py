"""R4 adversarial probe suite — authored by builder, executed by independent
verifier. One file, one run, six probes. Fresh temp DBs; no shared state."""
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(r"C:\temp\JARVIS-EI-M2-Integration")
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(REPO / "tests" / "foundry"))

from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.discovery_candidates import generate_candidate, score_candidate
from jarvis_foundry.events import Event
from jarvis_foundry.executive.calibration import record_calibration, rebuild_calibration
from jarvis_foundry.executive.decisions import ExecutiveDecisionService
from jarvis_foundry.executive.eligibility import assess_eligibility
from jarvis_foundry.executive.scoring import rank_candidates
from jarvis_foundry.command_center import build_snapshot
from jarvis_foundry.owner_controls import OwnerControlService, sign_command

from worker_test_helpers import mission_database

SECRET = b"r4-probe-secret-32-bytes-min!!"
EPOCH = "r4-probe-epoch"
RESULTS: dict[str, str] = {}


def fresh_db(prefix: str):
    home = Path(tempfile.mkdtemp(prefix=prefix))
    db, repo = mission_database(home, resource_id="resource-1")
    svc = OwnerControlService(db, secret=SECRET, owner_id="owner")
    text = ("Improve gateway reliability and reduce customer outage risk today. "
            "priority: 3 success: latency stable; zero incidents")
    nonce = f"{prefix}-nonce"
    sig = sign_command(SECRET, "GOAL", "GLOBAL", text, nonce)
    svc.apply("GOAL", target="GLOBAL", exact_text=text, nonce=nonce, signature=sig)
    db.append(Event.create(
        "executive.activation_recorded", {"epoch": EPOCH},
        source_type="supervisor", source_id="r4",
        idempotency_key=f"executive.activation_recorded:{EPOCH}"))
    from jarvis_foundry.executive.eligibility import enable_admission_authority
    enable_admission_authority(db, EPOCH, source_id="r4")
    from jarvis_foundry.control.capabilities import set_capability_state
    set_capability_state(
        db, "resource-1", "write_registered_worktree_files", "READY",
        evidence={"source": "r4"})
    return db


def organic(db, objective, source="maintenance"):
    cid = generate_candidate(db, "mission-r4", source, objective)["candidate_id"]
    score_candidate(db, cid)
    return cid


# ---- P1: forged admission issuer inert; historical candidate excluded ----
# Candidate generated BEFORE activation (authority off => no trusted stamp).
# P1 uses its OWN db without the fresh_db() activation so generation happens
# pre-epoch; activation + forge happen after. The forge must stay inert.
db1_home = Path(tempfile.mkdtemp(prefix="r4p1_"))
db1, _repo1 = mission_database(db1_home, resource_id="resource-1")
_svc = OwnerControlService(db1, secret=SECRET, owner_id="owner")
_txt = ("Improve gateway reliability and reduce customer outage risk today. "
        "priority: 3 success: latency stable; zero incidents")
_nonce = "r4p1-nonce"
_sig = sign_command(SECRET, "GOAL", "GLOBAL", _txt, _nonce)
_svc.apply("GOAL", target="GLOBAL", exact_text=_txt, nonce=_nonce, signature=_sig)
cid_hist = generate_candidate(
    db1, "mission-r4", "maintenance",
    "Improve gateway reliability outage risk historical")["candidate_id"]
score_candidate(db1, cid_hist)
db1.append(Event.create(
    "executive.activation_recorded", {"epoch": EPOCH},
    source_type="supervisor", source_id="r4",
    idempotency_key="executive.activation_recorded:r4p1"))
from jarvis_foundry.executive.eligibility import enable_admission_authority
enable_admission_authority(db1, EPOCH, source_id="r4")
row = db1.row("candidates", "candidate_id", cid_hist)
r_pre = assess_eligibility(db1, row, activation_epoch=EPOCH, resource_id="resource-1")
db1.append(Event.create(
    "executive.candidate_admitted",
    {"candidate_id": cid_hist, "epoch": EPOCH, "issuer": "attacker"},
    source_type="supervisor", source_id="attacker",
    idempotency_key="executive.candidate_admitted:forge-1"))
r_post = assess_eligibility(db1, row, activation_epoch=EPOCH, resource_id="resource-1")
RESULTS["P1_forge_inert"] = (
    "PASS pre=False/%s post=False/%s (forge inert)" % (r_pre.reason, r_post.reason)
    if not r_pre.eligible and not r_post.eligible
    else "FAIL pre=%s post=%s" % (r_pre.eligible, r_post.eligible))

# ---- P2: evaluate() twice, same loop_id -> exactly one decision event ----
db2 = fresh_db("r4p2_")
organic(db2, "Improve gateway reliability reduce customer outage risk latency")
svc2 = ExecutiveDecisionService(
    db2, activation_epoch=EPOCH, min_action_score=0.30, source_id="r4")
a = svc2.evaluate(resource_id="resource-1", loop_id="loop-dup")
b = svc2.evaluate(resource_id="resource-1", loop_id="loop-dup")
events = [e for e in db2.events()
          if e.event_type == "executive.decision_recorded"]
RESULTS["P2_replay_idempotent"] = (
    "PASS events=1 identical=%s" % (a.decision_id == b.decision_id)
    if len(events) == 1 and a.decision_id == b.decision_id
    else "FAIL events=%d" % len(events))

# ---- P3: STOP blocks selection (STOP_EVERYTHING directive path) ----
db3 = fresh_db("r4p3_")
organic(db3, "Improve gateway reliability reduce customer outage risk latency")
db3.append(Event.create(
    "owner_directive.recorded",
    {"directive_id": "dir-r4-stop", "command_type": "STOP_EVERYTHING",
     "target": "GLOBAL", "exact_text": "stop everything", "reason": "probe"},
    source_type="owner", source_id="r4",
    idempotency_key="owner_directive.recorded:dir-r4-stop"))
svc3 = ExecutiveDecisionService(
    db3, activation_epoch=EPOCH, min_action_score=0.30, source_id="r4")
dec3 = svc3.evaluate(resource_id="resource-1", loop_id="loop-stop")
RESULTS["P3_stop_blocks"] = (
    "PASS kind=WAIT" if dec3.kind == "WAIT" else "FAIL kind=%s" % dec3.kind)

# ---- P4: latest_decision is NEWEST (the fixed defect) ----
db4 = fresh_db("r4p4_")
organic(db4, "Improve gateway reliability reduce outage risk alpha")
organic(db4, "Improve gateway reliability documentation on-call rotation beta")
svc4 = ExecutiveDecisionService(
    db4, activation_epoch=EPOCH, min_action_score=0.30, source_id="r4")
first = svc4.evaluate(resource_id="resource-1", loop_id="loop-first")
second = svc4.evaluate(resource_id="resource-1", loop_id="loop-second")
snap = build_snapshot(db4)["executive"]
latest = snap["latest_decision"] or {}
ids = [d.get("decision_id") for d in snap["recent_decisions"]]
ok = (
    latest.get("decision_id") == second.decision_id
    and ids[:1] == [second.decision_id]
    and ids.index(second.decision_id) < ids.index(first.decision_id)
)
RESULTS["P4_latest_newest"] = "PASS" if ok else (
    "FAIL latest=%s ids=%s" % (latest.get("decision_id"), ids))

# ---- P5: keyword-bait damping causal ----
db5 = fresh_db("r4p5_")
bait = organic(
    db5, "URGENT crash outage revenue fix tonight blocked deadline now",
    "workflow_opportunity")
aligned = organic(
    db5, "Improve gateway reliability documentation for on-call rotation",
    "maintenance")
with db5.connect() as conn:
    conn.execute(
        "UPDATE candidate_scores SET weights_json=? WHERE candidate_id=?",
        (json.dumps({"value": 0.9, "urgency": 0.8}), bait))
    conn.execute(
        "UPDATE candidate_scores SET weights_json=? WHERE candidate_id=?",
        (json.dumps({"value": 0.4}), aligned))
rows = [db5.row("candidates", "candidate_id", bait),
        db5.row("candidates", "candidate_id", aligned)]
ranked = rank_candidates(db5, rows)
RESULTS["P5_bait_damped"] = (
    "PASS top=%s" % ranked[0].candidate_id
    if ranked[0].candidate_id == aligned else
    "FAIL top=%s" % ranked[0].candidate_id)

# ---- P6: calibration FAILED negative, BLOCKED neutral, both recorded ----
# BLOCKED is measured in ISOLATION (own source) because source-level learning
# intentionally shares penalties across a source's candidates: a FAILED under
# the same source must and does lower every candidate from that source.
db6 = fresh_db("r4p6_")
obj_f = "Improve gateway reliability failure case"
obj_b = "Deploy gateway patch after dependency blocked case"
cid_f = organic(db6, obj_f, "recovery-failed")
cid_b = organic(db6, obj_b, "recovery-blocked")
record_calibration(db6, decision_id="d-f", candidate_id=cid_f,
                   mission_id="m-f", source="recovery-failed", objective=obj_f,
                   outcome="FAILED", terminal_event_id="t-f")
record_calibration(db6, decision_id="d-b", candidate_id=cid_b,
                   mission_id="m-b", source="recovery-blocked", objective=obj_b,
                   outcome="BLOCKED", terminal_event_id="t-b")
cal_events = [e for e in db6.events()
              if e.event_type == "executive.calibration_recorded"]
state = rebuild_calibration(db6)
adj_f = state.adjustment_for(source="recovery-failed", objective=obj_f)
adj_b = state.adjustment_for(source="recovery-blocked", objective=obj_b)
ok6 = len(cal_events) == 2 and adj_f < 0 and adj_b == 0.0
RESULTS["P6_calibration"] = (
    "PASS events=2 failed_adj=%.3f blocked_adj=%.3f" % (adj_f, adj_b)
    if ok6 else "FAIL events=%d adj_f=%s adj_b=%s" % (len(cal_events), adj_f, adj_b))

print(json.dumps({
    "head": __import__("subprocess").run(
        ["git", "-C", str(REPO), "rev-parse", "HEAD"],
        capture_output=True, text=True).stdout.strip(),
    "results": RESULTS,
    "verdict": ("PASS" if all(v.startswith("PASS") for v in RESULTS.values())
                else "FAIL"),
}, indent=1))
