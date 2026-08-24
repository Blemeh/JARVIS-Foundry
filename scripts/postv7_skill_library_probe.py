"""Run the disposable post-V7 procedure-library factory."""
from __future__ import annotations

import argparse
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from jarvis_foundry.procedure_library import build_catalog, run_library_proof


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, help="owned disposable sandbox root")
    parser.add_argument("--output", type=Path, help="machine-readable report path")
    args = parser.parse_args()
    if args.root is not None:
        report = run_library_proof(build_catalog(), args.root)
        payload = report.as_dict()
        temporary = None
    else:
        with tempfile.TemporaryDirectory(prefix="jarvis-postv7-skills-") as root:
            report = run_library_proof(build_catalog(), root)
            payload = report.as_dict()
            temporary = root
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({
        "passed": report.passed,
        "proven_count": report.proven_count,
        "trusted_count": report.trusted_count,
        "procedure_count": len(report.catalog.definitions),
        "sandbox_root": str(args.root or temporary),
    }, sort_keys=True))
    return 0 if report.passed and report.proven_count >= 15 else 1


if __name__ == "__main__":
    raise SystemExit(main())
