import os

# Valkey
VALKEY_URL = os.getenv("VALKEY_URL", "valkey://localhost:6379")

# Identity
USER_AGENT = os.getenv("USER_AGENT", "AiQL/1.0 (URL enrichment bot; +https://github.com/nourix2/aiql)")

# HTTP timeouts (seconds)
PREFLIGHT_TIMEOUT = int(os.getenv("PREFLIGHT_TIMEOUT", "10"))
SCRAPE_TIMEOUT = int(os.getenv("SCRAPE_TIMEOUT", "15"))

# Scoring thresholds
FAST_RESPONSE_THRESHOLD = float(os.getenv("FAST_RESPONSE_THRESHOLD", "2.0"))
TEXT_DENSITY_THRESHOLD = float(os.getenv("TEXT_DENSITY_THRESHOLD", "0.5"))
LANGUAGE_CONFIDENCE_THRESHOLD = float(os.getenv("LANGUAGE_CONFIDENCE_THRESHOLD", "0.8"))
STOPWORD_RATIO_MIN = float(os.getenv("STOPWORD_RATIO_MIN", "0.25"))
STOPWORD_RATIO_MAX = float(os.getenv("STOPWORD_RATIO_MAX", "0.50"))
ENTROPY_MIN = float(os.getenv("ENTROPY_MIN", "3.0"))
ENTROPY_MAX = float(os.getenv("ENTROPY_MAX", "5.5"))
TFIDF_DOMINANCE_THRESHOLD = float(os.getenv("TFIDF_DOMINANCE_THRESHOLD", "0.10"))
TFIDF_MIN_WORDS = int(os.getenv("TFIDF_MIN_WORDS", "10"))
LINK_SCORE_MULTIPLIER = float(os.getenv("LINK_SCORE_MULTIPLIER", "0.1"))
LINK_SCORE_MAX = float(os.getenv("LINK_SCORE_MAX", "1.0"))
MAX_REDIRECT_CHAIN = int(os.getenv("MAX_REDIRECT_CHAIN", "1"))

# Limits
MAX_OUTBOUND_LINKS = int(os.getenv("MAX_OUTBOUND_LINKS", "10"))
NER_TEXT_LIMIT = int(os.getenv("NER_TEXT_LIMIT", "5000"))
