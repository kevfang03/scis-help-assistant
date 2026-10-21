"""Hybrid retrieval (ARCHITECTURE.md D1): dense cosine top-N and BM25 top-N fused with
Reciprocal Rank Fusion (k = 60), at most ``max_chunks_per_page`` chunks per URL, cut to ``k``.
"""

from dataclasses import dataclass

import numpy as np

from scis_help.common import paths
from scis_help.common.config import Settings
from scis_help.index import store
from scis_help.index.bm25 import Bm25Index
from scis_help.index.embed import Embedder, get_embedder
from scis_help.sources.base import Audience

RRF_K = 60


@dataclass(frozen=True, slots=True)
class Chunk:
    id: int
    url: str
    title: str
    heading: str
    text: str
    source: str
    audience: Audience


@dataclass(frozen=True, slots=True)
class Hit:
    chunk: Chunk
    rank: int  # 1-based position in the fused list
    rrf: float
    dense: float  # cosine similarity to the query
    bm25: float | None  # None when BM25 did not return this chunk


@dataclass(frozen=True, slots=True)
class RetrievalResult:
    hits: list[Hit]  # fused, <= max_chunks_per_page per URL, length <= k
    best_dense: float
    best_bm25: float
    confident: bool  # best_dense >= min_score or best_bm25 >= min_bm25


class IndexMissing(Exception):
    """No index, or an index built with another embedding model. The message says which."""


class Retriever:
    def __init__(
        self,
        settings: Settings,
        embedder: Embedder,
        chunks: list[Chunk],
        vectors: np.ndarray,
        bm25: Bm25Index,
    ) -> None:
        self._settings = settings
        self._embedder = embedder
        self._chunks = chunks
        self._vectors = vectors
        self._bm25 = bm25
        public = np.array([chunk.audience == "public" for chunk in chunks], dtype=bool)
        self._masks: dict[Audience, np.ndarray] = {
            "public": public,
            "desk": np.ones(len(chunks), dtype=bool),
        }

    @classmethod
    def load(cls, settings: Settings, *, embedder: Embedder | None = None) -> "Retriever":
        directory = paths.index_dir()
        manifest = store.read_manifest(directory)
        if manifest is None:
            raise IndexMissing("no index found")
        if manifest.get("model_id") != settings.index.embed_model:
            raise IndexMissing(
                f"the index was built with {manifest.get('model_id')!r} but the settings name"
                f" {settings.index.embed_model!r}"
            )
        try:
            rows = store.read_chunks(directory)
            vectors = store.read_vectors(directory)
            bm25 = Bm25Index.load(directory / store.BM25_DIR, size=len(rows))
        except Exception as error:  # a damaged or half-deleted index folder
            raise IndexMissing(f"the index could not be read ({type(error).__name__})") from error
        if vectors.ndim != 2 or vectors.shape[0] != len(rows):
            raise IndexMissing("the index is damaged (vector and chunk counts differ)")
        chunks = [
            Chunk(
                id=chunk_id,
                url=row.url,
                title=row.title,
                heading=row.heading,
                text=row.text,
                source=row.source,
                audience=row.audience,
            )
            for chunk_id, row in rows
        ]
        return cls(settings, embedder or get_embedder(settings), chunks, vectors, bm25)

    def search(
        self, query: str, *, k: int | None = None, audience: Audience = "public"
    ) -> RetrievalResult:
        config = self._settings.retrieval
        k = k or config.k
        allowed = self._masks[audience]
        if not query.strip() or not allowed.any():
            return RetrievalResult(hits=[], best_dense=0.0, best_bm25=0.0, confident=False)

        dense = self._vectors @ self._embedder.embed_query(query)
        masked = np.where(allowed, dense, -np.inf)
        order = np.argsort(-masked, kind="stable")
        dense_ids = [int(i) for i in order[: config.candidates] if allowed[i]]
        bm25_hits = self._bm25.search(query, config.candidates, mask=allowed.astype(np.int8))
        bm25_scores = dict(bm25_hits)

        fused: dict[int, float] = {}
        for ranked in (dense_ids, [doc for doc, _ in bm25_hits]):
            for rank, chunk_id in enumerate(ranked, start=1):
                fused[chunk_id] = fused.get(chunk_id, 0.0) + 1.0 / (RRF_K + rank)
        ordered = sorted(fused, key=lambda i: (-fused[i], -float(dense[i]), i))

        hits: list[Hit] = []
        per_page: dict[str, int] = {}
        for chunk_id in ordered:
            chunk = self._chunks[chunk_id]
            if per_page.get(chunk.url, 0) >= config.max_chunks_per_page:
                continue
            per_page[chunk.url] = per_page.get(chunk.url, 0) + 1
            hits.append(
                Hit(
                    chunk=chunk,
                    rank=len(hits) + 1,
                    rrf=fused[chunk_id],
                    dense=float(dense[chunk_id]),
                    bm25=bm25_scores.get(chunk_id),
                )
            )
            if len(hits) == k:
                break

        best_dense = float(masked[order[0]])
        best_bm25 = bm25_hits[0][1] if bm25_hits else 0.0
        confident = best_dense >= config.min_score or best_bm25 >= config.min_bm25
        return RetrievalResult(
            hits=hits, best_dense=best_dense, best_bm25=best_bm25, confident=confident
        )
