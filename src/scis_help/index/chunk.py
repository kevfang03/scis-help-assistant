"""Split documents into retrieval chunks (ARCHITECTURE.md D2).

Sections start at ``## `` / ``### `` lines and keep their heading path (``"H2 › H3"``). A
section under ``min_words`` is merged with the next one; a section over ``max_words`` is split
on sentence boundaries with a one-sentence overlap. Pages under 30 words are one chunk.
"""

import math
import re
from dataclasses import dataclass

from scis_help.sources.base import Document

HEADING_SEPARATOR = " › "
SHORT_PAGE_WORDS = 30

# A sentence ends at . ! or ? followed by whitespace; lines (list items, steps) are units too.
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True, slots=True)
class ChunkDraft:
    heading: str  # "H2 › H3", "H2", "H3" or "" (text before the first heading)
    text: str


@dataclass(slots=True)
class _Section:
    heading: str
    lines: list[str]

    @property
    def words(self) -> int:
        return sum(len(line.split()) for line in self.lines)


def embed_text(title: str, heading: str, text: str) -> str:
    """The text that is embedded and BM25-indexed for one chunk."""
    label = f"{title}{HEADING_SEPARATOR}{heading}" if heading else title
    return f"{label}\n{text}"


def _heading_text(line: str) -> tuple[int, str] | None:
    if line.startswith("### "):
        return 3, line[4:].strip()
    if line.startswith("## "):
        return 2, line[3:].strip()
    return None


def _sections(text: str) -> list[_Section]:
    sections: list[_Section] = [_Section("", [])]
    h2 = h3 = ""
    for line in text.splitlines():
        if not line.strip():
            continue
        heading = _heading_text(line)
        if heading is None:
            sections[-1].lines.append(line)
            continue
        level, value = heading
        if level == 2:
            h2, h3 = value, ""
        else:
            h3 = value
        path = HEADING_SEPARATOR.join(part for part in (h2, h3) if part)
        sections.append(_Section(path, []))
    return [section for section in sections if section.lines]


def _merge(sections: list[_Section], min_words: int, max_words: int) -> list[_Section]:
    """Merge small sections into the next one (or, for the last, the previous one) while the
    result stays within ``max_words``. A merged-in section keeps its heading as a text line."""
    merged: list[_Section] = []
    for section in sections:
        if merged:
            last = merged[-1]
            if last.words < min_words and last.words + section.words <= max_words:
                label = [section.heading.split(HEADING_SEPARATOR)[-1]] if section.heading else []
                last.lines.extend(label + section.lines)
                if not last.heading:
                    last.heading = section.heading
                continue
        merged.append(_Section(section.heading, list(section.lines)))
    if len(merged) > 1:
        tail, previous = merged[-1], merged[-2]
        if tail.words < min_words and previous.words + tail.words <= max_words:
            label = [tail.heading.split(HEADING_SEPARATOR)[-1]] if tail.heading else []
            previous.lines.extend(label + tail.lines)
            merged.pop()
    return merged


def _units(lines: list[str], max_words: int) -> list[tuple[str, bool]]:
    """Sentences as (text, ends_line). A sentence over ``max_words`` is cut by words."""
    units: list[tuple[str, bool]] = []
    for line in lines:
        sentences = [part for part in _SENTENCE_END.split(line) if part.strip()]
        for position, sentence in enumerate(sentences):
            words = sentence.split()
            pieces = [
                " ".join(words[start : start + max_words])
                for start in range(0, len(words), max_words)
            ]
            for piece_number, piece in enumerate(pieces):
                ends_line = position == len(sentences) - 1 and piece_number == len(pieces) - 1
                units.append((piece, ends_line))
    return units


def _join(units: list[tuple[str, bool]]) -> str:
    text = ""
    for unit, ends_line in units:
        text += unit + ("\n" if ends_line else " ")
    return text.strip()


def _split(section: _Section, max_words: int) -> list[str]:
    """Split a long section into pieces of about equal size, each piece starting with the last
    sentence of the one before it."""
    units = _units(section.lines, max_words)
    sizes = [len(unit.split()) for unit, _ in units]
    total = sum(sizes)
    pieces_wanted = math.ceil(total / max_words)
    target = math.ceil(total / pieces_wanted)
    pieces: list[str] = []
    start = 0
    while start < len(units):
        end, words = start, 0
        while end < len(units) and (end == start or words + sizes[end] <= max_words):
            words += sizes[end]
            end += 1
            if words >= target:
                break
        pieces.append(_join(units[start:end]))
        if end >= len(units):
            break
        # One-sentence overlap, unless that sentence alone made up the piece.
        start = end - 1 if end - 1 > start else end
    return pieces


def chunk_document(doc: Document, *, min_words: int, max_words: int) -> list[ChunkDraft]:
    """The chunks of one document, in page order."""
    all_lines = [line for line in doc.text.splitlines() if line.strip()]
    body_words = sum(len(line.split()) for line in all_lines if _heading_text(line) is None)
    if body_words < SHORT_PAGE_WORDS:
        plain = [(_heading_text(line) or (0, line))[1] for line in all_lines]
        return [ChunkDraft("", "\n".join(plain))]
    drafts: list[ChunkDraft] = []
    for section in _merge(_sections(doc.text), min_words, max_words):
        if section.words <= max_words:
            drafts.append(ChunkDraft(section.heading, "\n".join(section.lines)))
        else:
            pieces = _split(section, max_words)
            drafts.extend(ChunkDraft(section.heading, piece) for piece in pieces)
    return drafts
