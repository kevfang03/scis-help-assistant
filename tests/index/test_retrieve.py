import json

import pytest

from scis_help.common import paths
from scis_help.common.config import IndexSettings
from scis_help.index.build import NoDocuments, build_index
from scis_help.index.retrieve import IndexMissing, Retriever
from tests.fakes import (
    ConstantEmbedder,
    FailingEmbedder,
    FakeSource,
    HashEmbedder,
    doc,
    make_settings,
)

DOCS = [
    doc("https://x.test/vpn/", "Example VPN", "Install the VPN client and connect to the gateway."),
    doc("https://x.test/print/", "Lab printers", "Printers are in room 201 on the second floor."),
    doc("https://x.test/wifi/", "Campus wifi", "Join the campus wireless network today."),
    doc("https://x.test/drive/", "Shared drive", "Map the EXREPO drive from File Explorer."),
]
DESK_DOC = doc(
    "https://wiki.test/desk/", "Desk notes", "Reset the lab kiosk with the desk key.", "desk"
)


def _build(docs=DOCS, embedder=None, **retrieval):
    settings = make_settings(**retrieval)
    build_index(settings, [FakeSource(list(docs))], embedder=embedder or HashEmbedder())
    return settings


def _load(settings, embedder=None) -> Retriever:
    return Retriever.load(settings, embedder=embedder or HashEmbedder())


def test_best_match_ranks_first_and_hit_fields():
    settings = _build()
    result = _load(settings).search("printers second floor")
    top = result.hits[0]
    assert top.chunk.url == "https://x.test/print/"
    assert top.rank == 1
    assert top.rrf > 0
    assert top.bm25 is not None and top.bm25 > 0
    assert top.chunk.title == "Lab printers"
    assert [hit.rank for hit in result.hits] == list(range(1, len(result.hits) + 1))


def test_lexical_only_match_ranks_first_via_bm25():
    # Every chunk gets the same vector, so only BM25 can find the right one.
    settings = _build(embedder=ConstantEmbedder())
    result = _load(settings, ConstantEmbedder()).search("EXREPO")
    assert result.hits[0].chunk.url == "https://x.test/drive/"
    assert [hit.chunk.url for hit in result.hits if hit.bm25 is not None] == [
        "https://x.test/drive/"
    ]


def test_k_limits_results():
    settings = _build()
    retriever = _load(settings)
    assert len(retriever.search("the network", k=2).hits) == 2
    assert len(retriever.search("the network").hits) == 4  # all pages, fewer than default k


def test_per_page_cap():
    sections = "\n".join(
        f"## Part {n}\n" + " ".join(f"printer queue step {n} word{i}." for i in range(8))
        for n in range(6)
    )
    long_doc = doc("https://x.test/long/", "Printer queue", sections)
    settings = _build(docs=[*DOCS, long_doc], max_chunks_per_page=2)
    result = _load(settings).search("printer queue step", k=5)
    urls = [hit.chunk.url for hit in result.hits]
    assert urls.count("https://x.test/long/") == 2
    assert len(urls) == 5


def test_audience_filter():
    settings = _build(docs=[*DOCS, DESK_DOC])
    retriever = _load(settings)
    public = retriever.search("reset the lab kiosk desk key", k=10)
    assert "https://wiki.test/desk/" not in [hit.chunk.url for hit in public.hits]
    desk = retriever.search("reset the lab kiosk desk key", k=10, audience="desk")
    assert desk.hits[0].chunk.url == "https://wiki.test/desk/"
    assert desk.hits[0].chunk.audience == "desk"


def test_confident_when_dense_score_is_high():
    settings = _build()
    result = _load(settings).search("Install the VPN client and connect to the gateway")
    assert result.best_dense >= 0.55
    assert result.confident


def test_not_confident_when_nothing_matches():
    settings = _build()
    result = _load(settings).search("best pizza near campus tonight")
    # "campus" overlaps one page a little; the scores stay low with the defaults.
    assert result.best_dense < 0.55
    assert result.best_bm25 < 5.0
    assert not result.confident


def test_confident_from_bm25_alone():
    settings = _build(embedder=ConstantEmbedder(), min_score=1.01, min_bm25=0.1)
    result = _load(settings, ConstantEmbedder()).search("EXREPO")
    assert result.best_bm25 >= 0.1
    assert result.confident


def test_blank_query_returns_nothing():
    settings = _build()
    result = _load(settings).search("   ")
    assert result.hits == [] and not result.confident


def test_missing_index_raises():
    with pytest.raises(IndexMissing, match="no index"):
        Retriever.load(make_settings(), embedder=HashEmbedder())


def test_model_mismatch_raises():
    settings = _build()
    other = settings.model_copy(update={"index": IndexSettings(embed_model="another/model")})
    with pytest.raises(IndexMissing, match="another/model"):
        Retriever.load(other, embedder=HashEmbedder())


def test_damaged_index_raises():
    settings = _build()
    (paths.index_dir() / "vectors.npy").unlink()
    with pytest.raises(IndexMissing, match="could not be read"):
        _load(settings)


def test_build_report_and_manifest():
    settings = make_settings()
    report = build_index(settings, [FakeSource(DOCS)], embedder=HashEmbedder())
    assert report.pages == 4
    assert report.chunks == 4
    assert report.model_id == "fake-hash-64"
    manifest = json.loads((paths.index_dir() / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["model_id"] == "fake-hash-64"
    assert manifest["dim"] == 64
    assert manifest["pages"] == 4
    assert manifest["chunks"] == 4
    assert manifest["schema"] == 1
    assert "built_at" in manifest


def test_failed_build_leaves_the_old_index():
    settings = _build()
    with pytest.raises(RuntimeError):
        build_index(
            settings,
            [FakeSource([*DOCS, DESK_DOC])],
            embedder=FailingEmbedder(),
        )
    retriever = _load(settings)
    result = retriever.search("reset the lab kiosk desk key", k=10, audience="desk")
    assert "https://wiki.test/desk/" not in [hit.chunk.url for hit in result.hits]
    leftovers = [p.name for p in paths.index_dir().parent.iterdir() if p.name.startswith(".")]
    assert leftovers == []


def test_rebuild_replaces_the_index():
    settings = _build()
    build_index(settings, [FakeSource([*DOCS, DESK_DOC])], embedder=HashEmbedder())
    result = _load(settings).search("reset the lab kiosk desk key", audience="desk")
    assert result.hits[0].chunk.url == "https://wiki.test/desk/"


def test_no_documents_raises():
    with pytest.raises(NoDocuments):
        build_index(make_settings(), [FakeSource([])], embedder=HashEmbedder())
    assert not paths.index_dir().exists()
