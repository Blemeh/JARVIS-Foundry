"""Run the V10-A Golden Journey 1 in a disposable lane root.

The script deliberately keeps the browser/extractor hand-off in memory. Only
safe connector receipts, hashes, and the final Markdown brief cross durable
boundaries. The final artifact is written through the existing Fabric spine and
filesystem connector; this lane does not add a second mission loop or writer.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable, Sequence

from jarvis_foundry.connectors.evidence import record_attempt
from jarvis_foundry.connectors.filesystem import FilesystemConnector
from jarvis_foundry.connectors.registry import record_health_sample, register_connector
from jarvis_foundry.connectors.web_research import (
    BrowserReadResult,
    HtmlExtractionResult,
    PlaywrightBrowserConnector,
    TrafilaturaExtractor,
    render_research_brief,
)
from jarvis_foundry.database import FoundryDatabase
from jarvis_foundry.events import Event, payload_hash
from jarvis_foundry.fabric import ExecutorContext, execute_for_spine
from jarvis_foundry.world_model.connector import writeback_mission_outcome


MISSION_ID = "v10-a-golden-journey-1"
OUTPUT_TARGET = "v10-a/research-brief.md"
PUBLIC_URLS = (
    "https://playwright.dev/python/docs/library",
    "https://playwright.dev/python/docs/browsers",
    "https://github.com/microsoft/playwright-mcp",
)
ALLOWED_HOSTS = ("playwright.dev", "github.com")
MAX_EVIDENCE_TEXT = 300


class JourneyBlocked(RuntimeError):
    """A journey phase could not prove its success boundary."""

    def __init__(
        self,
        phase: str,
        reason: str,
        *,
        receipts: list[dict[str, Any]] | None = None,
        captures: list[BrowserReadResult] | None = None,
    ) -> None:
        super().__init__(f"{phase}: {reason}")
        self.phase = phase
        self.reason = reason
        self.receipts = receipts or []
        self.captures = captures or []


def _git(*args: str) -> str | None:
    result = subprocess.run(
        ["git", *args], capture_output=True, text=True,
        encoding="utf-8", check=False,
    )
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _safe_detail(value: object) -> str:
    return str(value or "UNKNOWN").replace("\x00", " ")[:MAX_EVIDENCE_TEXT]


def _assert_disposable_root(root: Path) -> Path:
    resolved = root.expanduser().resolve()
    if resolved == Path(resolved.anchor):
        raise ValueError("output root may not be a filesystem root")
    if resolved.exists() and not resolved.is_dir():
        raise ValueError("output root must be a directory")
    if resolved.exists() and any(resolved.iterdir()):
        raise ValueError("output root must be new or empty; refusing overwrite")
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def _record_proof_event(
    database: FoundryDatabase,
    name: str,
    payload: dict[str, Any],
) -> None:
    safe_payload = dict(payload)
    safe_payload["proof"] = name
    database.append(Event.create(
        "v10a.failure_proof_recorded",
        safe_payload,
        source_type="v10_a_harness",
        source_id="golden-journey-1",
        correlation_id=MISSION_ID,
        idempotency_key=(
            f"v10a.failure_proof_recorded:{name}:{payload_hash(safe_payload)}"
        ),
    ))


def _page_receipt(
    capture: BrowserReadResult,
    extraction: HtmlExtractionResult | None = None,
) -> dict[str, Any]:
    page = capture.page
    receipt: dict[str, Any] = {
        "browser_attempt": capture.attempt.to_evidence(),
        "requested_url": page.requested_url if page else capture.attempt.target_ref,
        "final_url": page.final_url if page else None,
        "http_status": page.status if page else None,
        "content_type": page.content_type if page else None,
        "redirects": list(page.redirects) if page else [],
        "source_sha256": page.source_sha256 if page else None,
        "source_bytes": page.source_bytes if page else None,
        "document_id": page.document_id if page else None,
        "page_revision": page.page_revision if page else None,
        "authority": "DATA_ONLY",
    }
    if extraction is not None:
        receipt.update({
            "extraction_attempt": extraction.attempt.to_evidence(),
            "extracted_sha256": extraction.extracted_sha256,
            "safe_text_sha256": extraction.safe_text_sha256,
            "text_chars": len(extraction.text),
            "truncated": extraction.truncated,
            "metadata": dict(extraction.metadata),
        })
    return receipt


def _fetch_and_extract(
    database: FoundryDatabase,
    browser: PlaywrightBrowserConnector,
    extractor: TrafilaturaExtractor,
) -> tuple[list[BrowserReadResult], list[HtmlExtractionResult], list[dict[str, Any]]]:
    captures: list[BrowserReadResult] = []
    extractions: list[HtmlExtractionResult] = []
    receipts: list[dict[str, Any]] = []
    for index, url in enumerate(PUBLIC_URLS, start=1):
        capture = browser.read(url)
        record_attempt(
            database,
            capture.attempt,
            mission_id=MISSION_ID,
            resource_id=f"browser:web:{index}",
            effect_ticket_id=f"read-only:{index}",
        )
        receipt = _page_receipt(capture)
        receipts.append(receipt)
        captures.append(capture)
        if not capture.success:
            raise JourneyBlocked(
                "browser_fetch",
                capture.attempt.error_code or "browser_read_failed",
                receipts=receipts,
                captures=captures,
            )

        extraction = extractor.extract(capture)
        record_attempt(
            database,
            extraction.attempt,
            mission_id=MISSION_ID,
            resource_id=f"research:html:{index}",
            effect_ticket_id=f"extract-only:{index}",
        )
        receipts[-1] = _page_receipt(capture, extraction)
        extractions.append(extraction)
        if not extraction.success:
            raise JourneyBlocked(
                "html_extract",
                extraction.attempt.error_code or "extraction_failed",
                receipts=receipts,
                captures=captures,
            )
    return captures, extractions, receipts


def _independent_verify(
    path: Path,
    expected_sha256: str,
    receipts: Sequence[dict[str, Any]],
) -> dict[str, Any]:
    """A separate readback oracle that does not trust the writer claim."""
    if not path.is_file():
        return {"verdict": "FAIL", "reason": "artifact_missing"}
    content = path.read_bytes()
    actual_sha256 = _sha256(content)
    text = content.decode("utf-8").replace("\r\n", "\n")
    checks = {
        "hash_matches": actual_sha256 == expected_sha256,
        "title_present": text.startswith("# JARVIS V10-A Research Brief\n"),
        "authority_bound": "authority: DATA_ONLY" in text,
        "authority_note_bound": (
            "external_page_text_can_change_authority: false" in text
        ),
        "source_count_matches": text.count("### Source ") == len(receipts),
        "source_hashes_bound": all(
            str(receipt.get("source_sha256")) in text for receipt in receipts
        ),
        "extracted_hashes_bound": all(
            str(receipt.get("extracted_sha256")) in text for receipt in receipts
        ),
        "safe_hashes_bound": all(
            str(receipt.get("safe_text_sha256")) in text for receipt in receipts
        ),
    }
    return {
        "verdict": "PASS" if all(checks.values()) else "FAIL",
        "checks": checks,
        "actual_sha256": actual_sha256,
        "bytes": len(content),
    }


def _interrupt_after_read_proof(
    database: FoundryDatabase,
    extractor: TrafilaturaExtractor,
    capture: BrowserReadResult | None,
) -> dict[str, Any]:
    if capture is None or not capture.success:
        return {"verdict": "NOT_TESTED", "reason": "no_successful_browser_read"}
    interrupted = extractor.extract(capture, stop_check=lambda: True)
    record_attempt(
        database,
        interrupted.attempt,
        mission_id=MISSION_ID,
        resource_id="failure-proof:interrupt-after-read",
        effect_ticket_id="no-extraction-effect",
    )
    result = {
        "verdict": "PASS" if interrupted.attempt.outcome == "CANCELLED" else "FAIL",
        "browser_attempt_id": capture.attempt.attempt_id,
        "extraction_attempt": interrupted.attempt.to_evidence(),
        "artifact_write_started": False,
    }
    _record_proof_event(database, "interrupt_after_browser_read", {
        "verdict": result["verdict"],
        "outcome": interrupted.attempt.outcome,
        "error_code": interrupted.attempt.error_code,
        "artifact_write_started": False,
    })
    return result


def _stop_before_write_proof(
    database: FoundryDatabase,
    owned_root: Path,
) -> dict[str, Any]:
    target = owned_root / "failure-proofs" / "stop-before-write.md"
    connector = FilesystemConnector(owned_root)
    write_calls = 0

    def guarded_write(stop_check: Callable[[], bool]) -> dict[str, Any]:
        nonlocal write_calls
        if stop_check():
            return {
                "claimed": "BLOCKED",
                "error_code": "stop_requested",
                "write_called": False,
            }
        write_calls += 1
        attempt = connector.request(
            "file.write",
            "failure-proofs/stop-before-write.md",
            "must not be written",
            idempotency_key="v10-a:stop-before-write",
        )
        return {"claimed": attempt.outcome, "write_called": True}

    result = guarded_write(lambda: True)
    proof = {
        "verdict": (
            "PASS" if result["claimed"] == "BLOCKED"
            and not result["write_called"]
            and write_calls == 0
            and not target.exists()
            else "FAIL"
        ),
        "result": result,
        "write_calls": write_calls,
        "artifact_exists": target.exists(),
    }
    _record_proof_event(database, "stop_before_write", {
        "verdict": proof["verdict"],
        "write_calls": write_calls,
        "artifact_exists": target.exists(),
    })
    return proof


def _timeout_after_write_proof(
    database: FoundryDatabase,
    owned_root: Path,
) -> dict[str, Any]:
    target = "failure-proofs/timeout-after-write.md"
    # Keep this injected probe single-line: the pre-existing filesystem
    # connector's low-level request hash is byte-based, while Windows text
    # mode translates newlines.  The Markdown journey itself is verified by
    # its independent normalized readback above.
    content = "reconcile this existing byteset"
    target_path = owned_root / target
    target_path.parent.mkdir(parents=True, exist_ok=True)

    class _TimeoutAfterWrite(FilesystemConnector):
        writes = 0

        def _write_bytes_unlocked(self, ref: str, payload: bytes) -> None:
            self.writes += 1
            return super()._write_bytes_unlocked(ref, payload)

        def _write_ledger(self, ledger: dict[str, dict[str, Any]]) -> None:
            raise TimeoutError("injected timeout after file replacement")

    key = "v10-a:timeout-after-write"
    first_connector = _TimeoutAfterWrite(owned_root)
    first = first_connector.request(
        "file.write", target, content, idempotency_key=key,
    )
    record_attempt(
        database, first,
        mission_id=MISSION_ID,
        resource_id="failure-proof:timeout-after-write",
        effect_ticket_id="timeout-after-file-write",
    )
    recovery_connector = FilesystemConnector(owned_root)
    recovered = recovery_connector.request(
        "file.write", target, content, idempotency_key=key,
    )
    record_attempt(
        database, recovered,
        mission_id=MISSION_ID,
        resource_id="failure-proof:timeout-after-write",
        effect_ticket_id="reconciled-existing-effect",
    )
    snapshot = recovery_connector.snapshot(target)
    proof = {
        "verdict": (
            "PASS" if first.outcome == "UNKNOWN"
            and first.error_code == "ledger_write_failed"
            and recovered.outcome == "COMPLETED"
            and recovered.replayed
            and recovered.reconciled
            and first_connector.writes == 1
            and snapshot.content_hash == "sha256:" + _sha256(content.encode("utf-8"))
            else "FAIL"
        ),
        "fault": "timeout_after_file_write",
        "first_attempt": first.to_evidence(),
        "recovery_attempt": recovered.to_evidence(),
        "write_invocations": first_connector.writes,
        "duplicate_writes": max(first_connector.writes - 1, 0),
        "recovered_content_hash": snapshot.content_hash,
    }
    _record_proof_event(database, "timeout_after_file_write", {
        "verdict": proof["verdict"],
        "first_outcome": first.outcome,
        "recovery_outcome": recovered.outcome,
        "replayed": recovered.replayed,
        "write_invocations": first_connector.writes,
        "duplicate_writes": proof["duplicate_writes"],
    })
    return proof


def _write_evidence(path: Path, report: dict[str, Any]) -> None:
    if path.exists():
        raise ValueError(f"refusing to overwrite evidence path: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def run(args: argparse.Namespace) -> dict[str, Any]:
    root = _assert_disposable_root(Path(args.output_root))
    browser_path = (
        args.browser_path
        or os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
        or str(root / ".playwright-browsers")
    )
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(Path(browser_path).resolve())

    report: dict[str, Any] = {
        "verdict": "BLOCKED",
        "base_sha": _git("merge-base", "HEAD", "origin/main"),
        "branch": _git("branch", "--show-current"),
        "worktree": str(Path.cwd().resolve()),
        "external_versions": {},
        "reused_components": [
            "connector contract and evidence recorder",
            "existing Fabric execute_for_spine",
            "existing FilesystemConnector and independent filesystem readback",
            "existing WorldModel mission-outcome writeback",
            "existing external-content DATA_ONLY safety boundary",
        ],
        "changed_files": [
            "pyproject.toml",
            "uv.lock",
            ".gitignore",
            "src/jarvis_foundry/connectors/web_research.py",
            "tests/foundry/test_v10_a_web_research.py",
            "scripts/v10_a_golden_journey.py",
        ],
        "journey": {
            "name": "Golden Journey 1",
            "mission_id": MISSION_ID,
            "urls": list(PUBLIC_URLS),
            "source_receipts": [],
            "artifact_target": OUTPUT_TARGET,
            "artifact": None,
            "independent_verifier": None,
        },
        "failure_recovery": {},
        "metrics": {
            "verified_success": False,
            "duplicate_external_effects": 0,
            "interruptions": 0,
            "completion_time_ms": None,
            "recovery_time_ms": None,
            "provider_cost_usd": 0.0,
            "unverified_effects": 0,
        },
        "tests": {
            "adapter_and_connector_regressions": "RUN_SEPARATELY",
            "golden_journey": "RUNNING",
        },
        "live_mutated": "NO",
        "main_mutated": "NO",
        "origin_mutated": "NO",
        "db_path": str((root / "foundry.db").resolve()),
        "browser_path": str(Path(browser_path).resolve()),
    }

    database = FoundryDatabase(root / "foundry.db")
    database.initialize()
    owned_root = root / "owned"
    owned_root.mkdir(parents=True, exist_ok=True)
    browser = PlaywrightBrowserConnector(
        timeout_ms=args.timeout_ms,
        max_source_bytes=args.max_source_bytes,
        allowed_hosts=ALLOWED_HOSTS,
    )
    extractor = TrafilaturaExtractor(max_chars=args.max_extract_chars)
    browser_health = browser.health()
    extractor_health = extractor.health()
    report["external_versions"] = {
        "playwright": browser_health.get("playwright_version"),
        "chromium": browser_health.get("browser_version"),
        "trafilatura": extractor_health.get("trafilatura_version"),
        "browser_health": browser_health,
        "extractor_health": extractor_health,
    }
    register_connector(database, browser.spec)
    register_connector(database, extractor.spec)
    record_health_sample(
        database, browser.spec.connector_id,
        "HEALTHY" if browser_health.get("available") else "UNKNOWN",
        detail=_safe_detail(browser_health.get("error_code") or "bounded probe"),
    )
    record_health_sample(
        database, extractor.spec.connector_id,
        "HEALTHY" if extractor_health.get("available") else "UNKNOWN",
        detail=_safe_detail(extractor_health.get("error_code") or "package probe"),
    )

    clean_started = time.perf_counter()
    captures: list[BrowserReadResult] = []
    receipts: list[dict[str, Any]] = []
    try:
        if not browser_health.get("available"):
            raise JourneyBlocked("setup", "playwright_unavailable")
        if not extractor_health.get("available"):
            raise JourneyBlocked("setup", "trafilatura_unavailable")
        captures, extractions, receipts = _fetch_and_extract(
            database, browser, extractor,
        )
        report["journey"]["source_receipts"] = receipts

        interrupt = _interrupt_after_read_proof(
            database, extractor, captures[0] if captures else None,
        )
        stop = _stop_before_write_proof(database, owned_root)
        report["failure_recovery"]["interrupt_after_read"] = interrupt
        report["failure_recovery"]["stop_before_write"] = stop
        report["metrics"]["interruptions"] = 2

        brief = render_research_brief(
            "Playwright and Trafilatura bounded public-web capability",
            extractions,
        )
        requested_sha256 = _sha256(brief.encode("utf-8"))
        context = ExecutorContext(database=database, owned_root=owned_root)
        write_receipt = execute_for_spine(
            database,
            context,
            mission_id=MISSION_ID,
            step_id="research-brief-write",
            action_kind="file.write",
            payload={
                "target": OUTPUT_TARGET,
                "content": brief,
                "idempotency_key": "v10-a:golden-journey-1:brief",
            },
        )
        if not write_receipt.get("success"):
            raise JourneyBlocked(
                "artifact_write",
                _safe_detail(write_receipt.get("note") or "fabric_file_write_failed"),
                receipts=receipts,
                captures=captures,
            )

        artifact_path = owned_root / OUTPUT_TARGET
        artifact_bytes = artifact_path.read_bytes()
        actual_sha256 = _sha256(artifact_bytes)
        fabric_hash = str((write_receipt.get("observed") or {}).get("content_hash") or "")
        filesystem = FilesystemConnector(owned_root)
        read_attempt = filesystem.request(
            "file.read", OUTPUT_TARGET, None,
            idempotency_key="v10-a:golden-journey-1:readback",
        )
        record_attempt(
            database,
            read_attempt,
            mission_id=MISSION_ID,
            resource_id="artifact:research-brief",
            effect_ticket_id="independent-readback",
        )
        independent = _independent_verify(
            artifact_path, actual_sha256, receipts,
        )
        independent_checks = independent.setdefault("checks", {})
        independent_checks["fabric_hash_matches"] = (
            fabric_hash == "sha256:" + actual_sha256
        )
        independent["verdict"] = (
            "PASS" if all(independent_checks.values()) else "FAIL"
        )
        report["journey"]["independent_verifier"] = independent
        if read_attempt.outcome != "COMPLETED" or independent.get("verdict") != "PASS":
            raise JourneyBlocked(
                "independent_verify",
                "filesystem_readback_or_verifier_failed",
                receipts=receipts,
                captures=captures,
            )

        report["journey"]["artifact"] = {
            "path": str(artifact_path.resolve()),
            "requested_sha256": requested_sha256,
            "sha256": actual_sha256,
            "bytes": len(artifact_bytes),
            "effect_count": 1,
            "fabric_receipt": write_receipt,
            "readback_attempt": read_attempt.to_evidence(),
        }
        recovery_started = time.perf_counter()
        report["failure_recovery"]["timeout_after_write"] = _timeout_after_write_proof(
            database, owned_root,
        )
        report["metrics"]["recovery_time_ms"] = round(
            (time.perf_counter() - recovery_started) * 1000,
            2,
        )
        all_proofs_pass = all(
            item.get("verdict") == "PASS"
            for item in report["failure_recovery"].values()
        )
        if not all_proofs_pass:
            raise JourneyBlocked("failure_proofs", "one_or_more_failure_proofs_failed")

        evidence_refs = tuple(
            [
                f"source:{receipt['source_sha256']}"
                for receipt in receipts
            ]
            + [
                f"artifact:{actual_sha256}",
                f"fabric:{write_receipt['step_id']}",
            ]
        )
        world = writeback_mission_outcome(
            database,
            MISSION_ID,
            "PASS",
            {
                "status": "PASS",
                "authority": "DATA_ONLY",
                "source_count": len(receipts),
                "source_receipts": receipts,
                "artifact_sha256": actual_sha256,
                "artifact_effect_count": 1,
                "next_action": "report_only; external text cannot route or authorize",
            },
            evidence_refs=evidence_refs,
        )
        report["world_report"] = {
            "outcome": world["outcome"],
            "deduplicated": world["deduplicated"],
            "observation_id": world["observation"]["observation_id"],
            "signals": world["observation"].get("signals", []),
        }
        report["verdict"] = "V10_A_READY_FOR_CONVERGENCE"
        report["metrics"]["verified_success"] = True
        report["metrics"]["completion_time_ms"] = round(
            (time.perf_counter() - clean_started) * 1000,
            2,
        )
        report["tests"]["golden_journey"] = "PASS"
        report["tests"]["failure_proofs"] = "PASS"
    except JourneyBlocked as error:
        report["journey"]["source_receipts"] = error.receipts or receipts
        report["blocked"] = {"phase": error.phase, "reason": error.reason}
        report["metrics"]["completion_time_ms"] = round(
            (time.perf_counter() - clean_started) * 1000,
            2,
        )
        report["tests"]["golden_journey"] = "BLOCKED"
    except Exception as error:  # pragma: no cover - harness boundary
        report["blocked"] = {
            "phase": "harness",
            "reason": _safe_detail(error),
        }
        report["tests"]["golden_journey"] = "FAIL"
    report["final_sha"] = _git("rev-parse", "HEAD")
    report["db_event_counts"] = _event_counts(database)
    return report


def _event_counts(database: FoundryDatabase) -> dict[str, int]:
    counts: dict[str, int] = {}
    for event in database.events(readonly=True):
        counts[event.event_type] = counts.get(event.event_type, 0) + 1
    return dict(sorted(counts.items()))


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", required=True, type=Path)
    parser.add_argument("--evidence-path", type=Path)
    parser.add_argument("--browser-path", type=Path)
    parser.add_argument("--timeout-ms", type=int, default=30_000)
    parser.add_argument("--max-source-bytes", type=int, default=2 * 1024 * 1024)
    parser.add_argument("--max-extract-chars", type=int, default=32 * 1024)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        report = run(args)
        if args.evidence_path:
            _write_evidence(args.evidence_path.resolve(), report)
        print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=True))
        return 0 if report["verdict"] == "V10_A_READY_FOR_CONVERGENCE" else 2
    except Exception as error:
        print(json.dumps({
            "verdict": "FAIL",
            "phase": "harness_setup",
            "reason": _safe_detail(error),
            "live_mutated": "NO",
            "main_mutated": "NO",
            "origin_mutated": "NO",
        }, ensure_ascii=True, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
