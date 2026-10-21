import re

import httpx
import pytest

from scis_help import cli
from scis_help.answer import engine as engine_module
from scis_help.answer.engine import Engine
from scis_help.common import paths
from scis_help.common.exitcodes import EXIT_CONFIG, EXIT_LLM, EXIT_NETWORK, EXIT_NO_INDEX, EXIT_OK
from scis_help.index import build, retrieve
from scis_help.index.build import build_index
from scis_help.llm import client as llm_client
from scis_help.sources import support_site
from tests.fakes import BASE, FAKE_MODEL, FakeSite, FakeSource, HashEmbedder, doc, make_settings
from tests.llm_fakes import FakeLlm, FakeRetriever, FakeSdk, response, timeout_error

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


# -- payload 02: ask, llm, gaps, feedback, eval --

VPN_DOCS = [
    doc("https://x.test/vpn/", "Example VPN", "## Windows\nInstall the VPN client now."),
    doc("https://x.test/print/", "Lab printers", "Printers are in room 201."),
]


@pytest.fixture
def small_index(fake_models):
    build_index(make_settings(), [FakeSource(VPN_DOCS)], embedder=HashEmbedder())


@pytest.fixture
def fake_llm(monkeypatch):
    """Engine(settings) inside the CLI gets this LLM instead of the real client."""
    llm = FakeLlm()

    class Factory:  # a class, so annotations like `LlmClient | None` still evaluate
        def __new__(cls, settings):
            return llm

    monkeypatch.setattr(engine_module, "LlmClient", Factory)
    monkeypatch.setattr(llm_client, "LlmClient", Factory)
    return llm


@pytest.fixture
def tty(monkeypatch):
    """Pretend a person is typing; the list holds their answers to input()."""
    lines: list[str] = []
    monkeypatch.setattr(cli, "_is_tty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": lines.pop(0))
    return lines


def answered_reply(text="Install it [S1]."):
    return {"status": "answered", "answer": text, "used_sources": ["S1"]}


def clarify_reply():
    return {
        "status": "clarify",
        "answer": "",
        "used_sources": [],
        "clarifying_question": "Windows or Mac?",
    }


def test_bare_question_without_key_gives_links_only(small_index, capsys):
    assert cli.main(["install", "the", "vpn", "client"]) == EXIT_OK
    out = capsys.readouterr().out
    assert out.startswith("I can't write an answer right now")
    assert "no API key is saved" in out and "llm set-key" in out
    assert "[1] Example VPN — https://x.test/vpn/" in out


def test_routing_keeps_flags():
    commands = cli._command_names(cli._parser())
    assert cli._route_question(["-v", "--desk", "vpn"], commands) == ["-v", "ask", "--desk", "vpn"]
    assert cli._route_question(["search", "vpn"], commands) == ["search", "vpn"]
    assert cli._route_question([], commands) == []


def test_ask_answered_desk_output_and_scrub_note(small_index, fake_llm, capsys):
    fake_llm.queue.append(answered_reply("Install the client [S1]."))
    assert cli.main(["--desk", "password: Hunter2 install the vpn client"]) == EXIT_OK
    out = capsys.readouterr().out.splitlines()
    assert out[0] == cli.SCRUB_NOTE
    assert out[1] == "Install the client [1]."
    assert "Sources:" in out and "[1] Example VPN — https://x.test/vpn/" in out
    assert "Also retrieved:" in out and "- Lab printers — https://x.test/print/" in out


