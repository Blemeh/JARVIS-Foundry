"""Run disposable, real V12 adapter-factory proofs without catalog or live mutation."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

from jarvis_foundry.adapter_factory import (
    AdapterFactory,
    AdapterShape,
    CapabilityAdapterContract,
    ContractCase,
    DependencyDeclaration,
    DependencyKind,
    InvocationMapping,
    ProbeKind,
    ReadinessProbe,
    ReconciliationMapping,
    SelectedSolutionDescriptor,
    SolutionClass,
    disable_artifact,
    install_isolated_runtime,
    invoke_artifact,
    invoke_typed_adapter,
    probe_artifact,
    remove_artifact,
    remove_isolated_runtime,
)
from jarvis_foundry.effects import EffectClass
from jarvis_foundry.fabric.model import Semantic


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tree_hash(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in root.rglob("*") if item.is_file()):
        digest.update(path.relative_to(root).as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(path.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _output_schema(field: str, field_schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "required": ["claimed", field, "adapter_id", "capability_id", "action_kind"],
        "properties": {
            "claimed": {"type": "string", "enum": ["COMPLETED"]},
            field: field_schema,
            "adapter_id": {"type": "string"},
            "capability_id": {"type": "string"},
            "action_kind": {"type": "string"},
        },
    }


def _git_descriptor(git: str) -> SelectedSolutionDescriptor:
    version = subprocess.run(
        [git, "--version"], capture_output=True, text=True, encoding="utf-8",
        errors="replace", check=True, timeout=15,
    ).stdout.strip().removeprefix("git version ")
    return SelectedSolutionDescriptor(
        adapter_id="adapter.v12.git-ref-normalizer",
        solution_id="solution.git.cli",
        solution_class=SolutionClass.CLI,
        solution_name="Git CLI",
        solution_version=version,
        locator=git,
        adapter_shape=AdapterShape.CONNECTOR,
        readiness=ReadinessProbe(
            ProbeKind.COMMAND, git, ("--version",),
            {"returncode": 0, "stdout_contains": "git version"},
        ),
        capabilities=(CapabilityAdapterContract(
            capability_id="cap.git.ref.normalize",
            version="1.0.0",
            action_kind="git.ref.normalize.read",
            semantic=Semantic.READ,
            effect_class=EffectClass.READ_ONLY,
            input_schema={
                "type": "object", "required": ["name"],
                "properties": {"name": {"type": "string", "pattern": "^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$"}},
                "additionalProperties": False,
            },
            output_schema=_output_schema("normalized_ref", {"type": "string", "pattern": "^refs/heads/"}),
            invocation=InvocationMapping(
                target=git,
                arguments=("check-ref-format", "--normalize", "refs/heads/${input.name}"),
                output={"normalized_ref": "$raw.stdout"},
            ),
            reconciliation=ReconciliationMapping("repeat_read", ("normalized_ref",)),
            contract_cases=(ContractCase(
                "normalize-selected-ref", {"name": "feature/adapter-factory"},
                {"claimed": "COMPLETED", "normalized_ref": "refs/heads/feature/adapter-factory"},
            ),),
            limitations=("read-only ref syntax normalization",),
        ),),
        dependencies=(DependencyDeclaration("git", DependencyKind.EXECUTABLE, version, git),),
        provenance={"source": "real-local-runtime", "authority": "JARVIS_SYSTEM_METADATA"},
        selection={"selected_by": "Session 6 boundary", "factory_ranked": False},
    )


def _http_descriptor(base_url: str) -> SelectedSolutionDescriptor:
    return SelectedSolutionDescriptor(
        adapter_id="adapter.v12.disposable-capability-api",
        solution_id="solution.disposable.http-api",
        solution_class=SolutionClass.HTTP_API,
        solution_name="Disposable Capability HTTP API",
        solution_version="1.0.0",
        locator=base_url,
        adapter_shape=AdapterShape.CONNECTOR,
        readiness=ReadinessProbe(
            ProbeKind.HTTP, f"{base_url}/health", (),
            {"allowed_origins": [base_url], "status": 200, "json.ready": True},
        ),
        capabilities=(CapabilityAdapterContract(
            capability_id="cap.http.capability.describe",
            version="1.0.0",
            action_kind="http.capability.describe.read",
            semantic=Semantic.READ,
            effect_class=EffectClass.READ_ONLY,
            input_schema={
                "type": "object", "required": ["capability_id"],
                "properties": {"capability_id": {"type": "string", "pattern": "^[a-z][a-z0-9.-]{2,127}$"}},
                "additionalProperties": False,
            },
            output_schema=_output_schema("record", {"type": "object"}),
            invocation=InvocationMapping(
                target=f"{base_url}/v1/capabilities/${{input.capability_id}}",
                method="GET",
                allowed_origins=(base_url,),
                output={"record": "$raw.json", "response_sha256": "$raw.sha256"},
            ),
            reconciliation=ReconciliationMapping("repeat_read", ("record", "response_sha256")),
            contract_cases=(ContractCase(
                "describe-capability", {"capability_id": "adapter.factory"},
                {"claimed": "COMPLETED", "record": {"capability_id": "adapter.factory", "state": "AVAILABLE"}},
            ),),
            limitations=("loopback proof service only", "read-only GET only"),
        ),),
        dependencies=(DependencyDeclaration("capability-api", DependencyKind.SERVICE, "1.0.0", base_url),),
        provenance={"source": "disposable-real-http-process", "authority": "DATA_ONLY"},
        selection={"selected_by": "Session 6 boundary", "factory_ranked": False},
    )


_SERVER = '''\
import json,sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/health":
            value = {"ready": True, "service": "v12-adapter-proof"}
        elif self.path.startswith("/v1/capabilities/"):
            value = {"capability_id": self.path.rsplit("/", 1)[-1], "state": "AVAILABLE", "source": "disposable-http"}
        else:
            self.send_error(404)
            return
        body = json.dumps(value, sort_keys=True).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
    def log_message(self, *_args):
        return

ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler).serve_forever()
'''


def _start_http(root: Path) -> tuple[subprocess.Popen[str], str]:
    script = root / "fixtures" / "capability_api.py"
    script.parent.mkdir(parents=True, exist_ok=True)
    script.write_text(_SERVER, encoding="utf-8")
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    process = subprocess.Popen(
        [sys.executable, str(script), str(port)], cwd=root,
        stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
    )
    url = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if process.poll() is not None:
            stdout, stderr = process.communicate()
            raise RuntimeError(f"HTTP proof backend exited: {stdout}{stderr}")
        try:
            with urllib.request.urlopen(f"{url}/health", timeout=1) as response:
                if response.status == 200:
                    return process, url
        except OSError:
            time.sleep(0.1)
    process.terminate()
    raise RuntimeError("HTTP proof backend did not become ready")


def _stop(process: subprocess.Popen[str]) -> None:
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def _run_one(descriptor: SelectedSolutionDescriptor, root: Path, project_root: Path) -> dict[str, Any]:
    candidate = AdapterFactory(root / "build-artifacts").build(descriptor)
    installed = AdapterFactory(root / "installed-adapters").build(descriptor)
    environment = root / "runtimes" / descriptor.adapter_id
    install = install_isolated_runtime(installed, environment_root=environment, project_root=project_root)
    python = Path(install["python"])
    isolation = subprocess.run(
        [str(python), "-I", "-c", "import jarvis_foundry.adapter_factory as m; print(m.__file__)"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=30, check=False,
    )
    isolated_module = isolation.stdout.strip()
    probe = probe_artifact(installed.root, python_executable=python)
    invocation = invoke_typed_adapter(
        installed.root, descriptor.capabilities[0].capability_id,
        descriptor.capabilities[0].contract_cases[0].payload,
        mission_id=f"proof.{descriptor.adapter_id}", workflow_id="v12.adapter.factory",
        step_id="typed-invocation", python_executable=python,
    )
    test_env = dict(os.environ)
    test_env.pop("PYTHONPATH", None)
    test_env["PYTHONNOUSERSITE"] = "1"
    generated = subprocess.run(
        [str(python), str(installed.root / "tests" / "test_contract.py")],
        cwd=installed.root, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=120, check=False, env=test_env,
    )
    disabled = disable_artifact(installed.root, reason="proof clean disable")
    disabled_probe = probe_artifact(installed.root, python_executable=python)
    disabled_invocation = invoke_artifact(
        installed.root, descriptor.capabilities[0].capability_id,
        descriptor.capabilities[0].contract_cases[0].payload, python_executable=python,
    )
    removed = remove_artifact(installed.root, expected_adapter_id=descriptor.adapter_id)
    runtime_removed = remove_isolated_runtime(environment, expected_python=python)
    checks = {
        "isolated_python_used": (
            isolation.returncode == 0
            and str(environment).lower() in isolated_module.lower()
            and str(python) != sys.executable
            and not environment.exists()
        ),
        "ready": probe.get("ready") is True,
        "typed_invocation_success": invocation.get("success") is True,
        "receipt_verified": invocation.get("receipt", {}).get("state") == "VERIFIED",
        "readback_match": invocation.get("verified") == "MATCH" and invocation.get("reconciled") is True,
        "generated_contract_tests": generated.returncode == 0,
        "disabled_probe": disabled_probe.get("state") == "DISABLED",
        "disabled_invocation_blocked": disabled_invocation.get("error_code") == "adapter_disabled",
        "adapter_removed": removed.get("removed") is True and not installed.root.exists(),
        "runtime_removed": runtime_removed.get("removed") is True,
        "candidate_preserved": candidate.root.is_dir(),
        "catalog_untouched": install.get("catalog_mutated") is False and removed.get("catalog_mutated") is False,
        "live_untouched": install.get("live_mutated") is False,
    }
    return {
        "adapter_id": descriptor.adapter_id,
        "solution_class": descriptor.solution_class.value,
        "artifact_id": candidate.artifact_id,
        "candidate_artifact": str(candidate.root),
        "candidate_tree_sha256": _tree_hash(candidate.root),
        "descriptor_sha256": descriptor.descriptor_hash,
        "isolated_install": install,
        "isolation_probe": {"returncode": isolation.returncode, "module_file": isolated_module},
        "readiness": probe,
        "typed_invocation": invocation,
        "generated_contract_test": {
            "returncode": generated.returncode,
            "stdout": generated.stdout.strip(),
            "stderr": generated.stderr.strip(),
        },
        "disable": disabled,
        "disabled_probe": disabled_probe,
        "disabled_invocation": disabled_invocation,
        "remove": removed,
        "runtime_remove": runtime_removed,
        "checks": checks,
        "passed": all(checks.values()),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    output = args.output.expanduser().resolve()
    project_root = args.project_root.expanduser().resolve()
    if output.exists():
        raise SystemExit(f"refusing to overwrite proof root: {output}")
    output.mkdir(parents=True)
    git = shutil.which("git")
    if not git:
        raise SystemExit("Git CLI is required for the local CLI proof")
    service: subprocess.Popen[str] | None = None
    try:
        service, base_url = _start_http(output)
        adapters = [
            _run_one(_git_descriptor(git), output / "cli", project_root),
            _run_one(_http_descriptor(base_url), output / "http", project_root),
        ]
        head = subprocess.run(
            [git, "rev-parse", "HEAD"], cwd=project_root, capture_output=True,
            text=True, encoding="utf-8", errors="replace", check=True,
        ).stdout.strip()
        proof = {
            "contract_version": "jarvis.v12.adapter-factory-proof.v1",
            "repository": str(project_root),
            "source_sha": head,
            "output_root": str(output),
            "adapters": adapters,
            "catalog_registered": False,
            "live_mutated": False,
            "passed": all(adapter["passed"] for adapter in adapters),
        }
        _write_json(output / "proof.json", proof)
        print(json.dumps({
            "passed": proof["passed"], "proof": str(output / "proof.json"),
            "adapters": [{"adapter_id": item["adapter_id"], "passed": item["passed"]} for item in adapters],
        }, sort_keys=True))
        return 0 if proof["passed"] else 1
    finally:
        if service is not None:
            _stop(service)


if __name__ == "__main__":
    raise SystemExit(main())
