"""Test doubles for the Claude API: a fake SDK client, a fake LlmClient and a fake retriever."""

import json
from dataclasses import dataclass, field
from types import SimpleNamespace

import anthropic
import httpx2

from scis_help.index.retrieve import Chunk, Hit, RetrievalResult
from scis_help.llm.client import LlmFailed, LlmResult, LlmUnavailable, LlmUsage
from scis_help.llm.keys import get_api_key

API_URL = "https://api.anthropic.com/v1/messages"


def response(
    payload: dict | str | None = None,
    *,
    stop_reason: str = "end_turn",
    input_tokens: int = 1000,
    output_tokens: int = 100,
    cache_read: int = 0,
    cache_write: int = 0,
    thinking: bool = True,
) -> SimpleNamespace:
    """A Message-shaped object: an optional thinking block, then the JSON text block."""
    content = []
    if thinking:
        content.append(SimpleNamespace(type="thinking", thinking=""))
    if payload is not None:
        text = payload if isinstance(payload, str) else json.dumps(payload)
        content.append(SimpleNamespace(type="text", text=text))
    return SimpleNamespace(
        content=content,
        stop_reason=stop_reason,
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=cache_write,
        ),
    )


def status_error(cls: type, status: int) -> Exception:
    request = httpx2.Request("POST", API_URL)
    return cls("error", response=httpx2.Response(status, request=request), body=None)


def timeout_error() -> Exception:
    return anthropic.APITimeoutError(request=httpx2.Request("POST", API_URL))


def connection_error() -> Exception:
    return anthropic.APIConnectionError(request=httpx2.Request("POST", API_URL))


@dataclass
class FakeSdk:
    """``.messages.create(**kwargs)`` returns (or raises) the queued items in order."""

    queue: list = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)

    @property
    def messages(self) -> "FakeSdk":
        return self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.queue.pop(0) if self.queue else response({"ok": True})
        if isinstance(item, Exception):
            raise item
        return item


def usage(input_tokens: int = 1200, output_tokens: int = 80) -> LlmUsage:
    return LlmUsage(
        model="claude-haiku-5-5",
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=900,
        cache_write_tokens=0,
        cost_usd=0.0002,
    )


@dataclass
class FakeLlm:
    """Stands in for LlmClient: returns queued ModelAnswer dicts, statuses or exceptions."""

    queue: list = field(default_factory=list)
    calls: list[dict] = field(default_factory=list)

    @property
    def key_saved(self) -> bool:
        return get_api_key() is not None

    def budget_left(self) -> int:
        return 200_000

    def complete(self, *, system, user, output, purpose, max_tokens, force_enabled=False):
        self.calls.append(
            {"system": system, "user": user, "purpose": purpose, "max_tokens": max_tokens}
        )
        item = self.queue.pop(0)
        if isinstance(item, LlmUnavailable | LlmFailed):
            raise item
        if item in ("refused", "truncated"):
            return LlmResult(None, item, usage())
        return LlmResult(output.model_validate(item), "ok", usage())


def chunk(n: int, url: str | None = None, heading: str = "Steps") -> Chunk:
    return Chunk(
        id=n,
        url=url or f"https://support.example.test/page-{n}/",
        title=f"Page {n}",
        heading=heading,
        text=f"Text of page {n}. Do step {n}.",
        source="support_site",
        audience="public",
    )


@dataclass
class FakeRetriever:
    """Returns ``chunks`` as hits; ``confident`` decides the gate. Records every query."""

    chunks: list[Chunk] = field(default_factory=lambda: [chunk(1), chunk(2), chunk(3)])
    confident: bool = True
    queries: list[tuple[str, str]] = field(default_factory=list)

    def search(self, query: str, *, k: int | None = None, audience: str = "public"):
        self.queries.append((query, audience))
        hits = [
            Hit(chunk=c, rank=i, rrf=1 / (60 + i), dense=0.8 - i / 100, bm25=5.0)
            for i, c in enumerate(self.chunks, start=1)
        ]
        return RetrievalResult(
            hits=hits,
            best_dense=0.8 if self.confident else 0.3,
            best_bm25=5.0,
            confident=self.confident,
        )
