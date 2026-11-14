"""Codex CLI: `codex exec --json`, prompt on stdin. Cost is not reported by Codex."""

from __future__ import annotations

import tempfile
from pathlib import Path

from .. import proc
from .base import AgentRequest, AgentResult, AgentRunner, from_proc


class Codex(AgentRunner):
    name = "codex"

    def argv(self, req: AgentRequest, last_message: Path) -> list[str]:
        argv = [proc.which(self.command or "codex"), "exec", "--json", "--ephemeral",
                "--skip-git-repo-check", "-C", str(req.cwd),
                "-s", "read-only" if req.read_only else "workspace-write",
                "-o", str(last_message)]
        if self.model:
            argv += ["-m", self.model]
        return argv + self.args + ["-"]

    def run(self, req: AgentRequest, should_stop) -> AgentResult:
        with tempfile.TemporaryDirectory(prefix="jarvis-codex-") as tmp:
            last = Path(tmp) / "last.txt"
            res = proc.run(self.argv(req, last), req.cwd, timeout=req.timeout_s,
                           should_stop=should_stop, stdin_text=req.prompt)
            output = last.read_text(encoding="utf-8", errors="replace") if last.exists() else ""
        return from_proc(res, output.strip(), None, self.model or "codex default")
