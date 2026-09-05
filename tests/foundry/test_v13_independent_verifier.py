"""Independent proof-verifier contract tests."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from jarvis_foundry.platform.windows_subprocess import headless_popen_kwargs
from jarvis_foundry.supervisor.worker import process_creation_identity


def _verifier():
    path = Path(__file__).resolve().parents[2] / "scripts" / "v13_independent_verify.py"
    spec = importlib.util.spec_from_file_location("v13_independent_verify", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_manifest_is_portable_and_provenance_bound(tmp_path: Path) -> None:
    module = _verifier()
    pack = tmp_path / "pack"
    pack.mkdir()
    payload = pack / "payload.txt"
    payload.write_text("portable\n", encoding="utf-8")
    digest = hashlib.sha256(payload.read_bytes()).hexdigest()
    manifest = pack / "manifest.json"
    manifest.write_text(
        json.dumps({
            "root": ".",
            "checks": [{
                "name": "payload",
                "kind": "file_hash",
                "path": "payload.txt",
                "sha256": digest,
            }],
        }),
        encoding="utf-8",
    )

    result = module.verify_manifest(manifest)
    assert result["verdict"] == "PASS"
    assert result["root"] == "."
    assert result["manifest"] == "manifest.json"
    assert result["manifest_sha256"] == hashlib.sha256(
        manifest.read_bytes()
    ).hexdigest()
    assert result["verifier_sha256"]
    assert result["verified_at"].endswith("Z")

    copied = tmp_path / "copied"
    copied.mkdir()
    (copied / "payload.txt").write_bytes(payload.read_bytes())
    (copied / "manifest.json").write_bytes(manifest.read_bytes())
    assert module.verify_manifest(copied / "manifest.json")["verdict"] == "PASS"

    absolute = json.loads(manifest.read_text(encoding="utf-8"))
    absolute["root"] = str(pack)
    manifest.write_text(json.dumps(absolute), encoding="utf-8")
    with pytest.raises(ValueError, match="portable"):
        module.verify_manifest(manifest)

    absolute["root"] = "."
    absolute["checks"][0]["path"] = str(payload)
    manifest.write_text(json.dumps(absolute), encoding="utf-8")
    result = module.verify_manifest(manifest)
    assert result["verdict"] == "FAIL"
    assert "portable" in result["results"][0]["error"]


@pytest.mark.skipif(os.name != "nt", reason="exact process identity is Windows-only")
def test_process_verifier_tracks_exact_identity_for_live_and_closed(
    tmp_path: Path,
) -> None:
    module = _verifier()
    process = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        cwd=tmp_path,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **headless_popen_kwargs(),
    )
    try:
        identity = process_creation_identity(process.pid)
        assert identity.get("create_time") is not None
        assert identity.get("exe")
        assert module._verify_process({
            "identity": identity,
            "live": True,
        })["pass"] is True
        assert module._verify_process({
            "identity": identity,
            "live": False,
        })["pass"] is False
    finally:
        process.terminate()
        process.wait(timeout=5)

    closed = module._verify_process({
        "identity": identity,
        "live": False,
    })
    assert closed["pass"] is True
    assert closed["same_identity_live"] is False

    with pytest.raises(ValueError, match="requires pid"):
        module._verify_process({
            "identity": {"pid": process.pid},
            "live": False,
        })
