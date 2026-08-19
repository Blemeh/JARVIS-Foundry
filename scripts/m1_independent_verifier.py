"""Independent verifier for Mission 1 Executive Intelligence candidate."""
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
    check("v3_loop" not in open(REPO / "src/jarvis_foundry/runtime/worker_loop.py").read()
          or "_maybe_executive_select" in open(REPO / "src/jarvis_foundry/runtime/worker_loop.py").read(),
          "worker_loop must use executive module not v3_loop orchestration")

  # 2. v3_loop not production owner
    authority = open(REPO / "src/jarvis_foundry/runtime/authority.py").read()
    check(' "v3_loop": "THIN_WRAPPER"' in authority, "v3_loop authority classification missing")

    # 3. Historical firewall
    elig = open(REPO / "src/jarvis_foundry/executive/eligibility.py").read()
    check("stale_historical" in elig and "executive_epoch" in elig,
          "historical candidate firewall incomplete")

    # 4. Goals influence selection
    scoring = open(REPO / "src/jarvis_foundry/executive/scoring.py").read()
    check("goal_fit" in scoring and "goal_alignment_score" in scoring,
          "goal alignment not in scoring")

    # 5. Feature flag default off
    cfg = open(REPO / "src/jarvis_foundry/runtime/config.py").read()
    check("executive_selection_enabled: bool = False" in cfg,
          "executive not disabled by default")

    # 6. Run focused tests
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/foundry/test_executive_intelligence.py", "-q"],
        cwd=REPO, capture_output=True, text=True,
    )
    check(proc.returncode == 0, f"focused tests failed: {proc.stdout[-500:]}")

    # 7. Product proof
    proof_home = REPO / "var" / "m1-proof-verify"
    if proof_home.exists():
        import shutil
        shutil.rmtree(proof_home)
    proc2 = subprocess.run(
        [sys.executable, str(REPO / "scripts" / "m1_product_proof.py"), str(proof_home)],
        cwd=REPO / "tests" / "foundry", capture_output=True, text=True,
    )
    check(proc2.returncode == 0, f"product proof failed: {proc2.stdout[-800:]}")
    proof_file = proof_home / "evidence" / "M1_PRODUCT_PROOF.json"
    if proof_file.is_file():
        proof = json.loads(proof_file.read_text())
        for key in ("PROOF_1", "PROOF_2", "PROOF_3", "PROOF_4", "PROOF_5", "PROOF_6"):
            check(proof.get("proofs", {}).get(key) == "PASS", f"{key} not PASS")
    else:
        check(False, "product proof artifact missing")

    verdict = "PASS" if not FINDINGS else "FAIL"
    report = {"verdict": verdict, "findings": FINDINGS}
    out = REPO / "docs" / "evidence" / "M1_EXECUTIVE" / "INDEPENDENT_VERIFIER.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
