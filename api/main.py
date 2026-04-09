import json
import uuid
from contextlib import asynccontextmanager

from arq import create_pool
from arq.connections import RedisSettings
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse

from api.models import EnrichRequest, JobStatus, StepEvent
from api.valkey_conn import close_valkey, get_valkey
from config import VALKEY_URL

_arq_pool = None


def _redis_settings() -> RedisSettings:
    url = VALKEY_URL.replace("valkey://", "")
    host, port = url.split(":")
    return RedisSettings(host=host, port=int(port))


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _arq_pool
    _arq_pool = await create_pool(_redis_settings())
    yield
    await close_valkey()
    if _arq_pool:
        await _arq_pool.aclose()


app = FastAPI(title="AiQl Enrichment Pipeline", lifespan=lifespan)


STATIC_DIR = Path(__file__).parent / "static"


@app.get("/")
async def index():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/favicon.ico")
async def favicon():
    return FileResponse(STATIC_DIR / "favicon.ico")


@app.post("/enrich")
async def enrich(req: EnrichRequest):
    job_id = str(uuid.uuid4())
    vk = await get_valkey()

    await vk.hset(f"job:{job_id}:results", mapping={
        "job_id": job_id,
        "url": str(req.url),
        "status": "queued",
    })

    await _arq_pool.enqueue_job("run_pipeline", job_id, str(req.url))

    return {"job_id": job_id}


@app.get("/enrich/{job_id}/stream")
async def stream(job_id: str):
    async def event_generator():
        vk = await get_valkey()
        pubsub = vk.pubsub()
        await pubsub.subscribe(f"job:{job_id}:events")

        try:
            async for message in pubsub.listen():
                if message["type"] != "message":
                    continue
                data = message["data"]
                yield f"event: step\ndata: {data}\n\n"

                event = StepEvent.model_validate_json(data)
                if event.done:
                    break
        finally:
            await pubsub.unsubscribe(f"job:{job_id}:events")
            await pubsub.aclose()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/enrich/{job_id}")
async def get_job(job_id: str):
    vk = await get_valkey()
    data = await vk.hgetall(f"job:{job_id}:results")
    if not data:
        raise HTTPException(status_code=404, detail="job not found")

    # Parse JSON fields, skip internal metadata keys
    clean = {}
    for key, val in data.items():
        if key.startswith("_"):
            continue
        if key in ("scrape", "parse", "score", "result"):
            clean[key] = json.loads(val)
        else:
            clean[key] = val

    return JobStatus.model_validate(clean)
