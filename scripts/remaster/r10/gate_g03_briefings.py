"""R10-G03: briefings distinguish six categories in English and Albanian."""

from __future__ import annotations

import sys
import time
from pathlib import Path

from common import EVIDENCE_DIR, EVIDENCE_UTC, REPO_ROOT, sha256_file, write_json

from jarvis_foundry.command_center.briefings import generate_briefing
from jarvis_foundry.command_center.proving import ProvingLayout
from jarvis_foundry.command_center.seeding import seed_proving_stores
from jarvis_foundry.command_center.state import build_r10_snapshot

EN_PHRASES = (
    "Supervisor briefing - JARVIS Foundry",
    "Fact",
    "Uncertainty",
    "Recommendation",
    "Action",
    "Cost",
    "Missing evidence",
)
SQ_PHRASES = (
    "Përmbledhja e Supervizorit - JARVIS Foundry",
    "Fakt",
    "Pasiguri",
    "Rekomandim",
    "Veprim",
    "Kosto",
    "Dëshmi e munguar",
)


def main(argv: list[str] | None = None) -> int:
    del argv
    started = time.monotonic()
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    layout = ProvingLayout(Path("C:/Users/b/AppData/Local/Temp/jarvis-r10-builder"))
    seed_proving_stores(layout, REPO_ROOT / "docs" / "remaster" / "evidence")
    snapshot = build_r10_snapshot(layout, now=EVIDENCE_UTC)
    english = generate_briefing(snapshot, "en")
    albanian = generate_briefing(snapshot, "sq")
    en_text = english["text"]
    sq_text = albanian["text"]
    albanian_fragments = (
        "Faza r9 statusi:",
        "Totali i matur në USD është",
        "Mbajeni të mbyllur paketën e provave",
        "matje buxheti",
        "Faturat reale STT/TTS:",
        "Shenjat e shëndetit:",
        "Nuk ka fatura reale USD nga ofruesi",
    )
    english_fragments = (
        "Keep the R10 builder evidence package closed",
        "Measured USD total is",
        "No real provider USD receipts exist",
        "accepted missions, ",
        "active capabilities, ",
        "Real STT/TTS receipts:",
        "Health flags:",
    )
    checks = {
        "six_categories_en": len(english["categories"]) == 6,
        "six_categories_sq": len(albanian["categories"]) == 6,
        "en_correct_language": all(phrase in en_text for phrase in EN_PHRASES),
        "sq_correct_language": all(phrase in sq_text for phrase in SQ_PHRASES),
        "no_albanian_leak_in_en": "Përmbledhja" not in en_text,
        "no_english_header_in_sq": "Supervisor briefing - JARVIS Foundry" not in sq_text,
        "sq_body_is_albanian": all(fragment in sq_text for fragment in albanian_fragments),
        "sq_body_has_no_english_templates": all(
            fragment not in sq_text for fragment in english_fragments
        ),
        "per_language_content": english["content_hash"] != albanian["content_hash"],
        "evidence_refs_present": bool(english["evidence_refs"]),
        "evidence_refs_resolve": all(
            ref.get("record_id") in snapshot["evidence_index"] for ref in english["evidence_refs"]
        ),
        "from_durable_state": bool(snapshot["projects"] and snapshot["missions"]),
    }
    passed = all(checks.values())
    en_path = EVIDENCE_DIR / "R10_BRIEFING_EN.txt"
    sq_path = EVIDENCE_DIR / "R10_BRIEFING_SQ.txt"
    json_path = EVIDENCE_DIR / "R10_BRIEFINGS.json"
    en_path.write_text(en_text, encoding="utf-8")
    sq_path.write_text(sq_text, encoding="utf-8")
    write_json(json_path, {"english": english, "albanian": albanian})
    payload = {
        "schema": "jarvis-r10-g03-briefings-v1",
        "evidence_utc": EVIDENCE_UTC,
        "exit_code": 0 if passed else 1,
        "wall_seconds": round(time.monotonic() - started, 3),
        "passed": passed,
        "checks": checks,
        "outputs": {
            "english": "R10_BRIEFING_EN.txt",
            "albanian": "R10_BRIEFING_SQ.txt",
            "json": "R10_BRIEFINGS.json",
            "english_sha256": sha256_file(en_path),
            "albanian_sha256": sha256_file(sq_path),
            "json_sha256": sha256_file(json_path),
        },
        "evidence": ["R10_BRIEFING_EN.txt", "R10_BRIEFING_SQ.txt", "R10_BRIEFINGS.json"],
    }
    write_json(EVIDENCE_DIR / "R10_G03_BRIEFINGS.json", payload)
    print(
        f"g03 briefings: passed={passed} checks={sum(checks.values())}/{len(checks)} "
        f"wall_seconds={payload['wall_seconds']}"
    )
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
