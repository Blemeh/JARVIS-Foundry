"""Interactive ``jarvis setup`` for machine-local provider configuration."""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from ..runtime.provider_config import (
    ProviderConfigError,
    ProviderCredentialError,
    ProviderSettings,
    default_provider_config_path,
    hermes_provider_env,
    load_provider_settings,
)
from ..runtime.provider_registry import (
    OPENCODE_FAMILY_ID,
    ProviderService,
    UnsupportedProviderError,
    get_service,
    list_families,
    list_services,
    live_test_allowed,
    require_service,
)
from .hermes_probe import (
    HermesProbeRuntime,
    resolve_hermes_probe_runtime,
    run_hermes_live_probe,
)
from .masked_input import read_masked_api_key

_SECRET_TOKEN = re.compile(r"sk-[A-Za-z0-9]{16,}")
_REDACTED = "<REDACTED>"
_PENDING_APPLY_FILENAME = "provider.apply_pending"
_SKIP_REASON_BILLING = "BILLING PROTECTION"


@dataclass(frozen=True, slots=True)
class SetupView:
    family_display: str
    service_display: str
    provider_id: str
    model: str
    base_url: str
    api_key_configured: bool


@dataclass(frozen=True, slots=True)
class ValidationReport:
    configuration: str
    live_free_test: str
    live_inference: str
    skip_reason: str | None = None
    detail: str | None = None

    @property
    def can_save(self) -> bool:
        if self.configuration != "PASS":
            return False
        if self.live_free_test == "FAIL":
            return False
        if self.live_inference == "FAIL":
            return False
        return True

    @property
    def exit_code(self) -> int:
        return 0 if self.can_save or (
            self.configuration == "PASS"
            and self.live_inference == "SKIPPED"
        ) else 1


def _sanitize_error_text(text: str) -> str:
    if not text:
        return text
    return _SECRET_TOKEN.sub(_REDACTED, text)


def _pending_apply_path(config_path: Path) -> Path:
    return config_path.parent / _PENDING_APPLY_FILENAME


def mark_pending_apply(config_path: Path | None = None) -> Path:
    target = _pending_apply_path(
        config_path or default_provider_config_path())
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("pending\n", encoding="utf-8")
    return target


def is_pending_apply(config_path: Path | None = None) -> bool:
    return _pending_apply_path(
        config_path or default_provider_config_path()).is_file()


def build_setup_view(settings: ProviderSettings) -> SetupView:
    service = get_service(settings.provider)
    if service is None:
        family_display = "Unknown"
        service_display = settings.provider
    else:
        family = next(
            (item for item in list_families() if item.family_id == service.family_id),
            None,
        )
        family_display = family.display_name if family else service.family_id
        service_display = service.display_name
    return SetupView(
        family_display=family_display,
        service_display=service_display,
        provider_id=settings.provider,
        model=settings.model,
        base_url=settings.base_url,
        api_key_configured=bool(settings.api_key),
    )


def format_setup_show(settings: ProviderSettings) -> str:
    view = build_setup_view(settings)
    lines = [
        "JARVIS SETUP",
        "Current:",
        f"  Provider family: {view.family_display}",
        f"  Service: {view.service_display}",
        f"  Provider ID: {view.provider_id}",
        f"  Model: {view.model}",
        f"  Base URL: {view.base_url}",
        f"  API key configured: {'YES' if view.api_key_configured else 'NO'}",
    ]
    if is_pending_apply():
        lines.append("  Apply status: PENDING_SAFE_RESTART")
    return "\n".join(lines)


def _models_url(base_url: str) -> str:
    return f"{base_url.rstrip('/')}/models"


