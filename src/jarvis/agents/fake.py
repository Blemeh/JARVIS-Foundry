"""Deterministic scripted agent for tests.

A script maps a call kind to steps: {"code": [step, ...], "plan": step, "review": step}.
Code/repair attempt N uses code[N] (the last step repeats). A step may hold:
  write {path: text}, delete [path], commit (bool), sleep seconds (a real child process, so
  STOP and timeouts exercise the kill path), exit code, cost, model, output.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from .. import proc
from .base import AgentRequest, AgentResult, AgentRunner

DEFAULT_STEP = {"output": "No changes (fake agent).", "cost": 0.0}


class FakeAgent(AgentRunner):
    name = "fake"

    def __init__(self, script: dict | None = None, **kwargs):
        super().__init__(**kwargs)
        self.script = script or {}
        self.prompts: list[tuple[str, str]] = []  # (kind, prompt) for assertions

    @classmethod
    def from_file(cls, path: str) -> FakeAgent:
        if not path:
            return cls()
        return cls(json.loads(Path(path).expanduser().read_text(encoding="utf-8")))

    def step(self, req: AgentRequest) -> dict:
        if req.kind in ("plan", "review"):
            return self.script.get(req.kind, {"output": f"fake {req.kind}", "cost": 0.0})
        steps = self.script.get("code") or [DEFAULT_STEP]
        return steps[min(req.attempt, len(steps) - 1)]

    def run(self, req: AgentRequest, should_stop) -> AgentResult:
        self.prompts.append((req.kind, req.prompt))
        step = self.step(req)
        cost = step.get("cost", 0.0)
        model = step.get("model", "fake-model")
        duration, timed_out, stopped = 0.0, False, False
        if step.get("sleep"):
            res = proc.run([sys.executable, "-c", f"import time; time.sleep({step['sleep']})"],
                           req.cwd, timeout=req.timeout_s, should_stop=should_stop,
                           on_start=req.on_start)
            duration, timed_out, stopped = res.duration, res.timed_out, res.stopped
            if timed_out or stopped:
                return AgentResult(False, "", cost, model, duration, timed_out, stopped,
                                   "timed out" if timed_out else "stopped")
        if not req.read_only or step.get("write_anyway"):
            for rel, text in (step.get("write") or {}).items():
                target = req.cwd / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(text, encoding="utf-8")
            for rel in step.get("delete") or []:
                (req.cwd / rel).unlink(missing_ok=True)
            if step.get("commit"):  # a disobedient agent that commits its own work
                for args in (["add", "-A"], ["commit", "-qm", "agent commit"]):
                    subprocess.run(["git", *args], cwd=req.cwd, capture_output=True)
        code = step.get("exit", 0)
        return AgentResult(code == 0, step.get("output", "done"), cost, model, duration,
                           error="" if code == 0 else f"exit code {code}", exit_code=code)
