"""The ``Source`` protocol: anything that yields documents for the index."""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal, Protocol

Audience = Literal["public", "desk"]


@dataclass(frozen=True, slots=True)
class Document:
    url: str
    title: str
    text: str  # extracted plain text with "## " / "### " heading lines
    section: str
    lastmod: str | None
    source: str  # "support_site"
    audience: Audience


class Source(Protocol):
    name: str
    audience: Audience

    def iter_documents(self) -> Iterator[Document]:
        """Documents from the local store. Never touches the network."""
        ...
