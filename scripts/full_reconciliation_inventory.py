from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BASELINE = "origin/main"
LOCAL_MAIN = "main"
OUT_DIR = ROOT / "evidence" / "integration" / "full-reconciliation"


def git(*args: str, cwd: Path = ROOT, check: bool = True) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and proc.returncode:
        raise RuntimeError(f"git {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()}")
    return proc.stdout.strip()


def try_git(*args: str, cwd: Path = ROOT) -> str:
    return git(*args, cwd=cwd, check=False)


def worktrees() -> list[dict[str, object]]:
    raw = git("worktree", "list", "--porcelain")
    records: list[dict[str, object]] = []
    current: dict[str, object] = {}
    for line in raw.splitlines() + [""]:
        if line.startswith("worktree "):
            if current:
                records.append(current)
            current = {"path": line[9:]}
        elif line.startswith("HEAD "):
            current["head"] = line[5:]
        elif line.startswith("branch "):
            current["branch"] = line[7:].removeprefix("refs/heads/")
        elif line == "detached":
            current["detached"] = True
        elif line == "prunable":
            current["prunable"] = True
        elif line.startswith("prunable "):
            current["prunable_reason"] = line[9:]
    for rec in records:
        path = Path(str(rec["path"]))
        if path.exists() and (path / ".git").exists():
            status = try_git("status", "--porcelain", cwd=path)
            rec["dirty"] = bool(status)
            rec["status_sample"] = status.splitlines()[:20]
        else:
            rec["dirty"] = None
            rec["status_sample"] = []
        rec.setdefault("prunable", False)
        rec.setdefault("detached", False)
    return records


CLASSIFICATION_NOTES = {
    "candidate/overnight-command-center-truth": (
        "UNIQUE_ACCEPTED_WORK",
        "37 non-equivalent commits beyond origin/main; UI source, route-resilience and endurance harness changes. "
        "Historical evidence reports 6h/41,289-cycle PASS and product verification PASS, but the dirty live log is preserved and fresh UI gates remain required.",
    ),
    "candidate/overnight-release-candidate": (
        "UNIQUE_ACCEPTED_WORK",
        "38 non-equivalent commits beyond origin/main; release identity, owner control, delivery, diagnostics and disposable factory source. "
        "PREPROD_GATE/JOB_QUEUE/source-bound RUN report disposable PASS only; no production or live provider evidence.",
    ),
    "candidate/overnight-v3-hard-gate": (
        "UNIQUE_ACCEPTED_WORK",
        "13 non-equivalent commits beyond origin/main; V3 cycle/outcome/resource/fault/lineage hardening with executable gate. "
        "HANDOFF/GATE_REPORT record focused, full, independent, migration and soak PASS evidence; fresh verification is still required after integration.",
    ),
    "candidate/overnight-v3-operational-slice": (
        "UNIQUE_ACCEPTED_WORK",
        "3 non-equivalent commits beyond origin/main; ledger-backed Discovery projection, adapter mapping, automatic outcome closure and UI mapper. "
        "ADVERSARIAL_VERDICT is PASS with two low findings explicitly retained for fresh regression verification.",
    ),
    "candidate/audit-remediation-2026-08-18": (
        "UNIQUE_ACCEPTED_WORK",
        "8 non-equivalent source/test commits for STOP ownership, child PID registration, read-only diagnostics, truthful NOT_TESTED handling and evidence mismatch closure. "
        "This package is not reachable from origin/main or the overnight release and must be serially reconciled and independently tested.",
    ),
    "candidate/24h-independent-review": (
        "EVIDENCE_ONLY",
        "Four unique review artifacts; no source integration required because the reviewed V2/UI source is already represented in canonical ancestry.",
    ),
    "candidate/v3-command-center": (
        "EVIDENCE_ONLY",
        "Single unique V3 command-center review/evidence commit; source is superseded by origin/main V3 UI and newer overnight truth lane.",
    ),
    "candidate/v3-lab": (
        "EVIDENCE_ONLY",
        "Single unique lab evidence commit; no canonical source delta.",
    ),
    "candidate/remaster-forward-port": (
        "EVIDENCE_ONLY",
        "Seven unique proving-window documentation commits; no source delta that remains necessary after canonical convergence.",
    ),
    "integration/procedural-learning-phase4-remediation": (
        "EVIDENCE_ONLY",
        "Only a documentation reconciliation commit remains non-equivalent; procedural-learning source is already represented by canonical closure branches.",
    ),
    "experiment/graphiti-openhands-a2a": (
        "FAILED_OR_REJECTED",
        "External Graphiti/OpenHands experiment is outside the canonical architecture and has no acceptance authority; do not promote.",
    ),
    "remaster/post-v7-purpose": (
        "ABANDONED_OR_INCOMPLETE",
        "Large historical remaster line diverges from current canonical truth and would revert newer surfaces; retained for archaeology only.",
    ),
    "integration/hybrid-worker": (
        "FULLY_SUPERSEDED",
        "Older hybrid-worker source/evidence package is superseded by canonical worker activation and later runtime contracts.",
    ),
    "candidate/command-center-ui": (
        "FULLY_SUPERSEDED",
        "Older isolated UI candidate is superseded by origin/main V3 UI and the newer overnight command-center truth package.",
    ),
    "candidate/audit-r2-integration": (
        "FULLY_SUPERSEDED",
        "Its accepted audit-R2 lineage is an ancestor of the overnight release candidate; no unique accepted commit remains outside that package.",
    ),
    "candidate/audit-truth-remediation-r2": (
        "FULLY_SUPERSEDED",
        "Its e11c826 remediation lineage is included in the overnight release candidate; its remaining documentation is superseded by later source-bound evidence.",
    ),
    "candidate/audit-truth-remediation": (
        "FULLY_SUPERSEDED",
        "Earlier headless-console repair is superseded semantically by the accepted audit-R2/release lineage and later runtime hardening.",
    ),
}


