import pytest
from fastapi.testclient import TestClient

from scis_help.feedback import feedback
from scis_help.web import app as web_app
from tests.web.fakes import FakeEngine, web_settings

LOCAL = ("127.0.0.1", 50001)
REMOTE = ("203.0.113.5", 50002)  # TEST-NET-3, never routed


@pytest.fixture
def feedback_log(monkeypatch):
    """Records each (answer, helpful) the app rates, and still writes the real feedback.jsonl."""
    recorded: list[tuple[object, bool]] = []

    def record(answer, helpful):
        recorded.append((answer, helpful))
        feedback.record(answer, helpful)

    monkeypatch.setattr(web_app, "_record_feedback", record)
    return recorded


@pytest.fixture
def make_client(feedback_log):
    def make(engine=None, *, client=LOCAL, **web):
        engine = engine or FakeEngine()
        app = web_app.create_app(web_settings(**web), engine=engine)
        return TestClient(app, client=client), engine

    return make
