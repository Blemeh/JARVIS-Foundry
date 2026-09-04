"""Run the disposable V13 Windows/browser/local-app operator proof."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from jarvis_foundry.connectors.personal_operator import PersonalOperatorConnector
from jarvis_foundry.connectors.web_research import PlaywrightBrowserConnector
from jarvis_foundry.connectors.windows_operator import (
    ControlCondition,
    ControlSelector,
    OperatorAction,
    ProcessIdentity,
    WindowSelector,
    WindowsOperatorProvider,
)
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.effects import FROZEN, effect_state
from jarvis_foundry.fabric import (
    ExecutorContext,
    TaskIntent,
    execute_action,
    seed_builtins,
)
from jarvis_foundry.migrations import SCHEMA_VERSION
from jarvis_foundry.operations.procedure import (
    OperationStep,
    ProcedurePlan,
    complete_step,
    create_procedure,
    freeze_step,
    load_procedure,
    reconcile_step,
    start_step,
)


BASE_SHA = "51d2cfcce1be847fd252d1c857105f09f5f2c0e4"
V12_SOURCE_SHA = "7f153851517b5529dd1e582725a0c2a6d96ddc9b"
PERSONAL_OPERATOR_INPUT = "db4ad1d11af14a666ad5b1b6b1be0059d6950da1"
FIXTURE_SOURCE = ROOT / "scripts" / "v13_operator_fixture.cs"
CSC = Path(r"C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe")


class ProofFailure(RuntimeError):
    """The real disposable gate failed."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ProofFailure(message)


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _filesystem_content_hash(path: Path) -> str:
    return f"sha256:{_sha256_file(path)}"


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    if result.returncode != 0:
        raise ProofFailure(
            f"git {' '.join(args)} failed: {result.stderr.strip()}"
        )
    return result.stdout.strip()


