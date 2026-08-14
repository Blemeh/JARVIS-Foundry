"""Read-only R2 repository measurement for JARVIS-Foundry.

Computes a module/test inventory and static metric snapshot from a checkout
path without writing to it.  The only output is a JSON document on stdout.
Usage:
    python measure_repo.py <path-to-JARVIS-Foundry-checkout>
"""

from __future__ import annotations

import ast
import json
import re
import sys
from collections import Counter
from pathlib import Path


SRC_REL = Path("src") / "jarvis_foundry"
TEST_RELS = (Path("tests") / "foundry", Path("tests") / "acceptance")
MARKER_RE = re.compile(r"\b(TODO|FIXME|XXX|HACK)\b")
PORT_RE = re.compile(r"\b(876[0-9]|878[0-4]|3000|8000|8080|5000|5432)\b")
ENV_RE = re.compile(r"\bos\.environ\b|environ\.get\(")
SLEEP_RE = re.compile(r"\b(?:time\.sleep|asyncio\.sleep)\s*\(")
NETWORK_RE = re.compile(
    r"\b(requests|httpx|aiohttp|urllib|socket|openai|anthropic)\b"
)
SUBPROCESS_RE = re.compile(r"\b(subprocess|os\.system|Popen)\b")
SQLITE_PATH_RE = re.compile(
    r"sqlite3\.connect\s*\(\s*(['\"])([^'\"]*\.db)(['\"])"
)
CHDIR_RE = re.compile(r"\bos\.chdir\s*\(")


def code_lines(lines: list[str]) -> int:
    return sum(1 for line in lines if line.strip() and not line.lstrip().startswith("#"))


def except_kind(node: ast.ExceptHandler) -> str:
    if node.type is None:
        return "bare"
    return ast.unparse(node.type)


def collect_functions(tree: ast.Module) -> list[dict]:
    out: list[dict] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            params = [
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
            ]
            real = [p for p in params if p.arg not in ("self", "cls")]
            annotated = sum(1 for p in real if p.annotation is not None)
            out.append(
                {
                    "name": node.name,
                    "lineno": node.lineno,
                    "end_lineno": node.end_lineno,
                    "lines": (node.end_lineno or node.lineno) - node.lineno + 1,
                    "async": isinstance(node, ast.AsyncFunctionDef),
                    "params": len(real),
                    "params_annotated": annotated,
                    "return_annotated": node.returns is not None,
                }
            )
    return out


