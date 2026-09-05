"""Independently verify V13 personal-operator proof artifacts.

This script deliberately does not import ``jarvis_foundry``.  It consumes a
declarative check manifest and reopens artifacts through their underlying
format libraries or read-only operating-system probes.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Mapping


MAX_CHECKS = 200
MAX_REQUIRED_VALUES = 500


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _bounded_strings(value: object, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_REQUIRED_VALUES:
        raise ValueError(f"{field} must be a bounded list")
    result = [str(item) for item in value]
    if any(len(item) > 10_000 for item in result):
        raise ValueError(f"{field} contains an oversized value")
    return result


def _resolve(root: Path, raw: object) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("check path must be a non-empty string")
    candidate = Path(raw)
    if candidate.is_absolute():
        raise ValueError("check path must be portable and relative to the proof root")
    path = (root / candidate).resolve()
    try:
        path.relative_to(root)
    except ValueError as error:
        raise ValueError(f"check path escapes proof root: {raw}") from error
    return path


def _json_pointer(value: object, pointer: str) -> object:
    if not pointer.startswith("/"):
        raise ValueError("JSON pointer must start with '/'")
    current = value
    for raw_part in pointer.split("/")[1:]:
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if isinstance(current, Mapping):
            if part not in current:
                raise KeyError(pointer)
            current = current[part]
        elif isinstance(current, list):
            current = current[int(part)]
        else:
            raise KeyError(pointer)
    return current


def _process_identity(pid: int) -> dict[str, Any]:
    identity: dict[str, Any] = {"pid": int(pid), "live": False}
    if pid <= 0 or os.name != "nt":
        return identity
    try:
        import ctypes
        from ctypes import wintypes

        query = 0x1000
        still_active = 259
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = [
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        ]
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.GetExitCodeProcess.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.GetExitCodeProcess.restype = wintypes.BOOL
        kernel32.GetProcessTimes.argtypes = [
            wintypes.HANDLE,
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
            ctypes.POINTER(wintypes.FILETIME),
        ]
        kernel32.GetProcessTimes.restype = wintypes.BOOL
        kernel32.QueryFullProcessImageNameW.argtypes = [
            wintypes.HANDLE,
            wintypes.DWORD,
            wintypes.LPWSTR,
            ctypes.POINTER(wintypes.DWORD),
        ]
        kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(query, False, pid)
        if not handle:
            return identity
        try:
            exit_code = wintypes.DWORD()
            if kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                identity["live"] = int(exit_code.value) == still_active
            creation = wintypes.FILETIME()
            exit_time = wintypes.FILETIME()
            kernel = wintypes.FILETIME()
            user = wintypes.FILETIME()
            if kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_time),
                ctypes.byref(kernel),
                ctypes.byref(user),
            ):
                identity["create_time"] = (
                    int(creation.dwHighDateTime) << 32
                ) | int(creation.dwLowDateTime)
            size = wintypes.DWORD(32768)
            buffer = ctypes.create_unicode_buffer(size.value)
            if kernel32.QueryFullProcessImageNameW(
                handle, 0, buffer, ctypes.byref(size)
            ):
                identity["exe"] = buffer.value
        finally:
            kernel32.CloseHandle(handle)
    except Exception as error:  # noqa: BLE001 - verifier reports unavailable evidence
        identity["probe_error"] = type(error).__name__
    return identity


def _verify_file_hash(root: Path, check: Mapping[str, Any]) -> dict[str, Any]:
    path = _resolve(root, check.get("path"))
    actual = _sha256(path) if path.is_file() else None
    expected = str(check.get("sha256") or "")
    return {
        "pass": bool(actual and expected and actual == expected),
        "path": str(path),
        "expected_sha256": expected,
        "actual_sha256": actual,
    }


def _verify_json(root: Path, check: Mapping[str, Any]) -> dict[str, Any]:
    path = _resolve(root, check.get("path"))
    value = json.loads(path.read_text(encoding="utf-8"))
    required = _bounded_strings(check.get("required_keys"), "required_keys")
    if isinstance(value, Mapping):
        missing = [key for key in required if key not in value]
    else:
        missing = list(required)
    expected_rows = check.get("row_count")
    rows = (
        len(value)
        if isinstance(value, list)
        else len(value.get("records", []))
        if isinstance(value, Mapping) and isinstance(value.get("records"), list)
        else None
    )
    count_ok = expected_rows is None or rows == int(expected_rows)
    expected_values = check.get("expected_values") or {}
    if not isinstance(expected_values, Mapping):
        raise ValueError("expected_values must be an object")
    mismatches: dict[str, Any] = {}
    for pointer, expected in expected_values.items():
        try:
            actual = _json_pointer(value, str(pointer))
        except (KeyError, IndexError, ValueError, TypeError):
            actual = "<missing>"
        if actual != expected:
            mismatches[str(pointer)] = {
                "expected": expected,
                "actual": actual,
            }
    expected_key_count = check.get("object_key_count")
    key_count = len(value) if isinstance(value, Mapping) else None
    key_count_ok = (
        expected_key_count is None
        or key_count == int(expected_key_count)
    )
    object_key_counts = check.get("object_key_counts") or {}
    if not isinstance(object_key_counts, Mapping):
        raise ValueError("object_key_counts must be an object")
    key_count_mismatches: dict[str, Any] = {}
    for pointer, expected_count in object_key_counts.items():
        try:
            target = _json_pointer(value, str(pointer))
            actual_count = len(target) if isinstance(target, Mapping) else None
        except (KeyError, IndexError, ValueError, TypeError):
            actual_count = None
        if actual_count != int(expected_count):
            key_count_mismatches[str(pointer)] = {
                "expected": int(expected_count),
                "actual": actual_count,
            }
    return {
        "pass": (
            not missing
            and count_ok
            and not mismatches
            and key_count_ok
            and not key_count_mismatches
        ),
        "path": str(path),
        "missing_keys": missing,
        "row_count": rows,
        "object_key_count": key_count,
        "mismatches": mismatches,
        "key_count_mismatches": key_count_mismatches,
    }


def _verify_xlsx(root: Path, check: Mapping[str, Any]) -> dict[str, Any]:
    from openpyxl import load_workbook

    path = _resolve(root, check.get("path"))
    workbook = load_workbook(path, read_only=False, data_only=False, keep_links=False)
    try:
        sheet_name = str(check.get("sheet") or workbook.sheetnames[0])
        worksheet = workbook[sheet_name]
        expected_cells = check.get("cells") or {}
        expected_formulas = check.get("formulas") or {}
        if not isinstance(expected_cells, Mapping) or not isinstance(
            expected_formulas, Mapping
        ):
            raise ValueError("xlsx cells/formulas must be objects")
        mismatches: dict[str, Any] = {}
        for coordinate, expected in {**expected_cells, **expected_formulas}.items():
            actual = worksheet[str(coordinate)].value
            if actual != expected:
                mismatches[str(coordinate)] = {
                    "expected": expected,
                    "actual": actual,
                }
        expected_sheets = _bounded_strings(check.get("sheets"), "sheets")
        sheets_ok = not expected_sheets or workbook.sheetnames == expected_sheets
        expected_freeze = check.get("freeze_panes")
        freeze_ok = (
            expected_freeze is None
            or str(worksheet.freeze_panes or "") == str(expected_freeze)
        )
        expected_filter = check.get("auto_filter")
        filter_ok = (
            expected_filter is None
            or str(worksheet.auto_filter.ref or "") == str(expected_filter)
        )
        header_bold = check.get("header_bold")
        header_bold_ok = True
        if header_bold is not None:
            if not isinstance(header_bold, bool):
                raise ValueError("header_bold must be boolean")
            header_bold_ok = all(
                bool(worksheet.cell(1, column).font.bold) is header_bold
                for column in range(1, worksheet.max_column + 1)
            )
        return {
            "pass": (
                not mismatches
                and sheets_ok
                and freeze_ok
                and filter_ok
                and header_bold_ok
            ),
            "path": str(path),
            "sheet_names": list(workbook.sheetnames),
            "mismatches": mismatches,
            "freeze_panes": str(worksheet.freeze_panes or ""),
            "auto_filter": str(worksheet.auto_filter.ref or ""),
            "header_bold": header_bold_ok,
        }
    finally:
        workbook.close()


def _verify_docx(root: Path, check: Mapping[str, Any]) -> dict[str, Any]:
    from docx import Document

    path = _resolve(root, check.get("path"))
    document = Document(path)
    text_parts = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            text_parts.append(" | ".join(cell.text for cell in row.cells))
    text = "\n".join(text_parts)
    required = _bounded_strings(check.get("required_text"), "required_text")
    missing = [value for value in required if value not in text]
    min_paragraphs = int(check.get("min_paragraphs") or 0)
    min_tables = int(check.get("min_tables") or 0)
    return {
        "pass": (
            not missing
            and len(document.paragraphs) >= min_paragraphs
            and len(document.tables) >= min_tables
        ),
        "path": str(path),
        "paragraph_count": len(document.paragraphs),
        "table_count": len(document.tables),
        "missing_text": missing,
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def _verify_pdf(root: Path, check: Mapping[str, Any]) -> dict[str, Any]:
    from pypdf import PdfReader

    path = _resolve(root, check.get("path"))
    reader = PdfReader(path, strict=False)
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    required = _bounded_strings(check.get("required_text"), "required_text")
    missing = [value for value in required if value not in text]
    expected_pages = check.get("page_count")
    pages_ok = expected_pages is None or len(reader.pages) == int(expected_pages)
    return {
        "pass": not missing and pages_ok,
        "path": str(path),
        "page_count": len(reader.pages),
        "missing_text": missing,
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def _verify_pptx(root: Path, check: Mapping[str, Any]) -> dict[str, Any]:
    from pptx import Presentation
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    path = _resolve(root, check.get("path"))
    presentation = Presentation(path)
    texts: list[str] = []
    pictures = 0
    bounds_ok = True
    for slide in presentation.slides:
        for shape in slide.shapes:
            if getattr(shape, "shape_type", None) == MSO_SHAPE_TYPE.PICTURE:
                pictures += 1
            if hasattr(shape, "text"):
                texts.append(str(shape.text))
            left = int(getattr(shape, "left", 0))
            top = int(getattr(shape, "top", 0))
            width = int(getattr(shape, "width", 0))
            height = int(getattr(shape, "height", 0))
            if (
                left < 0
                or top < 0
                or width < 0
                or height < 0
                or left + width > int(presentation.slide_width)
                or top + height > int(presentation.slide_height)
            ):
                bounds_ok = False
    text = "\n".join(texts)
    required = _bounded_strings(check.get("required_text"), "required_text")
    missing = [value for value in required if value not in text]
    expected_slides = check.get("slide_count")
    slides_ok = (
        expected_slides is None
        or len(presentation.slides) == int(expected_slides)
    )
    min_pictures = int(check.get("min_pictures") or 0)
    return {
        "pass": (
            not missing
            and slides_ok
            and pictures >= min_pictures
            and bounds_ok
        ),
        "path": str(path),
        "slide_count": len(presentation.slides),
        "picture_count": pictures,
        "bounds_ok": bounds_ok,
        "missing_text": missing,
        "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
    }


def _verify_process(check: Mapping[str, Any]) -> dict[str, Any]:
    expected = check.get("identity")
    if not isinstance(expected, Mapping):
        raise ValueError("process check requires identity object")
    pid = int(expected.get("pid") or 0)
    create_time = expected.get("create_time")
    exe = str(expected.get("exe") or "")
    if pid <= 0 or create_time is None or not exe:
        raise ValueError(
            "process identity requires pid, create_time, and executable"
        )
    live_value = check.get("live")
    if not isinstance(live_value, bool):
        raise ValueError("process live expectation must be boolean")
    expected_live = live_value
    actual = _process_identity(pid)
    same_identity = (
        actual.get("live") is True
        and actual.get("create_time") == create_time
        and str(actual.get("exe") or "").casefold() == exe.casefold()
    )
    return {
        "pass": same_identity if expected_live else not same_identity,
        "expected_live": expected_live,
        "expected_identity": dict(expected),
        "actual_identity": actual,
        "same_identity_live": same_identity,
    }


def _verify_tree(root: Path, check: Mapping[str, Any]) -> dict[str, Any]:
    path = _resolve(root, check.get("path") or ".")
    if not path.is_dir():
        raise ValueError("tree check path must be a directory")
    forbidden = _bounded_strings(
        check.get("forbidden_globs"),
        "forbidden_globs",
    )
    forbidden_matches = sorted({
        str(candidate.relative_to(root))
        for pattern in forbidden
        for candidate in path.rglob(pattern)
        if candidate.is_file()
    })
    required = _bounded_strings(
        check.get("required_paths"),
        "required_paths",
    )
    missing = [
        value
        for value in required
        if not _resolve(root, value).exists()
    ]
    file_count = sum(1 for candidate in path.rglob("*") if candidate.is_file())
    return {
        "pass": not forbidden_matches and not missing,
        "path": str(path),
        "file_count": file_count,
        "forbidden_matches": forbidden_matches,
        "missing_paths": missing,
    }


def _verify_sqlite(root: Path, check: Mapping[str, Any]) -> dict[str, Any]:
    path = _resolve(root, check.get("path"))
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        quick_check = connection.execute("PRAGMA quick_check").fetchone()
    finally:
        connection.close()
    observed = str(quick_check[0]) if quick_check else ""
    return {
        "pass": observed == "ok",
        "path": str(path),
        "quick_check": observed,
    }


VERIFY = {
    "file_hash": lambda root, check: _verify_file_hash(root, check),
    "json": lambda root, check: _verify_json(root, check),
    "xlsx": lambda root, check: _verify_xlsx(root, check),
    "docx": lambda root, check: _verify_docx(root, check),
    "pdf": lambda root, check: _verify_pdf(root, check),
    "pptx": lambda root, check: _verify_pptx(root, check),
    "process": lambda root, check: _verify_process(check),
    "tree": lambda root, check: _verify_tree(root, check),
    "sqlite": lambda root, check: _verify_sqlite(root, check),
}


def verify_manifest(manifest_path: Path) -> dict[str, Any]:
    manifest_path = manifest_path.expanduser().resolve()
    manifest_sha256 = _sha256(manifest_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, Mapping):
        raise ValueError("manifest must be an object")
    root_raw = manifest.get("root", ".")
    if root_raw not in (".", ""):
        raise ValueError(
            "manifest root must be portable and relative to the manifest parent"
        )
    root = manifest_path.parent
    checks = manifest.get("checks")
    if not isinstance(checks, list) or not checks or len(checks) > MAX_CHECKS:
        raise ValueError("manifest checks must be a non-empty bounded list")
    results: list[dict[str, Any]] = []
    for index, raw_check in enumerate(checks):
        if not isinstance(raw_check, Mapping):
            raise ValueError(f"check {index} must be an object")
        kind = str(raw_check.get("kind") or "")
        verifier = VERIFY.get(kind)
        if verifier is None:
            raise ValueError(f"check {index} has unsupported kind {kind!r}")
        name = str(raw_check.get("name") or f"check-{index}")
        try:
            observed = verifier(root, raw_check)
            result = {"name": name, "kind": kind, **observed}
        except Exception as error:  # noqa: BLE001 - every failure stays evidence
            result = {
                "name": name,
                "kind": kind,
                "pass": False,
                "error": f"{type(error).__name__}: {error}",
            }
        results.append(result)
    passed = sum(bool(item.get("pass")) for item in results)
    return {
        "schema": "jarvis.v13.personal_operator.independent_verifier.v2",
        "verified_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "manifest": manifest_path.name,
        "root": ".",
        "manifest_sha256": manifest_sha256,
        "verifier_sha256": _sha256(Path(__file__).resolve()),
        "verdict": "PASS" if passed == len(results) else "FAIL",
        "checks_passed": passed,
        "checks_total": len(results),
        "results": results,
    }


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = _args()
    try:
        result = verify_manifest(args.manifest)
    except Exception as error:  # noqa: BLE001 - malformed manifests fail closed
        result = {
            "schema": "jarvis.v13.personal_operator.independent_verifier.v2",
            "verified_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "manifest": str(args.manifest),
            "verdict": "FAIL",
            "checks_passed": 0,
            "checks_total": 0,
            "error": f"{type(error).__name__}: {error}",
        }
    text = json.dumps(result, ensure_ascii=True, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        output = args.output.expanduser().resolve()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text, encoding="utf-8")
    sys.stdout.write(text)
    return 0 if result.get("verdict") == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
