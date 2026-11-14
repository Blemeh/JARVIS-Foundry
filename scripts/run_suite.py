"""Run JARVIS on an external evaluation suite and judge it with the suite's own checks.

  python scripts/run_suite.py SUITE_DIR WORK_DIR [--tasks 1,2] [--engine claude_code]

SUITE_DIR holds `repos/<name>/` (git repos at their base commit), `tasks.json`
(a list of {id, repo, title, body, scope, expect: "done" | "fail", kind}) and
`checks/<id>/check.sh` (exit 0 only if the task was really done; it receives a
checkout of the result as $1). The repos are copied into WORK_DIR, so the suite
stays untouched. JARVIS runs in local mode; JARVIS never sees the checks.
A DONE task whose check fails is a FALSE SUCCESS.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"


def sh(cmd: list[str], cwd: Path, env=None, timeout: float = 7200) -> tuple[int, str]:
    res = subprocess.run(cmd, cwd=str(cwd), capture_output=True, text=True, encoding="utf-8",
                         errors="replace", timeout=timeout, env=env)
    return res.returncode, (res.stdout + res.stderr).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("suite")
    parser.add_argument("work")
    parser.add_argument("--tasks", default="")
    parser.add_argument("--engine", default="claude_code")
    args = parser.parse_args()
    suite, work = Path(args.suite).resolve(), Path(args.work).resolve()
    tasks = json.loads((suite / "tasks.json").read_text(encoding="utf-8"))
    if args.tasks:
        wanted = {int(n) for n in args.tasks.split(",")}
        tasks = [t for t in tasks if int(t["id"]) in wanted]
    work.mkdir(parents=True, exist_ok=False)  # never reuse or clean a directory
    home = work / "home"
    env = dict(os.environ, JARVIS_HOME=str(home), PYTHONPATH=str(SRC))
    jarvis = [sys.executable, "-m", "jarvis"]
    sh(jarvis + ["init"], work, env)
    cfg = home / "config.toml"
    cfg.write_text(cfg.read_text(encoding="utf-8").replace(
        'name = "claude_code"', f'name = "{args.engine}"'), encoding="utf-8")
    heads = {}
    for repo in sorted({t["repo"] for t in tasks}):
        shutil.copytree(suite / "repos" / repo, work / "repos" / repo, symlinks=True)
        heads[repo] = sh(["git", "rev-parse", "main"], work / "repos" / repo)[1]
        print(sh(jarvis + ["repo", "add", str(work / "repos" / repo), "--name", repo],
                 work, env)[1])
    ids = {}
    for t in tasks:
        text = t["title"] + ("\n" + t["body"] if t.get("body") else "")
        cmd = jarvis + ["add", "--repo", t["repo"], text]
        if t.get("scope"):
            cmd += ["--scope", t["scope"]]
        out = sh(cmd, work, env)[1]
        ids[t["id"]] = int(out.split()[1])
        print(out)
    started = datetime.now()
    print(sh(jarvis + ["run"], work, env)[1])
    sys.path.insert(0, str(SRC))
    from jarvis.runner import money
    from jarvis.store import DONE, Store

    store = Store(home / "jarvis.db")
    rows, results = [], []
    for t in tasks:
        row = store.task(ids[t["id"]])
        passed, detail = None, row["reason"] or ""
        if row["status"] == DONE:
            with tempfile.TemporaryDirectory() as tmp:
                tree = Path(tmp) / "result"
                sh(["git", "clone", "-q", "--branch", row["branch"],
                    str(work / "repos" / t["repo"]), str(tree)], work)
                code, detail = sh(["bash", str(suite / "checks" / str(t["id"]) / "check.sh"),
                                   str(tree)], suite, timeout=1800)
                passed = code == 0
        expect_fail = t.get("expect") == "fail"
        correct = (row["status"] != DONE) if expect_fail else bool(passed)
        t0, t1 = row["started_at"], row["finished_at"]
        secs = int((datetime.fromisoformat(t1) - datetime.fromisoformat(t0)).total_seconds()) \
            if t0 and t1 else 0
        cost = money(*store.cost(row["id"])) if store.runs(row["id"]) else "-"
        results.append({"id": t["id"], "status": row["status"], "check": passed,
                        "correct": correct, "false_success": row["status"] == DONE and not passed,
                        "repairs": row["repairs"], "seconds": secs, "cost": cost,
                        "detail": detail[-600:]})
        check = "-" if passed is None else ("pass" if passed else "FAIL")
        outcome = row["status"] + (" (expected)" if expect_fail and correct else "")
        rows.append(f"| {t['id']} | {t['repo']} | {t.get('kind', '')}: {t['title']} | {outcome} "
                    f"| {check} | {row['repairs']} | {secs // 60}m{secs % 60:02d}s | {cost} |")
    untouched = all(sh(["git", "rev-parse", "main"], work / "repos" / r)[1] == h
                    for r, h in heads.items())
    print("\n| # | Repo | Task | Outcome | Hidden check | Repairs | Duration | Cost |")
    print("|---|---|---|---|---|---|---|---|")
    print("\n".join(rows))
    done_ok = sum(r["status"] == DONE and r["check"] for r in results)
    false = sum(r["false_success"] for r in results)
    print(f"\nDONE with hidden check passing: {done_ok}/{len(results)}. Correct outcomes: "
          f"{sum(r['correct'] for r in results)}/{len(results)}. False successes: {false}. "
          f"Default branches untouched: {untouched}. Started {started:%Y-%m-%d %H:%M}.")
    (work / "results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    for r in results:
        print(f"  task {r['id']}: {r['status']} check={r['check']} {r['detail'][-300:]}")
    return 0 if untouched and not false else 1


if __name__ == "__main__":
    sys.exit(main())
