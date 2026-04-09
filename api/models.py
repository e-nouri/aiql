from pydantic import BaseModel, HttpUrl, ConfigDict
from typing import Literal


class EnrichRequest(BaseModel):
    url: HttpUrl


class ScrapeResult(BaseModel):
    final_url: str
    status_code: int
    title: str | None = None
    text_content: str


class ParseResult(BaseModel):
    word_count: int
    language: str | None = None
    language_confidence: float | None = None
    meta_description: str | None = None
    outbound_links: list[str] = []


class ScoreSignals(BaseModel):
    http_ok: bool = False
    no_redirect: bool = False
    short_redirect_chain: bool = False
    fast_response: bool = False
    has_title: bool = False
    has_meta_description: bool = False
    not_gated: bool = False
    good_text_density: bool = False
    language_confident: bool = False
    good_stopword_ratio: bool = False
    normal_entropy: bool = False
    balanced_tfidf: bool = False
    has_entities: bool = False
    outbound_link_score: float = 0.0


class ScoreResult(BaseModel):
    score: int
    rationale: str
    signals: ScoreSignals


StepName = Literal["preflight", "scrape", "parse", "score"]
StepStatus = Literal["started", "progress", "completed", "error"]


class StepEvent(BaseModel):
    job_id: str
    step: StepName
    status: StepStatus
    message: str
    payload: ScrapeResult | ParseResult | ScoreResult | None = None
    done: bool = False


class EnrichResult(BaseModel):
    job_id: str
    url: str
    word_count: int
    i18n: str | None = None
    meta_description: str | None = None
    links: list[str] = []
    job_score: int
    job_rationale: str


class JobStatus(BaseModel):
    job_id: str
    url: str
    status: Literal["queued", "running", "completed", "failed", "rejected"]
    result: EnrichResult | None = None
    # debug: intermediate step results
    scrape: ScrapeResult | None = None
    parse: ParseResult | None = None
    score: ScoreResult | None = None
