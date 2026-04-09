from api.models import EnrichResult, StepEvent
from worker.helpers import get_valkey, publish
from worker.stages import preflight, scrape, parse, score


async def run_pipeline(ctx: dict, job_id: str, url: str) -> None:
    vk = await get_valkey()

    try:
        await vk.hset(f"job:{job_id}:results", "status", "running")

        # Pre-flight
        accepted = await preflight(vk, job_id, url)
        if not accepted:
            return

        # Pipeline steps — continue even if a step fails
        scrape_result = await scrape(vk, job_id, url)
        parse_result = await parse(vk, job_id, scrape_result, ctx) if scrape_result else None
        score_result = await score(vk, job_id, scrape_result, parse_result, ctx)

        # Build final result
        enriched = EnrichResult(
            job_id=job_id,
            url=url,
            word_count=parse_result.word_count if parse_result else 0,
            i18n=parse_result.language if parse_result else None,
            meta_description=parse_result.meta_description if parse_result else None,
            links=parse_result.outbound_links if parse_result else [],
            job_score=score_result.score if score_result else 0,
            job_rationale=score_result.rationale if score_result else "pipeline failed",
        )
        await vk.hset(f"job:{job_id}:results", "result", enriched.model_dump_json())
        await vk.hset(f"job:{job_id}:results", "status", "completed")

    except Exception as e:
        await vk.hset(f"job:{job_id}:results", "status", "failed")

    finally:
        # Always emit done
        await publish(vk, job_id, StepEvent(
            job_id=job_id, step="score", status="completed",
            message="Pipeline finished", done=True,
        ))
        await vk.aclose()
