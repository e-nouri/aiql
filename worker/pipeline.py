import asyncio
import os
import time

import httpx
from bs4 import BeautifulSoup
from valkey.asyncio import Valkey

from api.models import (
    EnrichResult,
    ParseResult,
    ScoreResult,
    ScoreSignals,
    ScrapeResult,
    StepEvent,
)

VALKEY_URL = os.getenv("VALKEY_URL", "valkey://localhost:6379")


async def _get_valkey() -> Valkey:
    return Valkey.from_url(VALKEY_URL, decode_responses=True)


async def _publish(vk: Valkey, job_id: str, event: StepEvent) -> None:
    await vk.publish(f"job:{job_id}:events", event.model_dump_json())


async def _store_step(vk: Valkey, job_id: str, key: str, result) -> None:
    await vk.hset(f"job:{job_id}:results", key, result.model_dump_json())


async def _preflight(vk: Valkey, job_id: str, url: str) -> bool:
    """Check MIME type via HEAD — reject non-HTML content."""
    await _publish(vk, job_id, StepEvent(
        job_id=job_id, step="preflight", status="started",
        message="Checking MIME type...",
    ))
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=10) as client:
            resp = await client.head(url)
            content_type = resp.headers.get("content-type", "")
            if "text/html" not in content_type and "text/plain" not in content_type:
                await vk.hset(f"job:{job_id}:results", "status", "rejected")
                await _publish(vk, job_id, StepEvent(
                    job_id=job_id, step="preflight", status="error",
                    message=f"Rejected: {content_type} — not supported yet",
                ))
                return False
        await _publish(vk, job_id, StepEvent(
            job_id=job_id, step="preflight", status="completed",
            message=f"{content_type.split(';')[0]} — accepted",
        ))
        return True
    except Exception as e:
        await vk.hset(f"job:{job_id}:results", "status", "rejected")
        await _publish(vk, job_id, StepEvent(
            job_id=job_id, step="preflight", status="error",
            message=f"Preflight failed: {e}",
        ))
        return False


async def _scrape(vk: Valkey, job_id: str, url: str) -> ScrapeResult | None:
    await _publish(vk, job_id, StepEvent(
        job_id=job_id, step="scrape", status="started",
        message="Fetching URL...",
    ))
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            start = time.monotonic()
            resp = await client.get(url)
            elapsed = time.monotonic() - start

        await _publish(vk, job_id, StepEvent(
            job_id=job_id, step="scrape", status="progress",
            message=f"Fetched in {elapsed:.2f}s — parsing HTML...",
        ))

        soup = BeautifulSoup(resp.text, "html.parser")

        title_tag = soup.find("title")
        title = title_tag.get_text(strip=True) if title_tag else None

        # Remove script/style before extracting text
        for tag in soup(["script", "style", "noscript"]):
            tag.decompose()
        text_content = soup.get_text(separator=" ", strip=True)

        result = ScrapeResult(
            final_url=str(resp.url),
            status_code=resp.status_code,
            title=title,
            text_content=text_content,
        )
        await _store_step(vk, job_id, "scrape", result)

        # Store metadata for parse + scoring steps
        await vk.hset(f"job:{job_id}:results", mapping={
            "_response_time": str(elapsed),
            "_raw_html": resp.text,
            "_raw_html_len": str(len(resp.text)),
            "_redirect_count": str(len(resp.history)),
            "_original_url": url,
        })
        await _publish(vk, job_id, StepEvent(
            job_id=job_id, step="scrape", status="completed",
            message="Scrape complete", payload=result,
        ))
        return result
    except Exception as e:
        await _publish(vk, job_id, StepEvent(
            job_id=job_id, step="scrape", status="error",
            message=str(e),
        ))
        return None