def _compile_fixture(destination: Path) -> dict[str, Any]:
    _require(CSC.is_file(), f"Windows C# compiler is missing: {CSC}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [
            str(CSC),
            "/nologo",
            "/target:winexe",
            f"/out:{destination}",
            "/reference:System.Windows.Forms.dll",
            "/reference:System.Drawing.dll",
            str(FIXTURE_SOURCE),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    _require(
        result.returncode == 0 and destination.is_file(),
        "fixture compilation failed\n"
        f"stdout:\n{result.stdout[-2000:]}\n"
        f"stderr:\n{result.stderr[-2000:]}",
    )
    return {
        "compiler": str(CSC),
        "source": str(FIXTURE_SOURCE.relative_to(ROOT)).replace("\\", "/"),
        "source_sha256": _sha256_file(FIXTURE_SOURCE),
        "executable": str(destination),
        "executable_sha256": _sha256_file(destination),
    }


class _ProofHandler(BaseHTTPRequestHandler):
    server: "_ProofServer"

    def do_GET(self) -> None:  # noqa: N802 - stdlib protocol name
        if self.path == "/":
            body = (
                b"<!doctype html><title>JARVIS V13 local proof</title>"
                b'<h1>Local report</h1><a href="/download" download '
                b'aria-label="Download local report">Download local report</a>'
            )
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/download":
            content = self.server.source_path.read_bytes()
            self.server.download_count += 1
            self.send_response(200)
            self.send_header("Content-Type", "text/csv")
            self.send_header(
                "Content-Disposition",
                'attachment; filename="local-report.csv"',
            )
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return
        self.send_error(404)

    def log_message(self, *_args: object) -> None:
        return


class _ProofServer(ThreadingHTTPServer):
    def __init__(self, source_path: Path) -> None:
        super().__init__(("127.0.0.1", 0), _ProofHandler)
        self.source_path = source_path
        self.download_count = 0


def _intent(action_kind: str, name: str, *, unattended: bool = True) -> TaskIntent:
    return TaskIntent(
        task_id=f"v13.operator.{name}",
        required_actions=(action_kind,),
        unattended=unattended,
    )


def _run_action(
    database: FoundryDatabase,
    context: ExecutorContext,
    action_kind: str,
    payload: Mapping[str, Any],
    *,
    name: str,
    workflow_id: str = "v13.operator.real-proof",
    step_id: str | None = None,
    unattended: bool = True,
) -> Any:
    return execute_action(
        database,
        context,
        _intent(action_kind, name, unattended=unattended),
        action_kind,
        payload,
        workflow_id=workflow_id,
        step_id=step_id or name,
    )


def _action_summary(result: Any) -> dict[str, Any]:
    observed = result.observed
    return {
        "capability_id": result.capability_id,
        "action_kind": result.action_kind,
        "claimed": result.claimed.value,
        "verified": result.verified.value,
        "success": result.success,
        "effect_id": result.receipt.get("effect_id"),
        "journal_state": observed.get("journal_state"),
        "performed": observed.get("performed"),
        "replayed": observed.get("replayed"),
        "error_code": observed.get("error_code") or result.note,
        "provider_ref": observed.get("provider_ref"),
    }


def _require_success(result: Any, label: str) -> None:
    _require(
        result.success,
        f"{label} failed: {json.dumps(_action_summary(result), sort_keys=True)}",
    )


def _record_start(
    database: FoundryDatabase,
    step_id: str,
    before: Mapping[str, Any],
) -> None:
    start_step(
        database,
        "procedure.v13.real-computer-operator",
        step_id,
        before_state=before,
        evidence_refs=(f"proof:v13:{step_id}:before",),
        source_id="v13-computer-operator-proof",
    )


def _record_complete(
    database: FoundryDatabase,
    step_id: str,
    after: Mapping[str, Any],
) -> None:
    complete_step(
        database,
        "procedure.v13.real-computer-operator",
        step_id,
        after_state=after,
        evidence_refs=(f"proof:v13:{step_id}:after",),
        postcondition_results={"verified": True},
        source_id="v13-computer-operator-proof",
    )


def _process_identity(
    connector: PersonalOperatorConnector,
    process_id: str,
) -> ProcessIdentity:
    record = connector.get_validated_process_record(process_id)
    creation = record["creation_identity"]
    return ProcessIdentity(
        logical_id=process_id,
        pid=int(record["pid"]),
        create_time=int(creation["create_time"]),
        executable=str(creation["exe"]),
    )


def _provider(
    connector: PersonalOperatorConnector,
    process_id: str,
    *,
    stop_check: Any = None,
) -> WindowsOperatorProvider:
    return WindowsOperatorProvider(
        _process_identity(connector, process_id),
        stop_check=stop_check,
    )


def _wait_for_window(
    provider: WindowsOperatorProvider,
    title: str,
    *,
    timeout_s: float = 8.0,
) -> Any:
    deadline = time.monotonic() + timeout_s
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            return provider.observe(WindowSelector(title=title))
        except Exception as error:  # noqa: BLE001 - bounded UI readiness poll
            last_error = error
            time.sleep(0.05)
    raise ProofFailure(f"window {title!r} was not observed: {last_error}")


def _browser_restart_probe(site: str) -> dict[str, Any]:
    connector = PlaywrightBrowserConnector(
        timeout_ms=5_000,
        allowed_hosts=("127.0.0.1",),
        allow_insecure_localhost=True,
    )
    health = connector.health()
    _require(health.get("available") is True, f"browser unavailable: {health}")
    session = connector.session()
    cleanup_error: str | None = None
    try:
        session.__enter__()
        before = session.navigate(f"{site}/")
        _require(before.success, "browser crash probe could not navigate")
        assert session._browser is not None
        session._browser.close()
        crashed = session.navigate(f"{site}/")
    finally:
        try:
            session.__exit__(None, None, None)
        except Exception as error:  # noqa: BLE001 - already-crashed cleanup
            cleanup_error = type(error).__name__
    with connector.session() as recovered_session:
        recovered = recovered_session.navigate(f"{site}/")
    _require(recovered.success, "fresh browser session did not recover")
    return {
        "health": health,
        "after_forced_close": {
            "outcome": crashed.attempt.outcome,
            "error_code": crashed.attempt.error_code,
        },
        "cleanup_error": cleanup_error,
        "fresh_session_recovered": recovered.success,
    }


def _plan() -> ProcedurePlan:
    step_data = (
        ("source_read", "file.read", "source/report.csv"),
        ("browser_download", "browser.web.execute", "downloads/report.csv"),
        ("download_inspect", "file.read", "downloads/report.csv"),
        ("app_open", "desktop.open", "fixture-main"),
        ("accessibility_inspect", "desktop.control.inspect", "DocumentText"),
        ("edit_text", "desktop.control.replace_text", "DocumentText"),
        ("save_reconcile", "desktop.control.click", "SaveButton"),
        ("final_readback", "file.read", "downloads/report.csv"),
    )
    return ProcedurePlan(
        procedure_id="procedure.v13.real-computer-operator",
        mission_id="mission.v13.real-computer-operator",
        target="disposable.local-computer",
        preconditions=(
            "isolated owned root exists",
            "localhost browser target only",
            "exact fixture executable allowlisted",
        ),
        capabilities=(
            "file.read",
            "browser.web.execute",
            "desktop.open",
            "desktop.control.inspect",
            "desktop.control.replace_text",
            "desktop.control.click",
        ),
        evidence_refs=("proof:v13:real-computer-operator",),
        metadata={
            "authority": "jarvis_foundry.operations.procedure",
            "scope": "disposable local proof",
        },
        steps=tuple(
            OperationStep(
                step_id=step_id,
                action_kind=action_kind,
                target=target,
                preconditions=("exact prior state observed",),
                capabilities=(action_kind,),
                risk_class="R1",
                reversibility="reversible",
                postconditions=("verified resulting state",),
                rollback={"strategy": "delete disposable proof root"},
                effect_ticket=f"effect:v13:procedure:{step_id}",
                evidence_refs=(f"proof:v13:{step_id}",),
            )
            for step_id, action_kind, target in step_data
        ),
    )


def _write_report(path: Path, report: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(f"{path}.tmp")
    temporary.write_text(
        json.dumps(report, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", required=True)
    parser.add_argument("--report", required=True)
    args = parser.parse_args()
    output_root = Path(args.output_root).expanduser().resolve()
    report_path = Path(args.report).expanduser().resolve()
    if output_root.exists():
        raise SystemExit("output root must not already exist")

    source_before = {
        "branch": _git("branch", "--show-current"),
        "head": _git("rev-parse", "HEAD"),
        "base_sha": BASE_SHA,
        "merge_base": _git("merge-base", BASE_SHA, "HEAD"),
        "v12_source_sha": V12_SOURCE_SHA,
        "personal_operator_input": PERSONAL_OPERATOR_INPUT,
        "schema": SCHEMA_VERSION,
        "main_sha": _git("rev-parse", "main"),
        "origin_main_sha": _git("rev-parse", "origin/main"),
        "worktree_clean": not bool(_git("status", "--short")),
    }
    _require(source_before["merge_base"] == BASE_SHA, "frozen base is not an ancestor")
    _require(source_before["worktree_clean"], "proof requires a clean source worktree")
    _git("cat-file", "-e", f"{PERSONAL_OPERATOR_INPUT}^{{commit}}")

    output_root.mkdir(parents=True)
    owned_root = output_root / "owned"
    owned_root.mkdir()
    source_path = owned_root / "source" / "report.csv"
    source_path.parent.mkdir()
    source_bytes = b"account,total\nalpha,17\nbeta,29\n"
    source_path.write_bytes(source_bytes)
    fixture_executable = owned_root / "bin" / "v13-operator-fixture.exe"
    fixture = _compile_fixture(fixture_executable)
    decoy_path = owned_root / "source" / "decoy.txt"
    decoy_path.write_text("unrelated decoy\n", encoding="utf-8")

    database = FoundryDatabase(output_root / "foundry.db")
    database.initialize()
    seed_builtins(database)
    connector = PersonalOperatorConnector(
        owned_root,
        executable_allowlist=(fixture_executable,),
    )
    context = ExecutorContext(
        database=database,
        owned_root=owned_root,
        operator_executable_allowlist=(fixture_executable,),
        operator_connector=connector,
    )
    create_procedure(
        database,
        _plan(),
        source_id="v13-computer-operator-proof",
    )

    server = _ProofServer(source_path)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    site = f"http://127.0.0.1:{server.server_port}"
    cleanup: dict[str, Any] = {}
    results: dict[str, Any] = {}
    main_title = "JARVIS V13 Main Editor"
    decoy_title = "JARVIS V13 Decoy Editor"
    restarted_title = "JARVIS V13 Restarted Editor"
    stop_path = output_root / "EMERGENCY_STOP"
    try:
        results["browser_restart"] = _browser_restart_probe(site)

        _record_start(
            database,
            "source_read",
            {"ref": "source/report.csv", "hash": _sha256_file(source_path)},
        )
        source_read = _run_action(
            database,
            context,
            "file.read",
            {"target": "source/report.csv"},
            name="source-read",
            step_id="source_read",
        )
        _require_success(source_read, "source read")
        _require(
            source_read.observed.get("content_hash")
            == _filesystem_content_hash(source_path),
            "source read hash mismatch",
        )
        _record_complete(
            database,
            "source_read",
            {"hash": _sha256_file(source_path), "ref": "source/report.csv"},
        )
        results["source_read"] = _action_summary(source_read)

        download_path = owned_root / "downloads" / "report.csv"
        _record_start(
            database,
            "browser_download",
            {"ref": "downloads/report.csv", "identity": "absent"},
        )
        download_payload = {
            "url": f"{site}/",
            "target_ref": f"{site}/",
            "target_id": "download-local-report",
            "role": "link",
            "name": "Download local report",
            "action": "download",
            "allow_downloads": True,
            "download_target": "downloads/report.csv",
            "max_download_bytes": 1024,
            "allowed_hosts": ("127.0.0.1",),
            "allow_insecure_localhost": True,
        }
        download = _run_action(
            database,
            context,
            "browser.web.execute",
            download_payload,
            name="browser-download",
            step_id="browser_download",
            unattended=False,
        )
        _require_success(download, "browser download")
        download_replay = _run_action(
            database,
            context,
            "browser.web.execute",
            download_payload,
            name="browser-download",
            step_id="browser_download",
            unattended=False,
        )
        _require_success(download_replay, "browser download replay")
        _require(server.download_count == 1, "download request duplicated")
        _require(download_path.read_bytes() == source_bytes, "download bytes differ")
        _record_complete(
            database,
            "browser_download",
            {
                "ref": "downloads/report.csv",
                "hash": _sha256_file(download_path),
                "effect_id": download.receipt["effect_id"],
            },
        )
        results["browser_download"] = {
            "first": _action_summary(download),
            "replay": _action_summary(download_replay),
            "server_request_count": server.download_count,
            "sha256": _sha256_file(download_path),
        }

        _record_start(
            database,
            "download_inspect",
            {"ref": "downloads/report.csv", "hash": _sha256_file(download_path)},
        )
        download_read = _run_action(
            database,
            context,
            "file.read",
            {"target": "downloads/report.csv"},
            name="download-inspect",
            step_id="download_inspect",
        )
        _require_success(download_read, "download inspect")
        _require(
            download_read.observed.get("content_hash")
            == _filesystem_content_hash(download_path),
            "download inspect hash mismatch",
        )
        _record_complete(
            database,
            "download_inspect",
            {"ref": "downloads/report.csv", "hash": _sha256_file(download_path)},
        )
        results["download_inspect"] = _action_summary(download_read)

        decoy_open = _run_action(
            database,
            context,
            "desktop.open",
            {
                "process_id": "fixture-decoy",
                "application": str(fixture_executable),
                "target": "source/decoy.txt",
                "args": (decoy_title,),
                "cwd": ".",
                "show_window": True,
            },
            name="decoy-open",
        )
        _require_success(decoy_open, "decoy open")
        decoy_provider = _provider(connector, "fixture-decoy")
        decoy_window = _wait_for_window(decoy_provider, decoy_title)

        _record_start(
            database,
            "app_open",
            {"ref": "fixture-main", "identity": "not-running"},
        )
        app_open = _run_action(
            database,
            context,
            "desktop.open",
            {
                "process_id": "fixture-main",
                "application": str(fixture_executable),
                "target": "downloads/report.csv",
                "args": (main_title,),
                "cwd": ".",
                "show_window": True,
            },
            name="app-open",
            step_id="app_open",
        )
        _require_success(app_open, "main app open")
        main_provider = _provider(connector, "fixture-main")
        main_window = _wait_for_window(main_provider, main_title)
        _record_complete(
            database,
            "app_open",
            {
                "identity": main_window.identity.to_dict(),
                "ref": "fixture-main",
            },
        )
        results["app_open"] = _action_summary(app_open)

        wrong_window = main_provider.perform(
            OperatorAction(
                action_id="wrong-window-proof",
                kind="click",
                window=decoy_window.identity,
                control=ControlSelector(automation_id="SaveButton"),
                readback_control=ControlSelector(automation_id="NeverAppears"),
                condition=ControlCondition("control_exists", True),
                timeout_s=1,
            )
        )
        _require(
            wrong_window.outcome == "FAILED"
            and wrong_window.error_code == "process_identity_mismatch",
            f"wrong-window protection failed: {wrong_window.to_dict()}",
        )
        results["wrong_window"] = wrong_window.to_dict()

        focus = _run_action(
            database,
            context,
            "desktop.focus",
            {"process_id": "fixture-main", "target": "fixture-main"},
            name="focus",
        )
        _require_success(focus, "main app focus")
        results["focus"] = _action_summary(focus)

        _record_start(
            database,
            "accessibility_inspect",
            {"identity": main_window.identity.to_dict(), "ref": "DocumentText"},
        )
        inspect = _run_action(
            database,
            context,
            "desktop.control.inspect",
            {
                "process_id": "fixture-main",
                "window": main_window.identity.to_dict(),
                "control": {"automation_id": "DocumentText"},
            },
            name="accessibility-inspect",
            step_id="accessibility_inspect",
        )
        _require_success(inspect, "accessibility inspect")
        controls = inspect.observed.get("controls") or []
        _require(len(controls) == 1, "DocumentText was not exact")
        _require(
            controls[0].get("value") is None
            and controls[0].get("value_sha256") == _sha256_bytes(source_bytes),
            "accessibility value evidence was not hash-bound",
        )
        _record_complete(
            database,
            "accessibility_inspect",
            {
                "ref": "DocumentText",
                "hash": controls[0]["value_sha256"],
                "control_count": len(controls),
            },
        )
        results["accessibility_inspect"] = _action_summary(inspect)

        unexpected = main_provider.perform(
            OperatorAction(
                action_id="unexpected-dialog-proof",
                kind="click",
                window=main_window.identity,
                control=ControlSelector(
                    automation_id="UnexpectedDialogButton",
                ),
                readback_control=ControlSelector(
                    automation_id="NeverAppears",
                ),
                condition=ControlCondition("control_exists", True),
                timeout_s=3,
            )
        )
        _require(
            unexpected.outcome == "UNKNOWN"
            and unexpected.error_code == "unexpected_dialog",
            f"unexpected dialog was not classified: {unexpected.to_dict()}",
        )
        dialog = _wait_for_window(
            main_provider,
            "JARVIS V13 Unexpected Dialog",
        )
        focus_while_dialog = connector.focus_process("fixture-main")
        _require(
            focus_while_dialog["status"] == "FAILED"
            and (focus_while_dialog.get("error") or {}).get("classification")
            == "window_ambiguous",
            "focus did not reject multiple visible windows",
        )
        close_dialog = main_provider.perform(
            OperatorAction(
                action_id="close-unexpected-dialog",
                kind="click",
                window=dialog.identity,
                control=ControlSelector(name="OK"),
                condition=ControlCondition("window_exists", False),
                timeout_s=3,
            )
        )
        _require(
            close_dialog.outcome == "COMPLETED",
            f"structured dialog close failed: {close_dialog.to_dict()}",
        )
        results["unexpected_dialog"] = {
            "classification": unexpected.to_dict(),
            "focus_ambiguity": focus_while_dialog,
            "structured_close": close_dialog.to_dict(),
        }

        final_text = source_bytes.decode("utf-8") + "reviewed_by=JARVIS V13\n"
        final_hash = _sha256_bytes(final_text.encode("utf-8"))
        _record_start(
            database,
            "edit_text",
            {"ref": "DocumentText", "hash": _sha256_bytes(source_bytes)},
        )
        edit_payload = {
            "process_id": "fixture-main",
            "target": "fixture-main/DocumentText",
            "window": main_window.identity.to_dict(),
            "control": {"automation_id": "DocumentText"},
            "value": final_text,
            "timeout_s": 3,
        }
        edit = _run_action(
            database,
            context,
            "desktop.control.replace_text",
            edit_payload,
            name="edit-text",
            step_id="edit_text",
        )
        _require_success(edit, "replace text")
        _record_complete(
            database,
            "edit_text",
            {"ref": "DocumentText", "hash": final_hash},
        )
        results["edit_text"] = _action_summary(edit)

        _record_start(
            database,
            "save_reconcile",
            {"ref": "downloads/report.csv", "hash": _sha256_file(download_path)},
        )

        stop_created = threading.Event()

        def stop_after_save_lands() -> None:
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                try:
                    if (
                        download_path.read_text(encoding="utf-8")
                        == final_text
                    ):
                        stop_path.write_text(
                            "owner stop after save landed\n",
                            encoding="utf-8",
                        )
                        stop_created.set()
                        return
                except OSError:
                    pass
                time.sleep(0.01)

        stop_thread = threading.Thread(
            target=stop_after_save_lands,
            daemon=True,
        )
        stop_thread.start()
        save_payload = {
            "process_id": "fixture-main",
            "target": "fixture-main/SaveButton",
            "window": main_window.identity.to_dict(),
            "control": {"automation_id": "SaveButton"},
            "readback_control": {"automation_id": "SavedStatus"},
            "condition": {"field": "control_exists", "expected": True},
            "timeout_s": 5,
            "stop_path": str(stop_path),
        }
        save = _run_action(
            database,
            context,
            "desktop.control.click",
            save_payload,
            name="save-reconcile",
            step_id="save_reconcile",
        )
        stop_thread.join(timeout=6)
        _require(stop_created.is_set(), "STOP watcher did not observe the save")
        _require(
            save.claimed.value == "UNKNOWN"
            and save.verified.value == "UNKNOWN"
            and save.observed.get("journal_state") == FROZEN,
            f"save ambiguity was not frozen: {_action_summary(save)}",
        )
        freeze_step(
            database,
            "procedure.v13.real-computer-operator",
            "save_reconcile",
            reason="STOP arrived after the native save effect",
            evidence_refs=("proof:v13:save:frozen",),
            source_id="v13-computer-operator-proof",
        )
        effect_id = str(save.observed["action_key"])
        reconcile = _run_action(
            database,
            context,
            "desktop.action.reconcile",
            {
                **save_payload,
                "original_effect_id": effect_id,
                "original_action_kind": "desktop.control.click",
                "original_action_id": save.observed["operator_receipt"][
                    "action_id"
                ],
            },
            name="save-reconcile-readback",
            step_id="save_reconcile_reconcile",
        )
        _require_success(reconcile, "save reconciliation")
        reconcile_step(
            database,
            "procedure.v13.real-computer-operator",
            "save_reconcile",
            outcome="COMPLETED",
            after_state={
                "ref": "downloads/report.csv",
                "hash": _sha256_file(download_path),
                "effect_id": effect_id,
            },
            evidence_refs=("proof:v13:save:reconciled",),
            provider_reference=str(
                reconcile.observed.get("provider_ref")
                or "computer:local-readback"
            ),
            source_id="v13-computer-operator-proof",
        )
        stop_path.unlink(missing_ok=True)
        save_replay = _run_action(
            database,
            context,
            "desktop.control.click",
            save_payload,
            name="save-reconcile",
            step_id="save_reconcile",
        )
        _require_success(save_replay, "save replay")
        save_count_path = Path(f"{download_path}.save-count")
        _require(
            save_count_path.read_text(encoding="ascii").strip() == "1",
            "native Save was dispatched more than once",
        )
        save_state = effect_state(database, effect_id)
        _require(
            save_state.get("status") == FROZEN
            and save_state.get("accepted") is True
            and save_state.get("terminal_outcome") == "COMPLETED",
            f"save effect did not reconcile: {save_state}",
        )
        results["save_reconcile"] = {
            "ambiguous": _action_summary(save),
            "reconciled": _action_summary(reconcile),
            "replay": _action_summary(save_replay),
            "save_count": 1,
            "effect_state": save_state,
        }

        _record_start(
            database,
            "final_readback",
            {"ref": "downloads/report.csv", "hash": _sha256_file(download_path)},
        )
        final_read = _run_action(
            database,
            context,
            "file.read",
            {"target": "downloads/report.csv"},
            name="final-readback",
            step_id="final_readback",
        )
        _require_success(final_read, "final readback")
        _require(
            final_read.observed.get("text") == final_text
            and final_read.observed.get("content_hash")
            == _filesystem_content_hash(download_path),
            "final artifact readback did not match the edited value",
        )
        _record_complete(
            database,
            "final_readback",
            {"ref": "downloads/report.csv", "hash": final_hash},
        )
        results["final_readback"] = _action_summary(final_read)

        procedure = load_procedure(
            database,
            "procedure.v13.real-computer-operator",
        )
        _require(
            procedure.status == "COMPLETED"
            and procedure.terminal_outcome == "COMPLETED",
            f"procedure did not complete: {procedure.status}",
        )
        results["procedure"] = {
            "status": procedure.status,
            "terminal_outcome": procedure.terminal_outcome,
            "history_event_count": len(procedure.history_sequences),
            "steps": [
                {"step_id": item.step_id, "status": item.status.value}
                for item in procedure.steps
            ],
        }

        crash = main_provider.perform(
            OperatorAction(
                action_id="process-crash-proof",
                kind="click",
                window=main_window.identity,
                control=ControlSelector(automation_id="CrashButton"),
                readback_control=ControlSelector(automation_id="NeverAppears"),
                condition=ControlCondition("control_exists", True),
                timeout_s=3,
            )
        )
        _require(
            crash.outcome == "UNKNOWN",
            f"process crash was not ambiguous: {crash.to_dict()}",
        )
        deadline = time.monotonic() + 5
        crashed_state: dict[str, Any] | None = None
        while time.monotonic() < deadline:
            crashed_state = connector.inspect_process("fixture-main")
            if (crashed_state.get("result") or {}).get("state") == "exited":
                break
            time.sleep(0.05)
        _require(
            crashed_state is not None
            and (crashed_state.get("result") or {}).get("state") == "exited",
            "crashed process did not reconcile as exited",
        )
        restarted_connector = PersonalOperatorConnector(
            owned_root,
            executable_allowlist=(fixture_executable,),
        )
        durable_old = restarted_connector.inspect_process("fixture-main")
        _require(
            (durable_old.get("result") or {}).get("state") == "exited",
            "restart did not reconstruct prior process truth",
        )
        context.operator_connector = restarted_connector
        restarted_open = _run_action(
            database,
            context,
            "desktop.open",
            {
                "process_id": "fixture-restarted",
                "application": str(fixture_executable),
                "target": "downloads/report.csv",
                "args": (restarted_title,),
                "cwd": ".",
                "show_window": True,
            },
            name="app-restart",
        )
        _require_success(restarted_open, "app restart")
        restarted_provider = _provider(
            restarted_connector,
            "fixture-restarted",
        )
        restarted_window = _wait_for_window(
            restarted_provider,
            restarted_title,
        )
        restarted_close = _run_action(
            database,
            context,
            "desktop.close",
            {
                "process_id": "fixture-restarted",
                "target": "fixture-restarted",
            },
            name="app-restart-close",
        )
        _require_success(restarted_close, "restarted app close")
        decoy_after = restarted_connector.inspect_process("fixture-decoy")
        _require(
            (decoy_after.get("result") or {}).get("state") == "running"
            and (decoy_after.get("result") or {}).get("matches") is True,
            "unrelated same-executable process was affected",
        )
        results["process_restart"] = {
            "crash": crash.to_dict(),
            "old_process_after_restart": durable_old,
            "new_process": restarted_window.identity.process.to_dict(),
            "close": _action_summary(restarted_close),
            "decoy_survived": True,
        }

        source_after = {
            "head": _git("rev-parse", "HEAD"),
            "main_sha": _git("rev-parse", "main"),
            "origin_main_sha": _git("rev-parse", "origin/main"),
            "worktree_clean_before_report": not bool(_git("status", "--short")),
        }
        _require(source_after["head"] == source_before["head"], "source HEAD changed")
        _require(
            source_after["main_sha"] == source_before["main_sha"],
            "main changed during proof",
        )
        _require(
            source_after["origin_main_sha"] == source_before["origin_main_sha"],
            "origin/main changed during proof",
        )
        _require(
            source_after["worktree_clean_before_report"],
            "source worktree changed during proof",
        )

        report = {
            "contract_version": "jarvis.v13.computer-operator-proof.v1",
            "verdict": "PASS",
            "final_status": "V13_COMPUTER_OPERATOR_READY",
            "source": {**source_before, **source_after},
            "fixture": fixture,
            "authority_spine": {
                "executive": "unchanged",
                "mission_loop": "unchanged",
                "procedure": "jarvis_foundry.operations.procedure",
                "fabric_catalog": "jarvis_foundry.fabric.catalog",
                "effect_journal": "jarvis_foundry.effects",
                "browser_runtime": (
                    "jarvis_foundry.connectors.web_research."
                    "PlaywrightBrowserConnector"
                ),
                "windows_provider": (
                    "jarvis_foundry.connectors.windows_operator."
                    "WindowsOperatorProvider"
                ),
                "process_owner": (
                    "jarvis_foundry.connectors.personal_operator."
                    "PersonalOperatorConnector"
                ),
            },
            "real_multi_app_proof": {
                "surfaces": [
                    "owned local source file",
                    "localhost HTTP server",
                    "Playwright Chromium",
                    "WinForms accessibility application",
                    "canonical procedure/effect ledger",
                ],
                "source_sha256": _sha256_file(source_path),
                "download_sha256": _sha256_file(download_path),
                "final_sha256": final_hash,
                "final_size_bytes": len(final_text.encode("utf-8")),
            },
            "results": results,
            "integrity": {
                "download_requests": server.download_count,
                "native_save_dispatches": 1,
                "wrong_window_blocked": True,
                "unexpected_dialog_classified": True,
                "stop_after_effect_frozen": True,
                "ambiguous_effect_reconciled": True,
                "same_executable_decoy_survived": True,
                "procedure_terminal": "COMPLETED",
            },
            "policy": {
                "gmail_read": "DEFERRED_BY_OWNER",
                "gmail_send": "DEFERRED_BY_OWNER",
                "gmail_observation": "DISABLED",
                "voice": "DEFERRED_BY_OWNER",
                "live_mutated": False,
                "main_origin_mutated": False,
            },
            "known_limitations": [
                "Windows UI Automation requires accessible application controls",
                "blind coordinate fallback is intentionally absent",
                "downloads are bounded to one explicit owned-root target",
                "public external mutation was not exercised",
            ],
        }
        _write_report(report_path, report)
        print(json.dumps({
            "verdict": report["verdict"],
            "final_status": report["final_status"],
            "report": str(report_path),
            "output_root": str(output_root),
            "source_sha": source_before["head"],
        }, sort_keys=True))
        return 0
    finally:
        stop_path.unlink(missing_ok=True)
        for process_id in (
            "fixture-main",
            "fixture-restarted",
            "fixture-decoy",
        ):
            try:
                cleanup[process_id] = (
                    context.operator_connector or connector
                ).close_process(
                    process_id,
                    idempotency_key=f"cleanup-{process_id}",
                    grace_seconds=1,
                )
            except Exception as error:  # noqa: BLE001 - best effort proof cleanup
                cleanup[process_id] = {"error": type(error).__name__}
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
