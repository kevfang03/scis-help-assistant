import threading

from scis_help.web.ratelimit import RateLimiter
from scis_help.web.sessions import MAX_ANSWERS, SessionStore
from tests.web.fakes import Conversation, answered


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_rate_limit_sliding_window():
    clock = Clock()
    limiter = RateLimiter(3, clock=clock)
    assert [limiter.check("a") for _ in range(3)] == [None, None, None]
    clock.now += 20
    assert limiter.check("a") == 40  # the first hit leaves the window in 40 s
    assert limiter.check("b") is None  # per client
    clock.now += 40
    assert limiter.check("a") is None
    assert limiter.check("a") is None
    assert limiter.check("a") is None
    assert limiter.check("a") is not None  # the two hits at +20 s are still in the window


def test_rate_limit_refusals_do_not_extend_the_wait_and_old_clients_are_dropped():
    clock = Clock()
    limiter = RateLimiter(1, clock=clock)
    assert limiter.check("a") is None
    for _ in range(5):
        clock.now += 10
        limiter.check("a")
    clock.now += 10.5
    assert limiter.check("b") is None
    assert len(limiter) == 1  # "a" left the window and is forgotten
    assert limiter.check("a") is None


def test_retry_after_is_at_least_one_second():
    clock = Clock()
    limiter = RateLimiter(1, clock=clock)
    limiter.check("a")
    clock.now += 59.9
    assert limiter.check("a") == 1


def store(clock: Clock, **kwargs) -> SessionStore:
    options = {"idle_minutes": 60, "max_sessions": 10} | kwargs
    return SessionStore(new_conversation=Conversation, clock=clock, **options)


def test_same_id_same_session_unknown_id_new_session():
    sessions = store(Clock())
    first = sessions.get("s1")
    first.conversation.messages = 3
    assert sessions.get("s1") is first
    assert sessions.get("s2") is not first
    assert sessions.get("s2").conversation.messages == 0
    assert sessions.find("missing") is None
    assert len(sessions) == 2


def test_idle_sessions_expire():
    clock = Clock()
    sessions = store(clock, idle_minutes=5)
    old = sessions.get("old")
    clock.now += 4 * 60
    sessions.get("fresh")
    clock.now += 61
    assert sessions.find("old") is None
    assert sessions.find("fresh") is not None
    assert sessions.get("old") is not old  # an expired id starts over


def test_least_recently_seen_sessions_are_evicted_beyond_the_cap():
    clock = Clock()
    sessions = store(clock, max_sessions=10)
    for i in range(10):
        clock.now += 1
        sessions.get(f"s{i}")
    clock.now += 1
    sessions.get("s0")  # touched: now the most recent
    sessions.get("s10")
    assert len(sessions) == 10
    assert sessions.find("s1") is None
    assert sessions.find("s0") is not None


def test_answers_kept_per_session_and_rated_once():
    session = store(Clock()).get("s")
    answers = [answered() for _ in range(MAX_ANSWERS + 2)]
    for answer in answers:
        session.remember(answer)
    assert len(session.answers) == MAX_ANSWERS
    assert session.take_for_feedback(answers[0].answer_id) is None  # dropped as oldest
    last = answers[-1].answer_id
    assert session.take_for_feedback(last) is answers[-1]
    assert session.take_for_feedback(last) is None


def test_store_is_thread_safe():
    sessions = store(Clock(), max_sessions=50)

    def worker(n: int) -> None:
        for i in range(200):
            sessions.get(f"t{n}-{i % 30}")

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(sessions) == 50