async def _parse(vk: Valkey, job_id: str, scrape: ScrapeResult) -> ParseResult | None:
    await _publish(vk, job_id, StepEvent(
        job_id=job_id, step="parse", status="started",
        message="Extracting metadata...",
    ))
    try:
        words = scrape.text_content.split()
        word_count = len(words)

        await _publish(vk, job_id, StepEvent(
            job_id=job_id, step="parse", status="progress",
            message=f"Word count: {word_count} — detecting language...",
        ))

        # Language detection
        from lingua import LanguageDetectorBuilder
        detector = LanguageDetectorBuilder.from_all_languages().build()
        confidence_values = detector.compute_language_confidence_values(scrape.text_content)
        if confidence_values:
            best = confidence_values[0]
            language = best.language.iso_code_639_1.name.lower()
            language_confidence = best.value
        else:
            language = None
            language_confidence = None

        await _publish(vk, job_id, StepEvent(
            job_id=job_id, step="parse", status="progress",
            message=f"Language: {language} ({language_confidence:.2f}) — extracting links...",
        ))

        # Meta description + links from raw HTML stored during scrape
        raw_html = await vk.hget(f"job:{job_id}:results", "_raw_html")
        full_soup = BeautifulSoup(raw_html, "html.parser")

        meta_tag = full_soup.find("meta", attrs={"name": "description"})
        meta_description = meta_tag["content"] if meta_tag and meta_tag.get("content") else None

        # Outbound links (up to 10)
        links = []
        for a in full_soup.find_all("a", href=True):
            href = a["href"]
            if href.startswith("http") and len(links) < 10:
                links.append(href)

        result = ParseResult(
            word_count=word_count,
            language=language,
            language_confidence=language_confidence,
            meta_description=meta_description,
            outbound_links=links,
        )
        await _store_step(vk, job_id, "parse", result)
        await _publish(vk, job_id, StepEvent(
            job_id=job_id, step="parse", status="completed",
            message="Parse complete", payload=result,
        ))
        return result
    except Exception as e:
        await _publish(vk, job_id, StepEvent(
            job_id=job_id, step="parse", status="error",
            message=str(e),
        ))
        return None


def _compute_signals(
    scrape: ScrapeResult | None,
    parse: ParseResult | None,
    meta: dict,
) -> ScoreSignals:
    signals = ScoreSignals()
    if not scrape:
        return signals

    text = scrape.text_content
    raw_html_len = int(meta.get("_raw_html_len", "0"))
    response_time = float(meta.get("_response_time", "999"))
    redirect_count = int(meta.get("_redirect_count", "0"))
    original_url = meta.get("_original_url", "")

    # 1. HTTP 200
    signals.http_ok = scrape.status_code == 200

    # 2. No redirect
    signals.no_redirect = str(scrape.final_url).rstrip("/") == original_url.rstrip("/")

    # 3. Short redirect chain
    signals.short_redirect_chain = redirect_count <= 1

    # 4. Fast response
    signals.fast_response = response_time < 2.0

    # 5. Has title
    signals.has_title = bool(scrape.title)

    # 6. Has meta description
    if parse:
        signals.has_meta_description = bool(parse.meta_description)

    # 7. Not content-gated
    gating_markers = ["captcha", "cloudflare", "access denied", "403 forbidden", "login required", "subscribe"]
    lower_text = text.lower()
    signals.not_gated = not any(m in lower_text for m in gating_markers) and scrape.status_code != 403

    # 8. Text-to-HTML density
    if raw_html_len > 0:
        density = len(text) / raw_html_len
        signals.good_text_density = density > 0.5

    # 9. Language confidence
    if parse and parse.language_confidence is not None:
        signals.language_confident = parse.language_confidence > 0.8

    # 10. Stopword ratio
    words = text.lower().split()
    if words:
        # Language-agnostic common stopwords (function words that appear in many languages)
        stopwords = {"the", "a", "an", "is", "are", "was", "were", "in", "on", "at", "to", "for",
                     "of", "and", "or", "but", "not", "with", "it", "this", "that", "be", "as",
                     "de", "la", "le", "et", "en", "les", "des", "un", "une", "du", "el", "y",
                     "der", "die", "das", "und", "in", "von", "zu", "den", "mit"}
        stop_count = sum(1 for w in words if w in stopwords)
        ratio = stop_count / len(words)
        signals.good_stopword_ratio = 0.25 <= ratio <= 0.50

    # 11. Shannon entropy
    import math
    if text:
        freq = {}
        for ch in text:
            freq[ch] = freq.get(ch, 0) + 1
        total = len(text)
        entropy = -sum((c / total) * math.log2(c / total) for c in freq.values())
        # Normal English/text entropy is roughly 3.5–5.0
        signals.normal_entropy = 3.0 <= entropy <= 5.5

    # 12. TF-IDF balance
    if words and len(words) > 10:
        word_freq = {}
        for w in words:
            word_freq[w] = word_freq.get(w, 0) + 1
        max_freq = max(word_freq.values())
        # If the most frequent word is > 10% of all words, it dominates
        signals.balanced_tfidf = (max_freq / len(words)) < 0.10

    # 13. NER entity count (uses spaCy — loaded at warmup)
    try:
        import spacy
        nlp = spacy.load("xx_ent_wiki_sm", disable=["tagger", "parser", "senter", "attribute_ruler", "lemmatizer"])
        # Limit text length to avoid slow processing
        doc = nlp(text[:5000])
        signals.has_entities = len(doc.ents) > 0
    except Exception:
        signals.has_entities = False

    # 14. Outbound links
    if parse:
        link_count = len(parse.outbound_links)
        signals.outbound_link_score = min(link_count * 0.1, 1.0)

    return signals


