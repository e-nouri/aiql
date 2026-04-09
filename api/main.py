import json
import uuid

from arq import create_pool
from arq.connections import RedisSettings
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, StreamingResponse

from api.models import EnrichRequest, JobStatus, StepEvent
from api.valkey_conn import VALKEY_URL, close_valkey, get_valkey

app = FastAPI(title="AiQl Enrichment Pipeline")

_arq_pool = None


def _redis_settings() -> RedisSettings:
    url = VALKEY_URL.replace("valkey://", "")
    host, port = url.split(":")
    return RedisSettings(host=host, port=int(port))


@app.on_event("startup")
async def startup():
    global _arq_pool
    _arq_pool = await create_pool(_redis_settings())


@app.on_event("shutdown")
async def shutdown():
    await close_valkey()
    if _arq_pool:
        await _arq_pool.aclose()


@app.get("/", response_class=HTMLResponse)
async def index():
    return "<h1>AiQl</h1><p>Coming soon</p>"


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
        return {"error": "job not found"}, 404

    for key in ("scrape", "parse", "score"):
        if key in data:
            data[key] = json.loads(data[key])

    return JobStatus.model_validate(data)
