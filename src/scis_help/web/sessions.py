"""In-memory chat sessions for the web app (lost on restart; fine for a local demo).

A session is keyed by the opaque id the widget generates. It holds the engine's
``Conversation`` and the session's last answers, so feedback can only rate an answer from the
same session, once. Session ids are never logged or written anywhere.
"""

import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # payload 02's types; imported lazily so this module loads without them
    from scis_help.answer.models import Answer, Conversation

MAX_ANSWERS = 20  # answers kept per session for feedback


@dataclass
class Session:
    conversation: "Conversation"
    last_seen: float
    answers: "OrderedDict[str, Answer]" = field(default_factory=OrderedDict)
    rated: set[str] = field(default_factory=set)
    # Held while the engine works on this session, so two requests can't race on its
    # Conversation.
    lock: threading.Lock = field(default_factory=threading.Lock)

    def remember(self, answer: "Answer") -> None:
        self.answers[answer.answer_id] = answer
        while len(self.answers) > MAX_ANSWERS:
            self.answers.popitem(last=False)

    def take_for_feedback(self, answer_id: str) -> "Answer | None":
        """The answer to rate, or None if it isn't in this session or was already rated."""
        with self.lock:
            answer = self.answers.get(answer_id)
            if answer is None or answer_id in self.rated:
                return None
            self.rated.add(answer_id)
            return answer


class SessionStore:
    def __init__(
        self,
        *,
        idle_minutes: int,
        max_sessions: int,
        new_conversation: Callable[[], Any],
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._idle_seconds = idle_minutes * 60.0
        self._max_sessions = max_sessions
        self._new_conversation = new_conversation
        self._clock = clock
        self._sessions: OrderedDict[str, Session] = OrderedDict()  # least recently seen first
        self._lock = threading.Lock()

    def get(self, session_id: str) -> Session:
        """The session for ``session_id``; an unknown or expired id starts a new one."""
        now = self._clock()
        with self._lock:
            self._expire(now)
            session = self._sessions.get(session_id)
            if session is None:
                while len(self._sessions) >= self._max_sessions:
                    self._sessions.popitem(last=False)
                session = Session(conversation=self._new_conversation(), last_seen=now)
                self._sessions[session_id] = session
            else:
                session.last_seen = now
                self._sessions.move_to_end(session_id)
            return session

    def find(self, session_id: str) -> Session | None:
        """The live session for ``session_id``, without creating one."""
        now = self._clock()
        with self._lock:
            self._expire(now)
            session = self._sessions.get(session_id)
            if session is not None:
                session.last_seen = now
                self._sessions.move_to_end(session_id)
            return session

    def _expire(self, now: float) -> None:
        cutoff = now - self._idle_seconds
        while self._sessions:
            oldest = next(iter(self._sessions.values()))
            if oldest.last_seen > cutoff:
                break
            self._sessions.popitem(last=False)

    def __len__(self) -> int:
        with self._lock:
            return len(self._sessions)