def analyze_file(path: Path, is_test: bool) -> dict:
    text = path.read_text(encoding="utf-8", errors="replace")
    lines = text.splitlines()
    try:
        tree = ast.parse(text)
        parse_error = None
    except SyntaxError as exc:
        tree = None
        parse_error = f"{exc.lineno}:{exc.offset} {exc.msg}"

    result: dict = {
        "path": str(path).replace("\\", "/"),
        "bytes": path.stat().st_size,
        "total_lines": len(lines),
        "nonblank_lines": sum(1 for line in lines if line.strip()),
        "code_lines": code_lines(lines),
        "markers": len(MARKER_RE.findall(text)),
        "parse_error": parse_error,
        "top_level": [],
        "functions": [],
        "except_handlers": Counter(),
        "typed_module_vars": 0,
        "test_functions": 0,
        "fixtures": 0,
        "parametrize": 0,
        "monkeypatch_capsys": 0,
        "tmp_path_use": 0,
        "sleep": 0,
        "ports": 0,
        "env": 0,
        "network_imports": 0,
        "subprocess": 0,
        "sqlite_literal_db": 0,
        "chdir": 0,
    }

    if tree is not None:
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                entry = {
                    "kind": "function",
                    "name": node.name,
                    "lineno": node.lineno,
                    "end_lineno": node.end_lineno,
                    "lines": (node.end_lineno or node.lineno) - node.lineno + 1,
                }
                result["top_level"].append(entry)
            elif isinstance(node, ast.ClassDef):
                methods = [
                    child
                    for child in node.body
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))
                ]
                entry = {
                    "kind": "class",
                    "name": node.name,
                    "lineno": node.lineno,
                    "end_lineno": node.end_lineno,
                    "lines": (node.end_lineno or node.lineno) - node.lineno + 1,
                    "method_count": len(methods),
                    "method_lines": sum(
                        (m.end_lineno or m.lineno) - m.lineno + 1 for m in methods
                    ),
                }
                result["top_level"].append(entry)
            elif isinstance(node, ast.AnnAssign):
                result["typed_module_vars"] += 1

        result["functions"] = collect_functions(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                result["except_handlers"][except_kind(node)] += 1
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "fixture":
                    result["fixtures"] += 1
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                if node.func.id == "pytest":
                    result["parametrize"] += 1

    if is_test and tree is not None:
        for node in tree.body:
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name.startswith("test_"):
                    result["test_functions"] += 1
            elif isinstance(node, ast.ClassDef):
                for child in node.body:
                    if isinstance(
                        child, (ast.FunctionDef, ast.AsyncFunctionDef)
                    ) and child.name.startswith("test_"):
                        result["test_functions"] += 1
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr == "parametrize":
                    result["parametrize"] += 1
        params = [
            a.arg
            for node in ast.walk(tree)
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            for a in [
                *node.args.posonlyargs,
                *node.args.args,
                *node.args.kwonlyargs,
            ]
        ]
        if any(p in params for p in ("monkeypatch", "capsys", "capfd", "capfdbinary")):
            result["monkeypatch_capsys"] += 1
        if any(p in params for p in ("tmp_path", "tmpdir")):
            result["tmp_path_use"] += 1

    if is_test:
        result["sleep"] = len(SLEEP_RE.findall(text))
        result["ports"] = len(PORT_RE.findall(text))
        result["env"] = len(ENV_RE.findall(text))
        result["network_imports"] = len(NETWORK_RE.findall(text))
        result["subprocess"] = len(SUBPROCESS_RE.findall(text))
        result["sqlite_literal_db"] = len(SQLITE_PATH_RE.findall(text))
        result["chdir"] = len(CHDIR_RE.findall(text))

    result["except_handlers"] = dict(result["except_handlers"])
    return result


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: python measure_repo.py <checkout-path>", file=sys.stderr)
        return 2
    root = Path(sys.argv[1]).resolve()
    src_dir = root / SRC_REL

    modules: list[dict] = []
    tests: list[dict] = []
    all_functions: list[dict] = []
    except_counter: Counter[str] = Counter()
    marker_counter = 0
    size_buckets: Counter[str] = Counter()

    for path in sorted(src_dir.rglob("*.py")):
        if ".egg-info" in path.parts:
            continue
        info = analyze_file(path, is_test=False)
        for fn in info["functions"]:
            fn["module"] = info["path"]
        modules.append(info)
        all_functions.extend(info["functions"])
        for kind, count in info["except_handlers"].items():
            except_counter[kind] += count
        marker_counter += info["markers"]

    test_support = {"conftest.py", "crash_fixture.py", "supervisor_fixture.py"}
    for test_dir in TEST_RELS:
        base = root / test_dir
        if base.is_dir():
            for path in sorted(base.rglob("*.py")):
                info = analyze_file(path, is_test=True)
                info["test_support"] = path.name in test_support
                for fn in info["functions"]:
                    fn["module"] = info["path"]
                tests.append(info)
                all_functions.extend(info["functions"])
                for kind, count in info["except_handlers"].items():
                    except_counter[kind] += count
                marker_counter += info["markers"]

    for fn in all_functions:
        size = fn["lines"]
        if size <= 10:
            bucket = "1-10"
        elif size <= 20:
            bucket = "11-20"
        elif size <= 40:
            bucket = "21-40"
        elif size <= 80:
            bucket = "41-80"
        elif size <= 160:
            bucket = "81-160"
        else:
            bucket = "161+"
        size_buckets[bucket] += 1

    top_functions = sorted(all_functions, key=lambda fn: fn["lines"], reverse=True)[:20]
    typed = sum(1 for fn in all_functions if fn["params_annotated"] == fn["params"])
    any_typed = sum(1 for fn in all_functions if fn["params_annotated"] or fn["return_annotated"])
    return_typed = sum(1 for fn in all_functions if fn["return_annotated"])
    untyped = sum(1 for fn in all_functions if fn["params"] and not fn["params_annotated"])

    # Cross-reference scan for source-defined names that are never referenced.
    src_texts: dict[str, str] = {}
    defined: dict[str, str] = {}
    for mod in modules:
        text = Path(root / mod["path"].replace("/", "\\")).read_text(
            encoding="utf-8", errors="replace"
        )
        src_texts[mod["path"]] = text
        for entry in mod["top_level"]:
            defined[entry["name"]] = mod["path"]

    test_text = "\n".join(
        Path(root / t["path"].replace("/", "\\")).read_text(
            encoding="utf-8", errors="replace"
        )
        for t in tests
    )
    src_all = "\n".join(src_texts.values())
    dead_candidates: list[dict] = []
    for name, path in sorted(defined.items()):
        count = len(re.findall(rf"\b{re.escape(name)}\b", src_all))
        test_count = len(re.findall(rf"\b{re.escape(name)}\b", test_text))
        if count <= 1 and test_count == 0:
            dead_candidates.append(
                {"name": name, "defined_in": path, "src_refs_after_def": count - 1}
            )

    metrics = {
        "src_modules": len(modules),
        "test_files": len(tests),
        "src_lines": sum(m["total_lines"] for m in modules),
        "src_code_lines": sum(m["code_lines"] for m in modules),
        "test_lines": sum(t["total_lines"] for t in tests),
        "test_code_lines": sum(t["code_lines"] for t in tests),
        "all_functions": len(all_functions),
        "function_size_buckets": dict(size_buckets),
        "except_handlers_total": sum(except_counter.values()),
        "except_handlers_by_kind": dict(except_counter),
        "markers_total": marker_counter,
        "typing": {
            "functions_total": len(all_functions),
            "functions_with_params": sum(1 for fn in all_functions if fn["params"]),
            "functions_all_params_and_return_annotated": typed,
            "functions_any_annotation": any_typed,
            "functions_return_annotated": return_typed,
            "functions_with_params_untyped": untyped,
            "typed_module_vars": sum(m["typed_module_vars"] for m in modules),
        },
        "test_metrics": {
            "test_functions": sum(t["test_functions"] for t in tests),
            "fixtures": sum(t["fixtures"] for t in tests),
            "parametrize": sum(t["parametrize"] for t in tests),
            "files_with_monkeypatch_or_capsys": sum(
                1 for t in tests if t["monkeypatch_capsys"]
            ),
            "files_with_tmp_path": sum(1 for t in tests if t["tmp_path_use"]),
            "sleep_calls": sum(t["sleep"] for t in tests),
            "fixed_port_hits": sum(t["ports"] for t in tests),
            "env_access": sum(t["env"] for t in tests),
            "network_import_hits": sum(t["network_imports"] for t in tests),
            "subprocess_hits": sum(t["subprocess"] for t in tests),
            "sqlite_literal_db_paths": sum(t["sqlite_literal_db"] for t in tests),
            "chdir_hits": sum(t["chdir"] for t in tests),
        },
        "dead_code_candidates": dead_candidates,
    }

    print(
        json.dumps(
            {
                "checkout": str(root),
                "modules": modules,
                "tests": tests,
                "top_functions": top_functions,
                "metrics": metrics,
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
