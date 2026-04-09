import httpx
from valkey.asyncio import Valkey

from api.models import JobState, StepEvent, StepName, StepStatus
from config import PREFLIGHT_TIMEOUT
from worker.helpers import publish
from worker.validation import sanitize_url


async def preflight(vk: Valkey, job_id: str, url: str) -> bool:
    """Validate URL security, then check MIME type via HEAD."""
    await publish(vk, job_id, StepEvent(
        job_id=job_id, step=StepName.PREFLIGHT, status=StepStatus.STARTED,
        message="Validating URL...",
    ))

    # URL security checks
    rejection = sanitize_url(url)
    if rejection:
        await vk.hset(f"job:{job_id}:results", "status", JobState.REJECTED)
        await publish(vk, job_id, StepEvent(
            job_id=job_id, step=StepName.PREFLIGHT, status=StepStatus.ERROR,
            message=rejection,
        ))
        return False

    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=PREFLIGHT_TIMEOUT) as client:
            resp = await client.head(url)
            content_type = resp.headers.get("content-type", "")
            if "text/html" not in content_type and "text/plain" not in content_type:
                await vk.hset(f"job:{job_id}:results", "status", JobState.REJECTED)
                return False
        await publish(vk, job_id, StepEvent(
            job_id=job_id, step=StepName.PREFLIGHT, status=StepStatus.COMPLETED,
            message=f"{content_type.split(';')[0]} — accepted",
        ))
        return True
    except Exception as e:
        await vk.hset(f"job:{job_id}:results", "status", JobState.REJECTED)
        await publish(vk, job_id, StepEvent(
            job_id=job_id, step=StepName.PREFLIGHT, status=StepStatus.ERROR,
            message=f"Preflight failed: {e}",
        ))
        return False
