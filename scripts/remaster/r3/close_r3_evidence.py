"""Atomic R3 evidence closure.

Regenerates the builder report and artifact manifest so they bind the FINAL
on-disk state of every evidence artifact, then re-runs artifact confinement
and verifies the whole package is self-consistent (manifest 100% matches,
confinement passed=true, no hidden files). This closes the generation-order
race where a final confinement re-run rewrote its own evidence file after the
manifest captured a stale hash.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
VENV_PY = Path(r"C:\Users\b\AppData\Local\Temp\jarvis-r3-venv\Scripts\python.exe")
FREEZE_TIMESTAMP = "2026-08-15T00:50:00+00:00"


def run(cmd: list[str], env: dict[str, str] | None = None) -> tuple[int, str]:
    completed = subprocess.run(
        [str(VENV_PY), "-B", *cmd],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=1200,
        check=False,
        env=env,
    )
    return completed.returncode, (completed.stdout + completed.stderr).strip()


def main() -> int:
    env = dict(os.environ)
    env["R3_RECORD_EVIDENCE"] = "1"
    env["R3_FREEZE_TIMESTAMP"] = FREEZE_TIMESTAMP

    # Step 1: regenerate report + manifest (they bind current on-disk state).
    rc, out = run(["scripts/remaster/r3/build_evidence.py"], env=env)
    print("build_evidence", rc, out[-400:])
    if rc != 0:
        return 1

    # Step 2: run artifact confinement in record mode ONLY if the confinement
    # evidence does not exist. Once frozen, it is never rewritten so the
    # closure is byte-identical across reruns.
    confinement_path = ROOT / "docs/remaster/evidence/r3/R3_ARTIFACT_CONFINEMENT.json"
    if confinement_path.is_file():
        print("artifact_confinement (immutable, skipped)")
    else:
        rc, out = run(["scripts/remaster/r3/artifact_confinement.py"], env=env)
        print("artifact_confinement", rc, out[-400:])

    # Step 4: verify final consistency without writing (R3_RECORD_EVIDENCE off).
    del env["R3_RECORD_EVIDENCE"]
    completed = subprocess.run(
        [str(VENV_PY), "-B", "scripts/remaster/r3/artifact_confinement.py"],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
        timeout=1200,
        check=False,
        env=env,
    )
    print("final check", completed.returncode, (completed.stdout + completed.stderr).strip()[-400:])

    # Step 5: verify the manifest binds every artifact (including confinement).
    manifest = json.loads(
        (ROOT / "docs/remaster/evidence/r3/R3_ARTIFACT_MANIFEST.json").read_text(encoding="utf-8")
    )
    bad: list[str] = []
    for artifact in manifest["artifacts"]:
        path = ROOT / artifact["path"]
        if artifact.get("sha256") == "SELF_REFERENTIAL_REPORT":
            continue
        if not path.is_file():
            bad.append(f"MISSING {artifact['path']}")
            continue
        digest = __import__("hashlib").sha256(path.read_bytes()).hexdigest()
        if digest != artifact["sha256"] or path.stat().st_size != artifact["size"]:
            bad.append(f"HASH {artifact['path']}")
    print(f"manifest entries={len(manifest['artifacts'])} bad={len(bad)}")
    for item in bad[:10]:
        print(" ", item)
    return 1 if (bad or completed.returncode != 0) else 0


if __name__ == "__main__":
    raise SystemExit(main())
