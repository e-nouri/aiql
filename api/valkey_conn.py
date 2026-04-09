import os
from valkey.asyncio import Valkey

VALKEY_URL = os.getenv("VALKEY_URL", "valkey://localhost:6379")

_pool: Valkey | None = None


async def get_valkey() -> Valkey:
    global _pool
    if _pool is None:
        _pool = Valkey.from_url(VALKEY_URL, decode_responses=True)
    return _pool


async def close_valkey() -> None:
    global _pool
    if _pool is not None:
        await _pool.aclose()
        _pool = None
