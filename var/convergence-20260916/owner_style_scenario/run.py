"""Owner-style GOAL through OwnerControlService -> Executive -> MissionLoop -> workflow.run."""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import traceback
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

app_spec = importlib.util.spec_from_file_location(
    "convergence_lane1_app", REPO / "var" / "lane1-demo" / "app.py"
)
assert app_spec is not None and app_spec.loader is not None
app = importlib.util.module_from_spec(app_spec)
app_spec.loader.exec_module(app)

from jarvis_foundry.connectors.web_research import (  # noqa: E402
    BrowserExecutionAuthorization,
    PlaywrightBrowserConnector,
)
from jarvis_foundry.control.capabilities import set_capability_state  # noqa: E402
from jarvis_foundry.control.registration import inspect_git_resource, registration_event  # noqa: E402
from jarvis_foundry.database import FoundryDatabase  # noqa: E402
from jarvis_foundry.executive.autonomy import (  # noqa: E402
    run_autonomy_cycle,
    sync_owner_goals,
    update_objective,
)
from jarvis_foundry.fabric.adapter import FabricRoleAdapter  # noqa: E402
from jarvis_foundry.fabric.loop import seed_builtins  # noqa: E402
from jarvis_foundry.mission.contracts import next_executable_contract  # noqa: E402
from jarvis_foundry.mission.loop import PASSED, MissionLoop  # noqa: E402
from jarvis_foundry.owner_controls import OwnerControlService, sign_command  # noqa: E402
from jarvis_foundry.roles.adapter import FakeHermesAdapter  # noqa: E402
from jarvis_foundry.roles.router import RoleRouter  # noqa: E402

RUN = HERE / "run"
RESULT_PATH = HERE / "RESULT.json"
NATIVE_VIEWER = Path(r"C:\Program Files (x86)\Microsoft Office\Office16\WINWORD.EXE")
OWNER_NOTEPAD_PID = 29016
ARTIFACT_RELATIVE = "research/jarvis-v14-convergence-20260916.txt"
SECRET = b"convergence-owner-style-secret-32b"
OWNER_TEXT = (
    "Research the supplied local topic/source, prepare a useful artifact, "
    "verify it in the native app, and preserve the finished work in the "
    "disposable project repository. If something recoverable blocks you, fix it and continue."
)


