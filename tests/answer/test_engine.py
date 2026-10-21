import json
from dataclasses import replace

import pytest

from scis_help.answer.engine import Engine
from scis_help.answer.models import Conversation
from scis_help.answer.prompts import LINKS_ONLY_TEXT, SYSTEM_PROMPT
from scis_help.common import paths
from scis_help.common.config import AnswerSettings, LlmSettings, Settings
from scis_help.common.logsetup import configure_logging
from scis_help.feedback import gaps
from scis_help.llm.client import LlmFailed, LlmUnavailable
from tests.llm_fakes import FakeLlm, FakeRetriever, chunk

CONTACT = 'the help desk: https://support.cci.drexel.edu/ ("Talk to a person")'


def answered(text="Do step one [S1].", used=("S1",)):
    return {"status": "answered", "answer": text, "used_sources": list(used)}


def make(*replies, confident=True, chunks=None, **answer):
    retriever = FakeRetriever(confident=confident)
    if chunks is not None:
        retriever.chunks = chunks
    llm = FakeLlm(list(replies))
    settings = Settings(answer=AnswerSettings(**answer))
    return Engine(settings, retriever=retriever, llm=llm), retriever, llm


def unanswered_lines():
    path = paths.data_dir() / gaps.FILE_NAME
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_answered_numbers_sources_and_links_from_chunks():
    engine, retriever, llm = make(answered("First [S2]. Then [S1, S3].", ["S1", "S2", "S3"]))
    answer = engine.ask("how do I do it")
    assert answer.status == "answered" and answer.reason is None
    assert answer.text == "First [1]. Then [2][3]."
    assert [(s.n, s.url, s.used) for s in answer.sources] == [
        (1, "https://support.example.test/page-2/", True),
        (2, "https://support.example.test/page-1/", True),
        (3, "https://support.example.test/page-3/", True),
    ]
    assert answer.sources[0].title == "Page 2" and answer.sources[0].heading == "Steps"
    assert llm.calls[0]["system"] == SYSTEM_PROMPT and llm.calls[0]["max_tokens"] == 1000


def test_user_turn_has_mode_sources_without_urls_and_question():
    engine, _, llm = make(answered())
    engine.ask("vpn drops")
    user = llm.calls[0]["user"]
    assert user.startswith("Mode: public")
    assert "[S1] Page 1 › Steps\nText of page 1." in user and "[S3] Page 3" in user
    assert "http" not in user
    assert "Clarifying question already asked: no" in user
    assert user.rstrip().endswith("Question: vpn drops")


def test_low_confidence_skips_the_llm_and_logs_the_gap():
    engine, _, llm = make(confident=False)
    answer = engine.ask("best pizza near campus")
    assert answer.status == "not_covered" and answer.reason == "low_confidence"
    assert answer.text == f"The SCIS support pages don't cover this. Talk to a person at {CONTACT}."
    assert llm.calls == [] and answer.usage is None and answer.sources == []
    [line] = unanswered_lines()
    assert line["question"] == "best pizza near campus" and line["mode"] == "public"
    assert line["top_url"] == "https://support.example.test/page-1/"


def test_invented_source_id_is_dropped_with_its_citation():
    engine, _, _ = make(answered("Do it [S1]. Also this [S9]. Both [S1, S7].", ["S1", "S9"]))
    answer = engine.ask("q")
    assert answer.text == "Do it [1]. Also this . Both [1]."
    assert [s.url for s in answer.sources] == ["https://support.example.test/page-1/"]


def test_answered_without_a_valid_source_becomes_not_covered():
    engine, _, _ = make(answered("Trust me [S8].", ["S8"]))
    answer = engine.ask("q")
    assert answer.status == "not_covered" and answer.reason == "model"
    assert CONTACT in answer.text
    assert len(unanswered_lines()) == 1


def test_model_not_covered_is_logged():
    engine, _, _ = make({"status": "not_covered", "answer": "No.", "used_sources": []})
    answer = engine.ask("parking permit")
    assert answer.status == "not_covered" and answer.reason == "model"
    assert unanswered_lines()[0]["reason"] == "model"


def test_used_source_listed_but_not_cited_still_counts():
    engine, _, _ = make(answered("Do step one.", ["S2"]))
    answer = engine.ask("q")
    assert answer.status == "answered"
    assert [s.n for s in answer.sources] == [1]


def test_links_come_from_chunks_even_when_the_model_writes_urls():
    reply = answered(
        "See [the guide](https://evil.example/phish) or https://evil.example/x [S1].", ["S1"]
    )
    engine, _, _ = make(reply)
    answer = engine.ask("q")
    assert "evil.example" not in answer.text
    assert "the guide" in answer.text
    assert [s.url for s in answer.sources] == ["https://support.example.test/page-1/"]


