from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from scis_help.common.config import WebSettings
from scis_help.web import app as web_app
from tests.web.fakes import Conversation, FakeAnswerSettings, FakeEngine

LOCAL = ("127.0.0.1", 50001)
REMOTE = ("203.0.113.5", 50002)  # TEST-NET-3, never routed


@pytest.fixture
def feedback_log(monkeypatch):
    """Payload 02's conversation type, feedback store and key check, faked until 02 lands."""
    recorded: list[tuple[object, bool]] = []
    monkeypatch.setattr(web_app, "_new_conversation", Conversation)
    monkeypatch.setattr(web_app, "_record_feedback", lambda a, h: recorded.append((a, h)))
    monkeypatch.setattr(web_app, "_llm_state", lambda settings: "no_key")
    return recorded


@pytest.fixture
def make_client(feedback_log):
    def make(engine=None, *, client=LOCAL, **web):
        settings = SimpleNamespace(web=WebSettings(**web), answer=FakeAnswerSettings())
        engine = engine or FakeEngine()
        app = web_app.create_app(settings, engine=engine)
        return TestClient(app, client=client), engine

    return make
