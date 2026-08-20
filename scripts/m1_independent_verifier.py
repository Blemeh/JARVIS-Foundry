"""Builder regression checker for Mission 1 Executive Intelligence candidate."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FINDINGS: list[str] = []


def check(condition: bool, msg: str) -> None:
    if not condition:
        FINDINGS.append(msg)


def main() -> int:
    # 1. Single executive authority
    check((REPO / "src/jarvis_foundry/executive/decisions.py").is_file(),
          "missing ExecutiveDecisionService")
    worker_loop_src = (REPO / "src/jarvis_foundry/runtime/worker_loop.py").read_text()
    check("_maybe_executive_select" in worker_loop_src,
          "worker_loop must use executive module")

    # 2. Organic eligibility stamping at candidate generation
    discovery = (REPO / "src/jarvis_foundry/discovery_candidates.py").read_text()
    check("stamp_executive_eligibility" in discovery,
          "generate_candidate must stamp organic executive eligibility")

    # 3. Governed provenance firewall
    elig = (REPO / "src/jarvis_foundry/executive/eligibility.py").read_text()
    check("eligibility_provenance" in elig and "_has_governed_epoch_marker" in elig,
          "governed eligibility provenance required")
    check("maintenance" not in elig or '_LAB_SOURCE_PREFIXES' in elig,
          "maintenance must not be blanket-blocked by prefix")

    # 4. Distinct wait kinds
    check("WAIT_NO_ELIGIBLE" in elig and "WAIT_COMPARATIVE" in elig,
          "NO_ELIGIBLE vs COMPARATIVE_WAIT not distinguished")

    # 5. Decision materialization event (not duplicate decision_recorded)
    check("executive.decision_materialized" in worker_loop_src,
          "materialization must use distinct event type")

    # 6. Calibration for all terminal outcomes
    cal = (REPO / "src/jarvis_foundry/executive/calibration.py").read_text()
    check("BLOCKED_EXTERNAL_DEPENDENCY" in cal,
          "blocked outcome semantics missing")

    # 7. Feature flag default off
    cfg = (REPO / "src/jarvis_foundry/runtime/config.py").read_text()
    check("executive_selection_enabled: bool = False" in cfg,
          "executive not disabled by default")

    # 8. Run focused tests
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/foundry/test_executive_intelligence.py", "-q"],
        cwd=REPO, capture_output=True, text=True,
    )
    check(proc.returncode == 0, f"focused tests failed: {proc.stdout[-800:]}")

    # 9. Product proof (organic flow)
    proof_home = REPO / "var" / "m1-proof-verify"
    if proof_home.exists():
        import shutil
        try:
            shutil.rmtree(proof_home)
        except OSError:
            proof_home = REPO / "var" / f"m1-proof-verify-{id(proof_home)}"
    proc2 = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "m1_product_proof.py"), str(proof_home)],
        cwd=REPO, capture_output=True, text=True,
    )
    check(proc2.returncode == 0, f"product proof failed: {proc2.stdout[-800:]}")
    proof_file = proof_home / "evidence" / "M1_PRODUCT_PROOF.json"
    if proof_file.is_file():
        proof = json.loads(proof_file.read_text())
        for key in ("PROOF_1", "PROOF_2", "PROOF_3", "PROOF_4", "PROOF_5", "PROOF_6"):
            check(proof.get("proofs", {}).get(key) == "PASS", f"{key} not PASS")
        check(proof.get("proofs", {}).get("no_eligible_wait") == "PASS",
              "no_eligible_wait not PASS")
        check(proof.get("proofs", {}).get("decision_uniqueness") == "PASS",
              "decision_uniqueness not PASS")
    else:
        check(False, "product proof artifact missing")

    verdict = "PASS" if not FINDINGS else "FAIL"
    report = {"verdict": verdict, "findings": FINDINGS, "role": "builder_regression_check"}
    out = REPO / "docs" / "evidence" / "M1_EXECUTIVE" / "BUILDER_REGRESSION.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
