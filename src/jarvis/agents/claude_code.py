"""Claude Code headless: `claude -p --output-format json`, prompt on stdin."""

from __future__ import annotations

import json
from pathlib import Path

from .. import proc
from .base import AgentRequest, AgentResult, AgentRunner, from_proc

WRITE_TOOLS = "Bash,Read,Edit,Write,Glob,Grep,NotebookEdit,TodoWrite"
READ_TOOLS = "Read,Glob,Grep"


def parse(stdout: str) -> dict:
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                data = json.loads(line)
            except ValueError:
                continue
            if isinstance(data, dict) and data.get("type") == "result":
                return data
    try:
        data = json.loads(stdout)
        return data if isinstance(data, dict) else {}
    except ValueError:
        return {}


class ClaudeCode(AgentRunner):
    name = "claude_code"
    _help: str | None = None

    def supports(self, flag: str) -> bool:
        """Whether this claude CLI offers a flag (checked once with --help)."""
        if self._help is None:
            res = proc.run([proc.which(self.command or "claude"), "--help"], Path("."),
                           timeout=30)
            self._help = res.out + res.err
        return flag in self._help

    def argv(self, req: AgentRequest) -> list[str]:
        argv = [proc.which(self.command or "claude"), "-p", "--output-format", "json",
                "--no-session-persistence"]
        if self.supports("--strict-mcp-config"):
            # Unattended runs do not inherit the user's MCP servers; opt in via
            # [engine] args = ["--mcp-config", "..."]. Also keeps the prompt small.
            argv.append("--strict-mcp-config")
        if self.model:
            argv += ["--model", self.model]
        if req.max_cost_usd:
            argv += ["--max-budget-usd", f"{req.max_cost_usd:.2f}"]
        if req.read_only:
            argv += ["--permission-mode", "default", "--allowedTools", READ_TOOLS,
                     "--disallowedTools", "Bash,Edit,Write,NotebookEdit"]
            if self.supports("--tools"):  # only load what a read-only call can use
                argv += ["--tools", READ_TOOLS]
        else:
            # acceptEdits confines file edits to the worktree (the cwd); Bash is
            # pre-approved so tests can run unattended. JARVIS verifies the diff.
            argv += ["--permission-mode", "acceptEdits", "--allowedTools", WRITE_TOOLS]
        return argv + self.args

    def run(self, req: AgentRequest, should_stop) -> AgentResult:
        res = proc.run(self.argv(req), req.cwd, timeout=req.timeout_s, should_stop=should_stop,
                       stdin_text=req.prompt, on_start=req.on_start)
        data = parse(res.out)
        cost = data.get("total_cost_usd")
        usage = data.get("modelUsage") or {}
        model = max(usage, key=lambda m: (usage[m] or {}).get("costUSD", 0)) if usage \
            else (self.model or "default")
        error = ""
        if data.get("is_error") or (data and data.get("subtype") not in (None, "success")):
            error = f"{data.get('subtype', 'error')}: {str(data.get('result', ''))[:500]}"
        elif not data and res.code == 0:
            error = "no JSON result from claude"
        return from_proc(res, str(data.get("result") or ""),
                         float(cost) if isinstance(cost, int | float) else None, model, error)
