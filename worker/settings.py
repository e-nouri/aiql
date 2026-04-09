import logging
import os

from arq.connections import RedisSettings

from worker.pipeline import run_pipeline

logger = logging.getLogger(__name__)

VALKEY_URL = os.getenv("VALKEY_URL", "valkey://localhost:6379")


def _redis_settings() -> RedisSettings:
    url = VALKEY_URL.replace("valkey://", "")
    host, port = url.split(":")
    return RedisSettings(host=host, port=int(port))


async def startup(ctx: dict) -> None:
    """Warm up NLP models before accepting jobs."""
    import spacy
    from lingua import LanguageDetectorBuilder

    logger.info("Loading spaCy xx_ent_wiki_sm...")
    nlp = spacy.load("xx_ent_wiki_sm", disable=["tagger", "parser", "senter", "attribute_ruler", "lemmatizer"])
    nlp("warm up")  # force lazy init
    ctx["nlp"] = nlp
    logger.info("spaCy ready")

    logger.info("Loading lingua detector...")
    detector = LanguageDetectorBuilder.from_all_languages().build()
    detector.detect_language_of("warm up")  # force lazy init
    ctx["lingua"] = detector
    logger.info("lingua ready")

    logger.info("Worker warm-up complete")


class WorkerSettings:
    functions = [run_pipeline]
    on_startup = startup
    redis_settings = _redis_settings()
