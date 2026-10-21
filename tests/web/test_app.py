import logging
import threading
import uuid

import pytest

from scis_help.web.app import is_loopback
from tests.web.conftest import REMOTE
from tests.web.fakes import DRIVE_URL, VPN_URL, FakeEngine, SourceLink, answered, make_answer


def sid() -> str:
    return str(uuid.uuid4())


def chat(client, session_id, message="vpn won't connect", mode="public"):
    return client.post(
        "/api/chat", json={"session_id": session_id, "message": message, "mode": mode}
    )


def test_chat_happy_path(make_client):
    client, engine = make_client()
    response = chat(client, sid(), "  vpn won't connect  ")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {
        "answer_id",
        "status",
        "answer_html",
        "sources",
        "clarifying_question",
        "scrub_notice",
        "notice",
    }
    assert body["status"] == "answered"
    assert f'<sup><a href="{VPN_URL}"' in body["answer_html"]
    assert body["sources"] == [
        {"n": 1, "url": VPN_URL, "title": "Example VPN", "heading": "Windows", "used": True}
    ]
    assert body["notice"] is None and body["scrub_notice"] is False
    assert engine.calls == [("vpn won't connect", "public")]
    assert response.headers["cache-control"] == "no-store"


def test_engine_runs_off_the_event_loop_thread(make_client):
    client, engine = make_client()
    chat(client, sid())
    assert engine.threads and threading.get_ident() not in engine.threads


def test_clarify_across_two_requests_in_one_session(make_client):
    def reply(query, mode, conversation):
        if not conversation.clarify_asked:
            return make_answer(
                "clarify", "", clarifying_question="Are you on the VPN or on campus Wi-Fi?"
            )
        return answered(question=query)

    client, engine = make_client(FakeEngine(reply=reply))
    session = sid()
    first = chat(client, session, "can't reach the shared drive").json()
    assert first["status"] == "clarify"
    assert first["clarifying_question"] == "Are you on the VPN or on campus Wi-Fi?"
    assert "Are you on the VPN or on campus Wi-Fi?" in first["answer_html"]
    second = chat(client, session, "on the VPN").json()
    assert second["status"] == "answered"
    assert engine.calls[1] == ("on the VPN", "public")
    assert chat(client, sid(), "on the VPN").json()["status"] == "clarify"  # a new session


def test_clarify_question_already_in_text_is_not_repeated(make_client):
    question = "Which printer?"
    engine = FakeEngine(
        reply=lambda q, m, c: make_answer("clarify", question, clarifying_question=question)
    )
    client, _ = make_client(engine)
    assert chat(client, sid()).json()["answer_html"].count(question) == 1


def test_scrub_notice_and_budget_notice(make_client):
    links = [SourceLink(1, DRIVE_URL, "Shared drive", "", True)]
    engine = FakeEngine(
        reply=lambda q, m, c: make_answer(
            "links_only",
            "I can't write an answer right now [1].",
            links,
            scrub_notice=True,
            reason="budget",
        )
    )
    client, _ = make_client(engine)
    body = chat(client, sid()).json()
    assert body["scrub_notice"] is True
    assert "budget" in body["notice"]
    assert body["status"] == "links_only"


def test_session_message_cap(make_client):
    client, engine = make_client(rate_per_minute=100, session_max_messages=20)
    session = sid()
    for _ in range(20):
        assert chat(client, session).json()["status"] == "answered"
    body = chat(client, session).json()
    assert body["status"] == "not_covered"
    assert body["notice"] and body["answer_id"] is None
    assert "This chat has reached its limit" in body["answer_html"]
    assert 'href="https://support.example.test/"' in body["answer_html"]  # the contact links
    assert len(engine.calls) == 20  # no engine call for message 21
    assert chat(client, sid()).json()["status"] == "answered"  # a new chat is fine


def test_session_token_cap(make_client):
    client, engine = make_client(FakeEngine(tokens_per_answer=600), session_max_tokens=1000)
    session = sid()
    assert chat(client, session).json()["status"] == "answered"  # 600 tokens
    assert chat(client, session).json()["status"] == "answered"  # 1,200 tokens
    assert chat(client, session).json()["notice"]
    assert len(engine.calls) == 2


def test_rate_limit_429_on_the_11th_message(make_client):
    client, engine = make_client(rate_per_minute=10)
    for i in range(10):
        assert chat(client, sid()).status_code == 200, i
    response = chat(client, sid())
    assert response.status_code == 429
    assert 1 <= int(response.headers["retry-after"]) <= 60
    assert "wait" in response.json()["detail"]
    assert len(engine.calls) == 10


@pytest.mark.parametrize(
    "payload",
    [
        {"session_id": "not-a-uuid", "message": "hi"},
        {"message": "hi"},
        {"session_id": str(uuid.uuid4())},
        {"session_id": str(uuid.uuid4()), "message": "   "},
        {"session_id": str(uuid.uuid4()), "message": "x" * 501},
        {"session_id": str(uuid.uuid4()), "message": "hi", "mode": "admin"},
        {"session_id": str(uuid.uuid4()), "message": 5},
    ],
)
def test_bad_input_is_400_and_never_echoed(make_client, payload):
    client, engine = make_client()
    response = client.post("/api/chat", json=payload)
    assert response.status_code == 400
    assert "x" * 50 not in response.text and "admin" not in response.text
    assert engine.calls == []


