import time

import httpx
from bs4 import BeautifulSoup
from valkey.asyncio import Valkey

from api.models import ScrapeResult, StepEvent
from worker.helpers import publish, store_step


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
