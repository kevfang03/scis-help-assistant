import pytest
import uvicorn

from scis_help import cli
from scis_help.answer.engine import Engine
from scis_help.common.exitcodes import EXIT_CONFIG, EXIT_NO_INDEX, EXIT_OK
from scis_help.index import retrieve
from scis_help.index.build import build_index
from scis_help.llm.keys import set_api_key
from tests.fakes import FAKE_MODEL, FakeSource, HashEmbedder, doc, make_settings


@pytest.fixture
def served(monkeypatch, feedback_log):
    """`scis-help web` with a built fake index and uvicorn.run recorded instead of served."""
    monkeypatch.setenv("SCIS_HELP_INDEX__EMBED_MODEL", FAKE_MODEL)
    monkeypatch.setattr(retrieve, "get_embedder", lambda settings: HashEmbedder())
    docs = [doc("https://x.test/vpn/", "Example VPN", "Install the VPN client.")]
    build_index(make_settings(), [FakeSource(docs)], embedder=HashEmbedder())
    runs: list[dict] = []
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: runs.append(kwargs | {"app": app}))
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url: opened.append(url))
    return runs


def test_web_without_index_exits_4_with_hint(capsys, monkeypatch):
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: pytest.fail("server started"))
    assert cli.main(["web", "--no-browser"]) == EXIT_NO_INDEX
    err = capsys.readouterr().err
    assert "scis-help crawl" in err and "scis-help index" in err


def test_web_starts_on_loopback_without_access_log(served, capsys):
    assert cli.main(["web", "--no-browser"]) == EXIT_OK
    out = capsys.readouterr().out
    assert isinstance(served[0]["app"].state.engine, Engine)  # the real engine, built once
    assert "Open http://127.0.0.1:8765/ (Ctrl+C to stop)" in out
    assert "no API key saved" in out  # the in-memory test keyring is empty
    assert "Warning" not in out
    (run,) = served
    assert (run["host"], run["port"], run["access_log"]) == ("127.0.0.1", 8765, False)


def test_web_warns_when_not_on_loopback(served, capsys):
    assert cli.main(["web", "--no-browser", "--host", "0.0.0.0", "--port", "9001"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "no authentication" in out
    assert "Open http://0.0.0.0:9001/" in out
    assert (served[0]["host"], served[0]["port"]) == ("0.0.0.0", 9001)


def test_web_with_a_saved_key_has_no_note(served, capsys):
    set_api_key("sk-ant-test-0000")
    assert cli.main(["web", "--no-browser"]) == EXIT_OK
    assert "Note" not in capsys.readouterr().out


def test_web_bad_port(served, capsys):
    assert cli.main(["web", "--no-browser", "--port", "70000"]) == EXIT_CONFIG
    assert served == []
