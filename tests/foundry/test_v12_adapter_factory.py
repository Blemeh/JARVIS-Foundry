"""V12 adapter factory contract, runtime, and lifecycle tests."""
from __future__ import annotations

import hashlib
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from jarvis_foundry.adapter_factory import (
    AdapterFactory,
    AdapterFactoryError,
    AdapterShape,
    CapabilityAdapterContract,
    ContractCase,
    DependencyDeclaration,
    DependencyKind,
    GeneratedWorkerAdapter,
    InvocationMapping,
    ProbeKind,
    ReadinessProbe,
    ReconciliationMapping,
    SelectedSolutionDescriptor,
    SolutionClass,
    disable_artifact,
    enable_artifact,
    invoke_artifact,
    invoke_typed_adapter,
    load_verified_artifact,
    probe_artifact,
    remove_artifact,
    validate_schema,
)
from jarvis_foundry.effects import EffectClass
from jarvis_foundry.fabric.model import Semantic, UnattendedMode
from jarvis_foundry.workers.adapters.base import ProviderUnavailable
from jarvis_foundry.workers.contracts import WorkerStepContract


OBJECT_SCHEMA = {
    "type": "object",
    "required": ["text"],
    "properties": {"text": {"type": "string", "minLength": 1, "maxLength": 100}},
    "additionalProperties": False,
}


def _output_schema(field: str, field_schema: dict | None = None) -> dict:
    return {
        "type": "object",
        "required": ["claimed", field, "adapter_id", "capability_id", "action_kind"],
        "properties": {
            "claimed": {"type": "string", "enum": ["COMPLETED"]},
            field: field_schema or {"type": "string"},
            "adapter_id": {"type": "string"},
            "capability_id": {"type": "string"},
            "action_kind": {"type": "string"},
        },
    }


def _cli_script(tmp_path: Path) -> Path:
    script = tmp_path / "echo_cli.py"
    script.write_text(
        "import json,sys\n"
        "print(json.dumps({'echo': sys.argv[1], 'length': len(sys.argv[1])}, sort_keys=True))\n",
        encoding="utf-8",
    )
    return script


def _cli_descriptor(tmp_path: Path, *, shape: AdapterShape = AdapterShape.CONNECTOR) -> SelectedSolutionDescriptor:
    script = _cli_script(tmp_path)
    return SelectedSolutionDescriptor(
        adapter_id="adapter.local.echo",
        solution_id="solution.local.python-cli",
        solution_class=SolutionClass.CLI,
        solution_name="Python fixture CLI",
        solution_version=sys.version.split()[0],
        locator=str(script),
        adapter_shape=shape,
        readiness=ReadinessProbe(
            ProbeKind.COMMAND, sys.executable, ("--version",),
            {"returncode": 0},
        ),
        capabilities=(
            CapabilityAdapterContract(
                capability_id="cap.local.echo",
                version="1.0.0",
                action_kind="local.echo.read",
                semantic=Semantic.READ,
                effect_class=EffectClass.READ_ONLY,
                input_schema=OBJECT_SCHEMA,
                output_schema=_output_schema("echo"),
                invocation=InvocationMapping(
                    target=sys.executable,
                    arguments=(str(script), "$input.text"),
                    output={"echo": "$raw.json.echo", "length": "$raw.json.length"},
                ),
                reconciliation=ReconciliationMapping("repeat_read", ("echo", "length")),
                contract_cases=(ContractCase("echo", {"text": "jarvis"}, {"claimed": "COMPLETED", "echo": "jarvis", "length": 6}),),
            ),
        ),
        dependencies=(DependencyDeclaration("python", DependencyKind.EXECUTABLE, sys.version.split()[0], sys.executable),),
        provenance={"source": "test", "authority": "JARVIS_SYSTEM_METADATA"},
    )


class _ApiHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 - stdlib server contract
        if self.path == "/health":
            payload = {"ready": True, "service": "adapter-proof"}
        else:
            name = self.path.rsplit("/", 1)[-1]
            payload = {"id": name, "kind": "capability", "state": "available"}
        data = json.dumps(payload, sort_keys=True).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args) -> None:
        return


