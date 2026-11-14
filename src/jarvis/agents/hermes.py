"""Hermes Agent (optional), ported from the Foundry adapter's subprocess call.

`hermes -z <prompt> --in <worktree> --yolo --cli [--model M]`. Provider flags go
in `[engine] args`. No spend gateway, no model pinning, no profile rewriting.
Hermes does not report cost here, so cost is NOT REPORTED.
"""

from __future__ import annotations

import shlex

from .. import proc
from .base import AgentRequest, AgentResult, AgentRunner, from_proc


class Hermes(AgentRunner):
    name = "hermes"

    def argv(self, req: AgentRequest) -> list[str]:
        base = shlex.split(self.command) if self.command else ["hermes"]
        base[0] = proc.which(base[0])
        argv = base + ["-z", req.prompt, "--in", str(req.cwd), "--yolo", "--cli"]
        if self.model:
            argv += ["--model", self.model]
        return argv + self.args

    def run(self, req: AgentRequest, should_stop) -> AgentResult:
        res = proc.run(self.argv(req), req.cwd, timeout=req.timeout_s, should_stop=should_stop)
        return from_proc(res, res.out.strip()[-4000:], None, self.model or "hermes default")