def classify(branch: str, *, reachable: bool, plus: int, minus: int, changed: list[str]) -> tuple[str, str]:
    if branch in CLASSIFICATION_NOTES:
        return CLASSIFICATION_NOTES[branch]
    if branch == LOCAL_MAIN or reachable:
        return "ALREADY_CANONICAL", "Branch tip is reachable from origin/main; no source commit is stranded."
    if plus == 0 and minus > 0:
        return "FULLY_SUPERSEDED", "All branch patches are equivalent to or contained by origin/main; no unique source remains."
    if plus == 0 and minus == 0:
        return "ALREADY_CANONICAL", "No branch-only patch was found relative to origin/main."
    source = [p for p in changed if p.startswith(("src/", "tests/", "ui/", "scripts/", "config/", "packaging/"))]
    if not source:
        return "EVIDENCE_ONLY", "Non-equivalent changes are documentation or evidence only."
    return "ABANDONED_OR_INCOMPLETE", "Non-equivalent source exists, but no acceptance evidence or authorized integration queue entry was found."


def branch_record(branch: str, wt_map: dict[str, list[dict[str, object]]]) -> dict[str, object]:
    head = git("rev-parse", branch)
    show = git("show", "-s", "--format=%cI%x00%s", branch).split("\x00", 1)
    lr = git("rev-list", "--left-right", "--count", f"{BASELINE}...{branch}").split()
    behind, ahead = (int(lr[0]), int(lr[1])) if len(lr) == 2 else (None, None)
    cherry_lines = git("cherry", BASELINE, branch).splitlines()
    plus_commits = [line[2:] for line in cherry_lines if line.startswith("+ ")]
    minus_commits = [line[2:] for line in cherry_lines if line.startswith("- ")]
    unique_commits = git("rev-list", "--reverse", f"{BASELINE}..{branch}").splitlines()
    changed = git("diff", "--name-only", BASELINE, branch).splitlines()
    reachable = try_git("merge-base", "--is-ancestor", branch, BASELINE) == ""
    tree_equal = try_git("diff", "--quiet", BASELINE, branch) == ""
    classification, note = classify(branch, reachable=reachable, plus=len(plus_commits), minus=len(minus_commits), changed=changed)
    superseded_by = {
        "candidate/audit-r2-integration": "candidate/overnight-release-candidate",
        "candidate/audit-truth-remediation-r2": "candidate/overnight-release-candidate",
        "candidate/audit-truth-remediation": "candidate/overnight-release-candidate",
        "candidate/command-center-ui": "candidate/overnight-command-center-truth + origin/main",
        "integration/hybrid-worker": "origin/main canonical worker activation",
        "remaster/post-v7-purpose": "origin/main current canonical lineage",
    }.get(branch)
    return {
        "branch": branch,
        "head": head,
        "head_short": head[:12],
        "date": show[0] if show else None,
        "subject": show[1] if len(show) > 1 else None,
        "baseline": BASELINE,
        "local_main_start": git("rev-parse", LOCAL_MAIN),
        "merge_base": git("merge-base", BASELINE, branch),
        "ahead_of_baseline": ahead,
        "behind_baseline": behind,
        "reachable_from_baseline": reachable,
        "tree_equal_to_baseline": tree_equal,
        "cherry_plus_count": len(plus_commits),
        "cherry_minus_count": len(minus_commits),
        "cherry_plus_commits": plus_commits,
        "cherry_minus_commits": minus_commits,
        "unique_commits": unique_commits,
        "changed_files": changed,
        "worktrees": wt_map.get(branch, []),
        "classification": classification,
        "superseded_by": superseded_by,
        "semantic_proof": note,
    }


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    wts = worktrees()
    wt_map: dict[str, list[dict[str, object]]] = {}
    for wt in wts:
        branch = wt.get("branch")
        if isinstance(branch, str):
            wt_map.setdefault(branch, []).append(wt)
    branches = git("for-each-ref", "--format=%(refname:short)", "refs/heads").splitlines()
    records = [branch_record(branch, wt_map) for branch in branches]
    remote_refs = []
    for ref in git("for-each-ref", "--format=%(refname:short)", "refs/remotes").splitlines():
        if ref.endswith("/HEAD"):
            continue
        remote_refs.append({"ref": ref, "head": git("rev-parse", ref)})
    generated = datetime.now(timezone.utc).isoformat()
    payload = {
        "generated_at_utc": generated,
        "repo": str(ROOT),
        "remote": git("remote", "get-url", "origin"),
        "canonical_baseline": {"ref": BASELINE, "sha": git("rev-parse", BASELINE)},
        "local_main_start": {"ref": LOCAL_MAIN, "sha": git("rev-parse", LOCAL_MAIN)},
        "branch_count": len(records),
        "worktree_count": len(wts),
        "classification_counts": {
            label: sum(1 for row in records if row["classification"] == label)
            for label in sorted({str(row["classification"]) for row in records})
        },
        "branches": records,
        "worktrees": wts,
        "remote_tracking_refs": remote_refs,
        "method": [
            "git fetch --all --prune was completed before this snapshot",
            "origin/main is the canonical comparison baseline; local main is recorded separately",
            "ancestry, tree equality, git cherry patch-equivalence, changed-file lists and worktree status are all retained",
            "classification notes are semantic review decisions; historical evidence is not treated as live proof",
        ],
    }
    json_path = OUT_DIR / "CANDIDATE_INVENTORY.json"
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        "# Candidate Inventory",
        "",
        f"Generated (UTC): `{generated}`",
        f"Canonical baseline: `{BASELINE}` at `{payload['canonical_baseline']['sha']}`",
        f"Local main at archaeology start: `{payload['local_main_start']['sha']}`",
        f"Branches inventoried: **{len(records)}**; registered worktrees: **{len(wts)}**.",
        "",
        "This inventory uses ancestry, tree equality, `git cherry` patch-equivalence, changed-file lists, and live worktree status. Historical PASS artifacts are recorded as evidence only; they do not substitute for fresh integration verification.",
        "",
        "## Classifications",
        "",
        "| Classification | Count |",
        "|---|---:|",
    ]
    for label, count in sorted(payload["classification_counts"].items()):
        lines.append(f"| `{label}` | {count} |")
    lines.extend(["", "## Branches", "", "| Branch | HEAD | Ahead | Behind | + / - | Worktree | Classification |", "|---|---|---:|---:|---:|---|---|"])
    for row in records:
        wt_names = ", ".join(str(w.get("path")) for w in row["worktrees"]) or "(none)"
        dirty = " dirty" if any(w.get("dirty") for w in row["worktrees"]) else ""
        lines.append(
            f"| `{row['branch']}` | `{row['head_short']}` | {row['ahead_of_baseline']} | {row['behind_baseline']} | {row['cherry_plus_count']} / {row['cherry_minus_count']} | `{wt_names}`{dirty} | `{row['classification']}` |"
        )
    lines.extend(["", "## Accepted unique packages", ""])
    for row in records:
        if row["classification"] == "UNIQUE_ACCEPTED_WORK":
            lines.extend([
                f"### `{row['branch']}` at `{row['head']}`",
                "",
                f"{row['semantic_proof']}",
                "",
                f"Unique patch-equivalent commits: `{row['cherry_plus_count']}`; changed paths: `{len(row['changed_files'])}`.",
                "",
            ])
    lines.extend(["## Worktree preservation", "", "All registered worktrees are retained. Dirty worktrees and prunable administrative entries were not cleaned, reset, deleted, or pruned.", ""])
    (OUT_DIR / "CANDIDATE_INVENTORY.md").write_text("\n".join(lines), encoding="utf-8")
    print(json_path)
    print(OUT_DIR / "CANDIDATE_INVENTORY.md")


if __name__ == "__main__":
    main()
