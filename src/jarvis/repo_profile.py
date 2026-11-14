"""Detect a repo's setup, test, lint and build commands from its manifests."""

from __future__ import annotations

import json
import re
import shutil
import sys
import tempfile
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


REQUIREMENTS = ("requirements.txt", "requirements-dev.txt", "requirements-test.txt",
                "requirements_dev.txt", "requirements_test.txt", "dev-requirements.txt",
                "test-requirements.txt", "dev.requirements.txt", "requirements/dev.txt",
                "requirements/test.txt", "requirements/tests.txt")
TEST_EXTRAS = ("test", "tests", "testing", "dev")
VENV_PY = ".venv\\Scripts\\python.exe" if sys.platform == "win32" else ".venv/bin/python"


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _python(path: Path) -> Profile:
    """Python projects without uv.lock get a private .venv in the task worktree, built
    from their declared dependencies, so tests import the worktree's own code."""
    try:
        pyproject = tomllib.loads(_read(path / "pyproject.toml"))
    except tomllib.TOMLDecodeError:
        pyproject = {}
    config = "".join(_read(path / n) for n in ("pyproject.toml", "setup.cfg", "tox.ini"))
    requirements = [n for n in REQUIREMENTS if (path / n).is_file()]
    project = pyproject.get("project", {})
    extras_declared = set(project.get("optional-dependencies", {}))
    cfg_extras = re.search(r"\[options\.extras_require\]([^\[]*)", _read(path / "setup.cfg"))
    if cfg_extras:
        extras_declared |= set(re.findall(r"^(\w[\w-]*)\s*=", cfg_extras.group(1), re.M))
    extras = [e for e in TEST_EXTRAS if e in extras_declared]
    groups = [g for g in TEST_EXTRAS if g in pyproject.get("dependency-groups", {})]
    deps = "".join(_read(path / n) for n in requirements) + json.dumps(
        [project.get("optional-dependencies", {}).get(e) for e in extras]
        + [pyproject["dependency-groups"][g] for g in groups])
    uses_pytest = "pytest" in config + deps or (path / "conftest.py").exists() \
        or (path / "pytest.ini").exists()
    configured_ruff = "ruff" in pyproject.get("tool", {}) or (path / "ruff.toml").exists()
    if (path / "uv.lock").exists():
        return Profile(test="uv run python -m pytest -q" if uses_pytest
                       else "uv run python -m unittest",
                       lint="uv run ruff check ." if configured_ruff else "")
    installable = bool(project) or (path / "setup.py").exists() or "[metadata]" in _read(
        path / "setup.cfg")
    packages = ([f'-e ".[{",".join(extras)}]"' if extras else "-e ."] if installable else [])
    packages += [f"--group {g}" for g in groups] + [f"-r {r}" for r in requirements]
    packages += ["pytest"] if uses_pytest else []
    ruff_in_venv = "ruff" in deps
    if ruff_in_venv:
        packages.append("ruff")
    if not packages:  # nothing to install: the stdlib test runner is enough
        return Profile(test=f"{PY} -m unittest",
                       lint="ruff check ." if configured_ruff and shutil.which("ruff") else "")
    if shutil.which("uv"):
        setup = f"uv venv -q .venv && uv pip install -q --python {VENV_PY} " + " ".join(packages)
    else:
        pip = [p for p in packages if not p.startswith("--group")]  # pip has no groups
        setup = f"{PY} -m venv .venv && {VENV_PY} -m pip install -q " + " ".join(pip)
    lint = ""
    if configured_ruff or ruff_in_venv:
        lint = f"{VENV_PY} -m ruff check ." if ruff_in_venv else (
            "ruff check ." if shutil.which("ruff") else "")
    return Profile(setup=setup, lint=lint,
                   test=f"{VENV_PY} -m pytest -q" if uses_pytest else f"{VENV_PY} -m unittest")


def detect(path: Path) -> Profile:
    if (path / "package.json").exists():
        profile = _node(path)
        if profile.test:
            return profile
    python_files = ("pyproject.toml", "setup.py", "setup.cfg", "pytest.ini", *REQUIREMENTS)
    if any((path / n).is_file() for n in python_files):
        return _python(path)
    if (path / "go.mod").exists():
        return Profile(test="go test ./...", lint="go vet ./...")
    if (path / "Cargo.toml").exists():
        return Profile(test="cargo test")
    if any(path.glob("test*.py")) or (path / "tests").is_dir():  # weaker Python signals
        return _python(path)
    makefile = path / "Makefile"
    if makefile.exists() and "\ntest:" in "\n" + makefile.read_text(encoding="utf-8",
                                                                      errors="replace"):
        return Profile(test="make test")
    return Profile()


MANIFESTS = {"package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "uv.lock",
             "pyproject.toml", "setup.py", "setup.cfg", "tox.ini", "pytest.ini", "conftest.py",
             "go.mod", "Cargo.toml", "Makefile", *REQUIREMENTS}


def detect_ref(repo_path: Path, ref: str) -> Profile:
    """Detect from a branch's committed manifests instead of the current checkout."""
    from .worktree import git

    with tempfile.TemporaryDirectory(prefix="jarvis-profile-") as tmp:
        root = Path(tmp)
        for entry in git(repo_path, "ls-tree", "-z", ref).split("\0"):
            if "\t" not in entry:
                continue
            meta, name = entry.split("\t", 1)
            if meta.split()[1] == "tree":
                (root / name).mkdir(exist_ok=True)
            elif name in MANIFESTS:
                (root / name).write_text(git(repo_path, "show", f"{ref}:{name}"),
                                         encoding="utf-8")
            else:
                (root / name).touch()
        for name in (n for n in MANIFESTS if "/" in n):  # e.g. requirements/dev.txt
            text = git(repo_path, "show", f"{ref}:{name}", check=False)
            if text:
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text(text, encoding="utf-8")
        return detect(root)


def resolve(repo: RepoConfig, path: Path | None = None, ref: str | None = None) -> Profile:
    """Detected commands with config overrides (None: keep detected, "": disable)."""
    profile = detect_ref(repo.path, ref) if ref else detect(path or repo.path)
    for name in ("setup", "test", "lint", "build"):
        override = getattr(repo, name)
        if override is not None:
            setattr(profile, name, override)
    return profile
