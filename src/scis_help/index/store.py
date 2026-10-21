"""The index on disk: ``chunks.sqlite``, ``vectors.npy``, ``bm25/`` and ``manifest.json``.

Chunk ids are row positions: chunk ``i`` is row ``i`` of the vectors and document ``i`` in
BM25. All files of one index sit in one folder, written whole by ``build.py``.
"""

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from scis_help.common.atomic import write_json_atomic
from scis_help.sources.base import Audience

SCHEMA_VERSION = 1
CHUNKS_FILE = "chunks.sqlite"
VECTORS_FILE = "vectors.npy"
BM25_DIR = "bm25"
MANIFEST_FILE = "manifest.json"

_SCHEMA = """
CREATE TABLE chunks (
    id INTEGER PRIMARY KEY,
    url TEXT NOT NULL,
    title TEXT NOT NULL,
    heading TEXT NOT NULL,
    text TEXT NOT NULL,
    source TEXT NOT NULL,
    audience TEXT NOT NULL
);
"""


@dataclass(frozen=True, slots=True)
class ChunkRow:
    url: str
    title: str
    heading: str
    text: str
    source: str
    audience: Audience


def write_chunks(directory: Path, rows: Sequence[ChunkRow]) -> None:
    db = sqlite3.connect(directory / CHUNKS_FILE)
    try:
        db.executescript(_SCHEMA)
        db.executemany(
            "INSERT INTO chunks (id, url, title, heading, text, source, audience)"
            " VALUES (?, ?, ?, ?, ?, ?, ?)",
            [
                (i, row.url, row.title, row.heading, row.text, row.source, row.audience)
                for i, row in enumerate(rows)
            ],
        )
        db.commit()
    finally:
        db.close()


def read_chunks(directory: Path) -> list[tuple[int, ChunkRow]]:
    db = sqlite3.connect(directory / CHUNKS_FILE)
    try:
        rows = db.execute(
            "SELECT id, url, title, heading, text, source, audience FROM chunks ORDER BY id"
        ).fetchall()
    finally:
        db.close()
    return [(row[0], ChunkRow(*row[1:])) for row in rows]


def write_vectors(directory: Path, vectors: np.ndarray) -> None:
    np.save(directory / VECTORS_FILE, vectors.astype(np.float32), allow_pickle=False)


def read_vectors(directory: Path) -> np.ndarray:
    return np.load(directory / VECTORS_FILE, allow_pickle=False)


def write_manifest(directory: Path, manifest: dict[str, object]) -> None:
    write_json_atomic(directory / MANIFEST_FILE, manifest)


def read_manifest(directory: Path) -> dict[str, object] | None:
    """The manifest, or None when there is no (readable) index in ``directory``."""
    path = directory / MANIFEST_FILE
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None
