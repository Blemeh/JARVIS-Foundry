"""Coding engines behind one interface. The engine and model come from config."""

from __future__ import annotations

from .base import AgentRequest, AgentResult, AgentRunner

ENGINES = ("claude_code", "codex", "hermes", "fake")


def make_runner(cfg) -> AgentRunner:
    if cfg.engine == "claude_code":
        from .claude_code import ClaudeCode as cls
    elif cfg.engine == "codex":
        from .codex import Codex as cls
    elif cfg.engine == "hermes":
        from .hermes import Hermes as cls
    elif cfg.engine == "fake":
        from .fake import FakeAgent

        return FakeAgent.from_file(cfg.script)
    else:
        raise ValueError(f"unknown engine {cfg.engine!r}; choose one of {', '.join(ENGINES)}")
    return cls(model=cfg.model, command=cfg.command, args=cfg.args)


__all__ = ["AgentRequest", "AgentResult", "AgentRunner", "ENGINES", "make_runner"]
