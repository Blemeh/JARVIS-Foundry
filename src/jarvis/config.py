"""Configuration: $JARVIS_HOME/config.toml (default ~/.jarvis)."""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path


class ConfigError(Exception):
    pass


@dataclass
class RepoConfig:
    name: str
    path: Path
    github: str = ""  # "owner/name" enables GitHub mode; empty means local mode
    base: str = ""  # base branch for task branches; empty: detect the default branch
    remote: str = "origin"
    setup: str | None = None  # None: detect from manifests; "": none
    test: str | None = None
    lint: str | None = None
    build: str | None = None
    scope: list[str] = field(default_factory=list)  # allowed paths; empty: whole repo
    protected: list[str] = field(default_factory=list)  # paths the agent may never touch
    branch_prefix: str = "jarvis/"
    draft: bool = False
    label: str = "jarvis"


@dataclass
class Config:
    home: Path
    engine: str = "claude_code"
    model: str = ""  # empty: the engine's own default
    command: str = ""  # empty: the engine's default executable
    args: list[str] = field(default_factory=list)  # extra engine CLI arguments
    script: str = ""  # fake engine only: path to a JSON script
    agent_timeout_s: int = 1800
    max_repairs: int = 2
    task_timeout_s: int = 3600
    max_cost_usd: float = 5.0  # 0 disables the cap
    verify_timeout_s: int = 900
    plan_over_words: int = 150  # 0: plan only with `jarvis add --plan`
    review: bool = True
    concurrency: int = 1
    poll_interval_s: int = 120
    webhook: str = ""
    repos: dict[str, RepoConfig] = field(default_factory=dict)

    @property
    def path(self) -> Path:
        return self.home / "config.toml"

    @property
    def db_path(self) -> Path:
        return self.home / "jarvis.db"


# section -> {toml key: Config attribute}
SECTIONS = {
    "engine": {"name": "engine", "model": "model", "command": "command", "args": "args",
               "script": "script", "timeout_s": "agent_timeout_s"},
    "limits": {"max_repairs": "max_repairs", "task_timeout_s": "task_timeout_s",
               "max_cost_usd": "max_cost_usd", "verify_timeout_s": "verify_timeout_s",
               "plan_over_words": "plan_over_words"},
    "review": {"enabled": "review"},
    "serve": {"concurrency": "concurrency", "poll_interval_s": "poll_interval_s"},
    "webhook": {"url": "webhook"},
}

TEMPLATE = """\
# JARVIS configuration. Reference: README.md, "Config reference".

[engine]
name = "claude_code"   # claude_code | codex | hermes | fake
model = ""             # empty: the engine's own default model
command = ""           # empty: the engine's default executable
timeout_s = 1800       # per agent call

[limits]
max_repairs = 2        # repair attempts after a failed verification
task_timeout_s = 3600  # wall-clock cap per task
max_cost_usd = 5.0     # cost cap per task (0 disables; only engines that report cost)
verify_timeout_s = 900 # per verification command
plan_over_words = 150  # tasks longer than this need an approved plan (0: only --plan)

[review]
enabled = true         # second agent call lists risks in the PR body

[serve]
concurrency = 1
poll_interval_s = 120  # GitHub issue polling interval

[webhook]
url = ""               # POST {"event": "pr_ready" | "failed", ...}

# Repositories are added with `jarvis repo add`.
"""


def jarvis_home() -> Path:
    value = os.environ.get("JARVIS_HOME")
    return Path(value).expanduser() if value else Path.home() / ".jarvis"


def _check_type(where: str, value, default) -> None:
    expected = type(default)
    if expected is float and isinstance(value, int) and not isinstance(value, bool):
        return
    if default is None or isinstance(default, Path):
        expected = str
    if not isinstance(value, expected) or (expected is int and isinstance(value, bool)):
        raise ConfigError(f"{where}: expected {expected.__name__}, got {value!r}")


def load(home: Path | None = None) -> Config:
    home = home or jarvis_home()
    cfg = Config(home=home)
    if not cfg.path.exists():
        raise ConfigError(f"no config at {cfg.path}; run `jarvis init` first")
    try:
        data = tomllib.loads(cfg.path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"{cfg.path}: {exc}") from exc
    for section, values in data.items():
        if section == "repos":
            continue
        mapping = SECTIONS.get(section)
        if mapping is None or not isinstance(values, dict):
            raise ConfigError(f"unknown config section [{section}]")
        for key, value in values.items():
            if key not in mapping:
                raise ConfigError(f"unknown key {key!r} in [{section}]")
            attr = mapping[key]
            _check_type(f"[{section}] {key}", value, getattr(Config(home=home), attr))
            setattr(cfg, attr, float(value) if attr == "max_cost_usd" else value)
    repo_fields = {f.name for f in fields(RepoConfig)} - {"name"}
    for name, values in data.get("repos", {}).items():
        unknown = set(values) - repo_fields
        if unknown:
            raise ConfigError(f"unknown key(s) {sorted(unknown)} in [repos.{name}]")
        if "path" not in values:
            raise ConfigError(f"[repos.{name}] needs a path")
        template = RepoConfig(name=name, path=Path("."))
        for key, value in values.items():
            _check_type(f"[repos.{name}] {key}", value, getattr(template, key))
        values = dict(values, path=Path(values["path"]).expanduser())
        cfg.repos[name] = RepoConfig(name=name, **values)
    return cfg


def init(home: Path | None = None) -> tuple[Config, bool]:
    """Create JARVIS_HOME and a config template. Returns (config, created)."""
    home = home or jarvis_home()
    home.mkdir(parents=True, exist_ok=True)
    path = home / "config.toml"
    created = not path.exists()
    if created:
        path.write_text(TEMPLATE, encoding="utf-8")
    return load(home), created


def toml_value(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, list):
        return "[" + ", ".join(toml_value(v) for v in value) + "]"
    if isinstance(value, int | float):
        return repr(value)
    return json.dumps(str(value))  # JSON string escapes are valid TOML basic strings


def append_repo(cfg: Config, name: str, values: dict) -> None:
    if name in cfg.repos:
        raise ConfigError(f"repo {name!r} already exists in {cfg.path}")
    lines = [f"\n[repos.{json.dumps(name)}]"]
    lines += [f"{key} = {toml_value(value)}" for key, value in values.items()]
    with cfg.path.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
