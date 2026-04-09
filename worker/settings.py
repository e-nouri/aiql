import os

from arq.connections import RedisSettings

from worker.pipeline import run_pipeline

VALKEY_URL = os.getenv("VALKEY_URL", "valkey://localhost:6379")


def _redis_settings() -> RedisSettings:
    url = VALKEY_URL.replace("valkey://", "")
    host, port = url.split(":")
    return RedisSettings(host=host, port=int(port))


class WorkerSettings:
    functions = [run_pipeline]
    redis_settings = _redis_settings()
