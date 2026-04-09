from valkey.asyncio import Valkey

from api.models import ParseResult, ScoreResult, ScrapeResult, StepEvent, StepName, StepStatus
from worker.helpers import publish, store_step
from worker.scoring.rationale import build_rationale
from worker.scoring.signals import compute_signals


async def score(vk: Valkey, job_id: str, scrape: ScrapeResult | None, parse: ParseResult | None, ctx: dict) -> ScoreResult | None:
    await publish(vk, job_id, StepEvent(
        job_id=job_id, step=StepName.SCORE, status=StepStatus.STARTED,
        message="Running heuristics...",
    ))
    try:
        meta = await vk.hgetall(f"job:{job_id}:results")

        await publish(vk, job_id, StepEvent(
            job_id=job_id, step=StepName.SCORE, status=StepStatus.PROGRESS,
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
            job_id=job_id, step=StepName.SCORE, status=StepStatus.COMPLETED,
            message=f"Score: {final_score}/100 — {rationale}", payload=result,
        ))
        return result
    except Exception as e:
        await publish(vk, job_id, StepEvent(
            job_id=job_id, step=StepName.SCORE, status=StepStatus.ERROR,
            message=str(e),
        ))
        return None