def _authorized_request(
    url: str,
    *,
    api_key: str,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> tuple[int, str]:
    data = None
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            return response.status, body
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        return error.code, body


def discover_models(
    *,
    base_url: str,
    api_key: str,
    fetch: Callable[..., tuple[int, str]] | None = None,
) -> list[str]:
    """Optional convenience: fetch model ids from ``/models`` when available."""
    fetcher = fetch or _authorized_request
    status, body = fetcher(_models_url(base_url), api_key=api_key)
    if status < 200 or status >= 300:
        raise ProviderConfigError(
            _sanitize_error_text(
                f"model discovery failed with HTTP {status}"))
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as error:
        raise ProviderConfigError("model discovery returned invalid JSON") from error
    data = payload.get("data")
    if not isinstance(data, list):
        raise ProviderConfigError("model discovery response missing data list")
    models: list[str] = []
    for item in data:
        if isinstance(item, dict):
            model_id = item.get("id")
            if isinstance(model_id, str) and model_id.strip():
                models.append(model_id.strip())
    if not models:
        raise ProviderConfigError("model discovery returned no models")
    return models


def try_discover_models(
    *,
    base_url: str,
    api_key: str,
    fetch: Callable[..., tuple[int, str]] | None = None,
) -> list[str] | None:
    try:
        return discover_models(base_url=base_url, api_key=api_key, fetch=fetch)
    except ProviderConfigError:
        return None


def validate_configuration_structure(settings: ProviderSettings) -> tuple[bool, str]:
    try:
        require_service(settings.provider)
    except UnsupportedProviderError as error:
        return False, str(error)
    if not settings.api_key:
        return False, "API key is not configured"
    if not settings.model.strip():
        return False, "model is required"
    if not settings.base_url.strip().startswith("https://"):
        return False, "base URL must be an https URL"
    try:
        hermes_provider_env(settings)
    except (ProviderConfigError, ProviderCredentialError) as error:
        return False, str(error)
    return True, ""


def evaluate_provider_configuration(
    settings: ProviderSettings,
    *,
    fetch: Callable[..., tuple[int, str]] | None = None,
    probe_runtime: HermesProbeRuntime | None = None,
    run_probe: Callable[..., Any] | None = None,
) -> ValidationReport:
    ok, reason = validate_configuration_structure(settings)
    if not ok:
        return ValidationReport(
            configuration="FAIL",
            live_free_test="SKIPPED",
            live_inference="SKIPPED",
            detail=_sanitize_error_text(reason),
        )

    if not live_test_allowed(settings.provider, settings.model):
        return ValidationReport(
            configuration="PASS",
            live_free_test="SKIPPED",
            live_inference="SKIPPED",
            skip_reason=_SKIP_REASON_BILLING,
        )

    runtime = probe_runtime or resolve_hermes_probe_runtime()
    if runtime is None:
        return ValidationReport(
            configuration="PASS",
            live_free_test="FAIL",
            live_inference="FAIL",
            detail="Hermes probe runtime is not configured",
        )

    probe = run_probe or run_hermes_live_probe
    result = probe(settings, runtime)
    if result.success:
        return ValidationReport(
            configuration="PASS",
            live_free_test="PASS",
            live_inference="PASS",
        )
    return ValidationReport(
        configuration="PASS",
        live_free_test="FAIL",
        live_inference="FAIL",
        detail=_sanitize_error_text(result.error or "Hermes probe failed"),
    )


def format_validation_report(
    report: ValidationReport,
    *,
    provider_id: str,
    model: str,
) -> str:
    lines = [
        f"Provider: {provider_id}",
        f"Model: {model}",
        f"Configuration validation: {report.configuration}",
    ]
    if report.live_free_test == "PASS":
        lines.append("Live free test: PASS")
    elif report.live_inference == "SKIPPED" and report.skip_reason:
        lines.append("Live inference: SKIPPED")
        lines.append(f"Reason: {report.skip_reason}")
    else:
        lines.append(f"Live free test: {report.live_free_test}")
        if report.detail:
            lines.append(f"Detail: {report.detail}")
    return "\n".join(lines)


def format_test_report(report: ValidationReport) -> str:
    """Backward-compatible alias for ``jarvis setup --test`` output."""
    if report.skip_reason:
        return "\n".join([
            f"Configuration validation: {report.configuration}",
            "Selected-model live test: SKIPPED",
            f"Reason: {report.skip_reason}",
        ])
    if report.live_free_test == "PASS":
        return "\n".join([
            f"Configuration validation: {report.configuration}",
            "Selected-model live test: PASS",
        ])
    lines = [f"Configuration validation: {report.configuration}"]
    if report.live_free_test != "SKIPPED":
        lines.append(f"Selected-model live test: {report.live_free_test}")
    if report.detail:
        lines.append(f"Detail: {report.detail}")
    return "\n".join(lines)


def _serialize_provider_yaml(fields: dict[str, str]) -> str:
    lines = [
        "# Machine-local JARVIS provider configuration (never commit).",
        f"provider: {fields['provider']}",
        f"model: {fields['model']}",
        f"base_url: {fields['base_url']}",
        f"api_key: {fields['api_key']}",
    ]
    return "\n".join(lines) + "\n"


def save_provider_config_atomic(
    *,
    config_path: Path,
    provider: str,
    model: str,
    base_url: str,
    api_key: str,
) -> None:
    config_path.parent.mkdir(parents=True, exist_ok=True)
    content = _serialize_provider_yaml({
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "api_key": api_key,
    })
    fd, temp_name = tempfile.mkstemp(
        prefix=".provider-", suffix=".yaml", dir=config_path.parent)
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, config_path)
    finally:
        if temp_path.exists():
            temp_path.unlink(missing_ok=True)


def _read_existing_api_key(config_path: Path) -> str | None:
    if not config_path.is_file():
        return None
    try:
        settings = load_provider_settings(config_path=config_path)
    except ProviderConfigError:
        return None
    return settings.api_key


