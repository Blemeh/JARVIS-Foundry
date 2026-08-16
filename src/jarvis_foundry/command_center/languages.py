"""Embedded English and Albanian UI strings for the Command Center."""

from __future__ import annotations

SUPPORTED_LANGS: tuple[str, ...] = ("en", "sq")

UI_LABELS: dict[str, dict[str, str]] = {
    "app_title": {"en": "JARVIS Command Center", "sq": "Qendra e Komandës JARVIS"},
    "app_subtitle": {
        "en": "Real durable state from isolated proving stores",
        "sq": "Gjendje reale e qëndrueshme nga depot e izoluara",
    },
    "status": {"en": "Status", "sq": "Statusi"},
    "nav_overview": {"en": "Overview", "sq": "Përmbledhje"},
    "nav_missions": {"en": "Missions", "sq": "Misionet"},
    "nav_timeline": {"en": "Timeline", "sq": "Kronologjia"},
    "nav_evidence": {"en": "Evidence", "sq": "Dëshmitë"},
    "control_title": {
        "en": "Owner directive control",
        "sq": "Kontrolli i direktivave të pronarit",
    },
    "control_note": {
        "en": "The UI submits; the durable directive ledger and PortfolioControl are the authority.",
        "sq": "UI-ja dërgon; regjistri i qëndrueshëm i direktivave dhe PortfolioControl janë autoriteti.",
    },
    "directive_stop": {"en": "STOP", "sq": "STOP"},
    "directive_veto": {"en": "VETO", "sq": "VETO"},
    "directive_pause": {"en": "PAUSE", "sq": "PAUZË"},
    "directive_resume": {"en": "RESUME", "sq": "RIFILLO"},
    "directive_reprioritize": {"en": "REPRIORITIZE", "sq": "RIPRIORITIZO"},
    "directive_redirect": {"en": "REDIRECT", "sq": "RIDREJTO"},
    "target": {"en": "Target", "sq": "Objektivi"},
    "reason": {"en": "Reason", "sq": "Arsyeja"},
    "value": {"en": "Value", "sq": "Vlera"},
    "nonce": {"en": "Nonce", "sq": "Nonce"},
    "submit_directive": {"en": "Submit directive", "sq": "Dërgo direktivën"},
    "briefing_title": {"en": "Briefings", "sq": "Përmbledhjet"},
    "briefing_en": {"en": "English", "sq": "Anglisht"},
    "briefing_sq": {"en": "Albanian", "sq": "Shqip"},
    "section_goals": {"en": "Goals", "sq": "Qëllimet"},
    "section_projects": {"en": "Projects", "sq": "Projektet"},
    "section_missions": {"en": "Missions", "sq": "Misionet"},
    "section_decisions": {
        "en": "Candidate decisions",
        "sq": "Vendimet e kandidatëve",
    },
    "section_rejected": {
        "en": "Rejected alternatives",
        "sq": "Alternativat e refuzuara",
    },
    "section_workers": {"en": "Workers", "sq": "Punëtorët"},
    "section_verification": {"en": "Verification", "sq": "Verifikimi"},
    "section_findings": {"en": "Findings", "sq": "Gjetjet"},
    "section_releases": {"en": "Releases", "sq": "Publikimet"},
    "section_capabilities": {"en": "Capabilities", "sq": "Aftësitë"},
    "section_costs": {"en": "Costs", "sq": "Kostot"},
    "section_resources": {"en": "Resources", "sq": "Burimet"},
    "section_evidence": {"en": "Evidence", "sq": "Dëshmitë"},
    "section_health": {"en": "Health", "sq": "Shëndeti"},
    "section_incidents": {"en": "Incidents", "sq": "Incidentet"},
    "section_waiting": {
        "en": "Waiting / blockers",
        "sq": "Në pritje / bllokime",
    },
    "section_next": {
        "en": "Next autonomous actions",
        "sq": "Veprimet e ardhshme autonome",
    },
    "section_timeline": {
        "en": "Decision and effect timeline",
        "sq": "Kronologjia e vendimeve dhe efekteve",
    },
    "empty_state": {
        "en": "No durable records yet",
        "sq": "Ende pa regjistrime të qëndrueshme",
    },
    "stale_state": {"en": "Stale state", "sq": "Gjendje e vjetëruar"},
    "degraded_state": {"en": "Degraded", "sq": "E degraduar"},
    "provider_failure": {
        "en": "Provider failure recorded",
        "sq": "Dështim i ofruesit i regjistruar",
    },
    "evidence_drilldown": {
        "en": "Evidence drill-down",
        "sq": "Detajet e dëshmisë",
    },
    "close": {"en": "Close", "sq": "Mbylle"},
    "refresh": {"en": "Refresh", "sq": "Rifresko"},
    "authority_note": {
        "en": "Authority: durable stores",
        "sq": "Autoriteti: depot e qëndrueshme",
    },
    "generated_at": {"en": "Generated", "sq": "Krijuar më"},
    "live_workers": {"en": "Live workers", "sq": "Punëtorë aktivë"},
    "language": {"en": "Language", "sq": "Gjuha"},
    "english": {"en": "English", "sq": "Anglisht"},
    "albanian": {"en": "Albanian", "sq": "Shqip"},
    "not_available": {"en": "NOT AVAILABLE", "sq": "E PADISPONUESHME"},
    "unknown": {"en": "UNKNOWN", "sq": "E PANJOHUR"},
    "data_quality": {"en": "Data quality", "sq": "Cilësia e të dhënave"},
    "voice_status": {"en": "Voice status", "sq": "Statusi i zërit"},
    "provider_failure_badge": {
        "en": "Provider failure",
        "sq": "Dështim i ofruesit",
    },
}


def localize(lang: str, key: str, fallback: str | None = None) -> str:
    if lang not in SUPPORTED_LANGS:
        lang = "en"
    value = UI_LABELS.get(key, {}).get(lang)
    if value is not None:
        return value
    return fallback if fallback is not None else key


def is_supported_lang(lang: str) -> bool:
    return lang in SUPPORTED_LANGS
