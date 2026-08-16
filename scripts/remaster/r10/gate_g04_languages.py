"""R10-G04: real English and Albanian interaction through the Command Center."""

from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from common import EVIDENCE_DIR, EVIDENCE_UTC, REPO_ROOT, sha256_file, write_json

from jarvis_foundry.command_center.proving import ProvingLayout
from jarvis_foundry.command_center.seeding import seed_proving_stores
from jarvis_foundry.command_center.server import serve


def _get(base: str, path: str) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(f"{base}{path}", timeout=15) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8")


def main(argv: list[str] | None = None) -> int:
    del argv
    started = time.monotonic()
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    layout = ProvingLayout(Path("C:/Users/b/AppData/Local/Temp/jarvis-r10-builder"))
    seed_proving_stores(layout, REPO_ROOT / "docs" / "remaster" / "evidence")
    running = serve(layout, evidence_root=REPO_ROOT / "docs" / "remaster" / "evidence", seed=False)
    base = running.base_url
    try:
        en_status, en_html = _get(base, "/")
        sq_status, sq_html = _get(base, "/?lang=sq")
        en_briefing_status, en_briefing = _get(base, "/api/briefings?lang=en")
        sq_briefing_status, sq_briefing = _get(base, "/api/briefings?lang=sq")
        bad_status, bad_body = _get(base, "/api/briefings?lang=de")
        en_briefing_json = json.loads(en_briefing)
        sq_briefing_json = json.loads(sq_briefing)
        checks = {
            "english_page_ok": en_status == 200 and '<html lang="en">' in en_html,
            "english_page_content": "JARVIS Command Center" in en_html
            and "Owner directive control" in en_html,
            "albanian_page_ok": sq_status == 200 and '<html lang="sq">' in sq_html,
            "albanian_page_content": "Qendra e Komandës JARVIS" in sq_html
            and "Kontrolli i direktivave të pronarit" in sq_html
            and "Misionet" in sq_html,
            "english_briefing_ok": en_briefing_status == 200
            and en_briefing_json["lang"] == "en"
            and en_briefing_json["categories"][0]["category"] == "Fact",
            "albanian_briefing_ok": sq_briefing_status == 200
            and sq_briefing_json["lang"] == "sq"
            and sq_briefing_json["categories"][0]["category"] == "Fakt"
            and "Faza" in sq_briefing_json["categories"][0]["text"],
            "per_language_content": (
                en_briefing_json["content_hash"] != sq_briefing_json["content_hash"]
            ),
            "unsupported_language_rejected": bad_status == 400,
        }
        passed = all(checks.values())
        en_path = EVIDENCE_DIR / "R10_LANGUAGES_EN.html"
        sq_path = EVIDENCE_DIR / "R10_LANGUAGES_SQ.html"
        en_path.write_text(en_html, encoding="utf-8")
        sq_path.write_text(sq_html, encoding="utf-8")
        payload = {
            "schema": "jarvis-r10-g04-languages-v1",
            "evidence_utc": EVIDENCE_UTC,
            "exit_code": 0 if passed else 1,
            "wall_seconds": round(time.monotonic() - started, 3),
            "passed": passed,
            "checks": checks,
            "endpoints": {
                "english_page": {"status": en_status, "html": "R10_LANGUAGES_EN.html"},
                "albanian_page": {"status": sq_status, "html": "R10_LANGUAGES_SQ.html"},
                "english_briefing": {"status": en_briefing_status},
                "albanian_briefing": {"status": sq_briefing_status},
                "unsupported_language": {"status": bad_status, "body": bad_body[:200]},
            },
            "outputs": {
                "english_html": "R10_LANGUAGES_EN.html",
                "albanian_html": "R10_LANGUAGES_SQ.html",
                "english_html_sha256": sha256_file(en_path),
                "albanian_html_sha256": sha256_file(sq_path),
            },
            "evidence": ["R10_LANGUAGES_EN.html", "R10_LANGUAGES_SQ.html"],
        }
        write_json(EVIDENCE_DIR / "R10_G04_LANGUAGES.json", payload)
        print(
            f"g04 languages: passed={passed} checks={sum(checks.values())}/{len(checks)} "
            f"wall_seconds={payload['wall_seconds']}"
        )
        return 0 if passed else 1
    finally:
        running.close()


if __name__ == "__main__":
    sys.exit(main())
