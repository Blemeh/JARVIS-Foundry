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

from jarvis_foundry.connectors.personal_operator import (
    PersonalOperatorConnector,
    _visible_application_windows,
)
from jarvis_foundry.connectors.windows_operator import (
    ControlCondition,
    OperatorAction,
    ProcessIdentity,
    ProcessIdentityMismatch,
    WindowsOperatorProvider,
)
from jarvis_foundry.fabric.computer_operator import open_artifact_and_assert

DEMO_DIR = Path(__file__).resolve().parent
NOTEPAD = Path(r"C:\Windows\System32\notepad.exe")
WORD = Path(r"C:\Program Files (x86)\Microsoft Office\Office16\WINWORD.EXE")
EXCEL = Path(r"C:\Program Files (x86)\Microsoft Office\Office16\EXCEL.EXE")
EXPLORER = Path(r"C:\Windows\explorer.exe")
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
            try:
                dialog = provider.invoke_file_dialog(
                    window.identity,
                    path=str(opened_file),
                    action="open",
                    timeout_s=12.0,
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
                    after_open = provider.observe_main_window()
                    read = provider.find_document_control(after_open.identity)
                    note(
                        "file_open_readback",
                        window_title=after_open.identity.title,
                        value_length=read.to_dict().get("value_length"),
                        value_sha256=read.to_dict().get("value_sha256"),
                    )
            except Exception as error:
                note(
                    "file_open_dialog",
                    outcome="FAILED",
                    detail=f"{type(error).__name__}: {error}",
                )
            try:
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
            except Exception as error:
                note(
                    "type_paste",
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
                ["/separate", "."],
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
        if notepad_live and launched.get("status") == "COMPLETED":
            rec_a_before = connector.get_validated_process_record("lane2-notepad")
            rec_b_before = connector.get_validated_process_record(second_app)
            focus_b = connector.focus_process(second_app)
            note(
                "focus_second",
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
            rec_b = connector.get_validated_process_record(second_app)
            note(
                "identity_handoff",
                notepad_pid=rec_a["pid"],
                second_pid=rec_b["pid"],
                notepad_pid_stable=rec_a["pid"] == rec_a_before["pid"],
                second_pid_stable=rec_b["pid"] == rec_b_before["pid"],
                swapped=rec_a["pid"] == rec_b["pid"],
            )

        if second_app in {"lane2-word", "lane2-excel"} and launched.get("status") == "COMPLETED":
            try:
                office = _provider(connector, second_app)
                observed = office.observe_main_window(
                    title_contains=target.stem,
                    class_name="OpusApp" if second_app == "lane2-word" else None,
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

        explorer = connector.launch_process(
            "lane2-folder",
            str(EXPLORER),
            ["/separate", "."],
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
        result_path = DEMO_DIR / "DEMO_RESULT.json"
        result_path.write_text(
            json.dumps(report, indent=2, default=str), encoding="utf-8"
        )
        print(json.dumps(report, indent=2, default=str))
        return 0 if report.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
