"""Detect a repo's setup, test, lint and build commands from its manifests."""

from __future__ import annotations

import json
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .config import RepoConfig

PY = "python" if sys.platform == "win32" else "python3"
NPM_DEFAULT_TEST = 'echo "Error: no test specified"'


@dataclass
class Profile:
    setup: str = ""
    test: str = ""
    lint: str = ""
    build: str = ""

    def commands(self) -> list[tuple[str, str]]:
        """Verification commands in the order they run."""
        return [(name, cmd) for name, cmd in
                (("lint", self.lint), ("build", self.build), ("test", self.test)) if cmd]


def _node(path: Path) -> Profile:
    try:
        data = json.loads((path / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Profile()
    scripts = data.get("scripts") or {}
    pm, install = "npm", "npm install"
    if (path / "pnpm-lock.yaml").exists():
        pm, install = "pnpm", "pnpm install --frozen-lockfile"
    elif (path / "yarn.lock").exists():
        pm, install = "yarn", "yarn install --frozen-lockfile"
    elif (path / "package-lock.json").exists():
        install = "npm ci"
    has_deps = bool(data.get("dependencies") or data.get("devDependencies"))
    test = scripts.get("test", "")
    return Profile(
        setup=install if has_deps else "",
        test=f"{pm} test" if test and not test.startswith(NPM_DEFAULT_TEST) else "",
        lint=f"{pm} run lint" if "lint" in scripts else "",
        build=f"{pm} run build" if "build" in scripts else "",
    )


def _python(path: Path) -> Profile:
    text = ""
    for name in ("pyproject.toml", "setup.cfg", "tox.ini", "pytest.ini", "requirements-dev.txt",
                 "requirements.txt"):
        try:
            text += (path / name).read_text(encoding="utf-8") + "\n"
        except OSError:
            pass
    try:
        pyproject = tomllib.loads((path / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        pyproject = {}
    uses_pytest = "pytest" in text or (path / "conftest.py").exists() \
        or (path / "pytest.ini").exists()
    uses_ruff = "ruff" in pyproject.get("tool", {}) or "ruff" in text
    run = "uv run " if (path / "uv.lock").exists() else ""
    py = "python" if run else PY
    return Profile(
        test=f"{run}{py} -m pytest -q" if uses_pytest else f"{run}{py} -m unittest",
        lint=f"{run}ruff check ." if uses_ruff and run else ("ruff check ." if uses_ruff else ""),
    )


def detect(path: Path) -> Profile:
    if (path / "package.json").exists():
        profile = _node(path)
        if profile.test:
            return profile
    if any((path / n).exists() for n in ("pyproject.toml", "setup.py", "setup.cfg", "pytest.ini")) \
            or any(path.glob("test*.py")) or (path / "tests").is_dir():
        return _python(path)
    if (path / "go.mod").exists():
        return Profile(test="go test ./...", lint="go vet ./...")
    if (path / "Cargo.toml").exists():
        return Profile(test="cargo test")
    makefile = path / "Makefile"
    if makefile.exists() and "\ntest:" in "\n" + makefile.read_text(encoding="utf-8",
                                                                      errors="replace"):
        return Profile(test="make test")
    return Profile()


def resolve(repo: RepoConfig, path: Path | None = None) -> Profile:
    """Detected commands with config overrides (None: keep detected, "": disable)."""
    profile = detect(path or repo.path)
    for name in ("setup", "test", "lint", "build"):
        override = getattr(repo, name)
        if override is not None:
            setattr(profile, name, override)
    return profile