def with_text(n: int, text: str):
    return replace(chunk(n), text=text)


def test_url_verbatim_in_a_source_is_kept_as_code():
    source = "Open http://neverssl.com or ping tux.cs.example.edu to test."
    chunks = [with_text(1, source), chunk(2)]
    reply = answered("Open http://neverssl.com. Then ping tux.cs.example.edu [S1].", ["S1"])
    engine, _, _ = make(reply, chunks=chunks)
    answer = engine.ask("is my internet working")
    assert answer.text == "Open `http://neverssl.com`. Then ping `tux.cs.example.edu` [1]."


def test_invented_url_and_host_are_stripped():
    chunks = [with_text(1, "Open neverssl.com to test."), chunk(2)]
    reply = answered(
        "Open https://neverssl.com/ or www.evil.example and fake-help.com, see neverssl.com [S1].",
        ["S1"],
    )
    engine, _, _ = make(reply, chunks=chunks)
    answer = engine.ask("q")
    assert "evil" not in answer.text and "fake-help" not in answer.text
    assert "https://" not in answer.text
    assert answer.text.endswith("see `neverssl.com` [1].")


def test_url_from_a_source_that_was_not_provided_is_stripped():
    # The host is on another page of the site, but that chunk was not sent to the model; the
    # page URLs from the index metadata are not in any chunk text either.
    reply = answered("Use vpn.example.edu or https://support.example.test/page-1/ [S1].", ["S1"])
    engine, retriever, _ = make(reply)
    assert all("vpn.example.edu" not in c.text for c in retriever.chunks)
    answer = engine.ask("q")
    assert answer.text == "Use  or  [1]."


def test_urls_inside_code_spans_and_emails():
    chunks = [with_text(1, "Run ssh user@tux.example.edu or mail help@example.edu."), chunk(2)]
    reply = answered(
        "Run `ssh user@tux.example.edu`, not `evil.example.com`, or mail help@example.edu [S1].",
        ["S1"],
    )
    engine, _, _ = make(reply, chunks=chunks)
    answer = engine.ask("q")
    assert answer.text == "Run `ssh user@tux.example.edu`, not , or mail help@example.edu [1]."


def test_two_chunks_of_one_page_share_a_number():
    url = "https://support.example.test/vpn/"
    chunks = [chunk(1, url, "Windows"), chunk(2, url, "Mac"), chunk(3)]
    engine, _, _ = make(
        answered("Win [S1]. Mac [S2]. Other [S3].", ["S1", "S2", "S3"]), chunks=chunks
    )
    answer = engine.ask("q")
    assert answer.text == "Win [1]. Mac [1]. Other [2]."
    assert [(s.n, s.heading) for s in answer.sources] == [(1, "Windows"), (2, "Steps")]


def test_desk_mode_lists_also_retrieved_and_uses_desk_tokens():
    engine, retriever, llm = make(answered("Do [S2].", ["S2"]))
    answer = engine.ask("q", mode="desk")
    assert retriever.queries == [("q", "desk")]
    assert llm.calls[0]["max_tokens"] == 1400 and llm.calls[0]["user"].startswith("Mode: desk")
    assert [(s.n, s.used, s.url[-7:]) for s in answer.sources] == [
        (1, True, "page-2/"),
        (0, False, "page-1/"),
        (0, False, "page-3/"),
    ]


def test_public_mode_has_no_also_retrieved():
    engine, _, _ = make(answered())
    assert all(s.used for s in engine.ask("q").sources)


def test_clarify_once_then_follow_up_merges_question():
    clarify = {
        "status": "clarify",
        "answer": "I can help.",
        "used_sources": [],
        "clarifying_question": "Are you on the VPN?",
    }
    engine, retriever, llm = make(clarify, answered())
    conversation = Conversation()
    first = engine.ask("internet broken", conversation=conversation)
    assert first.status == "clarify" and first.clarifying_question == "Are you on the VPN?"
    assert first.sources == []
    assert conversation.clarify_asked and conversation.pending_question == "internet broken"
    second = engine.ask("yes, at home", conversation=conversation)
    assert second.status == "answered"
    assert retriever.queries[1][0] == "internet broken yes, at home"
    assert conversation.pending_question is None
    assert "Clarifying question already asked: yes" in llm.calls[1]["user"]
    assert conversation.messages == 2 and conversation.tokens > 0


