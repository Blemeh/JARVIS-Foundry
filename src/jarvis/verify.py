"""Independent verification: never trust the agent, check the worktree itself."""

from __future__ import annotations

import fnmatch
import os
import re
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from . import proc, worktree
from .repo_profile import Profile

PASS, EMPTY, SCOPE, COMMANDS, NO_COMMANDS, STOPPED = (
    "pass", "empty", "scope", "commands", "no_commands", "stopped")

# Paths that are tests in the usual layouts of Python, JS/TS, Go, Rust, Java and Ruby.
TEST_PATH = re.compile(
    r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]*\.py$|_test\.(py|go)$"
    r"|\.(test|spec)\.[cm]?[jt]sx?$|Tests?\.(java|kt|cs)$|_spec\.rb$")


def test_files(files: list[str]) -> list[str]:
    return [f for f in files if TEST_PATH.search(f)]


@dataclass
class CommandResult:
    name: str
    command: str
    code: int | None
    output: str
    duration: float
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.code == 0 and not self.timed_out


@dataclass
class Verdict:
    kind: str
    reasons: list[str]
    files: list[str] = field(default_factory=list)
    results: list[CommandResult] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.kind == PASS

    def feedback(self) -> str:
        """What the agent sees on a repair attempt."""
        parts = list(self.reasons)
        for r in self.results:
            if not r.ok:
                status = "timed out" if r.timed_out else f"exit code {r.code}"
                parts.append(f"$ {r.command}\n({status})\n{r.output}")
        return "\n\n".join(parts)


def _matches(path: str, pattern: str) -> bool:
    pattern = pattern.replace("\\", "/").strip()
    if any(ch in pattern for ch in "*?["):
        return fnmatch.fnmatchcase(path, pattern)
    pattern = pattern.strip("/")
    return pattern in ("", ".") or path == pattern or path.startswith(pattern + "/")


def scope_violations(files: list[str], allowed: list[str], protected: list[str]) -> list[str]:
    bad = []
    for path in files:
        if allowed and not any(_matches(path, p) for p in allowed):
            bad.append(f"{path} is outside the allowed scope ({', '.join(allowed)})")
        elif any(_matches(path, p) for p in protected):
            bad.append(f"{path} is a protected path")
    return bad


def run_command(name: str, command: str, cwd: Path, timeout: float,
                should_stop: Callable[[], bool]) -> CommandResult:
    # Python validates .pyc files by mtime (1s resolution) and size, so a same-size
    # edit within a second can run stale bytecode. Give every command a fresh,
    # never-written cache location so verification always runs the current source.
    with tempfile.TemporaryDirectory(prefix="jarvis-pyc-", ignore_cleanup_errors=True) as pyc:
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONPYCACHEPREFIX=pyc)
        res = proc.run(command, cwd, timeout=timeout, should_stop=should_stop, env=env)
    output = proc.redact(proc.tail((res.out + ("\n" + res.err if res.err else "")).strip()))
    return CommandResult(name, command, res.code if not res.stopped else None, output,
                         round(res.duration, 1), res.timed_out)


def run_all(profile: Profile, cwd: Path, timeout: float,
            should_stop: Callable[[], bool]) -> list[CommandResult]:
    results = []
    for name, command in profile.commands():
        if should_stop():
            break
        results.append(run_command(name, command, cwd, timeout, should_stop))
    return results


def verify(wt: Path, base_sha: str, profile: Profile, *, allowed: list[str],
           protected: list[str], timeout: float, should_stop: Callable[[], bool],
           require_tests: bool = False) -> Verdict:
    files = worktree.changed_files(wt, base_sha)
    if not files:
        return Verdict(EMPTY, ["The agent produced no changes (empty diff)."])
    violations = scope_violations(files, allowed, protected)
    if violations:
        return Verdict(SCOPE, violations, files)
    commands = profile.commands()
    if not commands:
        return Verdict(NO_COMMANDS, ["No test or lint command is configured or detected, "
                                     "so the change cannot be verified."], files)
    results = run_all(profile, wt, timeout, should_stop)
    if should_stop():
        return Verdict(STOPPED, ["Stopped during verification."], files, results)
    reasons = []
    failed = [r for r in results if not r.ok]
    if failed:
        reasons.append(f"Verification failed: {', '.join(r.command for r in failed)}")
    if require_tests and not test_files(files):
        reasons.append("No test file was added or changed; this repo requires a test with "
                       "every change.")
    return Verdict(COMMANDS if reasons else PASS, reasons, files, results)