def _prompt_yes_no(
    prompt: str,
    *,
    default_yes: bool = True,
    input_func: Callable[[str], str] = input,
) -> bool:
    suffix = "[Y/n]" if default_yes else "[y/N]"
    while True:
        answer = input_func(f"{prompt} {suffix} ").strip().lower()
        if not answer:
            return default_yes
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("Please answer y or n.")


def _prompt_choice(
    prompt: str,
    options: list[str],
    *,
    input_func: Callable[[str], str] = input,
) -> int:
    for index, label in enumerate(options, start=1):
        print(f"  {index}. {label}")
    while True:
        raw = input_func(f"{prompt} ").strip()
        if not raw.isdigit():
            print("Enter a number from the list.")
            continue
        choice = int(raw)
        if 1 <= choice <= len(options):
            return choice - 1
        print("Enter a number from the list.")


def _prompt_api_key(
    read_secret: Callable[[str], str] = read_masked_api_key,
) -> str:
    while True:
        value = read_secret("API key: ").strip()
        if value:
            return value
        print("API key is required.")


def _select_service(*, input_func: Callable[[str], str] = input) -> ProviderService:
    families = list_families()
    if not families:
        raise ProviderConfigError("no provider families are registered")
    family_labels = [item.display_name for item in families]
    family_index = _prompt_choice(
        "Select provider family:", family_labels, input_func=input_func)
    family_id = families[family_index].family_id
    services = list(list_services(family_id=family_id))
    if not services:
        raise ProviderConfigError(f"no services for family {family_id}")
    service_labels = [item.display_name for item in services]
    service_index = _prompt_choice(
        "Select OpenCode service:", service_labels, input_func=input_func)
    return services[service_index]


def _select_model(
    *,
    base_url: str,
    api_key: str,
    fetch: Callable[..., tuple[int, str]] | None = None,
    input_func: Callable[[str], str] = input,
) -> str:
    models = try_discover_models(base_url=base_url, api_key=api_key, fetch=fetch)
    if models is None:
        print("Model discovery unavailable.")
        manual = input_func("Enter model id manually: ").strip()
        if not manual:
            raise ProviderConfigError("model selection is required")
        return manual
    print("Available models:")
    for index, model_id in enumerate(models, start=1):
        print(f"  {index}. {model_id}")
    while True:
        raw = input_func("Select default model (number or model id): ").strip()
        if raw.isdigit():
            choice = int(raw)
            if 1 <= choice <= len(models):
                return models[choice - 1]
        elif raw in models:
            return raw
        manual = raw
        if manual:
            return manual
        print("Enter a listed number or exact model id.")


def run_interactive_setup(
    *,
    config_path: Path | None = None,
    input_func: Callable[[str], str] = input,
    read_secret: Callable[[str], str] = read_masked_api_key,
    fetch: Callable[..., tuple[int, str]] | None = None,
    probe_runtime: HermesProbeRuntime | None = None,
    run_probe: Callable[..., Any] | None = None,
) -> int:
    path = config_path or default_provider_config_path()
    current = load_provider_settings(config_path=path)
    print(format_setup_show(current))
    print()

    service = _select_service(input_func=input_func)
    base_url = service.default_base_url
    existing_key = _read_existing_api_key(path)
    api_key = existing_key
    if existing_key and service.family_id == OPENCODE_FAMILY_ID:
        print("Existing OpenCode credential found.")
        if not _prompt_yes_no("Reuse it?", default_yes=True, input_func=input_func):
            api_key = _prompt_api_key(read_secret)
    else:
        api_key = _prompt_api_key(read_secret)

    model = _select_model(
        base_url=base_url,
        api_key=api_key,
        fetch=fetch,
        input_func=input_func,
    )
    candidate = ProviderSettings(
        provider=service.provider_id,
        model=model,
        base_url=base_url,
        api_key=api_key,
        source="setup",
    )
    print()
    report = evaluate_provider_configuration(
        candidate,
        fetch=fetch,
        probe_runtime=probe_runtime,
        run_probe=run_probe,
    )
    print(format_validation_report(
        report,
        provider_id=service.provider_id,
        model=model,
    ))
    if not report.can_save:
        print("Configuration not saved because required validation failed.")
        return 1

    save_provider_config_atomic(
        config_path=path,
        provider=service.provider_id,
        model=model,
        base_url=base_url,
        api_key=api_key,
    )
    mark_pending_apply(path)
    print("Configuration saved. Live apply is pending a safe restart.")
    return 0


def run_show(*, config_path: Path | None = None) -> int:
    settings = load_provider_settings(config_path=config_path)
    print(format_setup_show(settings))
    return 0


def run_test(
    *,
    config_path: Path | None = None,
    probe_runtime: HermesProbeRuntime | None = None,
    run_probe: Callable[..., Any] | None = None,
) -> int:
    settings = load_provider_settings(config_path=config_path)
    report = evaluate_provider_configuration(
        settings,
        probe_runtime=probe_runtime,
        run_probe=run_probe,
    )
    print(format_test_report(report))
    return report.exit_code
