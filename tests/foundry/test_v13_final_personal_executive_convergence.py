from __future__ import annotations

from pathlib import Path

from scripts import v13_final_personal_executive_convergence_proof as proof


def _receipts() -> dict:
    return {
        "owner_context": {
            "artifacts": {
                "bounded_context": {
                    "bounds": {"used_items": 5},
                },
            },
        },
        "personal_operator": {
            "data": {"rows_in": 3},
            "independent_verifier": {
                "artifact": {"verdict": "PASS"},
            },
        },
        "general_work": {
            "verdict": "PASS",
            "general_work_planner": {
                "status": "PASS",
                "plans": {
                    "D": {"inferred_formats": ["xlsx", "docx"]},
                },
            },
            "proofs": {"C": {"status": "PASS"}},
            "duplicate_mutations": {
                "partial_pdf_duplicate_mutations": 0,
            },
            "stop": {"action_starts_during_stop": 0},
            "independent_verifier": {"status": "PASS"},
        },
        "computer_operator": {
            "integrity": {
                "native_save_dispatches": 1,
                "upload_requests": 1,
                "ambiguous_effect_reconciled": True,
            },
        },
        "world_intelligence": {
            "proofs": {
                "A": {
                    "final": {
                        "recommendation": {"status": "PASS"},
                    },
                },
                "D": {
                    "action": "WAIT_AND_RESEARCH_LATER",
                    "recommendation": {
                        "status": "INSUFFICIENT_EVIDENCE",
                    },
                    "dossier": {
                        "freshness": {"status": "STALE"},
                    },
                },
            },
        },
        "chief_of_staff": {
            "checks": {
                "08_low_value_urgent_work_is_rejected": True,
                "09_adjacent_opportunity_waits_for_authority": True,
                "19_command_center_packet_match_and_gets_are_read_only": True,
            },
            "counts": {
                "candidates": 1,
                "candidate_routed": 1,
            },
            "artifacts": {
                "briefing_content_digest": "briefing-digest",
            },
        },
        "command_center": {
            "all_checks_pass": True,
        },
    }


def test_shared_spine_proves_owner_paths_worker_stop_and_restart(
    tmp_path: Path,
) -> None:
    result = proof._run_spine_proof(tmp_path / "proof", _receipts())

    assert result["schema"] == 40
    assert result["ledger"]["quick_check"] == "ok"
    assert result["ledger"]["events"] == result["ledger"][
        "integrity_verified_events"
    ]
    assert all(
        item["status"] == "PASS"
        for item in result["proofs"].values()
    )
    assert result["proofs"]["A"]["owner_text"] == "Fix it."
    assert result["proofs"]["A"]["verifier_fail"]["verdict"] == "FAIL"
    assert result["proofs"]["A"]["verifier_pass"]["verdict"] == "PASS"
    assert result["proofs"]["C"]["worker"]["package_status"] == "ACCEPTED"
    assert result["proofs"]["C"]["worker"]["replayed"] is True
    assert result["proofs"]["E"]["dispatches_during_stop"] == 0
    assert result["proofs"]["F"]["command_center_source_backed"] is True
    assert set(result["proofs"]["F"]["exact_owner_texts"]) >= set(
        proof.OWNER_TEXT.values()
    )
    assert all(
        value == 0 for value in result["duplicate_counters"].values()
    )


def test_receipt_validation_rejects_wrong_source() -> None:
    receipts = _receipts()
    receipts.update(
        {
            "owner_context": {
                "passed": True,
                "checks": {"proof": True},
                "source_attestations": {"source": True},
                "source": {"source_sha": "1" * 40},
            },
            "personal_operator": {
                "verdict": "PASS",
                "source_sha": "1" * 40,
                "assertions": {"proof": True},
                "independent_verifier": {
                    "artifact": {"verdict": "PASS"},
                },
            },
            "general_work": {
                **receipts["general_work"],
                "source": {"source_sha": "1" * 40},
            },
            "computer_operator": {
                "verdict": "PASS",
                "source": {"head": "1" * 40},
                "integrity": {
                    "upload_requests": 1,
                    "native_save_dispatches": 1,
                    "browser_ambiguity_reconciled_after_runtime_restart": True,
                    "same_executable_decoy_survived": True,
                },
            },
            "world_intelligence": {
                **receipts["world_intelligence"],
                "contract": "jarvis.v13.world-intelligence-proof.v1",
                "source_sha": "1" * 40,
                "schema": 40,
                "boundaries": {"live_mutated": False},
            },
            "world_verifier": {
                "verdict": "PASS",
                "source_sha": "1" * 40,
                "failures": [],
            },
            "chief_of_staff": {
                **receipts["chief_of_staff"],
                "passed": True,
                "source": {"source_sha": "1" * 40},
                "checks": {
                    **receipts["chief_of_staff"]["checks"],
                    "all": True,
                },
            },
            "chief_verifier": {
                "verdict": "PASS",
                "source_sha": "1" * 40,
                "findings": [],
            },
            "command_center": {
                "all_checks_pass": True,
                "checks": {"proof": True},
            },
        }
    )

    checks = proof._validate_receipts(receipts, source_sha="1" * 40)
    assert set(checks) == set(proof.RECEIPT_KEYS)
    assert all(checks.values())

    try:
        proof._validate_receipts(receipts, source_sha="2" * 40)
    except proof.ProofFailure as error:
        assert "lane receipt checks failed" in str(error)
    else:
        raise AssertionError("wrong source receipts were accepted")
