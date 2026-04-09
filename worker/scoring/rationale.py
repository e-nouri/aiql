from api.models import ScoreSignals

# (signal_attr, expected_value, issue_label)
_SIGNAL_ISSUES = [
    ("http_ok",            True,  "non-200 status"),
    ("not_gated",          True,  "content gated"),
    ("good_text_density",  True,  "low text density"),
    ("has_title",          True,  "no title"),
    ("has_entities",       True,  "no entities detected"),
    ("i18n_supported",     True,  "i18n not supported"),
    ("language_confident", True,  "low language confidence"),
    ("normal_entropy",     True,  "abnormal text entropy"),
    ("balanced_tfidf",     True,  "single term dominates"),
]


def build_rationale(signals: ScoreSignals) -> str:
    if signals.js_gated:
        return "page requires JavaScript"

    issues = [
        label
        for attr, expected, label in _SIGNAL_ISSUES
        if getattr(signals, attr) != expected
    ]

    return "; ".join(issues[:3]) if issues else "all signals passed"