def test_second_clarify_becomes_not_covered():
    clarify = {
        "status": "clarify",
        "answer": "",
        "used_sources": [],
        "clarifying_question": "Which one?",
    }
    engine, _, llm = make(clarify)
    conversation = Conversation(clarify_asked=True)
    answer = engine.ask("q", conversation=conversation)
    assert answer.status == "not_covered" and answer.reason == "model"
    assert len(llm.calls) == 1


@pytest.mark.parametrize(
    ("reply", "reason"),
    [
        (LlmUnavailable("budget"), "budget"),
        (LlmUnavailable("no_key"), "no_key"),
        (LlmUnavailable("disabled"), "disabled"),
        (LlmFailed("timeout"), "llm_failed:timeout"),
        (LlmFailed("bad_output"), "llm_failed:bad_output"),
        ("refused", "refused"),
        ("truncated", "llm_failed:truncated"),
    ],
)
def test_llm_problems_give_links_only(reply, reason):
    url = "https://support.example.test/vpn/"
    chunks = [chunk(1, url), chunk(2, url), chunk(3), chunk(4), chunk(5)]
    engine, _, _ = make(reply, chunks=chunks)
    answer = engine.ask("q")
    assert answer.status == "links_only" and answer.reason == reason
    assert answer.text == LINKS_ONLY_TEXT
    assert [(s.n, s.url, s.used) for s in answer.sources] == [
        (1, url, False),
        (2, "https://support.example.test/page-3/", False),
        (3, "https://support.example.test/page-4/", False),
    ]
    assert unanswered_lines() == []


def test_empty_message_rejected_and_long_message_cut():
    engine, retriever, _ = make(answered(), max_message_chars=50)
    with pytest.raises(ValueError):
        engine.ask("   ")
    engine.ask("word " * 40)
    query = retriever.queries[0][0]
    assert len(query) <= 50 and query.endswith("word")


def test_scrubbed_text_is_all_that_reaches_retriever_llm_and_logs(isolated_home):
    configure_logging(paths.log_dir())
    engine, retriever, llm = make(answered())
    answer = engine.ask("my password is Hunter2 and my id is 12345678, vpn fails")
    assert answer.scrub_notice
    assert answer.question_scrubbed == "my password is [PASSWORD] and my id is [ID], vpn fails"
    assert "Hunter2" not in retriever.queries[0][0] and "12345678" not in retriever.queries[0][0]
    assert "Hunter2" not in llm.calls[0]["user"] and "12345678" not in llm.calls[0]["user"]
    # and a gated one, which writes the gaps log
    engine_gated, _, _ = make(confident=False)
    engine_gated.ask("password: Hunter2 12345678 parking")
    for path in isolated_home.rglob("*"):
        if path.is_file():
            text = path.read_text(encoding="utf-8", errors="replace")
            assert "Hunter2" not in text and "12345678" not in text, path


def test_answer_log_line_has_no_text(isolated_home):
    configure_logging(paths.log_dir())
    engine, _, _ = make(answered("SECRET-ANSWER [S1]."))
    engine.ask("SECRET-QUESTION")
    log_text = (paths.log_dir() / "scis-help.log").read_text(encoding="utf-8")
    line = json.loads(next(line for line in log_text.splitlines() if '"answer"' in line))
    assert line["status"] == "answered" and line["hits"] == 3 and line["sources_used"] == 1
    assert "SECRET" not in log_text


def test_disabled_llm_through_the_real_client():
    from scis_help.llm.client import LlmClient

    settings = Settings(llm=LlmSettings(enabled=False))
    engine = Engine(settings, retriever=FakeRetriever(), llm=LlmClient(settings, api_key=None))
    answer = engine.ask("vpn")
    assert answer.status == "links_only" and answer.reason == "disabled"


def test_system_prompt_is_long_enough_to_cache():
    assert len(SYSTEM_PROMPT.split()) >= 450


def test_bare_number_citations_are_read_as_source_ids():
    engine, _, _ = make(answered("First [2]. Then [1, 3]. Bad [9].", ["S2", "1"]))
    answer = engine.ask("q")
    assert answer.text == "First [1]. Then [2][3]. Bad ."
    assert [s.url[-7:] for s in answer.sources] == ["page-2/", "page-1/", "page-3/"]


def test_clarify_text_is_just_the_question():
    clarify = {
        "status": "clarify",
        "answer": "I can help. Which one?",
        "used_sources": [],
        "clarifying_question": "Mac or Windows?",
    }
    engine, _, _ = make(clarify)
    answer = engine.ask("onedrive not syncing")
    assert answer.text == "Mac or Windows?" == answer.clarifying_question