def _lowest_rationale(signals: ScoreSignals) -> str:
    """Generate rationale from the worst signals."""
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
    if not signals.language_confident:
        issues.append("low language confidence")
    if not signals.normal_entropy:
        issues.append("abnormal text entropy")
    if not signals.balanced_tfidf:
        issues.append("single term dominates")
    if not issues:
        return "all signals passed"
    return "; ".join(issues[:3])


async def _score(vk: Valkey, job_id: str, scrape: ScrapeResult | None, parse: ParseResult | None) -> ScoreResult | None:
    await _publish(vk, job_id, StepEvent(
        job_id=job_id, step="score", status="started",
        message="Running heuristics...",
    ))
    try:
        meta = await vk.hgetall(f"job:{job_id}:results")

        await _publish(vk, job_id, StepEvent(
            job_id=job_id, step="score", status="progress",
            message="Computing signals...",
        ))

        signals = _compute_signals(scrape, parse, meta)

        # Calculate score: sum boolean signals + outbound_link_score, normalize to 0-100
        bool_signals = [
            signals.http_ok, signals.no_redirect, signals.short_redirect_chain,
            signals.fast_response, signals.has_title, signals.has_meta_description,
            signals.not_gated, signals.good_text_density, signals.language_confident,
            signals.good_stopword_ratio, signals.normal_entropy, signals.balanced_tfidf,
            signals.has_entities,
        ]
        raw = sum(1 for s in bool_signals if s) + signals.outbound_link_score
        score = round((raw / 14) * 100)

        rationale = _lowest_rationale(signals)

        result = ScoreResult(score=score, rationale=rationale, signals=signals)
        await _store_step(vk, job_id, "score", result)
        await _publish(vk, job_id, StepEvent(
            job_id=job_id, step="score", status="completed",
            message=f"Score: {score}/100 — {rationale}", payload=result,
        ))
        return result
    except Exception as e:
        await _publish(vk, job_id, StepEvent(
            job_id=job_id, step="score", status="error",
            message=str(e),
        ))
        return None


async def run_pipeline(ctx: dict, job_id: str, url: str) -> None:
    vk = await _get_valkey()

    try:
        await vk.hset(f"job:{job_id}:results", "status", "running")

        # Pre-flight
        accepted = await _preflight(vk, job_id, url)
        if not accepted:
            return

        # Pipeline steps — continue even if a step fails
        scrape = await _scrape(vk, job_id, url)
        parse = await _parse(vk, job_id, scrape) if scrape else None
        score = await _score(vk, job_id, scrape, parse)

        # Build final result
        enriched = EnrichResult(
            job_id=job_id,
            url=url,
            word_count=parse.word_count if parse else 0,
            i18n=parse.language if parse else None,
            meta_description=parse.meta_description if parse else None,
            links=parse.outbound_links if parse else [],
            job_score=score.score if score else 0,
            job_rationale=score.rationale if score else "pipeline failed",
        )
        await vk.hset(f"job:{job_id}:results", "result", enriched.model_dump_json())
        await vk.hset(f"job:{job_id}:results", "status", "completed")

    except Exception as e:
        await vk.hset(f"job:{job_id}:results", "status", "failed")

    finally:
        # Always emit done
        await _publish(vk, job_id, StepEvent(
            job_id=job_id, step="score", status="completed",
            message="Pipeline finished", done=True,
        ))
        await vk.aclose()