def test_malformed_json_is_400(make_client):
    client, _ = make_client()
    response = client.post(
        "/api/chat", content=b"{nope", headers={"content-type": "application/json"}
    )
    assert response.status_code == 400


def test_message_at_the_limit_is_accepted(make_client):
    client, _ = make_client()
    assert chat(client, sid(), "x" * 500).status_code == 200


def test_desk_from_a_non_loopback_client_is_403(make_client):
    client, engine = make_client(client=REMOTE)
    assert chat(client, sid(), mode="desk").status_code == 403
    assert client.get("/desk").status_code == 403
    assert chat(client, sid(), mode="public").status_code == 200
    assert engine.calls == [("vpn won't connect", "public")]


def test_desk_from_loopback_and_when_not_local_only(make_client):
    client, _ = make_client()
    body = chat(client, sid(), mode="desk").json()
    assert [s["used"] for s in body["sources"]] == [True, False]
    assert client.get("/desk").status_code == 200
    remote, _ = make_client(client=REMOTE, desk_local_only=False)
    assert chat(remote, sid(), mode="desk").status_code == 200


def test_is_loopback():
    assert is_loopback("127.0.0.1") and is_loopback("::1") and is_loopback("127.0.0.2")
    assert is_loopback("::ffff:127.0.0.1")
    assert not is_loopback("203.0.113.5") and not is_loopback("testclient")
    assert not is_loopback(None) and not is_loopback("")


def test_feedback_once_only(make_client, feedback_log):
    client, _ = make_client()
    session = sid()
    answer_id = chat(client, session).json()["answer_id"]
    rate = {"session_id": session, "answer_id": answer_id, "helpful": True}
    response = client.post("/api/feedback", json=rate)
    assert response.status_code == 204 and response.content == b""
    assert len(feedback_log) == 1
    answer, helpful = feedback_log[0]
    assert answer.answer_id == answer_id and helpful is True
    assert client.post("/api/feedback", json=rate | {"helpful": False}).status_code == 404
    assert len(feedback_log) == 1


def test_feedback_for_another_sessions_answer_is_404(make_client, feedback_log):
    client, _ = make_client()
    answer_id = chat(client, sid()).json()["answer_id"]
    other = {"session_id": sid(), "answer_id": answer_id, "helpful": False}
    assert client.post("/api/feedback", json=other).status_code == 404
    unknown = {"session_id": sid(), "answer_id": "nope", "helpful": True}
    assert client.post("/api/feedback", json=unknown).status_code == 404
    assert feedback_log == []


def test_feedback_bad_input_is_400(make_client):
    client, _ = make_client()
    bad = {"session_id": sid(), "answer_id": "x", "helpful": "yes"}
    assert client.post("/api/feedback", json=bad).status_code == 400


def test_health(make_client):
    client, _ = make_client()
    assert client.get("/api/health").json() == {"ok": True, "index_pages": 0, "llm": "no_key"}


def test_health_counts_index_pages(make_client):
    from scis_help.common import paths
    from scis_help.index import store

    store.write_manifest(paths.index_dir(), {"pages": 690, "chunks": 1027})
    client, _ = make_client()
    assert client.get("/api/health").json()["index_pages"] == 690


def test_cors_off_by_default_and_on_for_allowed_origins(make_client):
    origin = "http://127.0.0.1:9000"
    preflight = {"Origin": origin, "Access-Control-Request-Method": "POST"}
    client, _ = make_client()
    response = client.options("/api/chat", headers=preflight)
    assert "access-control-allow-origin" not in response.headers
    client, _ = make_client(allowed_origins=[origin])
    response = client.options("/api/chat", headers=preflight)
    assert response.headers["access-control-allow-origin"] == origin
    other = client.options("/api/chat", headers=preflight | {"Origin": "http://evil.test"})
    assert "access-control-allow-origin" not in other.headers


def test_engine_error_is_500_without_details(make_client, caplog):
    def boom(query, mode, conversation):
        raise RuntimeError("secret detail from the depths")

    client, _ = make_client(FakeEngine(reply=boom))
    with caplog.at_level(logging.DEBUG):
        response = chat(client, sid())
    assert response.status_code == 500
    assert "secret" not in response.text and "secret" not in caplog.text


def test_no_session_id_ip_or_message_in_logs(make_client, caplog):
    client, _ = make_client(client=REMOTE, rate_per_minute=2)
    session = sid()
    with caplog.at_level(logging.DEBUG):
        chat(client, session, "my vpn is broken")
        answer_id = chat(client, session, "my vpn is broken").json()["answer_id"]
        assert chat(client, session, "my vpn is broken").status_code == 429
        client.post(
            "/api/feedback", json={"session_id": session, "answer_id": answer_id, "helpful": False}
        )
    assert caplog.records
    for record in caplog.records:
        dump = repr(record.__dict__) + record.getMessage()
        assert session not in dump and session.replace("-", "") not in dump
        assert REMOTE[0] not in dump
        assert "my vpn is broken" not in dump
        assert "testclient" not in dump  # the user agent
