"""Pure normalization functions used by the domain."""

import re
import unicodedata


_WHITESPACE = re.compile(r"\s+")


def normalize_exercise_name(value: str) -> str:
    """Return the stable comparison form for a personal exercise name."""

    normalized = unicodedata.normalize("NFKC", value)
    normalized = _WHITESPACE.sub(" ", normalized.strip())
    return normalized.casefold()


def clean_required_text(value: str) -> str:
    """Normalize surrounding/consecutive whitespace while preserving case."""

    return _WHITESPACE.sub(" ", unicodedata.normalize("NFKC", value).strip())
