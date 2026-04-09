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

        # Store metadata for scoring step
        await vk.hset(f"job:{job_id}:results", mapping={
            "_response_time": str(elapsed),
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
        # TODO: real parsing
        await asyncio.sleep(0.5)
        result = ParseResult(
            word_count=5, language="en", language_confidence=0.99,
            meta_description="A dummy page", outbound_links=[],
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


async def _score(vk: Valkey, job_id: str, scrape: ScrapeResult | None, parse: ParseResult | None) -> ScoreResult | None:
    await _publish(vk, job_id, StepEvent(
        job_id=job_id, step="score", status="started",
        message="Running heuristics...",
    ))
    try:
        # TODO: real 14-signal scoring
        await asyncio.sleep(0.5)
        result = ScoreResult(
            score=72, rationale="dummy scoring",
            signals=ScoreSignals(http_ok=True, has_title=True),
        )
        await _store_step(vk, job_id, "score", result)
        await _publish(vk, job_id, StepEvent(
            job_id=job_id, step="score", status="completed",
            message="Score complete", payload=result,
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
            await vk.hset(f"job:{job_id}:results", "status", "rejected")
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
