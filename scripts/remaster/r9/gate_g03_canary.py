"""R9-G03: real canary effects, receipts, reconciliation, idempotent replay."""

from __future__ import annotations

import sys
import time

from common import (
    CANARY_EVIDENCE_KEYS,
    EVIDENCE_DIR,
    EVIDENCE_UTC,
    TEMP_BASE,
    read_json,
    write_json,
)

from jarvis_foundry.capabilities_live.adapters.files import FilesAdapter
from jarvis_foundry.capabilities_live.adapters.git import GitAdapter
from jarvis_foundry.capabilities_live.broker import CredentialBroker
from jarvis_foundry.kernel.clock import FixedClock


def main() -> int:
    started = time.monotonic()
    files = read_json(EVIDENCE_DIR / "R9_CANARY_FILES.json")
    git = read_json(EVIDENCE_DIR / "R9_CANARY_GIT.json")
    services = read_json(EVIDENCE_DIR / "R9_CANARY_SERVICES.json")
    lifecycle = read_json(EVIDENCE_DIR / "R9_LIFECYCLE.json")

    files_real = bool(
        files["execute"]["outcome"] == "EXECUTED"
        and files["execute"]["before_state_hash"] != files["execute"]["after_state_hash"]
        and files["execute"]["receipt"] is not None
        and files["execute"]["reconciliation"]["reconciled"] is True
        and files["replay"]["outcome"] == "REPLAYED"
        and files["replay"]["receipt"]["effect_hash"] == files["execute"]["receipt"]["effect_hash"]
        and files["stop"]["outcome"] == "STOPPED"
        and files["stop"]["no_partial_effect"] is True
        and files["fault"]["outcome"] == "FAILED"
        and files["fault"]["no_partial_effect"] is True
    )
    git_real = bool(
        git["commit"]["outcome"] == "EXECUTED"
        and git["commit"]["before_state_hash"] != git["commit"]["after_state_hash"]
        and git["commit"]["receipt"] is not None
        and git["replay"]["outcome"] == "REPLAYED"
        and git["replay"]["receipt"]["effect_hash"] == git["commit"]["receipt"]["effect_hash"]
        and git["revert"]["outcome"] == "EXECUTED"
        and git["revert"]["after_detail"]["tree"] == git["revert_baseline_tree"]
        and git["stop"]["outcome"] == "STOPPED"
        and git["fault"]["no_partial_effect"] is True
    )
    services_real = bool(
        services["lifecycle"]["outcome"] == "EXECUTED"
        and services["lifecycle"]["before_state_hash"] == "stopped"
        and services["lifecycle"]["after_state_hash"] == "running"
        and services["lifecycle"]["receipt"] is not None
        and services["replay"]["outcome"] == "REPLAYED"
        and services["wrong_credential_probe"]["refused"] is True
        and services["stop"]["port_released"] is True
        and services["stop_refusal"]["outcome"] == "STOPPED"
        and services["fault"]["no_partial_effect"] is True
    )
    active_all = bool(
        lifecycle["capability_states"]["capability.files"]["status"] == "ACTIVE"
        and lifecycle["capability_states"]["capability.git"]["status"] == "ACTIVE"
        and lifecycle["capability_states"]["capability.services"]["status"] == "ACTIVE"
    )

    broker = CredentialBroker(
        TEMP_BASE / "stores" / "credentials.db",
        clock=FixedClock(EVIDENCE_UTC),
    )
    credential = broker.resolve(
        "cred-files",
        capability="capability.files",
        requested_target="canary/files/one.txt",
        at=EVIDENCE_UTC,
    )
    files_adapter = FilesAdapter(TEMP_BASE / "canary" / "files" / "proving")
    before_replay = files_adapter._dir_hash(files_adapter.root)
    replay = files_adapter.execute(
        "write",
        {
            "idempotency_key": "canary-files-write",
            "relative_path": "one.txt",
            "content": "r9 canary file\n",
        },
        credential,
    )
    after_replay = files_adapter._dir_hash(files_adapter.root)
    files_replay_ok = bool(
        replay.outcome.value == "REPLAYED"
        and replay.receipt is not None
        and replay.receipt.effect_hash == files["execute"]["receipt"]["effect_hash"]
        and before_replay == after_replay
    )
    files_journal = files_adapter._load_journal()
    files_journal_unique = (
        sum(
            1
            for entry in files_journal
            if entry.get("operation") == "write"
            and entry.get("idempotency_key") == "canary-files-write"
        )
        == 1
    )

    git_credential = broker.resolve(
        "cred-git",
        capability="capability.git",
        requested_target="canary/git/repo/note.md",
        at=EVIDENCE_UTC,
    )
    git_adapter = GitAdapter(TEMP_BASE / "canary" / "git" / "proving")
    git_replay = git_adapter.execute(
        "commit",
        {
            "idempotency_key": "canary-git-commit",
            "relative_path": "note.md",
            "content": "# R9 canary note\n",
            "message": "add r9 canary note",
        },
        git_credential,
    )
    git_replay_ok = bool(
        git_replay.outcome.value == "REPLAYED"
        and git_replay.receipt is not None
        and git_replay.receipt.effect_hash == git["commit"]["receipt"]["effect_hash"]
    )
    git_journal = git_adapter._load_journal()
    git_journal_unique = (
        sum(
            1
            for entry in git_journal
            if entry.get("operation") == "commit"
            and entry.get("idempotency_key") == "canary-git-commit"
        )
        == 1
    )

    passed = bool(
        files_real
        and git_real
        and services_real
        and active_all
        and files_replay_ok
        and files_journal_unique
        and git_replay_ok
        and git_journal_unique
    )
    payload = {
        "schema": "jarvis-r9-g03-canary-v1",
        "exit_code": 0 if passed else 1,
        "wall_seconds": round(time.monotonic() - started, 3),
        "passed": passed,
        "files_real": files_real,
        "git_real": git_real,
        "services_real": services_real,
        "active_all": active_all,
        "independent_replay": {
            "files": {
                "outcome": replay.outcome.value,
                "receipt_hash_matches": files_replay_ok,
                "state_unchanged": before_replay == after_replay,
                "journal_entry_unique": files_journal_unique,
            },
            "git": {
                "outcome": git_replay.outcome.value,
                "receipt_hash_matches": git_replay_ok,
                "journal_entry_unique": git_journal_unique,
            },
        },
        "evidence_files": CANARY_EVIDENCE_KEYS,
    }
    write_json(EVIDENCE_DIR / "R9_G03_CANARY.json", payload)
    print(f"g03 canary: passed={passed} wall_seconds={payload['wall_seconds']}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
