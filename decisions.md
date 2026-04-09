# Design Decisions

## Scope: Exercise (not a product)

This is a take-home coding exercise — not a POC, not an MVP, not production. Single user, no auth, no tests, no CI. The focus is on demonstrating architecture and engineering decisions, not shipping software. Containers are included not for production readiness but because they make it trivial for the evaluator to run the solution: `podman-compose up` and it works.

## API: REST + SSE (not GraphQL + WebSockets)

The task spec defines `POST /enrich` — keeping it as REST stays true to the spec and avoids unnecessary complexity. SSE is simpler than WebSockets for this use case: the data flows one direction (server → client), SSE auto-reconnects natively, and it works over plain HTTP with no upgrade handshake.

## Queue: arq (not Celery, not RabbitMQ)

arq is async-native, lightweight, and backed by Valkey — no extra broker needed. Job enqueueing from FastAPI is a single `await`. No Celery overhead, no RabbitMQ infra, no IronMQ vendor lock-in.

## Store + Pub/Sub: Valkey (not Postgres, not Redis)

Valkey handles three roles with one container:

| Role | Primitive | Key pattern |
|---|---|---|
| Job queue | arq internals | arq default queues |
| Live streaming | Pub/Sub | `job:{id}:events` |
| Result store | Hash + TTL | `job:{id}:results` |

**Note:** Raw HTML is stored temporarily in Valkey for cross-step access (parse reads it from scrape). Valkey does not support compression natively. In production, this should either be compressed in Python before storing (`zlib`), capped at a max size, or eliminated by extracting all needed data during scrape and not persisting raw HTML at all.

## Containers: Podman Compose (not single process)

3 containers: `api`, `worker`, `valkey`. Frontend is served as static HTML by the API container.

A single-process blob with in-memory queues works for a demo but breaks the moment you need to scale workers independently or survive an API restart without losing in-flight jobs. Containers add ~30 seconds of setup (`podman-compose up`) and give proper separation for free.

## Parsing: BeautifulSoup (not headless browser)

BS4 + httpx is lightweight, fast, and sufficient for most pages. No browser binary, no Playwright dependency, no Chromium in the container.

**Trade-off acknowledged:** A headless browser (Playwright, Puppeteer) handles JS-rendered SPAs and client-side routing. Tools like Tavily go further — they handle Cloudflare challenges, CAPTCHAs, IP rotation, and anti-bot detection out of the box. BS4 will fail on these. For a 30-minute take-home, BS4 is the right call. In production, you'd swap in Tavily or a headless browser behind the same interface.

## NLP: Local models only (no LLMs, no APIs)

All scoring and analysis runs locally. No reliance on LLMs, no structured output from LLMs, no LLM-as-a-judge, and no external API calls. Scoring is deterministic heuristics — not vibes from a language model.

**NER:** spaCy `xx_ent_wiki_sm` (~12MB) — multilingual NER model covering PER, ORG, LOC, MISC across languages. Only `ner` + `tok2vec` components are loaded, the rest disabled.

**Language detection:** `lingua-py` — lightest and most accurate option. Alternatives like `langdetect` are unreliable on short texts, and `fasttext` requires a ~126MB model download. lingua is pure Python, no external model files, and handles short extracted content well.

**Warm-up:** Both the NER model and lingua detector are loaded and warmed up on worker startup (before accepting jobs). First inference is slow due to lazy initialization — running a dummy prediction at boot eliminates that cold-start penalty from real jobs.

## HTTP Client: Native `fetch` (not Axios)

The frontend uses the browser's native `fetch` API. No Axios — recent supply chain compromise ([axios/axios#10604](https://github.com/axios/axios/issues/10604), [details](https://www.stepsecurity.io/blog/axios-compromised-on-npm-malicious-versions-drop-remote-access-trojan)). `fetch` is built into every modern browser, has no dependencies, and is sufficient for SSE via `EventSource`.

## Frontend: Minimal HTML served by FastAPI (not Next.js)

The spec says "no framework, no build step." A single HTML page at `GET /` that uses `EventSource` for SSE is all that's needed. No Node.js container, no build pipeline.

**Update:** Dropped the Next.js + shadcn/ui plan. One less container, zero JS build complexity.

## Scoring: 14-signal heuristic (not AI-based)

The score reflects how well the extraction went, not content quality in an editorial sense. All signals are deterministic and computed locally — no external API calls.

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

## Security: URL Sanitization (not exhaustive)

URLs are validated before any HTTP request is made. The current checks cover the most common attack vectors but are **not exhaustive** — this is an exercise, not a production security audit.

**Current checks:**
- Scheme restricted to `http` / `https`
- URL length capped at 2048 chars
- Non-standard ports rejected (only 80/443 allowed)
- Credentials in URL rejected (`user:pass@host`)
- Sensitive query params blocked (`password`, `token`, `api_key`, `access_token`, etc.)
- Base64-encoded content in path or query rejected
- DNS resolution verified — hostname must resolve
- Private/reserved IPs blocked (SSRF protection: `127.x`, `10.x`, `172.16-31.x`, `192.168.x`, `169.254.x`, `::1`)

**Not covered (would add in production):**
- DNS rebinding attacks
- URL redirect chains landing on internal IPs after initial resolution
- Rate limiting per IP / per session
- Request size limits on responses
- Timeout-based DoS protection beyond httpx defaults
- IPv6 edge cases
- URL normalization attacks (e.g., `http://127.0.0.1` vs `http://0x7f000001`)
