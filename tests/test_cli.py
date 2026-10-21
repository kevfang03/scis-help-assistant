import re

import httpx
import pytest

from scis_help import cli
from scis_help.common.exitcodes import EXIT_CONFIG, EXIT_NETWORK, EXIT_NO_INDEX, EXIT_OK
from scis_help.index import build, retrieve
from scis_help.index.build import build_index
from scis_help.sources import support_site
from tests.fakes import BASE, FAKE_MODEL, FakeSite, FakeSource, HashEmbedder, doc, make_settings

HIT_LINE = re.compile(
    r"^ ?\d+\. rrf \d\.\d{4}  dense -?\d\.\d{3}  bm25 +(\d+\.\d{2}|-)  .+  https://\S+$"
)


@pytest.fixture
def fake_models(monkeypatch):
    """The CLI builds and loads with the fake embedder, never the real model."""
    monkeypatch.setenv("SCIS_HELP_INDEX__EMBED_MODEL", FAKE_MODEL)
    monkeypatch.setattr(retrieve, "get_embedder", lambda settings: HashEmbedder())
    monkeypatch.setattr(build, "get_embedder", lambda settings: HashEmbedder())


@pytest.fixture
def fake_crawl(monkeypatch):
    """`scis-help crawl` against the fake site, with no real waiting."""
    site = FakeSite.default()
    real_crawl = support_site.crawl
    monkeypatch.setenv("SCIS_HELP_CRAWL__BASE_URL", BASE)

    def crawl(settings, **kwargs):
        return real_crawl(settings, transport=site.transport, sleep=site.clock.sleep, **kwargs)

    monkeypatch.setattr(support_site, "crawl", crawl)
    return site


def test_search_output_shape(fake_models, capsys):
    docs = [
        doc("https://x.test/vpn/", "Example VPN", "## Windows\nInstall the VPN client now."),
        doc("https://x.test/print/", "Lab printers", "Printers are in room 201."),
    ]
    settings = make_settings()
    build_index(settings, [FakeSource(docs)], embedder=HashEmbedder())
    assert cli.main(["search", "install the vpn client"]) == EXIT_OK
    lines = capsys.readouterr().out.splitlines()
    assert HIT_LINE.match(lines[0]), lines[0]
    assert "Example VPN" in lines[0] and "https://x.test/vpn/" in lines[0]
    assert all(HIT_LINE.match(line) for line in lines[:-1])
    confident = r"^confident: (yes|no) \(best dense -?\d\.\d\d, best bm25 \d+\.\d\)$"
    assert re.match(confident, lines[-1])


def test_search_without_index_exits_4_with_hint(fake_models, capsys):
    assert cli.main(["search", "vpn"]) == EXIT_NO_INDEX
    err = capsys.readouterr().err
    assert "scis-help crawl" in err and "scis-help index" in err


def test_status_with_empty_data_folder(capsys, isolated_home):
    assert cli.main(["status"]) == EXIT_OK
    out = capsys.readouterr().out
    assert f"Data folder: {isolated_home / 'data'}" in out
    assert "Pages stored: 0 (never crawled)" in out
    assert "Index: none" in out


def test_crawl_index_status_end_to_end(fake_models, fake_crawl, capsys):
    assert cli.main(["crawl"]) == EXIT_OK
    out = capsys.readouterr().out
    assert out.strip() == "Crawl: 4 in sitemap, 4 fetched, 0 unchanged, 0 removed, 0 failed."

    assert cli.main(["index"]) == EXIT_OK
    out = capsys.readouterr().out
    assert re.match(r"^Index: 4 pages, \d+ chunks, model fake-hash-64, \d+\.\d s\.$", out.strip())

    assert cli.main(["status"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "Pages stored: 4 (last crawl" in out
    assert "Index: " in out and "model fake-hash-64" in out


def test_crawl_exits_3_when_rate_limited(fake_crawl, capsys):
    fake_crawl.routes["/sitemap.xml"] = httpx.Response(429)
    assert cli.main(["crawl"]) == EXIT_NETWORK
    assert "Stopped early: rate_limited." in capsys.readouterr().out


def test_index_without_pages_exits_4(fake_models, capsys):
    assert cli.main(["index"]) == EXIT_NO_INDEX
    assert "scis-help crawl" in capsys.readouterr().err


def test_bad_setting_exits_2(monkeypatch, capsys):
    monkeypatch.setenv("SCIS_HELP_CRAWL__DELAY_SECONDS", "0.1")
    assert cli.main(["status"]) == EXIT_CONFIG
    assert "Configuration error" in capsys.readouterr().err


def test_no_command_prints_help(capsys):
    assert cli.main([]) == EXIT_OK
    assert "crawl" in capsys.readouterr().out
