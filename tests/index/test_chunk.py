from scis_help.index.chunk import chunk_document, embed_text
from tests.fakes import doc


def _words(prefix: str, count: int) -> str:
    """A sentence of ``count`` words."""
    return " ".join(f"{prefix}{i}" for i in range(count)) + "."


def _chunks(text: str, min_words: int = 20, max_words: int = 60):
    return chunk_document(doc("u", "Title", text), min_words=min_words, max_words=max_words)


def test_heading_paths():
    text = "\n".join(
        [
            _words("intro", 25),
            "## Windows",
            _words("win", 25),
            "### Install",
            _words("install", 25),
            "## Mac",
            "### Connect",
            _words("mac", 25),
        ]
    )
    headings = [chunk.heading for chunk in _chunks(text)]
    assert headings == ["", "Windows", "Windows › Install", "Mac › Connect"]


def test_small_sections_merge_with_the_next():
    text = "\n".join(["## A", _words("a", 5), "## B", _words("b", 5), "## C", _words("c", 30)])
    chunks = _chunks(text)
    assert len(chunks) == 1
    assert chunks[0].heading == "A"
    lines = chunks[0].text.splitlines()
    assert "B" in lines and "C" in lines  # merged-in headings stay as text lines
    assert lines[0].startswith("a0")


def test_small_last_section_merges_back():
    text = "\n".join(["## A", _words("a", 30), "## B", _words("b", 5)])
    chunks = _chunks(text)
    assert len(chunks) == 1
    assert chunks[0].heading == "A"
    assert chunks[0].text.splitlines()[-1].startswith("b0")


def test_long_sections_split_with_one_sentence_overlap():
    sentences = [_words(f"s{n}w", 15) for n in range(10)]  # 150 words
    text = "## Long\n" + " ".join(sentences)
    chunks = _chunks(text, max_words=60)
    assert len(chunks) >= 3
    assert all(chunk.heading == "Long" for chunk in chunks)
    assert all(len(chunk.text.split()) <= 60 for chunk in chunks)
    for earlier, later in zip(chunks, chunks[1:], strict=False):
        last_sentence = earlier.text.split(". ")[-1].rstrip(".")
        assert later.text.startswith(last_sentence)
    covered = " ".join(chunk.text for chunk in chunks)
    assert all(sentence.rstrip(".") in covered for sentence in sentences)


def test_one_giant_sentence_is_cut_by_words():
    text = "## Code\n" + " ".join(f"tok{i}" for i in range(200))
    chunks = _chunks(text, max_words=60)
    assert all(len(chunk.text.split()) <= 60 for chunk in chunks)
    assert "tok199" in chunks[-1].text


def test_short_page_is_one_chunk():
    text = "## Overview\nA short listing page.\n### Child page\nOne line."
    chunks = _chunks(text)
    assert len(chunks) == 1
    assert chunks[0].heading == ""
    lines = chunks[0].text.splitlines()
    assert lines == ["Overview", "A short listing page.", "Child page", "One line."]


def test_embed_text():
    assert embed_text("VPN", "Windows › Install", "Run it.") == "VPN › Windows › Install\nRun it."
    assert embed_text("VPN", "", "Run it.") == "VPN\nRun it."
