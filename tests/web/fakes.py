"""Test doubles for the web app: payload 02's answer types (same shapes) and a scripted engine.

Until payload 02 lands on main, these stand in for ``scis_help.answer.models`` and
``scis_help.answer.engine.Engine``; the app only touches the fields listed in 02's interfaces.
"""

import threading
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

Mode = Literal["public", "desk"]
Status = Literal["answered", "clarify", "not_covered", "links_only"]

VPN_URL = "https://support.example.test/getting-connected/example-vpn/"
PRINT_URL = "https://support.example.test/printing/lab-printers/"
DRIVE_URL = "https://support.example.test/getting-connected/shared-drive/"
HELP_CONTACT = "the help desk: https://support.example.test/ (Talk to a person)"


@dataclass(frozen=True, slots=True)
class SourceLink:
    n: int
    url: str
    title: str
    heading: str
    used: bool


@dataclass(frozen=True, slots=True)
class LlmUsage:
    model: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: float


@dataclass(frozen=True, slots=True)
class Answer:
    answer_id: str
    status: Status
    text: str
    sources: list[SourceLink]
    clarifying_question: str | None
    scrub_notice: bool
    question_scrubbed: str
    mode: Mode
    reason: str | None
    usage: LlmUsage | None


@dataclass
class Conversation:
    pending_question: str | None = None
    clarify_asked: bool = False
    messages: int = 0
    tokens: int = 0


@dataclass
class FakeAnswerSettings:
    help_contact: str = HELP_CONTACT
    max_message_chars: int = 500
    links_when_unavailable: int = 3


def answered(
    text: str = "Install the VPN client, then sign in [1].",
    *,
    mode: Mode = "public",
    question: str = "",
) -> Answer:
    sources = [SourceLink(1, VPN_URL, "Example VPN", "Windows", True)]
    if mode == "desk":
        sources.append(SourceLink(0, PRINT_URL, "Lab printers", "", False))
    return make_answer("answered", text, sources, mode=mode, question=question)


def make_answer(
    status: Status,
    text: str,
    sources: list[SourceLink] | None = None,
    *,
    clarifying_question: str | None = None,
    scrub_notice: bool = False,
    mode: Mode = "public",
    question: str = "",
    reason: str | None = None,
    tokens: int = 0,
) -> Answer:
    usage = LlmUsage("fake-model", tokens, 0, 0, 0, 0.0) if tokens else None
    return Answer(
        answer_id=uuid.uuid4().hex,
        status=status,
        text=text,
        sources=sources or [],
        clarifying_question=clarifying_question,
        scrub_notice=scrub_notice,
        question_scrubbed=question,
        mode=mode,
        reason=reason,
        usage=usage,
    )


@dataclass
class FakeEngine:
    """Answers with ``reply(message, mode, conversation)`` and does the bookkeeping the real
    engine does: counts messages, adds tokens and handles the one clarification."""

    reply: Callable[[str, Mode, Conversation], Answer] | None = None
    tokens_per_answer: int = 100
    calls: list[tuple[str, Mode]] = field(default_factory=list)
    threads: set[int] = field(default_factory=set)

    def ask(
        self, message: str, *, mode: Mode = "public", conversation: Conversation | None = None
    ) -> Answer:
        conversation = conversation if conversation is not None else Conversation()
        self.calls.append((message, mode))
        self.threads.add(threading.get_ident())
        conversation.messages += 1
        query = message
        if conversation.pending_question is not None:
            query = f"{conversation.pending_question} {message}"
            conversation.pending_question = None
        if self.reply is not None:
            answer = self.reply(query, mode, conversation)
        else:
            answer = answered(mode=mode, question=query)
        if answer.status == "clarify":
            conversation.clarify_asked = True
            conversation.pending_question = query
        conversation.tokens += self.tokens_per_answer
        return answer
