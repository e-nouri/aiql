import math
import re

from valkey.asyncio import Valkey

from api.models import (
    ParseResult,
    ScoreResult,
    ScoreSignals,
    ScrapeResult,
    StepEvent,
)
from config import (
    ENTROPY_MAX,
    ENTROPY_MIN,
    FAST_RESPONSE_THRESHOLD,
    LANGUAGE_CONFIDENCE_THRESHOLD,
    LINK_SCORE_MAX,
    LINK_SCORE_MULTIPLIER,
    MAX_REDIRECT_CHAIN,
    NER_TEXT_LIMIT,
    STOPWORD_RATIO_MAX,
    STOPWORD_RATIO_MIN,
    TEXT_DENSITY_THRESHOLD,
    TFIDF_DOMINANCE_THRESHOLD,
    TFIDF_MIN_WORDS,
)
from nltk.corpus import stopwords as nltk_stopwords
from worker.helpers import publish, store_step

# Map lingua ISO 639-1 codes to NLTK language names
_NLTK_LANG_MAP = {
    "ar": "arabic", "az": "azerbaijani", "da": "danish", "de": "german",
    "el": "greek", "en": "english", "es": "spanish", "fi": "finnish",
    "fr": "french", "hu": "hungarian", "id": "indonesian", "it": "italian",
    "kk": "kazakh", "nb": "norwegian", "nl": "dutch", "pt": "portuguese",
    "ro": "romanian", "ru": "russian", "sl": "slovene", "sv": "swedish",
    "tg": "tajik", "tr": "turkish",
}


def _get_stopwords(lang_code: str | None) -> set[str]:
    """Get NLTK stopwords for the detected language, fallback to English."""
    lang = _NLTK_LANG_MAP.get(lang_code, "english")
    try:
        return set(nltk_stopwords.words(lang))
    except OSError:
        return set(nltk_stopwords.words("english"))

JS_REQUIRED_RE = re.compile(
    r"enable\s+(javascript|js)"
    r"|javascript\s+(is\s+)?required"
    r"|requires?\s+javascript"
    r"|disable\s+(your\s+)?(ad\s*block|ad\s+blocker)"
    r"|ad\s*block\w*\s+detected",
    re.IGNORECASE,
)

GATING_RE = re.compile(
    r"captcha"
    r"|cloudflare.{0,20}challenge"
    r"|cf-browser-verification"
    r"|access\s+denied"
    r"|403\s+forbidden"
    r"|login\s+required",
    re.IGNORECASE,
)


