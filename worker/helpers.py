import os

from valkey.asyncio import Valkey

from api.models import StepEvent

VALKEY_URL = os.getenv("VALKEY_URL", "valkey://localhost:6379")


async def get_valkey() -> Valkey:
    return Valkey.from_url(VALKEY_URL, decode_responses=True)


async def publish(vk: Valkey, job_id: str, event: StepEvent) -> None:
    await vk.publish(f"job:{job_id}:events", event.model_dump_json())


async def store_step(vk: Valkey, job_id: str, key: str, result) -> None:
    await vk.hset(f"job:{job_id}:results", key, result.model_dump_json())
