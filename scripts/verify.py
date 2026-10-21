"""One-command project health check: ``python scripts/verify.py``.

Lanes (default ``all``):

* ``python`` - every ``tests/foundry/test_*.py`` file, run as its own pytest
  process in parallel with a per-file timeout. One hung or slow file can
  never stall the run or hide the other results.
* ``ui``     - command-center typecheck, lint, unit tests, production build.
* ``docs``   - ``scripts/doc_staleness_check.py``.

Examples::

    python scripts/verify.py                    # everything
    python scripts/verify.py --lane python -j 8 --timeout 240
    python scripts/verify.py --lane ui
    python scripts/verify.py --match v11 --match campaign   # subset by substring
    python scripts/verify.py --json out/verify.json

Exit status is 0 only when every selected lane passed. The summary lists each
non-passing file with its last failure line so a regression is attributable
without re-running anything.

The runner is deliberately stdlib-only and never touches the live install: it
points ``LOCALAPPDATA`` and the temp dirs at a throw-away directory.
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UI = ROOT / "ui" / "command-center"
TEST_DIR = ROOT / "tests" / "foundry"

_FAIL_LINE = re.compile(r"^(E\s+.+|FAILED .+|ERROR .+|.*Error: .+)$")


@dataclass
class FileResult:
    name: str
    status: str  # PASS | FAIL | TIMEOUT
    seconds: float
    detail: str = ""


@dataclass
class LaneResult:
    lane: str
    ok: bool
    seconds: float
    summary: str
    files: list[FileResult] = field(default_factory=list)


def python_executable() -> str:
    """Prefer the repo virtualenv so imports match the shipped dependencies."""
    candidates = (
        ROOT / ".venv" / "Scripts" / "python.exe",
        ROOT / ".venv" / "bin" / "python",
    )
    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return sys.executable


def writable(directory: Path) -> bool:
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=directory, delete=True):
            return True
    except OSError:
        return False


def scratch_root() -> Path:
    """A disposable directory for test temp files.

    Uses the system temp dir when it is writable. Otherwise (locked-down
    machines, agent sandboxes) falls back to ``<repo>/tmp``; that directory is
    git-ignored and named ``tmp`` because several disposable-root safety checks
    accept path components with that exact name.
    """
    system = Path(tempfile.gettempdir())
    if writable(system) and not _inside(system, ROOT):
        return Path(tempfile.mkdtemp(prefix="jarvis-verify-", dir=system))
    fallback = ROOT / "tmp"
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def hermetic_env(scratch: Path) -> dict[str, str]:
    env = dict(os.environ)
    local = scratch / "local-app-data"
    local.mkdir(parents=True, exist_ok=True)
    hermes = scratch / "hermes-home"
    hermes.mkdir(parents=True, exist_ok=True)
    env.update(
        LOCALAPPDATA=str(local),  # never read the live install / Foundry home
        HERMES_HOME=str(hermes),  # overrides LOCALAPPDATA for the live Hermes profile
        TEMP=str(scratch),
        TMP=str(scratch),
        TMPDIR=str(scratch),
        GIT_CEILING_DIRECTORIES=str(ROOT),
        PYTHONDONTWRITEBYTECODE="1",
    )
    for key in ("OBSIDIAN_VAULT", "JARVIS_OBSIDIAN_VAULT", "JARVIS_INSTALL_ROOT"):
        env.pop(key, None)
    return env


def discover_tests(matches: list[str]) -> list[Path]:
    files = sorted(TEST_DIR.glob("test_*.py"))
    if matches:
        files = [f for f in files if any(m in f.name for m in matches)]
    return files


def _last_failure_line(output: str) -> str:
    lines = [ln.strip() for ln in output.splitlines()]
    hits = [ln for ln in lines if _FAIL_LINE.match(ln)]
    text = hits[-1] if hits else (lines[-1] if lines else "")
    return text[:220]


def _kill_tree(process: subprocess.Popen) -> None:
    """Kill ``process`` and every descendant (scratch venvs, git, pip trials)."""
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                       capture_output=True, check=False)
    else:  # pragma: no cover - the project targets Windows
        process.kill()


def run_one_test_file(py: str, path: Path, timeout: float, env: dict[str, str]) -> FileResult:
    started = time.monotonic()
    cmd = [py, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--tb=line", str(path)]
    process = subprocess.Popen(
        cmd, cwd=ROOT, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace",
    )
    try:
        output, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(process)
        try:
            output, _ = process.communicate(timeout=15)
        except subprocess.TimeoutExpired:
            output = ""
        return FileResult(path.name, "TIMEOUT", time.monotonic() - started,
                          f"exceeded {timeout:.0f}s; {_last_failure_line(output or '')}".strip("; "))
    seconds = time.monotonic() - started
    if process.returncode == 0:
        return FileResult(path.name, "PASS", seconds)
    return FileResult(path.name, "FAIL", seconds, _last_failure_line(output))

def run_python_lane(jobs: int, timeout: float, matches: list[str], scratch: Path) -> LaneResult:
    started = time.monotonic()
    files = discover_tests(matches)
    py = python_executable()
    env = hermetic_env(scratch)
    results: list[FileResult] = []
    with futures.ThreadPoolExecutor(max_workers=max(1, jobs)) as pool:
        pending = {pool.submit(run_one_test_file, py, f, timeout, env): f for f in files}
        for done, future in enumerate(futures.as_completed(pending), 1):
            result = future.result()
            results.append(result)
            if result.status != "PASS":
                print(f"  [{done}/{len(files)}] {result.status:<7} {result.name}  {result.detail}", flush=True)
            elif done % 25 == 0:
                print(f"  [{done}/{len(files)}] ...", flush=True)
    results.sort(key=lambda r: (r.status != "PASS", r.name))
    counts = {s: sum(1 for r in results if r.status == s) for s in ("PASS", "FAIL", "TIMEOUT")}
    ok = counts["FAIL"] == 0 and counts["TIMEOUT"] == 0
    summary = f"{counts['PASS']} passed, {counts['FAIL']} failed, {counts['TIMEOUT']} timed out of {len(files)} files"
    return LaneResult("python", ok, time.monotonic() - started, summary, results)


def _node_env() -> dict[str, str]:
    env = dict(os.environ)
    node_dir = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "nodejs"
    if node_dir.exists():
        env["PATH"] = str(node_dir) + os.pathsep + env.get("PATH", "")
    return env


def _node_tool(relative: str, *args: str) -> list[str]:
    return ["node", str(UI / "node_modules" / relative), *args]


def run_ui_lane() -> LaneResult:
    started = time.monotonic()
    if not (UI / "node_modules").exists():
        return LaneResult("ui", False, 0.0,
                          "ui/command-center/node_modules missing; run `npm ci` there first")
    env = _node_env()
    steps = [
        ("typecheck", _node_tool("typescript/bin/tsc", "-b", "--noEmit")),
        ("lint", _node_tool("eslint/bin/eslint.js", "src", "e2e", "--max-warnings", "0")),
        ("unit tests", _node_tool("vitest/vitest.mjs", "run", "--reporter=dot")),
        ("build", _node_tool("vite/bin/vite.js", "build")),
    ]
    results: list[FileResult] = []
    for name, cmd in steps:
        step_start = time.monotonic()
        try:
            completed = subprocess.run(cmd, cwd=UI, env=env, capture_output=True, text=True,
                                       timeout=600, encoding="utf-8", errors="replace")
            status = "PASS" if completed.returncode == 0 else "FAIL"
            detail = "" if status == "PASS" else _last_failure_line(completed.stdout + "\n" + completed.stderr)
        except (subprocess.TimeoutExpired, FileNotFoundError) as error:
            status, detail = "FAIL", f"{type(error).__name__}: {error}"[:220]
        result = FileResult(name, status, time.monotonic() - step_start, detail)
        results.append(result)
        print(f"  ui {name:<10} {status}  {detail}", flush=True)
        if status != "PASS":
            break
    ok = all(r.status == "PASS" for r in results) and len(results) == len(steps)
    return LaneResult("ui", ok, time.monotonic() - started,
                      ", ".join(f"{r.name}={r.status}" for r in results), results)


def run_docs_lane(scratch: Path) -> LaneResult:
    started = time.monotonic()
    completed = subprocess.run(
        [python_executable(), str(ROOT / "scripts" / "doc_staleness_check.py")],
        cwd=ROOT, env=hermetic_env(scratch), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    ok = completed.returncode == 0
    detail = "" if ok else _last_failure_line(completed.stdout + "\n" + completed.stderr)
    return LaneResult("docs", ok, time.monotonic() - started,
                      "documentation pin current" if ok else detail,
                      [FileResult("doc_staleness_check", "PASS" if ok else "FAIL",
                                  time.monotonic() - started, detail)])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--lane", choices=("all", "python", "ui", "docs"), default="all")
    parser.add_argument("-j", "--jobs", type=int, default=max(2, (os.cpu_count() or 4) // 2))
    parser.add_argument("--timeout", type=float, default=240.0, help="per-test-file timeout (seconds)")
    parser.add_argument("--match", action="append", default=[], help="only test files whose name contains this")
    parser.add_argument("--json", type=Path, help="write the full result to this file")
    args = parser.parse_args(argv)

    scratch = scratch_root()
    lanes: list[LaneResult] = []
    try:
        if args.lane in ("all", "docs"):
            print("== docs", flush=True)
            lanes.append(run_docs_lane(scratch))
        if args.lane in ("all", "ui"):
            print("== ui", flush=True)
            lanes.append(run_ui_lane())
        if args.lane in ("all", "python"):
            print(f"== python ({args.jobs} jobs, {args.timeout:.0f}s per file)", flush=True)
            lanes.append(run_python_lane(args.jobs, args.timeout, args.match, scratch))
    finally:
        if scratch.name.startswith("jarvis-verify-"):
            shutil.rmtree(scratch, ignore_errors=True)

    print("\n== summary")
    for lane in lanes:
        print(f"{'PASS' if lane.ok else 'FAIL':<5} {lane.lane:<7} {lane.seconds:6.1f}s  {lane.summary}")
        for item in lane.files:
            if item.status != "PASS":
                print(f"        {item.status:<7} {item.name}  {item.detail}")
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps([asdict(lane) for lane in lanes], indent=2), encoding="utf-8")
    return 0 if lanes and all(lane.ok for lane in lanes) else 1


if __name__ == "__main__":
    sys.exit(main())
