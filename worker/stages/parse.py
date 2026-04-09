from bs4 import BeautifulSoup
from valkey.asyncio import Valkey

from api.models import ParseResult, ScrapeResult, StepEvent, StepName, StepStatus
from config import MAX_OUTBOUND_LINKS
from worker.helpers import publish, store_step


async def parse(vk: Valkey, job_id: str, scrape: ScrapeResult, ctx: dict) -> ParseResult | None:
    await publish(vk, job_id, StepEvent(
        job_id=job_id, step=StepName.PARSE, status=StepStatus.STARTED,
        message="Extracting metadata...",
    ))
    try:
        words = scrape.text_content.split()
        word_count = len(words)

        await publish(vk, job_id, StepEvent(
            job_id=job_id, step=StepName.PARSE, status=StepStatus.PROGRESS,
            message=f"Word count: {word_count} — detecting language...",
        ))

        # Language detection (pre-loaded in ctx)
        detector = ctx["lingua"]
        confidence_values = detector.compute_language_confidence_values(scrape.text_content)
        if confidence_values:
            best = confidence_values[0]
            language = best.language.iso_code_639_1.name.lower()
            language_confidence = best.value
        else:
            language = None
            language_confidence = None

        await publish(vk, job_id, StepEvent(
            job_id=job_id, step=StepName.PARSE, status=StepStatus.PROGRESS,
            message=f"Language: {language} ({language_confidence or 0:.2f}) — extracting links...",
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
            if href.startswith("http") and len(links) < MAX_OUTBOUND_LINKS:
                links.append(href)

        result = ParseResult(
            word_count=word_count,
            language=language,
            language_confidence=language_confidence,
            meta_description=meta_description,
            outbound_links=links,
        )
        await store_step(vk, job_id, "parse", result)
        await publish(vk, job_id, StepEvent(
            job_id=job_id, step=StepName.PARSE, status=StepStatus.COMPLETED,
            message="Parse complete", payload=result,
        ))
        return result
    except Exception as e:
        await publish(vk, job_id, StepEvent(
            job_id=job_id, step=StepName.PARSE, status=StepStatus.ERROR,
            message=str(e),
        ))
        return None
