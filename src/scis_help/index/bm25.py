"""Lexical retrieval with BM25 (``bm25s``), for course codes, product names and error strings
that embeddings blur ("CCIREPO", "GlobalProtect", "CS 265")."""

import re
from collections.abc import Sequence
from pathlib import Path

import bm25s
import numpy as np
from bm25s.stopwords import STOPWORDS_EN

_STOPWORDS = frozenset(STOPWORDS_EN)
# Words, numbers and joined forms such as "cs265", "cci-repo" or "vpn.drexel.edu".
_TOKEN = re.compile(r"[a-z0-9]+(?:[-_.][a-z0-9]+)*")
_PARTS = re.compile(r"[-_.]")
_LETTERS_DIGITS = re.compile(r"([a-z]+)(\d+)")


def tokenize(text: str) -> list[str]:
    """Lowercase tokens, English stopwords dropped. Joined forms are kept and their parts added,
    so "cci-repo" matches "cci repo" and "CS265" matches "CS 265" (and the other way round)."""
    tokens: list[str] = []
    previous = ""
    for match in _TOKEN.finditer(text.lower()):
        token = match.group()
        found = [token]
        parts = _PARTS.split(token)
        if len(parts) > 1:
            found.extend(parts)
        for part in parts:
            split = _LETTERS_DIGITS.fullmatch(part)
            if split:
                found.extend(split.groups())
        if previous.isalpha() and len(previous) <= 5 and token.isdigit():
            found.append(previous + token)
        previous = token
        tokens.extend(word for word in found if len(word) >= 2 and word not in _STOPWORDS)
    return tokens


class Bm25Index:
    """BM25 over a list of texts; document ids are list positions."""

    def __init__(self, model: bm25s.BM25, size: int) -> None:
        self._model = model
        self.size = size

    @classmethod
    def build(cls, texts: Sequence[str]) -> "Bm25Index":
        model = bm25s.BM25()
        model.index([tokenize(text) for text in texts], show_progress=False)
        return cls(model, len(texts))

    def save(self, directory: Path) -> None:
        self._model.save(str(directory), show_progress=False)

    @classmethod
    def load(cls, directory: Path, size: int) -> "Bm25Index":
        return cls(bm25s.BM25.load(str(directory), show_progress=False), size)

    def search(self, query: str, k: int, mask: np.ndarray | None = None) -> list[tuple[int, float]]:
        """Up to ``k`` (doc id, score) pairs with a positive score, best first. ``mask`` (one
        0/1 per document) leaves out documents marked 0."""
        tokens = tokenize(query)
        k = min(k, self.size)
        if not tokens or k < 1:
            return []
        weights = None if mask is None else mask.astype(np.float32)
        ids, scores = self._model.retrieve([tokens], k=k, show_progress=False, weight_mask=weights)
        return [
            (int(doc), float(score))
            for doc, score in zip(ids[0], scores[0], strict=True)
            if score > 0
        ]