def test_feedback_prompt_records_answer(small_index, fake_llm, tty, capsys):
    fake_llm.queue.append(answered_reply())
    tty.append("n")
    assert cli.main(["ask", "install the vpn client"]) == EXIT_OK
    assert "Thanks." in capsys.readouterr().out
    assert cli.main(["feedback"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "0 helpful, 1 not helpful" in out and "install the vpn client" in out


def test_non_tty_skips_the_feedback_prompt(small_index, fake_llm, capsys):
    fake_llm.queue.append(answered_reply())
    assert cli.main(["install the vpn client"]) == EXIT_OK
    assert "Did this help" not in capsys.readouterr().out


def test_no_feedback_flag(small_index, fake_llm, tty, capsys):
    fake_llm.queue.append(answered_reply())
    assert cli.main(["--no-feedback", "install the vpn client"]) == EXIT_OK
    assert "Did this help" not in capsys.readouterr().out


def test_clarify_loop_on_a_tty(small_index, fake_llm, tty, capsys):
    fake_llm.queue += [clarify_reply(), answered_reply()]
    tty += ["windows", ""]
    assert cli.main(["vpn client"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "Windows or Mac?" in out and "Install it [1]." in out
    assert "vpn client windows" in fake_llm.calls[1]["user"]


def test_clarify_without_tty_prints_question_and_exits_0(small_index, fake_llm, capsys):
    fake_llm.queue.append(clarify_reply())
    assert cli.main(["vpn client"]) == EXIT_OK
    assert capsys.readouterr().out.strip() == "Windows or Mac?"
    assert len(fake_llm.calls) == 1


def test_ask_without_index_exits_4(fake_models, capsys):
    assert cli.main(["vpn"]) == EXIT_NO_INDEX


def test_llm_status_without_key(capsys):
    assert cli.main(["llm", "status"]) == EXIT_OK
    out = capsys.readouterr().out
    assert "API key saved: no" in out and "LLM: enabled, model claude-haiku-5-5" in out
    assert "Budget: 0 of 200,000 tokens used" in out


def test_llm_test_without_key_exits_2(capsys):
    assert cli.main(["llm", "test"]) == EXIT_CONFIG
    assert "no API key saved" in capsys.readouterr().err


def test_llm_test_ok_and_failure(monkeypatch, memory_keyring, capsys):
    memory_keyring.set_password("scis-help", "anthropic-api-key", "sk-ant-test")
    sdk = FakeSdk([response({"ok": True}, input_tokens=40, output_tokens=12)])
    monkeypatch.setattr(llm_client.anthropic, "Anthropic", lambda **kwargs: sdk)
    monkeypatch.setenv("SCIS_HELP_LLM__ENABLED", "false")  # llm test still runs
    assert cli.main(["llm", "test"]) == EXIT_OK
    out = capsys.readouterr().out
    assert re.match(r"LLM test: ok \(in 40, out 12 tokens, ~\$\d\.\d{4}\)\.", out)
    sdk.queue.append(timeout_error())
    assert cli.main(["llm", "test"]) == EXIT_LLM
    assert "failed (timeout)" in capsys.readouterr().err


def test_set_key_hidden_prompt_prefix_check_and_replace(monkeypatch, memory_keyring, tty, capsys):
    keys = iter(["not-a-key", "sk-ant-first", "sk-ant-second"])
    monkeypatch.setattr(cli.getpass, "getpass", lambda prompt="": next(keys))
    stored = ("scis-help", "anthropic-api-key")
    assert cli.main(["llm", "set-key"]) == EXIT_CONFIG
    assert "not-a-key" not in capsys.readouterr().err
    assert cli.main(["llm", "set-key"]) == EXIT_OK
    tty.append("n")
    assert cli.main(["llm", "set-key"]) == EXIT_OK
    assert memory_keyring.passwords[stored] == "sk-ant-first"
    tty.append("y")
    assert cli.main(["llm", "set-key"]) == EXIT_OK
    assert memory_keyring.passwords[stored] == "sk-ant-second"
    assert "sk-ant" not in capsys.readouterr().out


def test_gaps_report(capsys):
    assert cli.main(["gaps"]) == EXIT_OK
    assert "No unanswered questions" in capsys.readouterr().out
    engine = Engine(make_settings(), retriever=FakeRetriever(confident=False), llm=FakeLlm())
    engine.ask("Parking permit?")
    engine.ask("parking permit")
    assert cli.main(["gaps", "--days", "7"]) == EXIT_OK
    assert "   2  parking permit" in capsys.readouterr().out


QUESTIONS_YAML = """\
- id: q01
  question: install the vpn client
  expected_urls: ["https://x.test/vpn/"]
  expect: answered
  split: dev
- id: q02
  question: printers room
  expected_urls: ["https://x.test/print/"]
  expect: answered
  split: holdout
- id: q03
  question: zebra giraffe
  expected_urls: []
  expect: not_covered
  split: dev
"""


def test_eval_retrieval_and_answers(small_index, fake_llm, tmp_path, memory_keyring, capsys):
    questions = tmp_path / "evals" / "questions.yaml"
    questions.parent.mkdir()
    questions.write_text(QUESTIONS_YAML, encoding="utf-8")
    assert cli.main(["eval", "retrieval", "--questions", str(questions)]) == EXIT_OK
    out = capsys.readouterr().out
    assert "q01  answered     rank 1" in out and "q02" not in out
    assert "| dev | 1 | 100% | 100% | 1.0 |" in out
    assert list((tmp_path / "evals" / "results").glob("*-dev.json"))

    assert cli.main(["eval", "answers", "--questions", str(questions), "--yes"]) == EXIT_CONFIG
    memory_keyring.set_password("scis-help", "anthropic-api-key", "sk-ant-test")
    fake_llm.queue += [answered_reply(), {"verdict": "supported", "reason": "ok"}]
    args = ["eval", "answers", "--questions", str(questions), "--yes", "--judge"]
    assert cli.main(args) == EXIT_OK
    out = capsys.readouterr().out
    assert "| dev | 1 + 1 | 100% |" in out and "| 100% (0%) |" in out
    assert not (paths.data_dir() / "unanswered.jsonl").exists()


def test_eval_answers_asks_before_spending(
    small_index, fake_llm, tmp_path, memory_keyring, tty, capsys
):
    memory_keyring.set_password("scis-help", "anthropic-api-key", "sk-ant-test")
    questions = tmp_path / "q.yaml"
    questions.write_text(QUESTIONS_YAML, encoding="utf-8")
    tty.append("n")
    assert cli.main(["eval", "answers", "--questions", str(questions)]) == EXIT_OK
    assert fake_llm.calls == []


def test_eval_bad_questions_file_exits_2(small_index, tmp_path, capsys):
    bad = tmp_path / "q.yaml"
    bad.write_text("- id: q1\n  question: x\n  expect: answered\n  split: dev\n", encoding="utf-8")
    assert cli.main(["eval", "retrieval", "--questions", str(bad)]) == EXIT_CONFIG
    assert "needs expected_urls" in capsys.readouterr().err