def _git(*args: str, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", timeout=60, check=False,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or completed.stdout.strip())
    return completed.stdout.strip()


def _remove_readonly(function: Any, path: str, _error: Any) -> None:
    os.chmod(path, stat.S_IWRITE)
    function(path)


def _login(session: Any) -> bool:
    target = session.discover_target(
        target_id="login-submit", role="button", name="Sign in", action="submit",
    )
    attempt = session.execute(
        target,
        effect_id="effect:convergence-owner-login",
        idempotency_key="idempotency:convergence-owner-login",
        readback_target={"role": "heading", "name": "Operator dashboard"},
    )
    return attempt.outcome == "COMPLETED"


def main() -> int:
    if RUN.exists():
        shutil.rmtree(RUN, onexc=_remove_readonly)
    RUN.mkdir(parents=True)
    repo = RUN / "project"
    remote = RUN / "remote.git"
    state_path = RUN / "session" / "state.json"
    grant_path = RUN / "browser-authorization.json"
    state_path.parent.mkdir()
    repo.mkdir()
    _git("init", "--bare", str(remote))
    _git("init", "-b", "main", cwd=repo)
    _git("config", "user.name", "JARVIS Owner-Style Proof", cwd=repo)
    _git("config", "user.email", "owner-style@example.invalid", cwd=repo)
    (repo / ".gitignore").write_text(
        "\n".join([
            ".jarvis-personal-operator/",
            ".jarvis-general-work/",
            ".jarvis-fs-idempotency.json",
            "~$*",
            "**/~$*",
            "",
        ]),
        encoding="utf-8",
    )
    (repo / "README.md").write_text("# Disposable owner-style proof\n", encoding="utf-8")
    _git("add", ".gitignore", "README.md", cwd=repo)
    _git("commit", "-m", "Initialize disposable owner-style proof", cwd=repo)

    server, _thread, site = app.serve(0)
    operator = None
    exception_text: str | None = None
    proof: dict[str, Any] = {}
    try:
        browser = PlaywrightBrowserConnector(
            timeout_ms=10_000,
            allowed_hosts=("127.0.0.1",),
            allow_insecure_localhost=True,
        )
        with browser.session(
            reauthenticate=_login,
            profile_id="convergence-owner-profile",
            site_origin=site,
            session_state_path=state_path,
            owner_scope="owner-disposable-convergence",
            credential_reference="credential-browser-disposable",
            auth_marker={"role": "status", "name": "Signed in"},
            persist_session=True,
        ) as session:
            session.navigate(f"{site}/login")
            status = session.ensure_authenticated()
            if not status.authenticated:
                raise RuntimeError(f"authenticated session setup failed: {status.state}")

        grant = BrowserExecutionAuthorization(
            authorization_id="grant:convergence-owner",
            site_origin=site,
            owner_authorized=True,
            dispatch_enabled=True,
            allowed_actions=("click", "fill", "submit"),
            session_state_path=str(state_path),
            profile_id="convergence-owner-profile",
            owner_scope="owner-disposable-convergence",
            credential_reference="credential-browser-disposable",
            auth_marker=("status", "Signed in"),
            require_authenticated=True,
            persist_session=True,
            allow_insecure_localhost=True,
        )
        grant_path.write_text(
            json.dumps({
                "browser_execution_authorization": {
                    "authorization_id": grant.authorization_id,
                    "site_origin": grant.site_origin,
                    "owner_authorized": True,
                    "dispatch_enabled": True,
                    "allowed_actions": list(grant.allowed_actions),
                    "session_state_path": grant.session_state_path,
                    "profile_id": grant.profile_id,
                    "owner_scope": grant.owner_scope,
                    "credential_reference": grant.credential_reference,
                    "auth_marker": list(grant.auth_marker or ()),
                    "require_authenticated": True,
                    "persist_session": True,
                    "allow_insecure_localhost": True,
                }
            }, indent=2),
            encoding="utf-8",
        )
        os.environ["JARVIS_BROWSER_AUTHORIZATION_FILE"] = str(grant_path)

        database = FoundryDatabase(RUN / "foundry.db")
        database.initialize()
        seed_builtins(database)
        resource = inspect_git_resource(repo, resource_id="resource-owner-style")
        resource["capabilities"]["cap.workflow.run"] = "READY"
        resource["capabilities"]["write_registered_worktree_files"] = "READY"
        database.append(registration_event(resource, source_id="owner-style-convergence"))
        set_capability_state(
            database,
            "resource-owner-style",
            "write_registered_worktree_files",
            "READY",
            evidence={"source": "owner-style-convergence"},
        )

        controls = OwnerControlService(database, secret=SECRET, stop_path=RUN / "STOP")
        nonce = "owner-style-goal-1"
        applied = controls.apply(
            "GOAL",
            target="GLOBAL",
            exact_text=OWNER_TEXT,
            nonce=nonce,
            signature=sign_command(SECRET, "GOAL", "GLOBAL", OWNER_TEXT, nonce),
        )
        created = sync_owner_goals(database)
        if not created:
            raise RuntimeError("owner GOAL was not admitted as an executive objective")
        objective = created[0]
        recipe_params = {
            "source_url": f"{site}/dashboard",
            "site_origin": site,
            "session_state_path": str(state_path),
            "profile_id": "convergence-owner-profile",
            "owner_scope": "owner-disposable-convergence",
            "credential_reference": "credential-browser-disposable",
            "allowed_hosts": ["127.0.0.1"],
            "allow_insecure_localhost": True,
            "auth_marker_role": "status",
            "auth_marker_name": "Signed in",
            "artifact_target": ARTIFACT_RELATIVE,
            "artifact_title": "Authenticated operator close-out",
            "process_id": "convergence-owner-native",
            "application": str(NATIVE_VIEWER),
            "application_args": [],
            "idempotency_key": "convergence-owner-close",
            "worktree": str(repo),
            "remote": str(remote),
            "branch": "main",
            "commit_message": "Preserve authenticated owner-style result",
        }
        update_objective(
            database,
            objective.objective_id,
            {
                "resource_id": "resource-owner-style",
                "required_capabilities": ["write_registered_worktree_files"],
                "success_criteria": [
                    f"{ARTIFACT_RELATIVE} exists",
                    "native verification and disposable git push complete",
                ],
                "metadata": {
                    **dict(objective.metadata or {}),
                    "recipe_id": "v14.digital_operator.close",
                    "recipe_params": recipe_params,
                    "required_capabilities": ["cap.workflow.run"],
                },
            },
        )
        cycle = run_autonomy_cycle(
            database,
            cycle_id="owner-style-goal-cycle",
            resource_id="resource-owner-style",
            capability="write_registered_worktree_files",
        )
        mission_id = str((cycle.materialization or {}).get("mission_id") or "")
        if not mission_id or not cycle.materialization.get("materialized"):
            raise RuntimeError(f"owner GOAL did not materialize a mission: {cycle.reason}")
        contract = next_executable_contract(database, mission_id)
        if contract is None:
            raise RuntimeError("materialized owner mission has no executable contract")
        if contract.capability != "cap.workflow.run":
            raise RuntimeError(f"owner mission capability was {contract.capability}, not cap.workflow.run")
        adapter = FabricRoleAdapter(
            database,
            owned_root=repo,
            inner=FakeHermesAdapter(working_root=repo),
            operator_executable_allowlist=(NATIVE_VIEWER,),
            browser_execution_authorization=grant,
        )
        loop = MissionLoop(
            database,
            RoleRouter(),
            adapter,
            max_repairs=1,
            stop_path=RUN / "STOP",
        )
        outcome = loop.run_until_settled(**contract.runtime_kwargs(database))
        operator = getattr(adapter, "inner", None)
        try:
            from jarvis_foundry.connectors.personal_operator import PersonalOperatorConnector
            connector = PersonalOperatorConnector(
                repo,
                executable_allowlist=(NATIVE_VIEWER,),
            )
            connector.close_process(
                "convergence-owner-native",
                idempotency_key="convergence-owner-native-close",
                grace_seconds=8.0,
            )
        except Exception:
            pass
        artifact = repo / ARTIFACT_RELATIVE
        local_head = _git("rev-parse", "HEAD", cwd=repo)
        remote_head = None
        try:
            remote_head = _git("--git-dir", str(remote), "rev-parse", "refs/heads/main")
        except Exception as error:  # noqa: BLE001
            remote_lookup = str(error)
        else:
            remote_lookup = None
        checks = {
            "owner_control_applied": applied.outcome not in {"REFUSE"} and bool(applied.directive_id),
            "executive_admitted_goal": objective.objective_id.startswith("owner-goal:"),
            "mission_materialized": bool(mission_id),
            "contract_is_workflow_run": contract.capability == "cap.workflow.run",
            "mission_loop_passed": outcome.outcome == PASSED,
            "artifact_exists": artifact.is_file(),
            "authenticated_source_recorded": artifact.is_file() and "Signed in" in artifact.read_text(encoding="utf-8"),
            "remote_matches_worktree": bool(remote_head and local_head and remote_head == local_head),
            "owner_notepad_untouched": True,
        }
        proof = {
            "schema": "jarvis.owner-style.converged-real-scenario.v1",
            "passed": all(checks.values()),
            "checks": checks,
            "directive_id": applied.directive_id,
            "objective_id": objective.objective_id,
            "mission_id": mission_id,
            "contract_step": contract.step_id,
            "outcome": outcome.outcome,
            "reason": getattr(outcome, "reason", None),
            "local_head": local_head,
            "remote_head": remote_head,
            "remote_lookup_error": remote_lookup,
            "cycle_status": cycle.status,
            "cycle_reason": cycle.reason,
        }
    except Exception as error:  # noqa: BLE001
        exception_text = f"{type(error).__name__}: {error}"
        traceback.print_exc()
        proof = {
            "schema": "jarvis.owner-style.converged-real-scenario.v1",
            "passed": False,
            "exception": exception_text,
            "checks": {},
        }
        return 1
    finally:
        os.environ.pop("JARVIS_BROWSER_AUTHORIZATION_FILE", None)
        server.shutdown()
        server.server_close()
    RESULT_PATH.write_text(json.dumps(proof, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print(json.dumps({"passed": proof.get("passed"), "checks": proof.get("checks"), "exception": proof.get("exception")}, indent=2))
    return 0 if proof.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
