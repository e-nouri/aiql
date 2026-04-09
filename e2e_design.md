# AiQl Enrichment Pipeline — End-to-End Design

## Stack

- **Python 3.11+**
- **FastAPI** + **Pydantic v2**
- **arq** — async job queue (Valkey-backed)
- **Valkey** — queue + pub/sub + result store
- **httpx** + **BeautifulSoup** — scraping & parsing
- **spaCy `xx_ent_wiki_sm`** — multilingual NER
- **lingua-py** — language detection
- **Native `fetch` + `EventSource`** — frontend (no Axios)

## Architecture

```
┌──────────┐    POST /enrich     ┌──────────┐    arq enqueue    ┌──────────┐
│  Browser  │ ──────────────────▶ │   API    │ ───────────────▶ │  Worker  │
│  (fetch)  │                    │ (FastAPI) │                  │  (arq)   │
│           │ ◀── SSE ────────── │          │ ◀── SUBSCRIBE ── │          │
└──────────┘  GET /enrich/{id}   └──────────┘                  └──────────┘
                                      │                             │
                                      └──────── Valkey ─────────────┘
                                            (queue/pubsub/store)
```

## Containers (Podman Compose)

| Container | Role |
|---|---|
| `api` | FastAPI — serves REST endpoints + static HTML frontend |
| `worker` | arq worker — runs pipeline steps |
| `valkey` | Queue + pub/sub + result store |

## Valkey Key Layout

| Role | Primitive | Key pattern |
|---|---|---|
| Job queue | arq internals | arq default queues |
| Live streaming | Pub/Sub | `job:{id}:events` |
| Result store | Hash | `job:{id}:results` |

## API Endpoints

| Method | Path | Description |
|---|---|---|
| `GET` | `/` | Serves the minimal HTML frontend |
| `POST` | `/enrich` | Accepts a URL, enqueues job, returns job ID immediately |
| `GET` | `/enrich/{job_id}/stream` | SSE stream — emits step events as they complete |
| `GET` | `/enrich/{job_id}` | Retrieve full result (for reconnects / after completion) |

## Pydantic Models

### Request

```python
class EnrichRequest(BaseModel):
    url: HttpUrl
```

### Step Results

```python
class ScrapeResult(BaseModel):
    final_url: str
    status_code: int
    title: str | None
    text_content: str

class ParseResult(BaseModel):
    word_count: int
    language: str | None
    language_confidence: float | None
    meta_description: str | None
    outbound_links: list[str]  # up to 10

class ScoreResult(BaseModel):
    score: int  # 0–100
    rationale: str
    signals: dict[str, bool | float]
```

### SSE Event

```python
class StepEvent(BaseModel):
    job_id: str
    step: Literal["preflight", "scrape", "parse", "score"]
    status: Literal["started", "progress", "completed", "error"]
    message: str
    payload: ScrapeResult | ParseResult | ScoreResult | None = None
    done: bool = False
```

### Job Status

```python
class JobStatus(BaseModel):
    job_id: str
    url: str
    status: Literal["queued", "running", "completed", "failed"]
    scrape: ScrapeResult | None = None
    parse: ParseResult | None = None
    score: ScoreResult | None = None
```

## Pipeline Flow

### Pre-flight

- HTTP `HEAD` request to check MIME type
- Accept `text/html` and `text/plain` only
- Anything else (PDF, video, binary) → reject immediately with `"not supported yet"`

### Step 1 — Scrape

- `httpx` async GET with redirect following
- `BeautifulSoup` to extract: final URL, HTTP status, page title, plain text content
- Detect SPA/JS-heavy or auth-protected pages as signals for scoring

### Step 2 — Parse

- Word count, language (`lingua-py`), meta description, outbound links (up to 10)
- All regex + BS4 — no NLP at this step

### Step 3 — Score

- Heuristic-based quality score (0–100) reflecting how well the extraction went
- One-line rationale generated from the lowest scoring signals

## Scoring Signals

| Group | # | Signal | Points |
|---|---|---|---|
| **Reachability** | 1 | HTTP 200 received | 1 |
| | 2 | Final URL == original (no redirect) | 1 |
| | 3 | Redirect chain ≤ 1 | 1 |
| **Performance** | 4 | Response time < 2s | 1 |
| **Structure** | 5 | Page has a title | 1 |
| | 6 | Meta description present | 1 |
| **Gating** | 7 | Not content-gated (auth / paywall / bot protection) | 1 |
| **Text Quality** | 8 | Text-to-HTML density > 0.5 | 1 |
| | 9 | Language detection confidence > 0.8 | 1 |
| | 10 | Stopword ratio 25–50% | 1 |
| | 11 | Shannon entropy in normal range | 1 |
| | 12 | TF-IDF — no single term dominates | 1 |
| | 13 | NER entity count > 0 (spaCy `xx_ent_wiki_sm`) | 1 |
| **Links** | 14 | Outbound links (0.1 × count, max 1) | 0–1 |

**Total max: 14 → normalized to 0–100**

## SSE Event Flow

```
POST /enrich          → { job_id: "abc123" }
GET  /enrich/abc123/stream →

event: step
data: { step: "preflight", status: "started",   message: "Checking MIME type..." }

event: step
data: { step: "preflight", status: "completed", message: "text/html — accepted" }

event: step
data: { step: "scrape",    status: "started",   message: "Fetching URL..." }

event: step
data: { step: "scrape",    status: "progress",  message: "Parsing HTML content..." }

event: step
data: { step: "scrape",    status: "completed", payload: { final_url, status_code, title, text_content } }

event: step
data: { step: "parse",     status: "started",   message: "Extracting metadata..." }

event: step
data: { step: "parse",     status: "completed", payload: { word_count, language, meta_description, outbound_links } }

event: step
data: { step: "score",     status: "started",   message: "Running heuristics..." }

event: step
data: { step: "score",     status: "completed", payload: { score: 87, rationale: "low text density", signals: {...} } }

event: done
data: { job_id: "abc123", done: true }
```

## Error Handling

- Step errors emit `status: "error"` with a structured message — pipeline continues to next step
- `done` event is always emitted, even if steps errored
- Pre-flight rejection stops the pipeline immediately (no point running scrape on a PDF)

## Client Disconnect

- If the client disconnects, the worker continues — the pipeline is fire-and-forget from arq's perspective
- Full result remains in Valkey, retrievable via `GET /enrich/{job_id}`

## Worker Startup

1. Load spaCy `xx_ent_wiki_sm` (only `ner` + `tok2vec` components)
2. Initialize lingua-py detector
3. Warm up both with a dummy prediction to eliminate cold-start latency
4. Start accepting jobs from arq queue
