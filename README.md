# Senior Python Developer — Take-Home Task

**Time limit:** 30 minutes | **Stack:** FastAPI · Pydantic v2 · your choice of queue and pub/sub

---

## Overview

Build a data enrichment pipeline exposed via a simple HTTP API. The browser submits a URL, the server runs it through a three-step workflow asynchronously, and streams each step's result back to the browser in real time.

---

## Tech Stack

- Python 3.11+
- FastAPI
- Pydantic v2
- Queue and pub/sub mechanism — your choice, document the decision in the README

---

## What to Build

### Pipeline steps

Run sequentially. Publish a result after each step completes.

1. **Scrape** — fetch the URL, extract: final URL, HTTP status, page title, plain text content.
2. **Parse** — extract: word count, language, meta description, outbound links (up to 10).
3. **Score** — produce a quality score (0–100) based on heuristics of your choice, plus a one-line rationale.

### Browser page

A minimal HTML page at `GET /` — no framework, no build step — that submits a URL, opens the stream, and renders each step result as it arrives.

---

## Requirements

- `POST /enrich` must return immediately — no blocking on I/O.
- Results stream incrementally, not batched at the end.
- A terminal `done` event must always be emitted.
- Step errors surface as structured error events — they do not crash the pipeline.
- If the client disconnects, the pipeline continues and the result remains retrievable.

### Pydantic models

Define typed models for: request input, each step's result, the streaming event payload, and the aggregate job status. No raw `dict` on the critical path.

---

## Deliverable

A public GitHub repo runnable with `pip install -r requirements.txt` + `uvicorn main:app --reload`.

`README.md` with setup steps, your tech choices, and any trade-offs made under time pressure.
