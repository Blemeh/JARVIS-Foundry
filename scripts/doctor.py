"""Machine/project health check: ``python scripts/doctor.py``.

Read-only by default. It reports the kinds of drift that quietly accumulate
around this project (found the hard way on 2026-10-21):

* git: branch, dirty tree, divergence from ``origin/main``, worktree/branch sprawl
* live install: the release ``CURRENT`` points at vs ``main``, Supervisor task state
* user ``PATH``: length, entries pointing nowhere, leaked test-temp entries
* scheduled tasks mentioning JARVIS/Hermes: state, and enabled ones with missing targets
* leftover scratch: ``C:\\temp`` JARVIS-ish folders, repo scratch dirs
* docs: staleness gate and the age of the roadmap execution-state file
* environment hazards: ``HERMES_HOME`` / ``OBSIDIAN_VAULT`` overrides

The only thing it can change is the user PATH, and only with ``--fix-path``: that
removes *test-temp* entries (pytest directories) and entries whose directory no
longer exists, after saving the original value next to ``--backup-dir``.

Exit status: 0 healthy, 1 warnings, 2 problems.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

OK, WARN, FAIL = "OK", "WARN", "FAIL"

# Directories that only ever appear on PATH because a test (or probe) bootstrapped
# a throw-away install: pytest basetemp dirs, pytest scratch under the repo, ...
_TEST_TEMP = re.compile(
    r"(?i)(\\pytest-of-[^\\]+\\|test_cli_setup_show_and_test\d*\\|\\\.pytest[^\\]*\\|\\pytest-\d+\\)"
)


@dataclass
class Finding:
    area: str
    status: str
    summary: str
    detail: list[str] = field(default_factory=list)


def _child_env() -> dict[str, str]:
    """Environment safe for spawning Windows PowerShell (repairs PSModulePath)."""
    try:
        sys.path.insert(0, str(ROOT / "src"))
        from jarvis_foundry.platform.windows_subprocess import sanitized_env

        return sanitized_env()
    except Exception:  # noqa: BLE001 - best effort; the plain environment still works for git
        return dict(os.environ)


def _run(*cmd: str, cwd: Path | None = None, timeout: float = 30.0) -> tuple[int, str]:
    try:
        done = subprocess.run(list(cmd), cwd=cwd or ROOT, capture_output=True, text=True, env=_child_env(),
                              timeout=timeout, encoding="utf-8", errors="replace")
        return done.returncode, (done.stdout + done.stderr).strip()
    except (OSError, subprocess.TimeoutExpired) as error:
        return 1, f"{type(error).__name__}: {error}"


# ---------------------------------------------------------------- PATH logic


def split_path(value: str) -> list[str]:
    return [part for part in (value or "").split(";") if part]


def classify_path_entries(entries: list[str], exists=lambda p: Path(os.path.expandvars(p)).exists()) -> dict[str, list[str]]:
    """Bucket PATH entries: ``test_temp``, ``missing`` (not test-temp), ``healthy``, ``duplicate``."""
    buckets: dict[str, list[str]] = {"test_temp": [], "missing": [], "healthy": [], "duplicate": []}
    seen: set[str] = set()
    for entry in entries:
        key = os.path.normcase(os.path.normpath(entry))
        if key in seen:
            buckets["duplicate"].append(entry)
            continue
        seen.add(key)
        if _TEST_TEMP.search(entry + "\\"):
            buckets["test_temp"].append(entry)
        elif not exists(entry):
            buckets["missing"].append(entry)
        else:
            buckets["healthy"].append(entry)
    return buckets


def cleaned_path(entries: list[str], buckets: dict[str, list[str]]) -> str:
    """The PATH without test-temp/dead entries, duplicates collapsed (first wins)."""
    bad = set(buckets["test_temp"]) | set(buckets["missing"])
    kept: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        if entry in bad:
            continue
        key = os.path.normcase(os.path.normpath(entry))
        if key in seen:
            continue
        seen.add(key)
        kept.append(entry)
    return ";".join(kept)

def read_user_path() -> str | None:
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_READ) as key:
            return str(winreg.QueryValueEx(key, "Path")[0])
    except OSError:
        return None


def write_user_path(value: str) -> None:
    import winreg

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0,
                        winreg.KEY_READ | winreg.KEY_WRITE) as key:
        winreg.SetValueEx(key, "Path", 0, winreg.REG_EXPAND_SZ, value)


def check_path(fix: bool, backup_dir: Path) -> Finding:
    raw = read_user_path()
    if raw is None:
        return Finding("path", OK, "user PATH not applicable on this platform")
    entries = split_path(raw)
    buckets = classify_path_entries(entries)
    problems = len(buckets["test_temp"]) + len(buckets["missing"]) + len(buckets["duplicate"])
    detail = [f"{len(entries)} entries, {len(raw)} chars (Windows warns past ~2000)"]
    for name in ("test_temp", "missing", "duplicate"):
        detail += [f"{name}: {item}" for item in buckets[name][:8]]
        if len(buckets[name]) > 8:
            detail.append(f"{name}: ... and {len(buckets[name]) - 8} more")
    if problems and fix:
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        backup = backup_dir / f"user-PATH-backup-{stamp}.txt"
        backup.write_text(raw, encoding="utf-8")
        write_user_path(cleaned_path(entries, buckets))
        detail.append(f"FIXED: removed {problems} entries; original saved to {backup}")
        return Finding("path", OK, f"user PATH cleaned ({problems} entries removed)", detail)
    if problems:
        detail.append("run with --fix-path to remove them (a backup is written first)")
        status = WARN if len(buckets["test_temp"]) < 10 and len(raw) < 1900 else FAIL
        return Finding("path", status, f"user PATH has {problems} dead/leaked entries", detail)
    return Finding("path", OK, "user PATH is clean", detail)


# ---------------------------------------------------------------------- git


def check_git() -> list[Finding]:
    findings: list[Finding] = []
    code, branch = _run("git", "branch", "--show-current")
    if code != 0:
        return [Finding("git", FAIL, "not a git checkout", [branch])]
    _, status = _run("git", "status", "--porcelain")
    dirty = [line for line in status.splitlines() if line.strip()]
    _run("git", "fetch", "-q", "origin", "main", timeout=45)
    _, ahead_behind = _run("git", "rev-list", "--left-right", "--count", "origin/main...HEAD")
    behind, ahead = (ahead_behind.split() + ["?", "?"])[:2]
    _, local_main = _run("git", "rev-parse", "main")
    _, remote_main = _run("git", "rev-parse", "origin/main")
    detail = [f"branch {branch}; {ahead} ahead / {behind} behind origin/main",
              f"main {local_main[:8]} vs origin/main {remote_main[:8]}"]
    status_level = OK
    if local_main != remote_main:
        status_level = WARN
        detail.append("local main differs from origin/main")
    if dirty:
        status_level = WARN
        detail += [f"dirty: {line}" for line in dirty[:10]]
    findings.append(Finding("git", status_level, f"{branch}: {len(dirty)} uncommitted change(s)", detail))

    _, worktrees = _run("git", "worktree", "list")
    wt_count = len(worktrees.splitlines())
    _, branches = _run("git", "branch", "--format=%(refname:short)")
    _, unmerged = _run("git", "branch", "--no-merged", "main", "--format=%(refname:short)")
    unmerged_count = len([b for b in unmerged.splitlines() if b.strip()])
    sprawl = WARN if wt_count > 12 or unmerged_count > 60 else OK
    findings.append(Finding(
        "sprawl", sprawl,
        f"{wt_count} worktree(s), {len(branches.splitlines())} local branch(es), {unmerged_count} not merged into main",
        ["many worktrees/branches slow git and hide the canonical line of work"] if sprawl != OK else []))
    return findings


# ------------------------------------------------------------- live install


def _local_app_data() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))


def _dir_size(path: Path, limit: int = 200_000) -> int:
    """Best-effort byte size of a directory tree (bounded walk)."""
    total = 0
    seen = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
            seen += 1
            if seen >= limit:
                return total
    return total

def check_live_install() -> list[Finding]:
    install = _local_app_data() / "JARVIS"
    current_file = install / "CURRENT"
    if not current_file.exists():
        return [Finding("live", WARN, "no installed release found", [str(current_file)])]
    current = current_file.read_text(encoding="utf-8", errors="replace").strip()
    _, behind = _run("git", "rev-list", "--count", f"{current}..main")
    detail = [f"installed CURRENT {current[:12]}",
              f"main is {behind} commit(s) ahead of the installed release"
              if behind.isdigit() else "installed release is not an ancestor of main (or unknown here)"]
    releases = install / "releases"
    if releases.exists():
        folders = [p for p in releases.iterdir() if p.is_dir()]
        sample = _dir_size(folders[0]) if folders else 0
        detail.append(
            f"{len(folders)} release folder(s) under {releases}, about "
            f"{len(folders) * sample / 1024**3:.0f} GB (estimated from one release)")
        previous_file = install / "PREVIOUS"
        previous = previous_file.read_text(encoding="utf-8", errors="replace").strip() if previous_file.exists() else ""
        stale = [p.name for p in folders if p.name not in {current, previous}]
        if len(stale) > 6:
            detail.append(
                f"{len(stale)} release(s) are neither CURRENT nor PREVIOUS (the rollback target); "
                "they are immutable, rebuildable history. The product has no retention policy; "
                "pruning is an owner decision on the live install.")
    findings = [Finding("live", WARN if behind.isdigit() and int(behind) > 0 else OK,
                        "installed release lags main" if behind.isdigit() and int(behind) > 0 else "installed release matches main",
                        detail + ["promotion requires an explicit owner goal (docs/agent/workflows/promotion.md)"])]
    findings.append(_scheduled_tasks())
    return findings


def _scheduled_tasks() -> Finding:
    if sys.platform != "win32":
        return Finding("tasks", OK, "scheduled tasks not applicable on this platform")
    script = ("Get-ScheduledTask | Where-Object { $_.TaskName -match 'JARVIS|Hermes|Foundry' } | "
              "ForEach-Object { $a = ($_.Actions | ForEach-Object { $_.Execute + ' ' + $_.Arguments }) -join ' | '; "
              "'{0}`t{1}`t{2}' -f $_.TaskName, $_.State, $a }")
    code, out = _run("powershell", "-NoProfile", "-Command", script, timeout=45)
    if code != 0:
        return Finding("tasks", WARN, "could not list scheduled tasks (needs a normal interactive Windows session)", [out.splitlines()[0][:160] if out else "no output"])
    rows = [line.split("\t", 2) for line in out.splitlines() if "\t" in line]
    broken = []
    for name, state, action in rows:
        if state.strip().lower() == "disabled":
            continue
        for match in re.findall(r'"?([A-Za-z]:\\[^"|]+?\.(?:ps1|cmd|bat|vbs|exe))"?', action):
            if not Path(match).exists():
                broken.append(f"{name}: target missing: {match}")
    disabled = sum(1 for _, state, _ in rows if state.strip().lower() == "disabled")
    detail = [f"{name}  [{state}]" for name, state, _ in rows] + broken
    return Finding("tasks", WARN if broken else OK,
                   f"{len(rows)} related task(s), {disabled} disabled, {len(broken)} enabled with a missing target", detail)


# -------------------------------------------------------------- scratch/docs


def _dir_count(path: Path, pattern: re.Pattern[str]) -> int:
    try:
        return sum(1 for child in path.iterdir() if child.is_dir() and pattern.search(child.name))
    except OSError:
        return 0


def check_scratch() -> Finding:
    detail: list[str] = []
    level = OK
    leftovers = _dir_count(Path("C:/temp"), re.compile(r"(?i)^jarvis")) if sys.platform == "win32" else 0
    if leftovers:
        level = WARN
        detail.append(f"C:\\temp has {leftovers} JARVIS-named scratch folder(s) (rehearsals/probes; safe to delete if no task points there)")
    repo_scratch = [p.name for p in ROOT.iterdir()
                    if p.is_dir() and re.match(r"(?i)^(pytest-of-|tmp.+|v8-test-|jarvis-lane3-proof-|pip-)", p.name)]
    if repo_scratch:
        level = WARN
        detail.append(f"repo root has {len(repo_scratch)} stray scratch folder(s): {', '.join(repo_scratch[:5])}")
    return Finding("scratch", level, "leftover scratch found" if detail else "no leftover scratch", detail)


def check_docs() -> list[Finding]:
    findings: list[Finding] = []
    code, out = _run(sys.executable, str(ROOT / "scripts" / "doc_staleness_check.py"), timeout=60)
    findings.append(Finding("docs", OK if code == 0 else WARN,
                            "documentation pin current" if code == 0 else "documentation pin stale (CURRENT_STATE.md)",
                            [] if code == 0 else out.splitlines()[-3:]))
    state = ROOT / "docs" / "roadmap" / "JARVIS_1_0_EXECUTION_STATE.yaml"
    if state.exists():
        match = re.search(r'last_reconciled:\s*"(\d{4}-\d{2}-\d{2})"', state.read_text(encoding="utf-8"))
        if match:
            age = (datetime.now(timezone.utc).date() - datetime.strptime(match.group(1), "%Y-%m-%d").date()).days
            findings.append(Finding("roadmap", WARN if age > 30 else OK,
                                    f"execution-state last reconciled {age} day(s) ago",
                                    ["the file names the only authorized next gate; a stale pointer invites drift"] if age > 30 else []))
    return findings


def check_env() -> Finding:
    hazards = [f"{name}={os.environ[name]}" for name in ("HERMES_HOME", "OBSIDIAN_VAULT", "JARVIS_INSTALL_ROOT")
               if os.environ.get(name)]
    return Finding("env", OK, "live-machine overrides present (tests redirect these)" if hazards else "no overrides set", hazards)


# -------------------------------------------------------------------- main


def run_checks(fix_path: bool, backup_dir: Path, skip: set[str]) -> list[Finding]:
    findings: list[Finding] = []
    if "path" not in skip:
        findings.append(check_path(fix_path, backup_dir))
    if "git" not in skip:
        findings += check_git()
    if "live" not in skip:
        findings += check_live_install()
    if "scratch" not in skip:
        findings.append(check_scratch())
    if "docs" not in skip:
        findings += check_docs()
    findings.append(check_env())
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fix-path", action="store_true", help="remove leaked/dead user PATH entries (backup written first)")
    parser.add_argument("--backup-dir", type=Path, default=Path.home() / "Documents" / "JARVIS-doctor-backups")
    parser.add_argument("--skip", action="append", default=[], choices=("path", "git", "live", "scratch", "docs"))
    parser.add_argument("--json", type=Path, help="also write the findings to this file")
    args = parser.parse_args(argv)

    findings = run_checks(args.fix_path, args.backup_dir, set(args.skip))
    for item in findings:
        print(f"{item.status:<4} {item.area:<8} {item.summary}")
        for line in item.detail:
            print(f"       {line}")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps([asdict(f) for f in findings], indent=2), encoding="utf-8")
    worst = max((f.status for f in findings), key=[OK, WARN, FAIL].index)
    return {OK: 0, WARN: 1, FAIL: 2}[worst]


if __name__ == "__main__":
    sys.exit(main())
