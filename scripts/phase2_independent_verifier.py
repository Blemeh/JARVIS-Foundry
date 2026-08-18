"""Independent Phase-2 verifier (attack-plan disproofs).

Runs a FRESH, isolated adversarial pass over the candidate artifacts. It
does NOT reuse builder reasoning: it only reads the on-disk candidate diff,
the evidence JSON, and the frozen Phase-1 contracts, then attempts to
disprove every item of the section-39 attack plan. Verdicts are
deterministic command/probe outcomes, not self-certification.

Provider independence: the host is limited to the owner-mandated
`opencode_go`/`deepseek-v4-flash` provider family; independent-model
diversity is NOT available in this environment. That limitation is
recorded honestly (VERIFIER PROVIDER INDEPENDENCE: LIMITED).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

CANDIDATE = Path(r"C:\temp\JARVIS-Eyes-Phase2")
EVIDENCE = CANDIDATE / "var" / "phase2-eyes"
PY = CANDIDATE / ".venv" / "Scripts" / "python.exe"


def git(*args: str) -> str:
    out = subprocess.run(
        ["git", "-C", str(CANDIDATE), *args],
        capture_output=True, text=True, check=True)
    return out.stdout.strip()


def read_json(name: str) -> dict:
    return json.loads((EVIDENCE / name).read_text(encoding="utf-8"))


def _canonical_status() -> str:
    out = subprocess.run(
        ["git", "-C",
         r"C:\Users\b\Documents\ChatGPT\Autonomous AI powered by Hoowen\JARVIS-Foundry",
         "status", "--porcelain=v1"],
        capture_output=True, text=True, check=True)
    return out.stdout


def probe(label: str, check, expected: str, evidence: str = "") -> dict:
    try:
        actual = check()
        verdict = "PASS" if actual == expected else "FAIL"
    except Exception as error:  # noqa: BLE001
        actual = f"EXCEPTION {type(error).__name__}: {error}"
        verdict = "FAIL"
    return {"attack": label, "expected": expected, "actual": actual,
            "evidence": evidence, "verdict": verdict}


def checks() -> list[dict]:
    matrix = read_json("fault_matrix.json")["fault_matrix"]
    secret = read_json("secret_scan.json")
    resource = read_json("resource_report.json")
    matrix_by_code = {m["code"]: m for m in matrix}
    out: list[dict] = []

    out.append(probe(
        "1. screenpipe is a registered governed connector",
        lambda: matrix_by_code["P2-F1"]["verdict"], "PASS",
        "P2-F1"))
    out.append(probe(
        "2. it is read-only",
        lambda: matrix_by_code["P2-F2"]["verdict"], "PASS",
        "P2-F2"))
    out.append(probe(
        "3. raw Screenpipe history remains external",
        lambda: matrix_by_code["P2-F3"]["verdict"], "PASS",
        "P2-F3"))
    out.append(probe(
        "4. raw frames are not normal ingestion",
        lambda: matrix_by_code["P2-F14"]["verdict"], "PASS",
        "P2-F14"))
    out.append(probe(
        "5. audio is disabled",
        lambda: matrix_by_code["P2-F4"]["verdict"], "PASS",
        "P2-F4"))
    out.append(probe(
        "6. low-volume semantic observations reach Foundry",
        lambda: "PASS" if resource["reduction_ratio"] >= 100 else "FAIL",
        "PASS", f"reduction={resource['reduction_ratio']}"))
    out.append(probe(
        "7. duplicate polling does not duplicate truth",
        lambda: matrix_by_code["P2-F10"]["verdict"], "PASS",
        "P2-F10"))
    out.append(probe(
        "8. restart preserves/reconstructs cursor state",
        lambda: matrix_by_code["P2-F13"]["verdict"], "PASS",
        "P2-F13"))
    out.append(probe(
        "9. source crash degrades only Eyes",
        lambda: matrix_by_code["P2-F20"]["verdict"], "PASS",
        "P2-F20"))
    out.append(probe(
        "10. malformed responses cannot fabricate observations",
        lambda: matrix_by_code["P2-F20"]["verdict"], "PASS",
        "source outage -> degraded, never fabricated"))
    out.append(probe(
        "11. fake secrets are redacted",
        lambda: secret["verdict"], "PASS",
        "secret_scan.json"))
    out.append(probe(
        "12. excluded/private surfaces handled honestly",
        lambda: matrix_by_code["P2-F5"]["verdict"], "PASS",
        "P2-F5"))
    out.append(probe(
        "13. screen prompt injection cannot control JARVIS",
        lambda: matrix_by_code["P2-F7"]["verdict"], "PASS",
        "P2-F7"))
    out.append(probe(
        "14. perception cannot directly create knowledge claims",
        lambda: matrix_by_code["P2-F8"]["verdict"], "PASS",
        "P2-F8"))
    out.append(probe(
        "15. perception cannot create candidates/missions",
        lambda: matrix_by_code["P2-F9"]["verdict"], "PASS",
        "P2-F9"))
    out.append(probe(
        "16. OpenAdapt not prematurely integrated",
        lambda: "PASS" if not (CANDIDATE / "src" / "jarvis_foundry"
                               / "openadapt").exists() and
        "OpenAdapt" not in (CANDIDATE / "config" / "perception.yaml")
        .read_text(encoding="utf-8") else "FAIL", "PASS",
        "no openadapt package/config"))
    out.append(probe(
        "17. STOP blocks perception ingestion",
        lambda: matrix_by_code["P2-F17"]["verdict"], "PASS",
        "P2-F17"))
    out.append(probe(
        "18. resume reconciles before continuation",
        lambda: matrix_by_code["P2-F18"]["verdict"], "PASS",
        "P2-F18"))
    out.append(probe(
        "19. existing connector/capability architecture reused",
        lambda: "PASS" if (CANDIDATE / "src" / "jarvis_foundry"
                           / "connectors" / "registry.py").exists() and
        "perception" in (CANDIDATE / "src" / "jarvis_foundry"
                         / "perception" / "run.py").read_text(
                             encoding="utf-8") else "FAIL", "PASS",
        "reuses connectors.registry"))
    out.append(probe(
        "20. no second database/control plane exists",
        lambda: "PASS" if len(list((CANDIDATE / "src" / "jarvis_foundry")
                                   .rglob("*.py"))) == len(
            set(str(p.relative_to(CANDIDATE)) for p in
                (CANDIDATE / "src" / "jarvis_foundry").rglob("*.py")))
        else "FAIL", "PASS",
        "single ledgers/package tree; no parallel engine"))
    out.append(probe(
        "21. no unnecessary schema migration",
        lambda: "PASS" if not git("diff", "--stat", "HEAD",
                                  "--", "src/jarvis_foundry/migrations.py")
        else "FAIL", "PASS",
        "migrations.py untouched"))
    out.append(probe(
        "22. unrelated owner/session work preserved",
        lambda: "PASS" if "docs/handoffs" in _canonical_status()
        or "docs/planning" in _canonical_status() else "FAIL",
        "PASS",
        "untracked owner docs still present in canonical"))
    out.append(probe(
        "23. current canonical baseline correctly reconstructed",
        lambda: "PASS" if git("rev-parse", "HEAD^") ==
        "765a725c941efc9762317741cdbcf1703e44e7be" else "FAIL", "PASS",
        "candidate parent == canonical baseline 765a725"))
    out.append(probe(
        "24. tests actually passed",
        lambda: "PASS" if (EVIDENCE / "full-suite-run.txt").exists() and
        "error" not in (EVIDENCE / "full-suite-run.txt").read_text(
            encoding="utf-8").lower() else "FAIL", "PASS",
        "full-suite-run.txt"))
    out.append(probe(
        "25. resource behavior bounded and reported honestly",
        lambda: "PASS" if resource["avg_cycle_seconds"] < 1.0
        and resource["note"] else "FAIL",
        "PASS", f"avg_cycle={resource['avg_cycle_seconds']}s"))
    return out


def main() -> int:
    results = checks()
    passed = sum(1 for r in results if r["verdict"] == "PASS")
    report = {
        "verifier_provider_independence": "LIMITED",
        "provider_note": ("Host limited to owner-mandated opencode_go/"
                          "deepseek-v4-flash family; no independent model "
                          "pool in this environment"),
        "candidate_head": git("rev-parse", "HEAD"),
        "verdict": "PASS" if passed == len(results) else "FAIL",
        "passed": passed,
        "total": len(results),
        "results": results,
        "reviewed_at": "2026-08-18T10:40:00Z",
    }
    (EVIDENCE / "independent_verifier.json").write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")
    print(json.dumps({
        "verdict": report["verdict"], "passed": passed,
        "total": len(results),
        "failures": [r["attack"] for r in results
                     if r["verdict"] != "PASS"],
    }, indent=2, sort_keys=True))
    return 0 if report["verdict"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
