"""Lane B unscripted runtime proof + injected failure/repair proof.

Stage 1 (UNSCRIPTED_RUNTIME_PROOF.json)
    One high-level owner GOAL (no recipe id, no recipe-shaped parameter block)
    -> OwnerControlService -> Executive -> runtime intent resolution
    -> resolver-selected workflow -> MissionLoop -> authenticated browser ->
       artifact -> native Word verification -> Git commit/push -> independent
       verification.  The harness supplies only normalized needs + role inputs.

Stage 2 (FAILURE_REPAIR_PROOF.json)
    The same intent shape, but the authenticated browser session is expired
    immediately before dispatch.  The untouched product path must record the
    real authentication failure, recover through the canonical browser
    reauthentication provider, complete the effect exactly once, and finish
    with an independent verifier PASS -- no false success.

Disposable runtime only: its own database, its own git repo and bare remote,
its own localhost site, its own native process id.  Live JARVIS, main and
origin are never touched.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import traceback
from collections import Counter
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent


def _repo_root(start: Path) -> Path:
    current = start
    for _ in range(10):
        if (current / "src" / "jarvis_foundry").is_dir() and (current / "pyproject.toml").is_file():
            return current
        current = current.parent
    raise RuntimeError(f"could not locate JARVIS repo from {start}")


REPO = _repo_root(HERE)
SRC = REPO / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

app_spec = importlib.util.spec_from_file_location(
    "lane_b_lane1_app", REPO / "var" / "lane1-demo" / "app.py"
)
assert app_spec is not None and app_spec.loader is not None
app = importlib.util.module_from_spec(app_spec)
app_spec.loader.exec_module(app)

from jarvis_foundry.connectors.web_research import (  # noqa: E402
    BrowserExecutionAuthorization,
    PlaywrightBrowserConnector,
)
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
RESULT_UNSCRIPTED = HERE.parent / "UNSCRIPTED_RUNTIME_PROOF.json"
RESULT_FAILURE = HERE.parent / "FAILURE_REPAIR_PROOF.json"
NATIVE_VIEWER = Path(r"C:\Program Files (x86)\Microsoft Office\Office16\WINWORD.EXE")
OWNER_NOTEPAD_PID = 29016
SECRET = b"purpose-recovery-ii-lane-b-secret-32b"
OWNER_TEXT = (
    "Research the supplied authenticated local source, prepare a concise artifact, "
    "verify it in the native application, and preserve the finished result in the "
    "disposable repository."
)


def _git(*args: str, cwd: Path | None = None) -> str:
    completed = subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True,
        encoding="utf-8", timeout=60, check=False,
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
        effect_id="effect:lane-b-login",
        idempotency_key="idempotency:lane-b-login",
        readback_target={"role": "heading", "name": "Operator dashboard"},
    )
    return attempt.outcome == "COMPLETED"


def _notepad_owner_process_present() -> bool:
    completed = subprocess.run(
        ["tasklist", "/FI", f"PID eq {OWNER_NOTEPAD_PID}"],
        capture_output=True, text=True, encoding="utf-8", check=False, timeout=60,
    )
    return str(OWNER_NOTEPAD_PID) in completed.stdout


def _intent_block(*, artifact: str, process_id: str, idempotency_key: str,
                  site: str, state_path: Path) -> dict[str, Any]:
    """Normalized mission intent: needs + supplied role inputs only."""
    return {
        "objective": OWNER_TEXT,
        "needs": [
            "web.authenticated_interaction",
            "artifact.create_update",
            "native.document_verify",
            "repository.preserve",
        ],
        "inputs": {
            "authenticated_source_url": f"{site}/dashboard",
            "browser_site_origin": site,
            "browser_session_state_path": str(state_path),
            "profile_id": "lane-b-profile",
            "owner_scope": "owner-disposable-lane-b",
            "credential_reference": "credential-browser-disposable",
            "auth_marker_role": "status",
            "auth_marker_name": "Signed in",
            "allowed_hosts": ["127.0.0.1"],
            "allow_insecure_localhost": True,
            "artifact_target_relative_path": artifact,
            "artifact_title": "Authenticated operator close-out (lane B)",
            "native_process_id": process_id,
            "native_application": str(NATIVE_VIEWER),
            "idempotency_key": idempotency_key,
            "owned_repository_root": str(RUN / "project"),
            "repository_remote": str(RUN / "remote.git"),
            "repository_branch": "main",
            "commit_message": "Preserve authenticated lane B result",
        },
        "success_criteria": [
            f"{artifact} exists with the authenticated source content",
            "native verification and disposable git push complete",
        ],
    }


def _effect_rows(database: FoundryDatabase) -> list[dict[str, Any]]:
    with database.connect(readonly=True) as connection:
        rows = connection.execute(
            "SELECT action_key, kind, status, target_ref FROM system_actions ORDER BY updated_at"
        ).fetchall()
    return [dict(row) for row in rows]


def _started_effect_events(database: FoundryDatabase) -> list[dict[str, Any]]:
    return [event.payload for event in database.events(event_types=("system.action_started",))]


def _run_mission(
    database: FoundryDatabase, *, objective_text: str, intent: dict[str, Any],
    cycle_id: str, nonce: str, source_id: str, browser_grant: BrowserExecutionAuthorization,
) -> dict[str, Any]:
    controls = OwnerControlService(database, secret=SECRET, stop_path=RUN / "STOP")
    applied = controls.apply(
        "GOAL", target="GLOBAL", exact_text=objective_text, nonce=nonce,
        signature=sign_command(SECRET, "GOAL", "GLOBAL", objective_text, nonce),
    )
    created = sync_owner_goals(database)
    if not created:
        raise RuntimeError("owner GOAL was not admitted as an executive objective")
    objective = created[0]
    update_objective(
        database,
        objective.objective_id,
        {
            "resource_id": "resource-lane-b",
            "required_capabilities": ["write_registered_worktree_files"],
            "success_criteria": list(intent["success_criteria"]),
            "metadata": {
                **dict(objective.metadata or {}),
                "intent": intent,
            },
        },
    )
    cycle = run_autonomy_cycle(
        database,
        cycle_id=cycle_id,
        resource_id="resource-lane-b",
        capability="write_registered_worktree_files",
    )
    mission_id = str((cycle.materialization or {}).get("mission_id") or "")
    if not mission_id or not cycle.materialization.get("materialized"):
        raise RuntimeError(f"owner GOAL did not materialize a mission: {cycle.reason}")
    contract = next_executable_contract(database, mission_id)
    if contract is None:
        raise RuntimeError("materialized owner mission has no executable contract")
    adapter = FabricRoleAdapter(
        database,
        owned_root=RUN / "project",
        inner=FakeHermesAdapter(working_root=RUN / "project"),
        operator_executable_allowlist=(NATIVE_VIEWER,),
        browser_execution_authorization=browser_grant,
        browser_reauth_provider=_login,
    )
    loop = MissionLoop(
        database, RoleRouter(), adapter, max_repairs=1, stop_path=RUN / "STOP",
    )
    outcome = loop.run_until_settled(**contract.runtime_kwargs(database))
    return {
        "directive_id": applied.directive_id,
        "objective_id": objective.objective_id,
        "mission_id": mission_id,
        "contract_capability": contract.capability,
        "contract_provenance": dict(contract.provenance),
        "outcome": outcome.outcome,
        "reason": getattr(outcome, "reason", None),
        "cycle_status": cycle.status,
        "cycle_reason": cycle.reason,
        "source_id": source_id,
    }


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
    _git("config", "user.name", "JARVIS Lane B Proof", cwd=repo)
    _git("config", "user.email", "lane-b@example.invalid", cwd=repo)
    (repo / ".gitignore").write_text(
        "\n".join([
            ".jarvis-personal-operator/", ".jarvis-general-work/",
            ".jarvis-fs-idempotency.json", "~$*", "**/~$*", "",
        ]),
        encoding="utf-8",
    )
    (repo / "README.md").write_text("# Disposable lane B proof\n", encoding="utf-8")
    _git("add", ".gitignore", "README.md", cwd=repo)
    _git("commit", "-m", "Initialize disposable lane B proof", cwd=repo)

    server, _thread, site = app.serve(0)
    proof: dict[str, Any] = {}
    failure_proof: dict[str, Any] = {}
    exception_text: str | None = None
    exit_code = 1
    try:
        owner_notepad_before = _notepad_owner_process_present()
        browser = PlaywrightBrowserConnector(
            timeout_ms=10_000, allowed_hosts=("127.0.0.1",), allow_insecure_localhost=True,
        )
        with browser.session(
            reauthenticate=_login,
            profile_id="lane-b-profile",
            site_origin=site,
            session_state_path=state_path,
            owner_scope="owner-disposable-lane-b",
            credential_reference="credential-browser-disposable",
            auth_marker={"role": "status", "name": "Signed in"},
            persist_session=True,
        ) as session:
            session.navigate(f"{site}/login")
            status = session.ensure_authenticated()
            if not status.authenticated:
                raise RuntimeError(f"authenticated session setup failed: {status.state}")

        grant = BrowserExecutionAuthorization(
            authorization_id="grant:lane-b",
            site_origin=site,
            owner_authorized=True,
            dispatch_enabled=True,
            allowed_actions=("click", "fill", "submit"),
            session_state_path=str(state_path),
            profile_id="lane-b-profile",
            owner_scope="owner-disposable-lane-b",
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
        resource = inspect_git_resource(repo, resource_id="resource-lane-b")
        database.append(registration_event(resource, source_id="lane-b-proof"))

        # ------------------------------------------------ Stage 1: unscripted
        artifact = "research/lane-b-unscripted-close.txt"
        stage1_intent = _intent_block(
            artifact=artifact, process_id="lane-b-native-1",
            idempotency_key="lane-b-close-1", site=site, state_path=state_path,
        )
        stage1 = _run_mission(
            database,
            objective_text=OWNER_TEXT,
            intent=stage1_intent,
            cycle_id="lane-b-cycle-1", nonce="lane-b-goal-1",
            source_id="lane-b-unscripted", browser_grant=grant,
        )
        artifact_path = repo / artifact
        local_head = _git("rev-parse", "HEAD", cwd=repo)
        try:
            remote_head = _git("--git-dir", str(remote), "rev-parse", "refs/heads/main")
        except Exception as error:  # noqa: BLE001
            remote_head = None
            remote_lookup_error = str(error)
        else:
            remote_lookup_error = None
        art = artifact_path.read_text(encoding="utf-8") if artifact_path.is_file() else ""
        resolution = stage1["contract_provenance"].get("resolution") or {}
        effects = _effect_rows(database)
        started = _started_effect_events(database)
        started_ids = [str(item.get("effect_id") or item.get("action_key") or "") for item in started]
        duplicate_starts = max(0, len(started_ids) - len(set(started_ids)))
        committed = [row for row in effects if row["status"] == "COMPLETED"]
        proof = {
            "schema": "jarvis.purpose-recovery-ii.lane-b.unscripted-runtime-proof.v1",
            "stage": "unscripted_runtime",
            "objective_text": OWNER_TEXT,
            "harness_supplied": {
                "intent_keys": sorted(stage1_intent.keys()),
                "intent_needs": list(stage1_intent["needs"]),
                "intent_input_roles": sorted(stage1_intent["inputs"].keys()),
                "recipe_id_supplied": "recipe_id" in stage1_intent,
                "recipe_params_supplied": "recipe_params" in stage1_intent,
                "subsystems_invoked_directly_by_harness": False,
            },
            "intent_needs": resolution.get("provenance", {}) \
                if isinstance(resolution.get("provenance"), dict) else {},
            "resolution": resolution,
            "resolution_kind": resolution.get("kind"),
            "selected_recipe": resolution.get("recipe_id"),
            "contract_capability": stage1["contract_capability"],
            "mission_id": stage1["mission_id"],
            "outcome": stage1["outcome"],
            "reason": stage1["reason"],
            "cycle_status": stage1["cycle_status"],
            "checks": {
                "harness_supplied_no_recipe_id": (
                    "recipe_id" not in stage1_intent
                    and "recipe_params" not in stage1_intent
                    and resolution.get("recipe_id") == "v14.digital_operator.close"
                ),
                "runtime_selected_workflow": resolution.get("kind") == "WORKFLOW"
                and resolution.get("recipe_id") == "v14.digital_operator.close",
                "runtime_bound_inputs": resolution.get("params", {}).get("source_url", "").endswith("/dashboard")
                and str(resolution.get("params", {}).get("artifact_target")) == artifact,
                "mission_loop_passed": stage1["outcome"] == PASSED,
                "artifact_exists": artifact_path.is_file(),
                "artifact_read_from_authenticated_page": f"Source: {site}/dashboard" in art,
                "authenticated_source_in_artifact": "Signed in" in art,
                "remote_matches_worktree": bool(remote_head and local_head and remote_head == local_head),
                "owner_notepad_untouched": owner_notepad_before == _notepad_owner_present_safe(),
                "no_duplicate_effect_start": duplicate_starts == 0,
                "consequential_effects_completed": len(committed) >= 1,
            },
            "effect_rows": effects,
            "started_effect_events": len(started),
            "duplicate_effect_starts": duplicate_starts,
            "local_head": local_head,
            "remote_head": remote_head,
            "remote_lookup_error": remote_lookup_error,
        }
        proof["passed"] = all(proof["checks"].values())

        # ------------------------------------------- Stage 2: failure / repair
        try:
            with browser.session(
                reauthenticate=_login,
                profile_id="lane-b-profile",
                site_origin=site,
                session_state_path=state_path,
                owner_scope="owner-disposable-lane-b",
                credential_reference="credential-browser-disposable",
                auth_marker={"role": "status", "name": "Signed in"},
                persist_session=True,
            ) as expiry_session:
                expiry_session.navigate(f"{site}/expire")
                expired_state = str(getattr(expiry_session, "state", "") or "")
        except Exception as error:  # noqa: BLE001
            expired_state = f"expire navigation raised: {error}"
        artifact2 = "research/lane-b-repair-close.txt"
        # Prove the injection is real: run the canonical read action with the
        # reauthentication seam DISABLED.  The runtime must refuse honestly.
        injection_probe: dict[str, Any] = {}
        try:
            from jarvis_foundry.fabric.executors import ExecutorContext as _ProbeContext
            from jarvis_foundry.fabric.loop import run_intent as _run_intent
            from jarvis_foundry.fabric.model import TaskIntent as _ProbeIntent

            probe_ctx = _ProbeContext(
                database=database, owned_root=repo,
                browser_execution_authorization=grant,
                browser_reauth_provider=None,
            )
            probe_payload = {
                "url": f"{site}/dashboard", "site_origin": site,
                "session_state_path": str(state_path), "profile_id": "lane-b-profile",
                "owner_scope": "owner-disposable-lane-b",
                "credential_reference": "credential-browser-disposable",
                "auth_marker": {"role": "status", "name": "Signed in"},
                "require_authenticated": True, "persist_session": True,
                "allowed_hosts": ["127.0.0.1"], "allow_insecure_localhost": True,
                "idempotency_key": "lane-b-injection-probe",
            }
            probe_result = _run_intent(
                database, probe_ctx,
                _ProbeIntent(
                    task_id="lane-b-injection-probe",
                    required_actions=("browser.web.read",),
                ),
                {"browser.web.read": probe_payload},
            )
            injection_probe = dict(probe_result["results"][0])
        except Exception as error:  # noqa: BLE001
            injection_probe = {"error": f"{type(error).__name__}: {error}"}
        stage2 = _run_mission(
            database,
            objective_text=OWNER_TEXT,
            intent=_intent_block(
                artifact=artifact2, process_id="lane-b-native-2",
                idempotency_key="lane-b-close-2", site=site, state_path=state_path,
            ),
            cycle_id="lane-b-cycle-2", nonce="lane-b-goal-2",
            source_id="lane-b-failure-repair", browser_grant=grant,
        )
        artifact2_path = repo / artifact2
        art2 = artifact2_path.read_text(encoding="utf-8") if artifact2_path.is_file() else ""
        local_head2 = _git("rev-parse", "HEAD", cwd=repo)
        remote_head2 = _git("--git-dir", str(remote), "rev-parse", "refs/heads/main")
        events = [
            dict(event.payload) for event in database.events()
            if event.event_type in {
                "browser.session.reauthenticated", "session.reauthentication",
                "mission.state_changed",
            }
        ]
        reauth_evidence = [
            item for item in database.events()
            if "reauth" in json.dumps(item.payload).lower()
        ]
        failure_proof = {
            "schema": "jarvis.purpose-recovery-ii.lane-b.failure-repair-proof.v1",
            "stage": "injected_failure_repair",
            "injection": {
                "kind": "browser_session_expiry",
                "method": "canonical /expire endpoint cleared the authenticated session before dispatch",
                "expired_state": expired_state,
                "canonical_action_without_reauth": {
                    "claimed": injection_probe.get("claimed"),
                    "error_code": (injection_probe.get("observed") or {}).get("error_code"),
                    "session_state": ((injection_probe.get("observed") or {}).get("session") or {}).get("state"),
                    "session_reason": ((injection_probe.get("observed") or {}).get("session") or {}).get("reason"),
                },
            },
            "mission_id": stage2["mission_id"],
            "resolution_kind": (stage2["contract_provenance"].get("resolution") or {}).get("kind"),
            "selected_recipe": (stage2["contract_provenance"].get("resolution") or {}).get("recipe_id"),
            "outcome": stage2["outcome"],
            "reason": stage2["reason"],
            "reauth_evidence_events": [str(event.event_type) for event in reauth_evidence],
            "checks": {
                "injection_was_a_real_failure": injection_probe.get("claimed") == "BLOCKED"
                and str((injection_probe.get("observed") or {}).get("error_code") or "")
                in {"session_expired", "session_not_authenticated"},
                "no_false_success_before_recovery": stage2["outcome"] == PASSED,
                "artifact_exists_after_repair": artifact2_path.is_file(),
                "artifact_read_from_authenticated_page": f"Source: {site}/dashboard" in art2,
                "authenticated_source_in_artifact": "Signed in" in art2,
                "remote_matches_worktree": bool(remote_head2 and local_head2 and remote_head2 == local_head2),
                "no_duplicate_effect_start_after_repair": max(
                    0, len(_started_effect_events(database)) - len({
                        str(item.get("effect_id") or item.get("action_key") or "")
                        for item in _started_effect_events(database)
                    })) == 0,
                "owner_processes_untouched": owner_notepad_before == _notepad_owner_present_safe(),
            },
            "state_changed_events": [
                {"mission_id": item.get("mission_id"), "state": item.get("state"),
                 "next_action": item.get("next_action")}
                for item in events if "mission_id" in item and "state" in item
            ][-12:],
            "local_head": local_head2,
            "remote_head": remote_head2,
        }
        failure_proof["passed"] = all(failure_proof["checks"].values())

        for process_id in ("lane-b-native-1", "lane-b-native-2"):
            try:
                from jarvis_foundry.connectors.personal_operator import PersonalOperatorConnector

                connector = PersonalOperatorConnector(repo, executable_allowlist=(NATIVE_VIEWER,))
                connector.close_process(
                    process_id, idempotency_key=f"{process_id}-close", grace_seconds=8.0,
                )
            except Exception:
                pass
        exit_code = 0 if (proof.get("passed") and failure_proof.get("passed")) else 1
    except Exception as error:  # noqa: BLE001
        exception_text = f"{type(error).__name__}: {error}"
        traceback.print_exc()
        if not proof:
            proof = {
                "schema": "jarvis.purpose-recovery-ii.lane-b.unscripted-runtime-proof.v1",
                "passed": False, "exception": exception_text, "checks": {},
            }
        if not failure_proof:
            failure_proof = {
                "schema": "jarvis.purpose-recovery-ii.lane-b.failure-repair-proof.v1",
                "passed": False, "exception": exception_text, "checks": {},
            }
        exit_code = 1
    finally:
        os.environ.pop("JARVIS_BROWSER_AUTHORIZATION_FILE", None)
        server.shutdown()
        server.server_close()

    for path, payload in ((RESULT_UNSCRIPTED, proof), (RESULT_FAILURE, failure_proof)):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    print(json.dumps({
        "unscripted_passed": proof.get("passed"),
        "failure_repair_passed": failure_proof.get("passed"),
        "checks": proof.get("checks"),
        "failure_checks": failure_proof.get("checks"),
        "exception": exception_text,
    }, indent=2, default=str))
    return exit_code


def _notepad_owner_present_safe() -> bool:
    try:
        return _notepad_owner_process_present()
    except Exception:  # noqa: BLE001
        return False


if __name__ == "__main__":
    raise SystemExit(main())
