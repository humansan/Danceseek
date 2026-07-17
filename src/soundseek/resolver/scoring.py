"""Name normalization for registry keys and fuzzy comparisons.

Candidate *judgment* lives in the batched LLM picker (picker.py); this module
only provides deterministic string utilities.
"""

from __future__ import annotations

import difflib
import re
import unicodedata

_FEAT_RE = re.compile(r"\b(ft|feat|featuring|pres|presents)\b\.?", re.IGNORECASE)
_PUNCT_RE = re.compile(r"[^\w\s]")
_WS_RE = re.compile(r"\s+")


def normalize(text: str) -> str:
    """Lowercase, strip accents/punctuation, unify '&'/'and', drop feat. markers."""
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace("&", " and ")
    text = _FEAT_RE.sub(" ", text)
    text = _PUNCT_RE.sub(" ", text)
    return _WS_RE.sub(" ", text).strip()


def similarity(a: str, b: str) -> float:
    na, nb = normalize(a), normalize(b)
    if not na or not nb:
        return 0.0
    return difflib.SequenceMatcher(None, na, nb).ratio()
