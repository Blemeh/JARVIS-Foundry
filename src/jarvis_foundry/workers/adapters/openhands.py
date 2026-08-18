"""Real OpenHands worker dispatch, safely bounded (DISABLED by default).

Uses the proven candidate surface (External Fusion benchmark):

    openhands.exe --headless --json -t <task> --override-with-envs
                  --exit-without-confirmation

with ``LLM_BASE_URL`` / ``LLM_API_KEY`` / ``LLM_MODEL`` /
``OPENHANDS_DATA_DIR`` / ``OH_PERSISTENCE_DIR`` from the environment.
Safety invariants:

- ``data_dir`` must be an explicit disposable directory (never
  ``~/.openhands``); a missing/None dir raises AdapterConfigurationError.
- ``enabled`` defaults to False; dispatch raises ProviderUnavailable until
  an operator explicitly enables it with a disposable data dir.
- The API key is only forwarded to the subprocess environment; it is never
  echoed, logged, or stored in the envelope.
- Sessions are bounded by ``timeout_seconds``; JSONL events are parsed for
  the ACTUAL model identity where the runtime exposes it.
- OpenHands containment (Phase 3): the OpenHands SDK anchors many paths to
  ``Path.home()/".openhands"`` (conversations, skills cache, plugins, auth,
  profiles). ``OH_PERSISTENCE_DIR`` covers only the agent-server stores, so
  the adapter ALSO runs the child with a disposable ``USERPROFILE``/``HOME``
  (plus disposable ``TMP``/``TEMP``) so every ``Path.home()`` anchor lands
  in the run-specific disposable directory. After every run the adapter
  snapshots the owner's real ``~/.openhands`` and fails closed (a BLOCKED
  envelope) if any new/removed/modified entry appeared there, so
  probe-owned state can never survive in owner state.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Callable

from ...clock import isoformat, utc_now
from ..contracts import WorkerStepContract
from ..envelope import (
    BLOCKED,
    COMPLETED,
    FAILED,
    NOT_AVAILABLE,
    VERDICT_FAIL,
    VERDICT_NOT_TESTED,
    ResultEnvelope,
)
from ..isolation import (
    diff_openhands_containment,
    snapshot_openhands_tree,
)
from .base import (
    AdapterConfigurationError,
    ProviderUnavailable,
    configured_api_key,
    redact_secrets,
    run_subprocess_with_heartbeat,
)
from ..result_contract import (
    MISSING_RESULT_NOTE,
    ingest_result_claim,
    result_env,
)


class OpenHandsWorkerAdapter:
    #: Owner pre-existing OpenHands state; never a valid disposable data dir.
    LIVE_OPENHANDS = Path.home() / ".openhands"

    _VERDICT_SEARCH = re.compile(
        r"VERDICT\s*:\s*(PASS|FAIL|BLOCKED|NOT_TESTED)\b", re.IGNORECASE)

    def __init__(
        self,
        *,
        openhands_exe: Path,
        data_dir: Path,
        enabled: bool = False,
        timeout_seconds: int = 900,
    ) -> None:
        self.openhands_exe = openhands_exe.resolve()
        self.data_dir = data_dir.resolve()
        self.enabled = enabled
        self.timeout_seconds = timeout_seconds
        self.framework = "openhands"
        self._owner_openhands = self.LIVE_OPENHANDS
        self._validate()

    def _validate(self) -> None:
        if not self.openhands_exe.exists():
            raise AdapterConfigurationError(
                f"openhands executable does not exist: {self.openhands_exe}")
        if (self.data_dir == self.LIVE_OPENHANDS
                or self.LIVE_OPENHANDS in self.data_dir.parents):
            raise AdapterConfigurationError(
                "refusing a disposable data dir inside the owner's "
                "~/.openhands state")
        if not self.data_dir.exists() and self.enabled:
            raise AdapterConfigurationError(
                f"openhands data_dir does not exist: {self.data_dir} "
                "(disposable data dir required)")

    def dispatch(
        self,
        contract: WorkerStepContract,
        *,
        heartbeat: Callable[[], None],
    ) -> ResultEnvelope:
        if not self.enabled:
            raise ProviderUnavailable(
                "real OpenHands dispatch is disabled; enable explicitly "
                "with a disposable OPENHANDS_DATA_DIR")
        started = isoformat(utc_now())
        env = dict(os.environ)
        env.update(result_env(contract.working_root))
        env["LLM_BASE_URL"] = os.environ.get(
            "JARVIS_OPENCODE_BASE_URL", "https://opencode.ai/zen/go/v1")
        env["LLM_API_KEY"] = os.environ.get("JARVIS_OPENCODE_API_KEY", "")
        env["LLM_MODEL"] = f"openai/{contract.model}"
        env["OPENHANDS_DATA_DIR"] = str(self.data_dir)
        # Phase 3 containment: redirect the entire OpenHands persistence
        # tree and the process home into the disposable run directory, never
        # the owner's ~/.openhands. Path.home() on Windows honors
        # USERPROFILE, so a disposable USERPROFILE/HOME redirects every SDK
        # anchor (conversations, skills cache, plugins, auth, profiles).
        persistence_dir = self.data_dir / "persistence"
        persistence_dir.mkdir(parents=True, exist_ok=True)
        env["OH_PERSISTENCE_DIR"] = str(persistence_dir)
        disposable_home = self.data_dir / "contained-home"
        disposable_home.mkdir(parents=True, exist_ok=True)
        disposable_temp = self.data_dir / "contained-temp"
        disposable_temp.mkdir(parents=True, exist_ok=True)
        env["USERPROFILE"] = str(disposable_home)
        env["HOME"] = str(disposable_home)
        env["HOMEDRIVE"] = str(disposable_home.drive or "")
        env["HOMEPATH"] = str(disposable_home).replace(
            disposable_home.drive or "", "")
        # Browser-use reads %LOCALAPPDATA% (ms-playwright installs) and
        # some tooling reads %APPDATA%; redirect both to the disposable
        # home so no tool-driven state escapes the run directory.
        env["LOCALAPPDATA"] = str(disposable_home / "AppData" / "Local")
        env["APPDATA"] = str(disposable_home / "AppData" / "Roaming")
        (disposable_home / "AppData" / "Local").mkdir(parents=True, exist_ok=True)
        (disposable_home / "AppData" / "Roaming").mkdir(parents=True, exist_ok=True)
        env["TMP"] = str(disposable_temp)
        env["TEMP"] = str(disposable_temp)
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUTF8"] = "1"
        env["NO_COLOR"] = "1"
        command = [
            str(self.openhands_exe),
            "--headless",
            "--json",
            "-t", contract.instructions or contract.objective or contract.run_id,
            "--override-with-envs",
            "--exit-without-confirmation",
        ]
        # Phase 3 containment: capture the owner OpenHands state BEFORE the
        # run so any probe-owned write after is detectable as a violation.
        before_owner = snapshot_openhands_tree(self._owner_openhands)
        try:
            process = run_subprocess_with_heartbeat(
                command,
                cwd=str(contract.working_root),
                env=env,
                timeout_seconds=self.timeout_seconds,
                heartbeat=heartbeat,
                encoding="utf-8",
                errors="replace",
            )
        except subprocess.TimeoutExpired:
            heartbeat()
            self._scrub_disposable_state()
            after_owner = snapshot_openhands_tree(self._owner_openhands)
            violations = diff_openhands_containment(
                before_owner, after_owner, home_root=self._owner_openhands)
            blockers = (f"timeout after {self.timeout_seconds}s",)
            if violations:
                blockers = blockers + tuple(
                    f"containment_violation:{v}" for v in violations)
            files, valid = ingest_result_claim(contract.working_root)
            summary = f"session exceeded {self.timeout_seconds}s bound"
            if not valid:
                summary = f"{summary}\n{MISSING_RESULT_NOTE}"
            return ResultEnvelope.from_contract(
                contract, framework=self.framework,
                worker_status=BLOCKED if violations else FAILED,
                verdict=VERDICT_NOT_TESTED,
                claimed_outcome="OpenHands session timed out",
                summary=summary,
                files_changed=files,
                started_at=started,
                exit_state="TIMEOUT",
                blockers=blockers,
                usage_availability=NOT_AVAILABLE,
            )
        except OSError as error:
            heartbeat()
            raise ProviderUnavailable(f"could not launch OpenHands: {error}") from error
        finally:
            heartbeat()

        # Phase 3 containment diff: the pre-run snapshot above is the
        # baseline; any new/removed/modified entry in the owner state after
        # the run forces a fail-closed BLOCKED result.
        stdout = redact_secrets(
            process.stdout or "", configured_key=configured_api_key())
        events = self._parse_jsonl(stdout)
        model_actual = contract.model
        for event in events:
            model = event.get("model") or (event.get("payload") or {}).get("model")
            if model:
                model_actual = str(model).removeprefix("openai/")
                break
        verdict = self._structured_verdict(stdout)
        after_owner = snapshot_openhands_tree(self._owner_openhands)
        containment_violations = diff_openhands_containment(
            before_owner, after_owner, home_root=self._owner_openhands)
        if containment_violations:
            self._scrub_disposable_state()
            return ResultEnvelope.from_contract(
                contract, framework=self.framework,
                worker_status=BLOCKED, verdict=VERDICT_NOT_TESTED,
                claimed_outcome="OpenHands run left probe state in owner ~/.openhands",
                summary="OpenHands containment violated: "
                        + "; ".join(containment_violations),
                started_at=started,
                model_actual=model_actual,
                blockers=tuple(containment_violations),
                usage_availability=NOT_AVAILABLE,
            )
        if process.returncode != 0:
            self._scrub_disposable_state()
            return ResultEnvelope.from_contract(
                contract, framework=self.framework,
                worker_status=FAILED,
                verdict=VERDICT_FAIL if contract.role == "verifier" else VERDICT_NOT_TESTED,
                claimed_outcome="OpenHands CLI exited non-zero",
                summary=redact_secrets(
                    process.stderr or stdout,
                    configured_key=configured_api_key())[-800:],
                started_at=started,
                model_actual=model_actual,
                exit_state=f"EXIT_{process.returncode}",
                usage_availability=NOT_AVAILABLE,
            )
        statuses = {e.get("event") or e.get("type") for e in events}
        warning = ""
        if statuses & {"error", "agent_state_changed"}:
            warning = "openhands reported error/state events; evidence must be reconciled"
        summary_tail = stdout.strip()[-2000:]
        self._scrub_disposable_state()
        result_files, valid_claim = ingest_result_claim(contract.working_root)
        if not valid_claim:
            summary_tail = (f"{summary_tail}\n{MISSING_RESULT_NOTE}"
                            if summary_tail else MISSING_RESULT_NOTE)
            verdict = VERDICT_NOT_TESTED
        return ResultEnvelope.from_contract(
            contract, framework=self.framework,
            worker_status=COMPLETED, verdict=verdict,
            claimed_outcome="OpenHands session completed",
            summary=summary_tail,
            model_actual=model_actual,
            worker_results=(summary_tail,),
            files_changed=result_files,
            started_at=started,
            warnings=(warning,) if warning else (),
            usage_availability=NOT_AVAILABLE,
        )

    @staticmethod
    def _parse_jsonl(stdout: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for line in stdout.splitlines():
            stripped = line.strip()
            if not stripped.startswith("{"):
                continue
            try:
                rows.append(json.loads(stripped))
            except json.JSONDecodeError:
                continue
        return rows

    def _scrub_disposable_state(self, limit: int = 5000) -> None:
        """Redact credential-shaped tokens from disposable run-state files
        (contained-home conversation events, editor history, etc.) so no
        retained artifact ever holds the secret value."""
        key = configured_api_key()
        if not self.data_dir.exists():
            return
        scanned = 0
        for path in self.data_dir.rglob("*"):
            if not path.is_file():
                continue
            scanned += 1
            if scanned > limit:
                break
            try:
                data = path.read_bytes()
            except OSError:
                continue
            if b"sk-" not in data:
                continue
            try:
                text = data.decode("utf-8")
            except UnicodeDecodeError:
                continue
            redacted = redact_secrets(text, configured_key=key)
            if redacted != text:
                try:
                    path.write_bytes(redacted.encode("utf-8"))
                except OSError:
                    pass

    @classmethod
    def _structured_verdict(cls, stdout: str) -> str:
        """Parse an explicit structured verdict from the session output.

        The verifier instructions require a final ``VERDICT: PASS | FAIL |
        BLOCKED | NOT_TESTED`` line; the adapter scans the session output
        (JSONL event payloads included). Missing output is NOT_TESTED —
        never PASS.
        """
        for line in (stdout or "").splitlines()[:200]:
            match = cls._VERDICT_SEARCH.search(line)
            if match:
                return match.group(1).upper()
        return VERDICT_NOT_TESTED


def make_openhands_worker_adapter(
    openhands_exe: Path,
    *,
    enabled: bool = False,
    **kwargs: Any,
) -> OpenHandsWorkerAdapter:
    """Convenience factory: disposable temp data dir unless provided."""
    data_dir = kwargs.pop("data_dir", None)
    if data_dir is None:
        import tempfile
        data_dir = Path(tempfile.mkdtemp(prefix="jf-worker-openhands-"))
    return OpenHandsWorkerAdapter(
        openhands_exe=openhands_exe, data_dir=Path(data_dir),
        enabled=enabled, **kwargs,
    )
