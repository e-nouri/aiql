from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx
from valkey.asyncio import Valkey

from api.models import JobState, StepEvent, StepName, StepStatus
from config import PREFLIGHT_TIMEOUT, USER_AGENT
from worker.helpers import publish
from worker.validation import sanitize_url


async def _check_robots(client: httpx.AsyncClient, url: str) -> bool:
    """Return True if robots.txt allows our user-agent to fetch url."""
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        resp = await client.get(robots_url)
        if resp.status_code != 200:
            return True
        rp = RobotFileParser()
        rp.parse(resp.text.splitlines())
        return rp.can_fetch(USER_AGENT, url)
    except Exception:
        return True


async def preflight(vk: Valkey, job_id: str, url: str) -> bool:
    """Validate URL security, check robots.txt, then check MIME type via HEAD."""
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
        headers = {"User-Agent": USER_AGENT}
        async with httpx.AsyncClient(
            follow_redirects=True, timeout=PREFLIGHT_TIMEOUT, headers=headers,
        ) as client:
            # Check robots.txt — informational only, we continue regardless
            if not await _check_robots(client, url):
                await publish(vk, job_id, StepEvent(
                    job_id=job_id, step=StepName.PREFLIGHT, status=StepStatus.PROGRESS,
                    message="robots.txt disallows scraping this URL — continuing anyway",
                ))

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
