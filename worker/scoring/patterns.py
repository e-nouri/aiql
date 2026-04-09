import re

from nltk.corpus import stopwords as nltk_stopwords

JS_REQUIRED_RE = re.compile(
    r"enable\s+(javascript|js)"
    r"|javascript\s+(is\s+)?required"
    r"|requires?\s+javascript"
    r"|disable\s+(your\s+)?(ad\s*block|ad\s+blocker)"
    r"|ad\s*block\w*\s+detected",
    re.IGNORECASE,
)

GATING_RE = re.compile(
    r"captcha"
    r"|cloudflare.{0,20}challenge"
    r"|cf-browser-verification"
    r"|access\s+denied"
    r"|403\s+forbidden"
    r"|login\s+required",
    re.IGNORECASE,
)

NLTK_LANG_MAP = {
    "ar": "arabic", "az": "azerbaijani", "da": "danish", "de": "german",
    "el": "greek", "en": "english", "es": "spanish", "fi": "finnish",
    "fr": "french", "hu": "hungarian", "id": "indonesian", "it": "italian",
    "kk": "kazakh", "nb": "norwegian", "nl": "dutch", "pt": "portuguese",
    "ro": "romanian", "ru": "russian", "sl": "slovene", "sv": "swedish",
    "tg": "tajik", "tr": "turkish",
}


def get_stopwords(lang_code: str | None) -> set[str]:
    """Get NLTK stopwords for the detected language, fallback to English."""
    lang = NLTK_LANG_MAP.get(lang_code, "english")
    try:
        return set(nltk_stopwords.words(lang))
    except OSError:
        return set(nltk_stopwords.words("english"))
