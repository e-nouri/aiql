import math

from api.models import ParseResult, ScoreSignals, ScrapeResult
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
from worker.scoring.patterns import GATING_RE, JS_REQUIRED_RE, NLTK_LANG_MAP, get_stopwords


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
    signals.i18n_supported = lang_code in NLTK_LANG_MAP
    if words and signals.i18n_supported:
        sw = get_stopwords(lang_code)
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
