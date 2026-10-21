"""Build the whole index from the sources: chunk, embed, BM25, then swap it into place.

The new index is written to a temporary folder next to ``index_dir()`` and swapped in only
when complete, so a failure (or Ctrl+C) mid-build leaves the old index working.
"""

import logging
import os
import shutil
import time
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from scis_help.common import paths
from scis_help.common.config import Settings
from scis_help.index import store
from scis_help.index.bm25 import Bm25Index
from scis_help.index.chunk import chunk_document, embed_text
from scis_help.index.embed import Embedder, get_embedder
from scis_help.sources.base import Source

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BuildReport:
    pages: int
    chunks: int
    seconds: float
    model_id: str


class NoDocuments(Exception):
    """The sources hold no documents (nothing crawled yet)."""


def build_index(
    settings: Settings, sources: Sequence[Source], *, embedder: Embedder | None = None
) -> BuildReport:
    started = time.perf_counter()
    embedder = embedder or get_embedder(settings)
    rows: list[store.ChunkRow] = []
    texts: list[str] = []
    pages = 0
    for source in sources:
        for doc in source.iter_documents():
            pages += 1
            drafts = chunk_document(
                doc,
                min_words=settings.index.chunk_min_words,
                max_words=settings.index.chunk_max_words,
            )
            for draft in drafts:
                rows.append(
                    store.ChunkRow(
                        url=doc.url,
                        title=doc.title,
                        heading=draft.heading,
                        text=draft.text,
                        source=doc.source,
                        audience=doc.audience,
                    )
                )
                texts.append(embed_text(doc.title, draft.heading, draft.text))
    if not rows:
        raise NoDocuments("no documents to index")

    target = paths.index_dir()
    target.parent.mkdir(parents=True, exist_ok=True)
    temp = target.parent / f".index-new-{uuid.uuid4().hex[:8]}"
    temp.mkdir()
    try:
        vectors = embedder.embed_passages(texts)
        if vectors.shape != (len(texts), embedder.dim):
            raise ValueError(f"embedder returned shape {vectors.shape}")
        store.write_chunks(temp, rows)
        store.write_vectors(temp, vectors)
        Bm25Index.build(texts).save(temp / store.BM25_DIR)
        store.write_manifest(
            temp,
            {
                "model_id": embedder.model_id,
                "dim": embedder.dim,
                "pages": pages,
                "chunks": len(rows),
                "built_at": datetime.now(UTC).isoformat(timespec="seconds"),
                "schema": store.SCHEMA_VERSION,
            },
        )
        _swap(temp, target)
    except BaseException:
        shutil.rmtree(temp, ignore_errors=True)
        raise
    seconds = time.perf_counter() - started
    report = BuildReport(pages=pages, chunks=len(rows), seconds=seconds, model_id=embedder.model_id)
    log.info(
        "index built",
        extra={"pages": pages, "chunks": len(rows), "seconds": round(seconds, 1)},
    )
    return report


def _swap(new: Path, target: Path) -> None:
    """Put ``new`` at ``target``; the old index is moved aside first and restored on failure."""
    old = target.parent / f".index-old-{uuid.uuid4().hex[:8]}"
    if target.exists():
        os.replace(target, old)
    try:
        os.replace(new, target)
    except BaseException:
        if old.exists():
            os.replace(old, target)
        raise
    shutil.rmtree(old, ignore_errors=True)
