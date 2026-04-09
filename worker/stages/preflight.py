import httpx
from valkey.asyncio import Valkey

from api.models import StepEvent
from config import PREFLIGHT_TIMEOUT
from worker.helpers import publish


async def preflight(vk: Valkey, job_id: str, url: str) -> bool:
    """Check MIME type via HEAD — reject non-HTML content."""
    await publish(vk, job_id, StepEvent(
        job_id=job_id, step="preflight", status="started",
        message="Checking MIME type...",
    ))
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=PREFLIGHT_TIMEOUT) as client:
            resp = await client.head(url)
            content_type = resp.headers.get("content-type", "")
            if "text/html" not in content_type and "text/plain" not in content_type:
                await vk.hset(f"job:{job_id}:results", "status", "rejected")
                return False
        await publish(vk, job_id, StepEvent(
            job_id=job_id, step="preflight", status="completed",
            message=f"{content_type.split(';')[0]} — accepted",
        ))
        return True
    except Exception as e:
        await vk.hset(f"job:{job_id}:results", "status", "rejected")
        await publish(vk, job_id, StepEvent(
            job_id=job_id, step="preflight", status="error",
            message=f"Preflight failed: {e}",
        ))
        return False
