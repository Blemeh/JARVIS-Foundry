"""The SHIPPED independent verifier probe suite.

Executed OUT-OF-PROCESS by evolution.verifier_orchestrator:
``python -m jarvis_foundry.evolution.verifier_probe <candidate_tree>``

The candidate tree root is passed as the final argv entry; the FROZEN
criteria list arrives via the JARVIS_VERIFIER_CRITERIA environment
variable (JSON array — set by the orchestrator, never by the
candidate). The probe derives its per-criterion report from REAL
checks over that tree and prints ONE JSON object to stdout:

    {"criteria": {<criterion>: {"status": ..., "evidence_hash": ...}},
     "findings": [...]}

Checks implemented, mapped to criteria by family keyword:

- artifact integrity  (family "correctness") — non-empty tree, every
  file hashes cleanly, size caps enforced;
- python syntax       (family "latency"/fallback) — every .py parses;
- marker hygiene      (family "cost") — no build markers/scratch dirs
  leaked into the artifact set;
- secret scan         (family "safety") — credential patterns refused.

A criterion whose family has no matching check is reported NOT_TESTED
with an explicit reason — never guessed. The probe reads only bytes on
disk under the given root.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

MAX_FILE_BYTES = 8 * 1024 * 1024
SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9]{16,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)(api_key|password|secret)\s*=\s*['\"][^'\"]{12,}"),
)

#: frozen-criterion keyword -> internal check key
FAMILY_KEYWORDS = {
    "correctness": "artifact_integrity",
    "integrity": "artifact_integrity",
    "safety": "secret_scan",
    "secret": "secret_scan",
    "cost": "marker_hygiene",
    "hygiene": "marker_hygiene",
    "latency": "python_syntax",
    "syntax": "python_syntax",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_files(root: Path):
    from .builder import _is_reparse_point

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        dirpath_path = Path(dirpath)
        dirnames[:] = [name for name in dirnames
                       if not _is_reparse_point(dirpath_path / name)]
        for filename in sorted(filenames):
            path = dirpath_path / filename
            if not _is_reparse_point(path):
                yield path.relative_to(root).as_posix(), path


def _run_checks(root: Path) -> tuple[dict[str, tuple[str, str | None]],
                                     list[dict[str, str]]]:
    files = list(_iter_files(root))
    findings: list[dict[str, str]] = []
    checks: dict[str, tuple[str, str | None]] = {}

    # artifact integrity (presence + hashing + size caps)
    if not files:
        checks["artifact_integrity"] = ("NOT_TESTED",
                                        "empty candidate tree")
        findings.append({"severity": "HIGH",
                         "finding": "candidate tree is EMPTY"})
    else:
        oversized, hash_failures = [], []
        for rel, path in files:
            try:
                if path.stat().st_size > MAX_FILE_BYTES:
                    oversized.append(rel)
                    continue
                _sha256(path)
            except OSError as error:
                hash_failures.append(f"{rel}: {error}")
        if oversized or hash_failures:
            checks["artifact_integrity"] = ("FAIL", None)
            findings.append({
                "severity": "HIGH",
                "finding": f"integrity failures: oversized={oversized[:3]} "
                           f"hash_failures={hash_failures[:3]}"})
        else:
            manifest_digest = hashlib.sha256(
                "\n".join(sorted(rel for rel, _ in files))
                .encode("utf-8")).hexdigest()
            checks["artifact_integrity"] = ("PASS", manifest_digest)

    # python syntax
    import ast
    syntax_errors: list[str] = []
    py_count = 0
    for rel, path in files:
        if rel.endswith(".py"):
            py_count += 1
            try:
                ast.parse(path.read_text(encoding="utf-8",
                                         errors="replace"))
            except (SyntaxError, ValueError) as error:
                syntax_errors.append(f"{rel}: {error}")
    if py_count == 0:
        checks["python_syntax"] = ("NOT_TESTED", "no python files")
    elif syntax_errors:
        checks["python_syntax"] = ("FAIL", None)
        findings.append({"severity": "HIGH",
                         "finding": f"syntax errors: "
                                    f"{syntax_errors[:3]}"})
    else:
        checks["python_syntax"] = ("PASS", hashlib.sha256(
            f"{py_count} files parsed".encode()).hexdigest())

    # marker hygiene
    from .builder import MARKER_NAME, RUNTIME_HOME_NAME

    leaked = [rel for rel, _ in files
              if rel == MARKER_NAME
              or rel.startswith(RUNTIME_HOME_NAME + "/")]
    if leaked:
        checks["marker_hygiene"] = ("FAIL", None)
        findings.append({"severity": "MEDIUM",
                         "finding": f"build artifacts leaked into "
                                    f"release tree: {leaked[:3]}"})
    else:
        checks["marker_hygiene"] = ("PASS", hashlib.sha256(
            b"no leaked markers").hexdigest())

    # secret scan
    secrets: list[str] = []
    scanned = 0
    for rel, path in files:
        if path.suffix.lower() in (".png", ".jpg", ".zip", ".exe", ".dll"):
            continue
        try:
            if path.stat().st_size > MAX_FILE_BYTES:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        scanned += 1
        for pattern in SECRET_PATTERNS:
            if pattern.search(text):
                secrets.append(f"{rel}")
                break
    if scanned == 0:
        checks["secret_scan"] = ("NOT_TESTED", "no scannable files")
    elif secrets:
        checks["secret_scan"] = ("FAIL", None)
        findings.append({"severity": "CRITICAL",
                         "finding": f"secret-like content in: "
                                    f"{secrets[:3]}"})
    else:
        checks["secret_scan"] = ("PASS", hashlib.sha256(
            f"{scanned} files clean".encode()).hexdigest())
    return checks, findings


def main() -> int:
    if len(sys.argv) < 2:
        print(json.dumps({"error": "usage: verifier_probe <tree>"}))
        return 2
    root = Path(sys.argv[-1]).resolve()
    if not root.is_dir():
        print(json.dumps({"error": f"tree {root} is not a directory"}))
        return 2
    raw_criteria = os.environ.get("JARVIS_VERIFIER_CRITERIA", "[]")
    try:
        criteria = json.loads(raw_criteria)
        _require_list = isinstance(criteria, list) and \
            all(isinstance(c, str) for c in criteria)
    except ValueError:
        criteria, _require_list = [], False
    if not _require_list or not criteria:
        print(json.dumps({
            "error": "JARVIS_VERIFIER_CRITERIA must be a JSON array of "
                     "frozen criterion strings"}))
        return 2

    checks, findings = _run_checks(root)

    criteria_report: dict[str, dict[str, Any]] = {}
    for criterion in criteria:
        lowered = criterion.lower()
        matched = None
        for keyword, check in FAMILY_KEYWORDS.items():
            if keyword in lowered:
                matched = check
                break
        if matched is None:
            criteria_report[criterion] = {
                "status": "NOT_TESTED",
                "evidence_hash": None,
                "reason": "no probe check maps to this criterion"}
            continue
        status, evidence = checks[matched]
        entry: dict[str, Any] = {"status": status,
                                 "evidence_hash": evidence}
        if status == "NOT_TESTED":
            entry["reason"] = f"check {matched} could not run"
        criteria_report[criterion] = entry

    print(json.dumps({"criteria": criteria_report,
                      "findings": findings}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

