"""R5-G04: recovery-unit restore rehearsal and previous-release readability."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from common import (
    EVIDENCE_DIR,
    REPO_ROOT,
    record_enabled,
    sha256_file,
    write_json,
)


def _secrets_metadata(temp_dir: Path) -> Path:
    manifest = json.loads(
        (REPO_ROOT / "docs/remaster/evidence/r0/R0_BASELINE_MANIFEST.json").read_text(
            encoding="utf-8"
        )
    )
    secrets = manifest["secret_metadata"]
    path = temp_dir / "secrets-metadata.json"
    path.write_text(
        json.dumps(
            {
                "values_copied": secrets["values_copied"],
                "auth_sha256": secrets["auth"]["sha256"],
                "env_sha256": secrets["env"]["sha256"],
                "config_sha256": manifest["provider_configuration"]["config_metadata"]["sha256"],
                "env_key_names": secrets["env"]["key_names"],
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


def _executable_metadata(temp_dir: Path) -> Path:
    manifest = json.loads(
        (REPO_ROOT / "docs/remaster/evidence/r0/R0_BASELINE_MANIFEST.json").read_text(
            encoding="utf-8"
        )
    )
    exe = manifest["live_executable_metadata"]
    import hashlib

    host_python = Path("C:/Python313/python.exe")
    host_hash = hashlib.sha256(host_python.read_bytes()).hexdigest()
    path = temp_dir / "executable-metadata.json"
    path.write_text(
        json.dumps(
            {
                "live_venv_python_sha256": exe["sha256"],
                "live_venv_python_size": exe["size"],
                "host_python_path": str(host_python),
                "host_python_sha256": host_hash,
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


def _executable_binary() -> Path:
    """The live venv python.exe bytes themselves, as a real executable component."""
    manifest = json.loads(
        (REPO_ROOT / "docs/remaster/evidence/r0/R0_BASELINE_MANIFEST.json").read_text(
            encoding="utf-8"
        )
    )
    exe = manifest["live_executable_metadata"]
    path = Path(exe["path"])
    if not path.is_file():
        raise FileNotFoundError(f"live executable missing: {path}")
    return path


def _evidence_snapshot_files() -> list[Path]:
    # Closure files are excluded because the restore point cannot stably bind
    # artifacts that are themselves regenerated after this gate runs. Binding
    # them would make the frozen restore point unreproducible on every closure.
    excluded = {
        "R5_ARTIFACT_MANIFEST.json",
        "R5_BUILDER_REPORT.json",
        "R5_ARTIFACT_CONFINEMENT.json",
        "R5_RESTORE.json",
        "R5_RESTORE_RAW.txt",
    }
    return sorted(
        path
        for path in EVIDENCE_DIR.glob("R5_*.json")
        if path.is_file() and path.name not in excluded
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=EVIDENCE_DIR / "R5_RESTORE.json")
    parser.add_argument("--raw", type=Path, default=EVIDENCE_DIR / "R5_RESTORE_RAW.txt")
    args = parser.parse_args(argv)

    from jarvis_foundry.kernel.compat.restore import (
        ComponentSpec,
        create_restore_point,
        previous_release_readable,
        restore_point,
    )

    started = time.monotonic()
    r0_restore = REPO_ROOT / "var/remaster/r0/restore"
    temp_dir = Path(tempfile.mkdtemp(prefix="jarvis-r5-restore-"))
    specs = [
        ComponentSpec(
            name="live_database",
            kind="database",
            source=r0_restore / "live-foundry-r0.db",
        ),
        ComponentSpec(
            name="live_database_wal",
            kind="wal",
            source=r0_restore / "live-foundry-r0.db-wal",
        ),
        ComponentSpec(
            name="live_database_shm",
            kind="wal",
            source=r0_restore / "live-foundry-r0.db-shm",
        ),
        ComponentSpec(
            name="isolated_v7_database",
            kind="database",
            source=r0_restore / "isolated-v7-r0.db",
        ),
        ComponentSpec(
            name="isolated_v7_database_wal",
            kind="wal",
            source=r0_restore / "isolated-v7-r0.db-wal",
        ),
        ComponentSpec(
            name="isolated_v7_database_shm",
            kind="wal",
            source=r0_restore / "isolated-v7-r0.db-shm",
        ),
        ComponentSpec(
            name="source_bundle",
            kind="bundle",
            source=r0_restore / "jarvis-foundry-93a3ddc.bundle",
        ),
        ComponentSpec(
            name="config",
            kind="config",
            source=r0_restore / "foundry-profile.yaml",
        ),
        ComponentSpec(
            name="task",
            kind="task",
            source=r0_restore / "JARVIS-Foundry-Supervisor.xml",
        ),
        ComponentSpec(
            name="dependency_freeze",
            kind="freeze",
            source=r0_restore / "live-venv-pip-freeze.txt",
        ),
        ComponentSpec(
            name="secrets_metadata",
            kind="secrets_metadata",
            source=_secrets_metadata(temp_dir),
        ),
        ComponentSpec(
            name="executable_metadata",
            kind="evidence",
            source=_executable_metadata(temp_dir),
        ),
        ComponentSpec(
            name="executable",
            kind="executable",
            source=_executable_binary(),
        ),
    ]
    for index, evidence in enumerate(_evidence_snapshot_files()):
        specs.append(
            ComponentSpec(
                name=f"evidence_{index:02d}",
                kind="evidence",
                source=evidence,
            )
        )

    point_dir = temp_dir / "point"
    target_dir = temp_dir / "restored"
    manifest = create_restore_point(specs, point_dir)
    report = restore_point(point_dir, target_dir)
    restored_db = target_dir / "live-foundry-r0.db"
    readability = previous_release_readable(restored_db)

    bundle = target_dir / "jarvis-foundry-93a3ddc.bundle"
    clone_dir = temp_dir / "clone"
    bundle_verify = subprocess.run(
        ["git", "bundle", "verify", str(bundle)],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    clone_result = subprocess.run(
        ["git", "clone", str(bundle), str(clone_dir)],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    task_path = target_dir / "JARVIS-Foundry-Supervisor.xml"
    task_parse = False
    try:
        import defusedxml.ElementTree as ET

        ET.fromstring(task_path.read_bytes().decode("utf-16"))
        task_parse = True
    except (
        UnicodeDecodeError,
        ET.ParseError,
        ValueError,
        OSError,
        ET.DefusedXmlException,
    ):
        task_parse = False
    try:
        import yaml

        yaml.safe_load((target_dir / "foundry-profile.yaml").read_text(encoding="utf-8"))
        config_parse = True
    except (ValueError, OSError, yaml.YAMLError):
        config_parse = False

    restored_evidence = [
        item["name"] for item in report["restored_components"] if item["kind"] == "evidence"
    ]
    full_report = {
        "schema": "jarvis-r5-restore-gate-v1",
        "restore_point_manifest": manifest,
        "restore_report": report,
        "previous_release_readability": readability,
        "bundle_verify_exit": bundle_verify.returncode,
        "bundle_clone_exit": clone_result.returncode,
        "clone_commit": (
            subprocess.run(
                ["git", "-C", str(clone_dir), "rev-parse", "HEAD"],
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            ).stdout.strip()
            if clone_dir.is_dir()
            else ""
        ),
        "task_xml_utf16_parses": task_parse,
        "config_yaml_parses": config_parse,
        "restored_evidence_components": restored_evidence,
        "passed": False,
        "wall_seconds": round(time.monotonic() - started, 3),
        "exit_code": 0,
    }
    full_report["passed"] = bool(
        report["restore_verified"]
        and readability["readable"]
        and bundle_verify.returncode == 0
        and clone_result.returncode == 0
        and task_parse
        and config_parse
    )
    lines = [
        f"restore_verified={report['restore_verified']}",
        f"previous_release_readable={readability['readable']} events={readability['event_count']}",
        f"bundle_verify_exit={bundle_verify.returncode} clone_exit={clone_result.returncode}",
        f"task_utf16_parses={task_parse} config_yaml_parses={config_parse}",
        f"restored_evidence_components={len(restored_evidence)}",
    ]
    raw_text = "\n".join(lines) + "\n"
    if record_enabled():
        args.raw.write_text(raw_text, encoding="utf-8")
        full_report["raw_output"] = args.raw.name
        full_report["raw_output_sha256"] = sha256_file(args.raw)
        write_json(args.output, full_report)
    shutil.rmtree(temp_dir, ignore_errors=True)
    print(raw_text)
    return 0 if full_report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
