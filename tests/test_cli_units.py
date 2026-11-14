"""CLI commands and the small pure pieces (config, profiles, scope, redaction, engines)."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

from conftest import FIXED, TEST_CMD
from jarvis import cli, config, proc, repo_profile, stop, verify
from jarvis.agents import make_runner
from jarvis.agents.base import AgentRequest
from jarvis.agents.claude_code import ClaudeCode, parse
from jarvis.agents.codex import Codex
from jarvis.agents.hermes import Hermes
from jarvis.store import MIGRATIONS, STOPPED, Store


def test_init_repo_add_add_ls_show(repo, tmp_path, capsys):
    assert cli.main(["init"]) == 0
    assert cli.main(["init"]) == 0  # idempotent
    home = config.jarvis_home()
    assert (home / "config.toml").exists() and (home / "jarvis.db").exists()
    assert cli.main(["repo", "add", str(repo), "--name", "calc", "--test", TEST_CMD,
                     "--scope", "calc.py, tests/", "--draft", "--branch-prefix", "bot/"]) == 0
    cfg = config.load()
    assert cfg.repos["calc"].scope == ["calc.py", "tests/"] and cfg.repos["calc"].draft
    assert cfg.repos["calc"].branch_prefix == "bot/"
    assert cli.main(["repo", "add", str(repo), "--name", "calc"]) == 2  # duplicate
    assert cli.main(["repo", "ls"]) == 0
    assert cli.main(["add", "--repo", "calc", "Fix add()\nIt subtracts."]) == 0
    assert cli.main(["add", "--repo", "nope", "x"]) == 2
    assert cli.main(["ls"]) == 0
    assert cli.main(["show", "1"]) == 0
    assert cli.main(["show", "99"]) == 2
    out = capsys.readouterr().out
    assert "Added repo 'calc'" in out and "Task 1 queued for calc" in out
    assert "QUEUED" in out and "It subtracts." in out
    assert (home / "notes" / "calc.md").exists()


def test_cli_run_processes_queue_with_fake_engine(repo, tmp_path, capsys):
    script = tmp_path / "script.json"
    script.write_text(json.dumps({"code": [{"write": {"calc.py": FIXED}}]}))
    cli.main(["init"])
    cfg = config.load()
    cfg.path.write_text(cfg.path.read_text().replace(
        'name = "claude_code"', f'name = "fake"\nscript = {json.dumps(str(script))}'))
    cli.main(["repo", "add", str(repo), "--name", "calc", "--test", TEST_CMD])
    cli.main(["add", "--repo", "calc", "Fix add()"])
    assert cli.main(["run"]) == 0
    out = capsys.readouterr().out
    assert "Processed 1 task(s)" in out and "#1 DONE" in out


def test_stop_and_resume_commands(make_cfg, capsys):
    cfg = make_cfg()
    store = Store(cfg.db_path)
    task_id = store.add_task("calc", "x")
    store.update(task_id, status=STOPPED)
    assert cli.main(["stop", "maintenance", "--wait", "0"]) == 0
    assert stop.active(cfg.home) and stop.read(cfg.home)["reason"] == "maintenance"
    assert cli.main(["run"]) == 1  # refuses while STOP is active
    assert cli.main(["resume"]) == 0
    assert not stop.active(cfg.home)
    assert store.task(task_id)["status"] == "QUEUED"
    assert "1 stopped task(s) re-queued" in capsys.readouterr().out


def test_serve_refuses_source_checkout(make_cfg, capsys):
    make_cfg()
    assert cli.dev_checkout()  # the test suite imports jarvis from src/
    assert cli.main(["serve"]) == 2
    assert "installed package" in capsys.readouterr().err


def test_config_errors(make_cfg, capsys):
    cfg = make_cfg()
    text = cfg.path.read_text()
    cfg.path.write_text(text + "\n[limits2]\nx = 1\n")
    assert cli.main(["ls"]) == 2
    cfg.path.write_text(text.replace("max_repairs = 2", "max_repairs = 'two'"))
    with pytest.raises(config.ConfigError, match="expected int"):
        config.load()
    cfg.path.write_text(text.replace("max_repairs = 2", "max_repairs = 2\nbogus = 1"))
    with pytest.raises(config.ConfigError, match="unknown key"):
        config.load()


def test_no_config_tells_user_to_init(capsys):
    assert cli.main(["ls"]) == 2
    assert "jarvis init" in capsys.readouterr().err


def test_repo_with_quotes_and_backslashes_round_trips(tmp_path):
    cfg, _ = config.init()
    odd = 'C:\\Users\\me\\My "Repo"'
    config.append_repo(cfg, "odd name", {"path": odd, "scope": ["a b/", "c"], "draft": True})
    repo = config.load().repos["odd name"]
    assert str(repo.path) == odd and repo.scope == ["a b/", "c"] and repo.draft is True


@pytest.mark.parametrize("files,expected", [
    ({"package.json": json.dumps({"scripts": {"test": "jest", "lint": "eslint ."},
                                  "devDependencies": {"jest": "1"}}),
      "package-lock.json": "{}"}, ("npm ci", "npm test", "npm run lint")),
    ({"package.json": json.dumps({"scripts": {"test": "vitest"}}), "pnpm-lock.yaml": ""},
     ("", "pnpm test", "")),
    ({"pyproject.toml": "[tool.ruff]\n[dependency-groups]\ndev=['pytest']\n", "uv.lock": ""},
     ("", "uv run python -m pytest -q", "uv run ruff check .")),
    ({"go.mod": "module x\n"}, ("", "go test ./...", "go vet ./...")),
    ({"go.mod": "module x\n", "tests/e2e_test.go": ""}, ("", "go test ./...", "go vet ./...")),
    ({"Cargo.toml": "[package]\n"}, ("", "cargo test", "")),
    ({"Makefile": "build:\n\ttrue\ntest:\n\ttrue\n"}, ("", "make test", "")),
    ({"README.md": "nothing"}, ("", "", "")),
])
def test_repo_profile_detection(tmp_path, files, expected):
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    p = repo_profile.detect(tmp_path)
    assert (p.setup, p.test, p.lint) == expected


VENV = repo_profile.VENV_PY


@pytest.mark.parametrize("files,setup_parts,test", [
    ({"requirements.txt": "python-dateutil\n", "requirements-dev.txt": "pytest\n"},
     ["-r requirements.txt", "-r requirements-dev.txt", "pytest"], f"{VENV} -m pytest -q"),
    ({"pyproject.toml": '[project]\nname="g"\nversion="1"\n'
                        '[project.optional-dependencies]\ntest=["pytest"]\n'},
     ['-e ".[test]"'], f"{VENV} -m pytest -q"),
    ({"pyproject.toml": '[project]\nname="g"\nversion="1"\n[dependency-groups]\ndev=["pytest"]\n'},
     ["-e .", "--group dev"], f"{VENV} -m pytest -q"),
    ({"setup.cfg": "[metadata]\nname = g\n[options.extras_require]\ntesting = pytest\n"},
     ['-e ".[testing]"'], f"{VENV} -m pytest -q"),
    ({"requirements/dev.txt": "pytest\n", "tests/test_a.py": ""},
     ["-r requirements/dev.txt"], f"{VENV} -m pytest -q"),
])
def test_python_projects_get_a_private_venv(tmp_path, files, setup_parts, test, monkeypatch):
    monkeypatch.setattr(repo_profile.shutil, "which", lambda name: "/usr/bin/" + name)
    for name, text in files.items():
        (tmp_path / name).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / name).write_text(text)
    p = repo_profile.detect(tmp_path)
    assert p.setup.startswith(f"uv venv -q .venv && uv pip install -q --python {VENV} ")
    for part in setup_parts:
        assert part in p.setup
    assert p.test == test


def test_python_venv_falls_back_to_pip_without_uv(tmp_path, monkeypatch):
    monkeypatch.setattr(repo_profile.shutil, "which", lambda name: None)
    (tmp_path / "requirements.txt").write_text("pytest\n")
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname="g"\nversion="1"\n[dependency-groups]\ndev=["pytest"]\n'
        "[tool.ruff]\n")
    p = repo_profile.detect(tmp_path)
    assert p.setup.startswith(f"{repo_profile.PY} -m venv .venv && {VENV} -m pip install -q ")
    assert "--group" not in p.setup and "-r requirements.txt" in p.setup
    assert p.lint == ""  # ruff configured but not installed anywhere: no lint command


def test_ruff_runs_from_the_venv_when_declared(tmp_path, monkeypatch):
    monkeypatch.setattr(repo_profile.shutil, "which", lambda name: None)
    (tmp_path / "requirements-dev.txt").write_text("pytest\nruff\n")
    p = repo_profile.detect(tmp_path)
    assert p.lint == f"{VENV} -m ruff check ." and "ruff" in p.setup.split()


def test_profile_overrides(tmp_path):
    (tmp_path / "go.mod").write_text("module x\n")
    repo = config.RepoConfig(name="x", path=tmp_path, test="go test ./pkg/...", lint="")
    p = repo_profile.resolve(repo)
    assert p.test == "go test ./pkg/..." and p.lint == "" and p.commands() == [
        ("test", "go test ./pkg/...")]


def test_python_fallback_uses_platform_interpreter(tmp_path):
    (tmp_path / "test_x.py").write_text("")
    assert repo_profile.detect(tmp_path).test == f"{repo_profile.PY} -m unittest"


def test_scope_matching():
    files = ["src/a.py", "src/sub/b.py", "README.md", "docs/x.md"]
    assert verify.scope_violations(files, [], []) == []
    bad = verify.scope_violations(files, ["src/", "README.md"], [])
    assert bad == ["docs/x.md is outside the allowed scope (src/, README.md)"]
    assert verify.scope_violations(files, ["src", "*.md"], []) == []  # * crosses folders
    assert verify.scope_violations(["src2/x"], ["src"], []) != []
    assert verify.scope_violations([".github/workflows/ci.yml"], [], [".github"]) != []


def test_redaction():
    text = ("token ghp_" + "a" * 36 + " key sk-ant-" + "b" * 30 + " API_KEY=hunter2hunter2"
            " AKIA" + "C" * 16)
    clean = proc.redact(text)
    assert "ghp_" not in clean and "sk-ant" not in clean and "hunter2" not in clean
    assert "AKIA" not in clean and clean.count("[REDACTED]") == 4


def test_store_migrates_old_schema(tmp_path):
    db = tmp_path / "old.db"
    conn = sqlite3.connect(db)
    conn.executescript(MIGRATIONS[0] + ";PRAGMA user_version=1;")
    conn.close()
    store = Store(db)
    assert store.db.execute("PRAGMA user_version").fetchone()[0] == len(MIGRATIONS)
    run_id = store.add_run(store.add_task("r", "t"), "code", 0, "fake")
    store.set_run_pid(run_id, 123)
    assert store.runs(1)[0]["pid"] == 123


def test_claude_code_argv_and_parse():
    agent = ClaudeCode(model="sonnet", args=["--effort", "high"])
    agent._help = "--strict-mcp-config --tools <tools...>"  # a CLI that has both flags
    write = agent.argv(AgentRequest("p", Path("."), 10, max_cost_usd=1.5))
    assert write[1:4] == ["-p", "--output-format", "json"]
    for pair in (["--model", "sonnet"], ["--max-budget-usd", "1.50"],
                 ["--permission-mode", "acceptEdits"]):
        assert write[write.index(pair[0]) + 1] == pair[1]
    assert write[-2:] == ["--effort", "high"]
    read = agent.argv(AgentRequest("p", Path("."), 10, read_only=True))
    assert "Bash,Edit,Write,NotebookEdit" in read and "acceptEdits" not in read
    assert read[read.index("--tools") + 1] == "Read,Glob,Grep"
    assert "--strict-mcp-config" in write and "--strict-mcp-config" in read
    assert "--tools" not in write  # write calls keep the default tool set
    old_cli = ClaudeCode()
    old_cli._help = "-p --print --model"  # an older CLI without these flags
    old_read = old_cli.argv(AgentRequest("p", Path("."), 10, read_only=True))
    assert "--tools" not in old_read and "--strict-mcp-config" not in old_read
    out = 'noise\n{"type":"result","subtype":"success","result":"ok","total_cost_usd":0.5}\n'
    assert parse(out)["total_cost_usd"] == 0.5


def test_claude_code_runs_a_scripted_cli(tmp_path):
    """The real argv/stdin/JSON path against a stand-in executable (not the real engine)."""
    stub = tmp_path / "claude_stub.py"
    stub.write_text(
        "import json, sys\n"
        "prompt = sys.stdin.read()\n"
        "open('touched.txt', 'w').write(prompt[:5])\n"
        "print(json.dumps({'type': 'result', 'subtype': 'success', 'result': 'did it',"
        " 'total_cost_usd': 0.25, 'modelUsage': {'m-1': {'costUSD': 0.25}}}))\n")
    agent = ClaudeCode()
    agent.argv = lambda req: [sys.executable, str(stub)]
    result = agent.run(AgentRequest("hello world", tmp_path, 30), lambda: False)
    assert result.ok and result.output == "did it" and result.cost_usd == 0.25
    assert result.model == "m-1" and (tmp_path / "touched.txt").read_text() == "hello"


def test_codex_and_hermes_argv(tmp_path):
    codex = Codex(model="gpt-x").argv(AgentRequest("p", tmp_path, 10), tmp_path / "last")
    assert codex[1:3] == ["exec", "--json"] and codex[-1] == "-"
    assert codex[codex.index("-s") + 1] == "workspace-write"
    assert codex[codex.index("-m") + 1] == "gpt-x"
    hermes = Hermes(command="python -m hermes_cli.main", args=["--provider", "x"]).argv(
        AgentRequest("do it", tmp_path, 10))
    assert hermes[1:3] == ["-m", "hermes_cli.main"]
    assert hermes[hermes.index("-z") + 1] == "do it" and "--yolo" in hermes
    assert hermes[-2:] == ["--provider", "x"]


def test_make_runner_rejects_unknown_engine(tmp_path):
    cfg = config.Config(home=tmp_path, engine="nope")
    with pytest.raises(ValueError, match="unknown engine"):
        make_runner(cfg)
    for name in ("claude_code", "codex", "hermes", "fake"):
        assert make_runner(config.Config(home=tmp_path, engine=name)).name == name


def test_proc_timeout_and_missing_executable(tmp_path):
    res = proc.run([sys.executable, "-c", "import time; time.sleep(30)"], tmp_path, timeout=0.5)
    assert res.timed_out and res.duration < 10
    res = proc.run(["definitely-not-a-real-binary-xyz"], tmp_path, timeout=5)
    assert res.code is None and "could not start" in res.err
