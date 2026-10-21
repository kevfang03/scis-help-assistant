"""Test doubles: a fake support site (httpx.MockTransport), a fake clock and fake embedders."""

import zlib
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import httpx
import numpy as np

from scis_help.common.config import CrawlSettings, IndexSettings, RetrievalSettings, Settings
from scis_help.index.bm25 import tokenize
from scis_help.index.embed import normalise
from scis_help.sources.base import Audience, Document

FIXTURES = Path(__file__).parent / "fixtures" / "site"
BASE = "https://support.example.test/"
FAKE_MODEL = "fake-hash-64"

PAGE_FILES = {
    "/": "home.html",
    "/getting-connected/example-vpn/": "vpn.html",
    "/printing/lab-printers/": "printing.html",
    "/getting-connected/shared-drive/": "shared-drive.html",
}


def fixture_text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def make_settings(**retrieval: object) -> Settings:
    return Settings(
        crawl=CrawlSettings(base_url=BASE, delay_seconds=1.5),
        index=IndexSettings(embed_model=FAKE_MODEL, chunk_min_words=20, chunk_max_words=60),
        retrieval=RetrievalSettings(**retrieval),
    )


@dataclass
class FakeClock:
    now: float = 0.0
    sleeps: list[float] = field(default_factory=list)

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


Response = httpx.Response | Callable[[httpx.Request], httpx.Response]


@dataclass
class FakeSite:
    """Serves the fixture site. ``routes`` maps a URL path to a response (or a function making
    one); unknown paths are 404. Every request is recorded with the fake clock's time."""

    clock: FakeClock = field(default_factory=FakeClock)
    routes: dict[str, Response] = field(default_factory=dict)
    requests: list[tuple[float, str]] = field(default_factory=list)

    @classmethod
    def default(cls) -> "FakeSite":
        site = cls()
        site.routes["/sitemap.xml"] = xml(fixture_text("sitemap.xml"))
        for path, name in PAGE_FILES.items():
            site.routes[path] = html(fixture_text(name))
        return site

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append((self.clock.now, str(request.url)))
        if request.url.host != "support.example.test":
            raise AssertionError(f"off-host request: {request.url}")
        route = self.routes.get(request.url.path)
        if route is None:
            return httpx.Response(404, text="not found")
        return route(request) if callable(route) else route

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)

    def paths(self) -> list[str]:
        return [httpx.URL(url).path for _, url in self.requests]


def html(text: str, status: int = 200) -> httpx.Response:
    return httpx.Response(status, text=text, headers={"content-type": "text/html; charset=utf-8"})


def xml(text: str) -> httpx.Response:
    return httpx.Response(200, text=text, headers={"content-type": "application/xml"})


class HashEmbedder:
    """Hashed bag of words: texts that share words get a high cosine, others zero."""

    model_id = FAKE_MODEL
    dim = 64

    def __init__(self) -> None:
        self.passage_calls = 0

    def _vector(self, text: str) -> np.ndarray:
        vector = np.zeros(self.dim, dtype=np.float32)
        for token in tokenize(text):
            vector[zlib.crc32(token.encode()) % self.dim] += 1.0
        return vector

    def embed_passages(self, texts: Sequence[str]) -> np.ndarray:
        self.passage_calls += 1
        return normalise(np.array([self._vector(t) for t in texts]).reshape(-1, self.dim))

    def embed_query(self, text: str) -> np.ndarray:
        return normalise(self._vector(text))


class ConstantEmbedder(HashEmbedder):
    """Every text gets the same vector: dense retrieval cannot tell chunks apart."""

    def _vector(self, text: str) -> np.ndarray:
        return np.ones(self.dim, dtype=np.float32)


class FailingEmbedder(HashEmbedder):
    def embed_passages(self, texts: Sequence[str]) -> np.ndarray:
        raise RuntimeError("model crashed mid-build")


@dataclass
class FakeSource:
    documents: list[Document]
    name: str = "fake"
    audience: Audience = "public"

    def iter_documents(self) -> Iterator[Document]:
        yield from self.documents


def doc(
    url: str, title: str, text: str, audience: Audience = "public", source: str = "fake"
) -> Document:
    return Document(
        url=url,
        title=title,
        text=text,
        section="",
        lastmod=None,
        source=source,
        audience=audience,
    )
