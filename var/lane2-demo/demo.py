"""Real UIA demo for Lane 2 Windows desktop operator.

Operates only on processes this script launches under var/lane2-demo.
Never targets owner Outlook/Chrome/existing untitled work.
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import traceback
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SOURCE_ROOT = ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from dataclasses import asdict

from jarvis_foundry.connectors.personal_operator import (
    PersonalOperatorConnector,
    _visible_application_windows,
)
from jarvis_foundry.connectors.windows_operator import (
    ControlCondition,
    ControlSelector,
    OperatorAction,
    ProcessIdentity,
    ProcessIdentityMismatch,
    WindowsOperatorProvider,
    normalize_document_text,
)
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.fabric.computer_operator import (
    dispatch_computer_operator,
    open_artifact_and_assert,
)
from jarvis_foundry.fabric.executors import ExecutorContext
from jarvis_foundry.fabric.loop import seed_builtins
from jarvis_foundry.supervisor.worker import process_creation_identity

DEMO_DIR = Path(__file__).resolve().parent
NOTEPAD = Path(r"C:\Windows\System32\notepad.exe")
WORD = Path(r"C:\Program Files (x86)\Microsoft Office\Office16\WINWORD.EXE")
EXCEL = Path(r"C:\Program Files (x86)\Microsoft Office\Office16\EXCEL.EXE")
EXPLORER = Path(r"C:\Windows\explorer.exe")
CALC = Path(r"C:\Windows\System32\calc.exe")
HELLO_TEXT = "JARVIS lane2 hello\n"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _provider(
    connector: PersonalOperatorConnector, process_id: str
) -> WindowsOperatorProvider:
    record = connector.get_validated_process_record(process_id)
    creation = record["creation_identity"]
    return WindowsOperatorProvider(
        ProcessIdentity(
            logical_id=str(record["process_id"]),
            pid=int(record["pid"]),
            create_time=int(creation["create_time"]),
            executable=str(creation.get("exe") or record["executable"]),
        )
    )


def _close(
    connector: PersonalOperatorConnector, process_id: str, key: str
) -> dict:
    try:
        return connector.close_process(
            process_id, idempotency_key=key, grace_seconds=2.0
        )
    except Exception as error:
        return {"status": "FAILED", "detail": f"{type(error).__name__}: {error}"}


def main() -> int:
    steps: list[dict[str, object]] = []
    owned_ids: list[str] = []
    calc_owned_pids: list[int] = []
    report: dict[str, object] = {
        "demo": "lane2-windows-operator",
        "steps": steps,
        "closed": [],
        "ok": False,
    }

    def note(name: str, **payload: object) -> None:
        steps.append({"step": name, **payload})

    DEMO_DIR.mkdir(parents=True, exist_ok=True)
    run_token = str(int(time.time()))
    (DEMO_DIR / "hello.txt").write_text(HELLO_TEXT, encoding="utf-8")
    hello_name = f"hello-{run_token}.txt"
    hello = DEMO_DIR / hello_name
    hello.write_text(HELLO_TEXT, encoding="utf-8")
    opened_file = DEMO_DIR / f"hello-open-{run_token}.txt"
    opened_file.write_text("JARVIS lane2 opened via dialog\n", encoding="utf-8")

    before = _visible_application_windows()
    report["before_windows"] = [
        {
            "hwnd": window.hwnd,
            "pid": window.pid,
            "title": window.title,
            "class_name": window.class_name,
        }
        for window in before
        if window.class_name in {"Notepad", "OpusApp", "XLMAIN"}
        or "Notepad" in window.title
    ]
    notepad_already = any(window.class_name == "Notepad" for window in before)

    allow = [NOTEPAD, EXPLORER]
    if WORD.exists():
        allow.append(WORD)
    if EXCEL.exists():
        allow.append(EXCEL)
    if CALC.exists():
        allow.append(CALC)

    import shutil

    state_dir = DEMO_DIR / ".jarvis-personal-operator"
    if state_dir.exists():
        shutil.rmtree(state_dir)

    connector = PersonalOperatorConnector(DEMO_DIR, executable_allowlist=allow)

    try:
        if notepad_already:
            note(
                "notepad_skipped_existing_instance",
                status="BLOCKED",
                detail="a Notepad window already existed; refusing to reuse it",
            )
        else:
            opened = open_artifact_and_assert(
                connector,
                process_id="lane2-notepad",
                executable=str(NOTEPAD),
                path=hello_name,
                expected=HELLO_TEXT,
                idempotency_key=f"lane2-notepad-open-{run_token}",
                cwd=".",
            )
            owned_ids.append("lane2-notepad")
            note(
                "open_artifact_and_assert",
                claimed=opened.get("claimed"),
                error_code=opened.get("error_code"),
                process=opened.get("process"),
                window=opened.get("window"),
                control=opened.get("control"),
                expected_sha256=opened.get("expected_sha256"),
                observed_sha256=opened.get("observed_sha256"),
                readback_verified=opened.get("readback_verified"),
            )
            if opened.get("claimed") != "COMPLETED":
                raise RuntimeError(
                    "notepad open_artifact_and_assert failed: "
                    + str(opened.get("error_code"))
                )
            provider = _provider(connector, "lane2-notepad")
            window = provider.observe_main_window(title_contains=hello_name)
            document = provider.find_document_control(window.identity)
            replaced = provider.perform(
                OperatorAction(
                    action_id="lane2-replace",
                    kind="replace_text",
                    window=window.identity,
                    control=document.selector,
                    value="JARVIS lane2 replaced",
                    timeout_s=8.0,
                )
            )
            note(
                "replace_text",
                outcome=replaced.outcome,
                error_code=replaced.error_code,
            )
            typed = provider.perform(
                OperatorAction(
                    action_id="lane2-type",
                    kind="type",
                    window=window.identity,
                    control=document.selector,
                    value="!",
                    condition=ControlCondition(
                        "value", "JARVIS lane2 replaced!"
                    ),
                    timeout_s=8.0,
                )
            )
            note("type", outcome=typed.outcome, error_code=typed.error_code)
            pasted = provider.perform(
                OperatorAction(
                    action_id="lane2-paste",
                    kind="paste",
                    window=window.identity,
                    control=document.selector,
                    value="JARVIS lane2 clipboard",
                    timeout_s=8.0,
                )
            )
            note(
                "paste",
                outcome=pasted.outcome,
                error_code=pasted.error_code,
                clipboard_sha=_sha("JARVIS lane2 clipboard"),
            )
            try:
                dialog = provider.invoke_file_dialog(
                    window.identity,
                    path=str(opened_file),
                    action="open",
                    timeout_s=20.0,
                    action_id="lane2-file-open",
                )
                note(
                    "file_open_dialog",
                    outcome=dialog.outcome,
                    error_code=dialog.error_code,
                    detail=dialog.detail,
                    dialog_title=(
                        dialog.dialog.identity.title if dialog.dialog else None
                    ),
                )
                if dialog.outcome == "COMPLETED":
                    after_open = provider.observe_main_window(
                        title_contains=opened_file.stem
                    )
                    read = provider.find_document_control(after_open.identity)
                    observed_text = normalize_document_text(
                        str(read.value or "")
                    )
                    expected_open = normalize_document_text(
                        "JARVIS lane2 opened via dialog\n"
                    )
                    note(
                        "file_open_readback",
                        window_title=after_open.identity.title,
                        value_length=len(observed_text),
                        value_sha256=_sha(observed_text),
                        expected_sha256=_sha(expected_open),
                        readback_verified=observed_text == expected_open,
                    )
                    save_as_path = DEMO_DIR / f"hello-save-{run_token}.txt"
                    fabric_db = FoundryDatabase(
                        DEMO_DIR
                        / ".jarvis-personal-operator"
                        / "lane2-fabric.db"
                    )
                    fabric_db.initialize()
                    seed_builtins(fabric_db)
                    fabric_ctx = ExecutorContext(
                        database=fabric_db,
                        owned_root=DEMO_DIR,
                        operator_connector=connector,
                    )
                    saved = dispatch_computer_operator(
                        fabric_ctx,
                        "desktop.file_dialog",
                        {
                            "process_id": "lane2-notepad",
                            "window": asdict(after_open.identity),
                            "path": save_as_path.name,
                            "action": "save_as",
                            "timeout_s": 20.0,
                            "action_id": "lane2-file-save-as",
                        },
                    )
                    note(
                        "file_save_as_dialog",
                        outcome=(saved or {}).get("claimed"),
                        error_code=(saved or {}).get("error_code"),
                        via="desktop.file_dialog",
                    )
                    if (saved or {}).get("claimed") == "COMPLETED":
                        after_save = provider.observe_main_window(
                            title_contains=save_as_path.stem
                        )
                        note(
                            "file_save_as_readback",
                            window_title=after_save.identity.title,
                            saved_exists=save_as_path.is_file(),
                        )
            except Exception as error:
                note(
                    "file_open_dialog",
                    outcome="FAILED",
                    detail=f"{type(error).__name__}: {error}",
                )
            live_window = provider.observe_main_window()
            document = provider.find_document_control(live_window.identity)
            waited = provider.perform(
                OperatorAction(
                    action_id="lane2-wait-enabled",
                    kind="wait",
                    window=live_window.identity,
                    control=document.selector,
                    condition=ControlCondition("enabled", True),
                    timeout_s=5.0,
                )
            )
            note(
                "wait_until_enabled",
                outcome=waited.outcome,
                error_code=waited.error_code,
            )
            try:
                view = provider.resolve_named_control(
                    live_window.identity,
                    names=("View",),
                    control_type="MenuItemControl",
                    automation_ids=("View",),
                )
                expanded = provider.perform(
                    OperatorAction(
                        action_id="lane2-view",
                        kind="click",
                        window=live_window.identity,
                        control=view.selector,
                        readback_control=ControlSelector(
                            control_type="MenuItemControl",
                            name="Word wrap",
                        ),
                        condition=ControlCondition("control_exists", True),
                        timeout_s=5.0,
                    )
                )
                wrap = provider.resolve_named_control(
                    live_window.identity,
                    names=("Word wrap", "Word Wrap"),
                    control_type="MenuItemControl",
                )
                before_wrap = wrap.selected
                toggled = provider.perform(
                    OperatorAction(
                        action_id="lane2-word-wrap",
                        kind="click",
                        window=live_window.identity,
                        control=wrap.selector,
                        readback_control=document.selector,
                        condition=ControlCondition("enabled", True),
                        timeout_s=6.0,
                    )
                )
                note(
                    "word_wrap_toggle",
                    outcome=toggled.outcome,
                    error_code=toggled.error_code,
                    before_selected=before_wrap,
                    view_outcome=expanded.outcome,
                )
            except Exception as error:
                note(
                    "word_wrap_toggle",
                    outcome="FAILED",
                    detail=f"{type(error).__name__}: {error}",
                )

        second_app = "lane2-explorer"
        second_exe = EXPLORER
        target: Path = DEMO_DIR
        if WORD.exists():
            from docx import Document

            docx = DEMO_DIR / "lane2.docx"
            word_doc = Document()
            word_doc.add_paragraph("JARVIS lane2 word")
            word_doc.save(docx)
            second_app = "lane2-word"
            second_exe = WORD
            target = docx
        elif EXCEL.exists():
            from openpyxl import Workbook

            xlsx = DEMO_DIR / "lane2.xlsx"
            book = Workbook()
            book.active["A1"] = "JARVIS lane2 excel"
            book.save(xlsx)
            second_app = "lane2-excel"
            second_exe = EXCEL
            target = xlsx

        if second_app == "lane2-explorer":
            launched = connector.launch_process(
                second_app,
                str(second_exe),
                [str(DEMO_DIR)],
                cwd=".",
                idempotency_key=f"lane2-explorer-app-{run_token}",
                show_window=True,
            )
        else:
            launched = connector.desktop_open(
                second_app,
                str(second_exe),
                target.name,
                cwd=".",
                idempotency_key=f"{second_app}-open-{run_token}",
                show_window=True,
            )
        owned_ids.append(second_app)
        note(
            "second_app",
            app=second_app,
            status=launched.get("status"),
            error=(launched.get("error") or {}).get("classification"),
            pid=(launched.get("result") or {}).get("pid"),
            window_hwnd=(launched.get("result") or {}).get("window_hwnd"),
        )

        notepad_live = False
        if "lane2-notepad" in owned_ids:
            try:
                connector.get_validated_process_record("lane2-notepad")
                notepad_live = True
            except Exception:
                notepad_live = False

        if second_app in {"lane2-word", "lane2-excel"} and launched.get("status") == "COMPLETED":
            try:
                office = _provider(connector, second_app)
                observed = office.observe_main_window(
                    title_contains=target.stem,
                )
                controls = office.inspect(observed.identity)
                note(
                    "office_observe",
                    title=observed.identity.title,
                    class_name=observed.identity.class_name,
                    hwnd=observed.identity.hwnd,
                    control_count=len(controls),
                    sample=[
                        {
                            "type": item.selector.control_type,
                            "name": item.selector.name,
                            "automation_id": item.selector.automation_id,
                        }
                        for item in controls[:8]
                    ],
                )
            except Exception as error:
                note(
                    "office_observe",
                    status="FAILED",
                    detail=f"{type(error).__name__}: {error}",
                )

        if WORD.exists() and EXCEL.exists() and second_app == "lane2-word":
            try:
                from openpyxl import Workbook

                xlsx = DEMO_DIR / f"lane2-excel-{run_token}.xlsx"
                book = Workbook()
                book.active["A1"] = "JARVIS lane2 excel"
                book.save(xlsx)
                excel = connector.desktop_open(
                    "lane2-excel",
                    str(EXCEL),
                    xlsx.name,
                    args=["/x"],
                    cwd=".",
                    idempotency_key=f"lane2-excel-open-{run_token}",
                    show_window=True,
                )
                owned_ids.append("lane2-excel")
                note(
                    "excel_open",
                    status=excel.get("status"),
                    error=(excel.get("error") or {}).get("classification"),
                    pid=(excel.get("result") or {}).get("pid"),
                    window_hwnd=(excel.get("result") or {}).get("window_hwnd"),
                )
                if excel.get("status") == "COMPLETED":
                    excel_provider = _provider(connector, "lane2-excel")
                    excel_window = excel_provider.observe_main_window(
                        title_contains=xlsx.stem,
                        class_name="XLMAIN",
                    )
                    excel_controls = excel_provider.inspect(excel_window.identity)
                    cell = next(
                        (
                            item
                            for item in excel_controls
                            if isinstance(item.value, str)
                            and "JARVIS lane2 excel" in item.value
                        ),
                        None,
                    )
                    note(
                        "excel_observe",
                        title=excel_window.identity.title,
                        class_name=excel_window.identity.class_name,
                        hwnd=excel_window.identity.hwnd,
                        control_count=len(excel_controls),
                        cell_value_sha=(
                            _sha(cell.value) if cell is not None else None
                        ),
                    )
            except Exception as error:
                note(
                    "excel_observe",
                    status="FAILED",
                    detail=f"{type(error).__name__}: {error}",
                )

        focus_app = None
        for candidate in (second_app, "lane2-excel"):
            if candidate not in owned_ids:
                continue
            try:
                connector.get_validated_process_record(candidate)
                focus_app = candidate
                break
            except Exception:
                continue
        if notepad_live and focus_app is not None:
            rec_a_before = connector.get_validated_process_record("lane2-notepad")
            rec_b_before = connector.get_validated_process_record(focus_app)
            focus_b = connector.focus_process(focus_app)
            note(
                "focus_second",
                app=focus_app,
                status=focus_b.get("status"),
                focused=(focus_b.get("result") or {}).get("focused"),
                hwnd=(focus_b.get("result") or {}).get("window_handle"),
            )
            focus_a = connector.focus_process("lane2-notepad")
            note(
                "focus_back_notepad",
                status=focus_a.get("status"),
                focused=(focus_a.get("result") or {}).get("focused"),
                hwnd=(focus_a.get("result") or {}).get("window_handle"),
            )
            rec_a = connector.get_validated_process_record("lane2-notepad")
            rec_b = connector.get_validated_process_record(focus_app)
            note(
                "identity_handoff",
                notepad_pid=rec_a["pid"],
                second_pid=rec_b["pid"],
                notepad_pid_stable=rec_a["pid"] == rec_a_before["pid"],
                second_pid_stable=rec_b["pid"] == rec_b_before["pid"],
                swapped=rec_a["pid"] == rec_b["pid"],
            )

        if notepad_live:
            identity = _provider(connector, "lane2-notepad").process
            os.kill(identity.pid, 9)
            time.sleep(0.4)
            stale = WindowsOperatorProvider(identity)
            try:
                stale.observe_main_window()
                note(
                    "stale_identity",
                    status="FAILED",
                    detail="stale identity still observed a window",
                )
            except ProcessIdentityMismatch as error:
                note(
                    "stale_identity",
                    status="COMPLETED",
                    error_code=error.code,
                    detail=str(error),
                )
            inspect = connector.inspect_process("lane2-notepad")
            note(
                "inspect_after_kill",
                status=inspect.get("status"),
                state=(inspect.get("result") or {}).get("state"),
                alive=(inspect.get("readback") or {}).get("alive"),
            )

        before_explorer = _visible_application_windows()
        explorer = connector.launch_process(
            "lane2-folder",
            str(EXPLORER),
            [str(DEMO_DIR)],
            cwd=".",
            idempotency_key=f"lane2-folder-{run_token}",
            show_window=True,
        )
        owned_ids.append("lane2-folder")
        note(
            "explorer",
            status=explorer.get("status"),
            error=(explorer.get("error") or {}).get("classification"),
            pid=(explorer.get("result") or {}).get("pid"),
        )
        if explorer.get("status") == "COMPLETED":
            bound = connector.bind_new_window(
                "lane2-folder",
                title_contains="lane2-demo",
                before=before_explorer,
                timeout_s=12.0,
            )
            bound_pid = (bound.get("result") or {}).get("pid")
            note(
                "explorer_bind",
                status=bound.get("status"),
                error=(bound.get("error") or {}).get("classification"),
                pid=bound_pid,
            )
            if bound.get("status") == "COMPLETED" and int(bound_pid or 0) != 13212:
                try:
                    folder = _provider(connector, "lane2-folder")
                    exp_window = folder.observe_main_window(
                        title_contains="lane2-demo"
                    )
                    item = folder.find_named_item(
                        exp_window.identity,
                        ("hello.txt", "hello"),
                    )
                    selected = folder.perform(
                        OperatorAction(
                            action_id="lane2-explorer-select",
                            kind="select",
                            window=exp_window.identity,
                            control=item.selector,
                            timeout_s=6.0,
                        )
                    )
                    note(
                        "explorer_select",
                        outcome=selected.outcome,
                        error_code=selected.error_code,
                        item_name=item.selector.name,
                        window_title=exp_window.identity.title,
                    )
                except Exception as error:
                    note(
                        "explorer_select",
                        outcome="FAILED",
                        detail=f"{type(error).__name__}: {error}",
                    )

        if CALC.exists():
            before_calc = _visible_application_windows()
            calc = connector.launch_process(
                "lane2-calc",
                str(CALC),
                (),
                cwd=".",
                idempotency_key=f"lane2-calc-{run_token}",
                show_window=True,
            )
            owned_ids.append("lane2-calc")
            note(
                "calculator_launch",
                status=calc.get("status"),
                error=(calc.get("error") or {}).get("classification"),
                pid=(calc.get("result") or {}).get("pid"),
            )
            if calc.get("status") == "COMPLETED":
                deadline = time.time() + 8.0
                calc_window = None
                while time.time() < deadline and calc_window is None:
                    after = _visible_application_windows()
                    before_keys = {window.key() for window in before_calc}
                    fresh = [
                        window
                        for window in after
                        if window.key() not in before_keys
                        and "calculator" in window.title.casefold()
                    ]
                    if len(fresh) == 1:
                        calc_window = fresh[0]
                        break
                    titled = [
                        window
                        for window in after
                        if "calculator" in window.title.casefold()
                    ]
                    if len(titled) == 1:
                        calc_window = titled[0]
                        break
                    time.sleep(0.2)
                if calc_window is not None:
                    calc_exe = str(
                        process_creation_identity(calc_window.pid).get("exe") or ""
                    )
                    if "ApplicationFrameHost" not in calc_exe:
                        calc_owned_pids.append(int(calc_window.pid))
                if calc_window is None:
                    note(
                        "calculator_observe",
                        status="FAILED",
                        detail="no unique Calculator window appeared",
                    )
                else:
                    identity = process_creation_identity(calc_window.pid)
                    if identity.get("create_time") is None:
                        note(
                            "calculator_observe",
                            status="FAILED",
                            detail="calculator window pid lacked create_time",
                            pid=calc_window.pid,
                        )
                    else:
                        time.sleep(0.8)
                        calc_provider = WindowsOperatorProvider(
                            ProcessIdentity(
                                logical_id="lane2-calc",
                                pid=int(calc_window.pid),
                                create_time=int(identity["create_time"]),
                                executable=str(identity.get("exe") or CALC),
                            )
                        )
                        try:
                            time.sleep(2.0)
                            observed = calc_provider.observe_main_window(
                                title_contains="Calculator"
                            )
                            one = calc_provider.find_control_on_any_window(
                                names=("One",),
                                control_type="ButtonControl",
                                automation_ids=("num1Button",),
                            )
                            clicked = calc_provider.perform(
                                OperatorAction(
                                    action_id="lane2-calc-one",
                                    kind="click",
                                    window=one.window,
                                    control=one.selector,
                                    condition=ControlCondition("enabled", True),
                                    timeout_s=5.0,
                                )
                            )
                            closer = calc_provider.resolve_named_control(
                                observed.identity,
                                names=("Close Calculator",),
                                control_type="ButtonControl",
                                automation_ids=("Close",),
                            )
                            closed_click = calc_provider.perform(
                                OperatorAction(
                                    action_id="lane2-calc-close",
                                    kind="click",
                                    window=observed.identity,
                                    control=closer.selector,
                                    condition=ControlCondition(
                                        "window_exists", False
                                    ),
                                    timeout_s=6.0,
                                )
                            )
                            note(
                                "calculator_observe",
                                status="COMPLETED",
                                title=observed.identity.title,
                                class_name=observed.identity.class_name,
                                hwnd=observed.identity.hwnd,
                                pid=calc_window.pid,
                                launch_pid=(calc.get("result") or {}).get("pid"),
                                click_one=clicked.outcome,
                                close=closed_click.outcome,
                            )
                        except Exception as error:
                            note(
                                "calculator_observe",
                                status="FAILED",
                                detail=f"{type(error).__name__}: {error}",
                                pid=calc_window.pid,
                            )

        report["ok"] = any(
            step.get("step") == "open_artifact_and_assert"
            and step.get("claimed") == "COMPLETED"
            for step in steps
        )
    except Exception as error:
        note(
            "fatal",
            detail=f"{type(error).__name__}: {error}",
            trace=traceback.format_exc()[-1500:],
        )
        report["ok"] = False
    finally:
        closed = []
        for process_id in list(dict.fromkeys(owned_ids)):
            receipt = _close(connector, process_id, f"close-{process_id}")
            closed.append(
                {
                    "process_id": process_id,
                    "status": receipt.get("status"),
                    "error": receipt.get("error"),
                }
            )
        report["closed"] = closed
        for pid in calc_owned_pids:
            try:
                os.kill(pid, 9)
            except OSError:
                pass
        result_path = DEMO_DIR / "DEMO_RESULT.json"
        result_path.write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8"
        )
        print(json.dumps(report, indent=2, default=str))
        return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