@pytest.fixture
def api_server():
    server = ThreadingHTTPServer(("127.0.0.1", 0), _ApiHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _http_descriptor(base_url: str) -> SelectedSolutionDescriptor:
    return SelectedSolutionDescriptor(
        adapter_id="adapter.http.capability-read",
        solution_id="solution.disposable.capability-api",
        solution_class=SolutionClass.HTTP_API,
        solution_name="Disposable capability HTTP API",
        solution_version="1.0.0",
        locator=base_url,
        adapter_shape=AdapterShape.CONNECTOR,
        readiness=ReadinessProbe(
            ProbeKind.HTTP, f"{base_url}/health", (),
            {"allowed_origins": [base_url], "status": 200, "json.ready": True},
        ),
        capabilities=(
            CapabilityAdapterContract(
                capability_id="cap.api.capability.read",
                version="1.0.0",
                action_kind="api.capability.read",
                semantic=Semantic.READ,
                effect_class=EffectClass.READ_ONLY,
                input_schema={
                    "type": "object", "required": ["name"],
                    "properties": {"name": {"type": "string", "pattern": "^[a-z-]+$"}},
                    "additionalProperties": False,
                },
                output_schema=_output_schema("record", {"type": "object"}),
                invocation=InvocationMapping(
                    target=f"{base_url}/capabilities/${{input.name}}",
                    method="GET",
                    allowed_origins=(base_url,),
                    output={"record": "$raw.json", "response_sha256": "$raw.sha256"},
                ),
                reconciliation=ReconciliationMapping("repeat_read", ("record", "response_sha256")),
                contract_cases=(ContractCase(
                    "read-capability", {"name": "adapter-factory"},
                    {"claimed": "COMPLETED", "record": {"id": "adapter-factory", "kind": "capability", "state": "available"}},
                ),),
            ),
        ),
        dependencies=(DependencyDeclaration("capability-api", DependencyKind.SERVICE, "1.0.0", base_url),),
    )


def test_cli_build_probe_typed_receipt_disable_enable_and_remove(tmp_path: Path) -> None:
    descriptor = _cli_descriptor(tmp_path)
    artifact = AdapterFactory(tmp_path / "artifacts").build(descriptor)
    replay = AdapterFactory(tmp_path / "artifacts").build(descriptor)
    assert replay.artifact_id == artifact.artifact_id
    root, manifest, loaded = load_verified_artifact(artifact.root)
    assert loaded.descriptor_hash == descriptor.descriptor_hash
    assert manifest["registered"] is False
    assert json.loads((root / "fabric-capabilities.json").read_text())["catalog_mutated"] is False
    admission = json.loads((root / "trial-admission.json").read_text())
    assert admission["registration_owner"] == "SESSION_7"
    assert admission["registered"] is False
    assert json.loads((root / "connector-spec.json").read_text())["capabilities"] == {"cap.local.echo": "DISCOVERED"}
    assert not list(root.rglob("__pycache__"))
    assert probe_artifact(root)["ready"] is True

    invoked = invoke_typed_adapter(
        root, "cap.local.echo", {"text": "hello"},
        mission_id="mission.adapter", workflow_id="workflow.adapter", step_id="echo",
    )
    assert invoked["success"] is True, invoked
    assert invoked["verified"] == "MATCH"
    assert invoked["receipt"]["state"] == "VERIFIED"
    assert invoked["receipt"]["observed"]["echo"] == "hello"
    assert invoked["reconciled"] is True

    disabled = disable_artifact(root, reason="test disable")
    assert disabled["disabled"] is True
    assert probe_artifact(root)["state"] == "DISABLED"
    assert invoke_artifact(root, "cap.local.echo", {"text": "blocked"})["claimed"] == "BLOCKED"
    assert enable_artifact(root)["enabled"] is True
    assert probe_artifact(root)["ready"] is True
    removed = remove_artifact(root, expected_adapter_id=descriptor.adapter_id)
    assert removed["removed"] is True
    assert not root.exists()


def test_real_http_process_build_probe_invoke_readback_and_generated_tests(tmp_path: Path, api_server: str) -> None:
    descriptor = _http_descriptor(api_server)
    artifact = AdapterFactory(tmp_path / "artifacts").build(descriptor)
    assert probe_artifact(artifact.root)["ready"] is True
    invoked = invoke_typed_adapter(
        artifact.root, "cap.api.capability.read", {"name": "adapter-factory"},
        mission_id="mission.api", workflow_id="workflow.api", step_id="read",
    )
    assert invoked["success"] is True, invoked
    assert invoked["result"]["record"]["state"] == "available"
    generated = __import__("subprocess").run(
        [sys.executable, str(artifact.root / "tests" / "test_contract.py")],
        cwd=artifact.root, capture_output=True, text=True, timeout=60,
    )
    assert generated.returncode == 0, generated.stdout + generated.stderr


def test_python_sdk_and_existing_jarvis_component_shapes(tmp_path: Path) -> None:
    python_descriptor = SelectedSolutionDescriptor(
        adapter_id="adapter.python.json",
        solution_id="solution.python.json",
        solution_class=SolutionClass.PYTHON_SDK,
        solution_name="Python JSON SDK",
        solution_version=sys.version.split()[0],
        locator="json",
        adapter_shape=AdapterShape.CONNECTOR,
        readiness=ReadinessProbe(ProbeKind.PYTHON_IMPORT, "json", (), {}),
        capabilities=(CapabilityAdapterContract(
            capability_id="cap.json.parse", version="1.0.0", action_kind="json.parse",
            semantic=Semantic.READ, effect_class=EffectClass.READ_ONLY,
            input_schema=OBJECT_SCHEMA, output_schema=_output_schema("data", {"type": "object"}),
            invocation=InvocationMapping(target="json:loads", arguments=("$input.text",), output={"data": "$raw.value"}),
            contract_cases=(ContractCase("parse", {"text": "{\"ok\": true}"}, {"data": {"ok": True}}),),
        ),),
        dependencies=(DependencyDeclaration("json", DependencyKind.PYTHON, "stdlib", "python-runtime", import_name="json"),),
    )
    built = AdapterFactory(tmp_path / "python").build(python_descriptor)
    result = invoke_artifact(built.root, "cap.json.parse", {"text": "{\"answer\": 42}"})
    assert result.get("data") == {"answer": 42}, result

    jarvis_descriptor = SelectedSolutionDescriptor(
        adapter_id="adapter.jarvis.canonical-json",
        solution_id="solution.jarvis.events",
        solution_class=SolutionClass.JARVIS_COMPONENT,
        solution_name="JARVIS canonical JSON",
        solution_version="1.0.0",
        locator="jarvis_foundry.events",
        adapter_shape=AdapterShape.CONNECTOR,
        readiness=ReadinessProbe(ProbeKind.JARVIS_IMPORT, "jarvis_foundry.events", (), {}),
        capabilities=(CapabilityAdapterContract(
            capability_id="cap.jarvis.canonical-json", version="1.0.0", action_kind="jarvis.json.canonicalize",
            semantic=Semantic.READ, effect_class=EffectClass.READ_ONLY,
            input_schema={"type": "object", "required": ["value"], "properties": {"value": {"type": "object"}}},
            output_schema=_output_schema("text"),
            invocation=InvocationMapping(target="jarvis_foundry.events:canonical_json", arguments=("$input.value",), output={"text": "$raw.value"}),
            contract_cases=(ContractCase("canonical", {"value": {"b": 2, "a": 1}}, {"text": "{\"a\":1,\"b\":2}"}),),
        ),),
        dependencies=(DependencyDeclaration("jarvis-foundry", DependencyKind.JARVIS, "0.1.0", "candidate-wheel"),),
    )
    jarvis = AdapterFactory(tmp_path / "jarvis").build(jarvis_descriptor)
    assert invoke_artifact(jarvis.root, "cap.jarvis.canonical-json", {"value": {"z": 1}})["text"] == "{\"z\":1}"


def test_mcp_stdio_surface_is_probed_and_called(tmp_path: Path) -> None:
    server = tmp_path / "mcp_server.py"
    server.write_text(
        "import json,sys\n"
        "for line in sys.stdin:\n"
        " r=json.loads(line)\n"
        " if r.get('id')==1: print(json.dumps({'jsonrpc':'2.0','id':1,'result':{'protocolVersion':'2025-06-18','capabilities':{},'serverInfo':{'name':'fixture','version':'1'}}}),flush=True)\n"
        " elif r.get('method')=='tools/list': print(json.dumps({'jsonrpc':'2.0','id':2,'result':{'tools':[{'name':'echo','inputSchema':{'type':'object'}}]}}),flush=True)\n"
        " elif r.get('method')=='tools/call': print(json.dumps({'jsonrpc':'2.0','id':2,'result':{'content':[{'type':'text','text':r['params']['arguments']['text']}]}}),flush=True)\n",
        encoding="utf-8",
    )
    descriptor = SelectedSolutionDescriptor(
        adapter_id="adapter.mcp.echo", solution_id="solution.mcp.fixture",
        solution_class=SolutionClass.MCP_STDIO, solution_name="MCP echo backend",
        solution_version="1.0.0", locator=str(server), adapter_shape=AdapterShape.CONNECTOR,
        readiness=ReadinessProbe(ProbeKind.MCP_TOOLS_LIST, sys.executable, (str(server),), {"tool": "echo"}),
        capabilities=(CapabilityAdapterContract(
            capability_id="cap.mcp.echo", version="1.0.0", action_kind="mcp.echo.read",
            semantic=Semantic.READ, effect_class=EffectClass.READ_ONLY,
            input_schema=OBJECT_SCHEMA, output_schema=_output_schema("content", {"type": "array"}),
            invocation=InvocationMapping(target="echo", keyword_arguments={"text": "$input.text"}, output={"content": "$raw.result.content"}),
            contract_cases=(ContractCase("mcp-echo", {"text": "hello"}, {"content": [{"type": "text", "text": "hello"}]}),),
        ),),
        dependencies=(DependencyDeclaration("mcp-fixture", DependencyKind.EXECUTABLE, "1.0.0", str(server)),),
    )
    artifact = AdapterFactory(tmp_path / "mcp").build(descriptor)
    assert probe_artifact(artifact.root)["ready"] is True
    result = invoke_artifact(artifact.root, "cap.mcp.echo", {"text": "hello"})
    assert result["content"] == [{"type": "text", "text": "hello"}]


def test_worker_shape_uses_worker_contract_but_keeps_independent_verification(tmp_path: Path) -> None:
    descriptor = _cli_descriptor(tmp_path, shape=AdapterShape.WORKER)
    artifact = AdapterFactory(tmp_path / "worker").build(descriptor)
    adapter = GeneratedWorkerAdapter(artifact.root, capability_id="cap.local.echo", enabled=True)
    contract = WorkerStepContract(
        run_id="run-1", mission_id="mission-1", step_id="step-1", attempt=1,
        role="builder", objective="echo", instructions="bounded echo",
        working_root=tmp_path, action_envelope={"payload": {"text": "worker"}},
    )
    envelope = adapter.dispatch(contract, heartbeat=lambda: None)
    assert envelope.worker_status == "COMPLETED"
    assert envelope.verdict == "NOT_TESTED"
    assert any(item.startswith("adapter_receipt:") for item in envelope.worker_results)
    with pytest.raises(ProviderUnavailable):
        GeneratedWorkerAdapter(artifact.root, capability_id="cap.local.echo").dispatch(contract, heartbeat=lambda: None)


def test_schema_tamper_mutating_authority_and_removal_guards(tmp_path: Path) -> None:
    descriptor = _cli_descriptor(tmp_path)
    artifact = AdapterFactory(tmp_path / "artifacts").build(descriptor)
    assert validate_schema({}, OBJECT_SCHEMA) == ("$.text: required field is missing",)
    bad = invoke_artifact(artifact.root, "cap.local.echo", {"unexpected": True})
    assert bad["error_code"] == "input_schema_mismatch"
    with pytest.raises(AdapterFactoryError):
        remove_artifact(artifact.root, expected_adapter_id="adapter.wrong")
    descriptor_path = artifact.root / "descriptor.json"
    descriptor_path.write_text(descriptor_path.read_text(encoding="utf-8") + " ", encoding="utf-8")
    with pytest.raises(AdapterFactoryError):
        load_verified_artifact(artifact.root)

    mutating = CapabilityAdapterContract(
        capability_id="cap.external.write", version="1.0.0", action_kind="external.write",
        semantic=Semantic.EXTERNAL_EFFECT, effect_class=EffectClass.REVERSIBLE_EXTERNAL,
        input_schema=OBJECT_SCHEMA, output_schema=_output_schema("echo"),
        invocation=descriptor.capabilities[0].invocation,
        reconciliation=ReconciliationMapping("provider_query", ("echo",), descriptor.capabilities[0].invocation),
        unattended=UnattendedMode.INTERACTIVE_REQUIRED,
        contract_cases=(ContractCase("blocked", {"text": "never"}, {"claimed": "BLOCKED"}),),
    )
    external_descriptor = SelectedSolutionDescriptor(
        adapter_id="adapter.external.write", solution_id="solution.external.fixture",
        solution_class=SolutionClass.CLI, solution_name="External write fixture", solution_version="1.0.0",
        locator=descriptor.locator, adapter_shape=AdapterShape.CONNECTOR,
        readiness=descriptor.readiness, capabilities=(mutating,), dependencies=descriptor.dependencies,
    )
    external = AdapterFactory(tmp_path / "external").build(external_descriptor)
    refused = invoke_artifact(external.root, "cap.external.write", {"text": "never"})
    assert refused["claimed"] == "BLOCKED"
    assert refused["error_code"] == "canonical_effect_authority_required"
    assert json.loads((external.root / "connector-spec.json").read_text())["idempotency_strategy"] == "provider-key"


def test_session_6_solution_kind_alias_is_composed_without_selecting_a_solution(tmp_path: Path) -> None:
    descriptor = _cli_descriptor(tmp_path)
    raw = descriptor.as_dict()
    raw.pop("solution_class")
    raw["solution_kind"] = "sdk"
    raw["candidate_id"] = raw.pop("solution_id")
    loaded = SelectedSolutionDescriptor.from_mapping(raw)
    assert loaded.solution_class is SolutionClass.PYTHON_SDK
    assert loaded.solution_id == descriptor.solution_id
