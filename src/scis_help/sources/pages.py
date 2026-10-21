"""``pages.sqlite``: extracted text of crawled pages (no raw HTML) plus a small ``meta`` table."""

import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
    url TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    section TEXT NOT NULL,
    text TEXT NOT NULL,
    lastmod TEXT,
    fetched_at TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    source TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""


@dataclass(frozen=True, slots=True)
class StoredPage:
    url: str
    title: str
    section: str
    text: str
    lastmod: str | None
    fetched_at: str
    content_hash: str
    source: str


class PageStore:
    """A thin wrapper over one SQLite file. Use as a context manager; commits on clean exit."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self._db = sqlite3.connect(path)
        self._db.executescript(_SCHEMA)

    def __enter__(self) -> "PageStore":
        return self

    def __exit__(self, exc_type: object, *_: object) -> None:
        if exc_type is None:
            self._db.commit()
        else:
            self._db.rollback()
        self.close()

    def close(self) -> None:
        self._db.close()

    def commit(self) -> None:
        self._db.commit()

    def lastmods(self, source: str) -> dict[str, str | None]:
        rows = self._db.execute("SELECT url, lastmod FROM pages WHERE source = ?", (source,))
        return dict(rows.fetchall())

    def upsert(self, page: StoredPage) -> None:
        self._db.execute(
            "INSERT INTO pages (url, title, section, text, lastmod, fetched_at, content_hash,"
            " source) VALUES (?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT(url) DO UPDATE SET title = excluded.title,"
            " section = excluded.section, text = excluded.text, lastmod = excluded.lastmod,"
            " fetched_at = excluded.fetched_at, content_hash = excluded.content_hash,"
            " source = excluded.source",
            (
                page.url,
                page.title,
                page.section,
                page.text,
                page.lastmod,
                page.fetched_at,
                page.content_hash,
                page.source,
            ),
        )

    def delete_missing(self, keep: set[str], source: str) -> int:
        """Delete this source's pages whose URL is not in ``keep``; return how many."""
        gone = [url for url in self.lastmods(source) if url not in keep]
        self._db.executemany("DELETE FROM pages WHERE url = ?", [(url,) for url in gone])
        return len(gone)

    def count(self, source: str | None = None) -> int:
        if source is None:
            row = self._db.execute("SELECT COUNT(*) FROM pages").fetchone()
        else:
            row = self._db.execute("SELECT COUNT(*) FROM pages WHERE source = ?", (source,))
            row = row.fetchone()
        return int(row[0])

    def iter_pages(self, source: str) -> Iterator[StoredPage]:
        rows = self._db.execute(
            "SELECT url, title, section, text, lastmod, fetched_at, content_hash, source"
            " FROM pages WHERE source = ? ORDER BY url",
            (source,),
        )
        for row in rows.fetchall():
            yield StoredPage(*row)

    def get_meta(self, key: str) -> str | None:
        row = self._db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return None if row is None else str(row[0])

    def set_meta(self, key: str, value: str) -> None:
        self._db.execute(
            "INSERT INTO meta (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
