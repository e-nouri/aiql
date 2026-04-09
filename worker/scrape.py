import time

import httpx
from bs4 import BeautifulSoup
from valkey.asyncio import Valkey

from api.models import ScrapeResult, StepEvent
from worker.helpers import publish, store_step

JS_GATING_PATTERNS = [
    "please enable javascript",
    "you need to enable javascript",
    "javascript is required",
    "this site requires javascript",
    "please turn on javascript",
    "please disable your ad",
    "disable your ad blocker",
    "ad blocker detected",
    "please disable adblock",
    "enable javascript to",
]


async def scrape(vk: Valkey, job_id: str, url: str) -> ScrapeResult | None:
    await publish(vk, job_id, StepEvent(
        job_id=job_id, step="scrape", status="started",
        message="Fetching URL...",
    ))
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
            start = time.monotonic()
            resp = await client.get(url)
            elapsed = time.monotonic() - start

        await publish(vk, job_id, StepEvent(
            job_id=job_id, step="scrape", status="progress",
            message=f"Fetched in {elapsed:.2f}s — parsing HTML...",
        ))

        soup = BeautifulSoup(resp.text, "html.parser")

        # Detect JS-required / ad-blocker gates before stripping tags
        raw_lower = resp.text.lower()
        noscript_tag = soup.find("noscript")
        noscript_text = noscript_tag.get_text(strip=True) if noscript_tag else ""
        js_gated = any(p in raw_lower or p in noscript_text.lower() for p in JS_GATING_PATTERNS)

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
        await store_step(vk, job_id, "scrape", result)

        # Store metadata for parse + scoring steps
        await vk.hset(f"job:{job_id}:results", mapping={
            "_response_time": str(elapsed),
            "_raw_html": resp.text,
            "_raw_html_len": str(len(resp.text)),
            "_redirect_count": str(len(resp.history)),
            "_original_url": url,
            "_js_gated": str(js_gated),
        })
        await publish(vk, job_id, StepEvent(
            job_id=job_id, step="scrape", status="completed",
            message="Scrape complete", payload=result,
        ))
        return result
    except Exception as e:
        await publish(vk, job_id, StepEvent(
            job_id=job_id, step="scrape", status="error",
            message=str(e),
        ))
        return None
