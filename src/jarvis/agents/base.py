"""The AgentRunner interface."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass
class AgentRequest:
    prompt: str
    cwd: Path
    timeout_s: float
    kind: str = "code"  # code | repair | plan | review
    attempt: int = 0
    read_only: bool = False
    max_cost_usd: float | None = None


@dataclass
class AgentResult:
    ok: bool
    output: str  # the agent's final message
    cost_usd: float | None  # None: the engine did not report cost
    model: str
    duration_s: float
    timed_out: bool = False
    stopped: bool = False
    error: str = ""
    exit_code: int | None = None
    transcript: str = ""  # raw engine output, kept on disk for `jarvis show`


class AgentRunner:
    name = "base"

    def __init__(self, model: str = "", command: str = "", args: list[str] | None = None):
        self.model = model
        self.command = command
        self.args = list(args or [])

    def run(self, req: AgentRequest, should_stop: Callable[[], bool]) -> AgentResult:
        raise NotImplementedError


def from_proc(res, output: str, cost: float | None, model: str, error: str = "") -> AgentResult:
    """Build a result from a finished subprocess (see jarvis.proc.Result)."""
    if res.timed_out:
        error = error or "timed out"
    elif res.stopped:
        error = error or "stopped"
    elif res.code != 0 and not error:
        error = f"exit code {res.code}: {(res.err or res.out).strip()[-500:]}"
    return AgentResult(ok=not error, output=output, cost_usd=cost, model=model,
                       duration_s=res.duration, timed_out=res.timed_out, stopped=res.stopped,
                       error=error, exit_code=res.code, transcript=res.out + "\n" + res.err)