def compute_signals(
    scrape: ScrapeResult | None,
    parse: ParseResult | None,
    meta: dict,
    ctx: dict,
) -> ScoreSignals:
    signals = ScoreSignals()
    if not scrape:
        return signals

    text = scrape.text_content
    raw_html = meta.get("_raw_html", "")
    raw_html_len = int(meta.get("_raw_html_len", "0"))
    response_time = float(meta.get("_response_time", "999"))
    redirect_count = int(meta.get("_redirect_count", "0"))
    original_url = meta.get("_original_url", "")

    # 1. HTTP 200
    signals.http_ok = scrape.status_code == 200

    # 2. No redirect
    signals.no_redirect = str(scrape.final_url).rstrip("/") == original_url.rstrip("/")

    # 3. Short redirect chain
    signals.short_redirect_chain = redirect_count <= MAX_REDIRECT_CHAIN

    # 4. Fast response
    signals.fast_response = response_time < FAST_RESPONSE_THRESHOLD

    # 5. Has title
    signals.has_title = bool(scrape.title)

    # 6. Has meta description
    if parse:
        signals.has_meta_description = bool(parse.meta_description)

    # 7. JS required — if true, nothing else counts
    signals.js_gated = bool(JS_REQUIRED_RE.search(raw_html))
    if signals.js_gated:
        return signals

    # 8. Not content-gated
    signals.not_gated = (
        not GATING_RE.search(raw_html)
        and scrape.status_code != 403
    )

    # 9. Text-to-HTML density
    if raw_html_len > 0:
        density = len(text) / raw_html_len
        signals.good_text_density = density > TEXT_DENSITY_THRESHOLD

    # 10. Language confidence
    if parse and parse.language_confidence is not None:
        signals.language_confident = parse.language_confidence > LANGUAGE_CONFIDENCE_THRESHOLD

    # 11. i18n support + Stopword ratio
    words = text.lower().split()
    lang_code = parse.language if parse else None
    signals.i18n_supported = lang_code in _NLTK_LANG_MAP
    if words and signals.i18n_supported:
        sw = _get_stopwords(lang_code)
        stop_count = sum(1 for w in words if w in sw)
        ratio = stop_count / len(words)
        signals.good_stopword_ratio = STOPWORD_RATIO_MIN <= ratio <= STOPWORD_RATIO_MAX

    # 12. Shannon entropy
    if text:
        freq = {}
        for ch in text:
            freq[ch] = freq.get(ch, 0) + 1
        total = len(text)
        entropy = -sum((c / total) * math.log2(c / total) for c in freq.values())
        signals.normal_entropy = ENTROPY_MIN <= entropy <= ENTROPY_MAX

    # 13. Term frequency balance
    if words and len(words) > TFIDF_MIN_WORDS:
        word_freq = {}
        for w in words:
            word_freq[w] = word_freq.get(w, 0) + 1
        max_freq = max(word_freq.values())
        signals.balanced_tfidf = (max_freq / len(words)) < TFIDF_DOMINANCE_THRESHOLD

    # 14. NER entity count (pre-loaded in ctx)
    try:
        nlp = ctx["nlp"]
        doc = nlp(text[:NER_TEXT_LIMIT])
        signals.has_entities = len(doc.ents) > 0
    except Exception:
        signals.has_entities = False

    # 15. Outbound links
    if parse:
        link_count = len(parse.outbound_links)
        signals.outbound_link_score = min(link_count * LINK_SCORE_MULTIPLIER, LINK_SCORE_MAX)

    return signals


def build_rationale(signals: ScoreSignals) -> str:
    """Generate rationale from the worst signals."""
    if signals.js_gated:
        return "page requires JavaScript"

    issues = []
    if not signals.http_ok:
        issues.append("non-200 status")
    if not signals.not_gated:
        issues.append("content gated")
    if not signals.good_text_density:
        issues.append("low text density")
    if not signals.has_title:
        issues.append("no title")
    if not signals.has_entities:
        issues.append("no entities detected")
    if not signals.i18n_supported:
        issues.append("i18n not supported")
    if not signals.language_confident:
        issues.append("low language confidence")
    if not signals.normal_entropy:
        issues.append("abnormal text entropy")
    if not signals.balanced_tfidf:
        issues.append("single term dominates")
    if not issues:
        return "all signals passed"
    return "; ".join(issues[:3])


async def score(vk: Valkey, job_id: str, scrape: ScrapeResult | None, parse: ParseResult | None, ctx: dict) -> ScoreResult | None:
    await publish(vk, job_id, StepEvent(
        job_id=job_id, step="score", status="started",
        message="Running heuristics...",
    ))
    try:
        meta = await vk.hgetall(f"job:{job_id}:results")

        await publish(vk, job_id, StepEvent(
            job_id=job_id, step="score", status="progress",
            message="Computing signals...",
        ))

        signals = compute_signals(scrape, parse, meta, ctx)

        bool_signals = [
            signals.http_ok, signals.no_redirect, signals.short_redirect_chain,
            signals.fast_response, signals.has_title, signals.has_meta_description,
            signals.not_gated, signals.good_text_density, signals.language_confident,
            signals.good_stopword_ratio, signals.normal_entropy, signals.balanced_tfidf,
            signals.has_entities,
        ]
        raw = sum(1 for s in bool_signals if s) + signals.outbound_link_score
        final_score = round((raw / 14) * 100)

        rationale = build_rationale(signals)

        result = ScoreResult(score=final_score, rationale=rationale, signals=signals)
        await store_step(vk, job_id, "score", result)
        await publish(vk, job_id, StepEvent(
            job_id=job_id, step="score", status="completed",
            message=f"Score: {final_score}/100 — {rationale}", payload=result,
        ))
        return result
    except Exception as e:
        await publish(vk, job_id, StepEvent(
            job_id=job_id, step="score", status="error",
            message=str(e),
        ))
        return None
